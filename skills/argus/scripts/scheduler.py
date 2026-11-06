#!/usr/bin/env python3
"""Host-driven durable queue. Claims reserve work; they never invoke an LLM or runner."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'prd-to-cases/scripts'))
from generation import digest, require, path_names


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate(plan):
    require(plan.get('schemaVersion') == 1 and plan.get('runId'), 'invalid identity')
    for name in ('generationConcurrency', 'executionConcurrency'):
        require(type(plan.get(name)) is int and plan[name] > 0, 'invalid ' + name)
    require(isinstance(plan.get('inputHash'), str) and len(plan['inputHash']) == 64
            and all(c in '0123456789abcdef' for c in plan['inputHash']), 'invalid input hash')
    tasks = plan['tasks']
    require(isinstance(tasks, list) and tasks, 'empty tasks')
    ids = [t['taskId'] for t in tasks]
    require(all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids), 'invalid task IDs')
    outputs = []
    for task in tasks:
        require(task['platform'] in ('web', 'mobile', 'shared'), 'invalid platform')
        require(task['kind'] in ('generation', 'execution'), 'invalid kind')
        require(isinstance(task.get('taskType'), str) and task['taskType'], 'missing task type')
        deps = task.get('dependsOn', [])
        require(isinstance(deps, list) and set(deps) <= set(ids) and task['taskId'] not in deps, 'invalid dependencies')
        require(positive(task.get('estimatedSeconds', 1)) and positive(task.get('timeoutSeconds', 600)), 'invalid budget')
        require(type(task.get('priority', 0)) is int, 'invalid priority')
        require(type(task.get('maxAttempts', 1)) is int and task.get('maxAttempts', 1) > 0, 'invalid attempts')
        require(type(task.get('retrySafe', False)) is bool, 'invalid retry safety')
        resources = task.get('resources', [])
        require(isinstance(resources, list) and all(isinstance(r, str) and r for r in resources), 'invalid resources')
        require(task['kind'] != 'execution' or task['platform'] != 'mobile' or
                any(r.startswith('device:') for r in resources), 'mobile execution requires device lease')
        require(task['kind'] != 'generation' or not resources, 'generation must not lease execution resources')
        require(isinstance(task.get('outputs', []), list), 'invalid outputs')
        require(task['kind'] != 'generation' or task.get('outputs'), 'generation requires owned outputs')
        for output in task.get('outputs', []):
            require(isinstance(output, str) and output and not Path(output).is_absolute()
                    and '..' not in Path(output).parts and output != '.'
                    and str(Path(output)) == output and '\\' not in output, 'unsafe output')
            outputs.append(output)
    path_names(outputs)
    done = set()
    while len(done) < len(ids):
        ready = {t['taskId'] for t in tasks if set(t.get('dependsOn', [])) <= done} - done
        require(ready, 'cyclic dependencies')
        done |= ready
    return {t['taskId']: t for t in tasks}


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        name = f.name
        try:
            json.dump(value, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


class Queue:
    def __init__(self, plan_path, state_path, leases):
        require(len({p.resolve() for p in (plan_path, state_path, leases)}) == 3,
                'plan, state and leases must be different files')
        self.plan = read(plan_path, {})
        self.tasks = validate(self.plan)
        self.state_path = state_path
        self.leases = leases

    def operate(self, operation, worker=None, task_id=None, token=None, outcome=None,
                stopped=False, evidence=None, now=None, usage=None):
        now = time.time() if now is None else now
        require(type(now) in (int, float) and math.isfinite(now), 'invalid time')
        # One registry lock serializes claims across runs as well as within a run.
        with locked(self.leases.with_suffix('.lock')):
            state = read(self.state_path, {'schemaVersion': 1, 'planHash': digest(self.plan),
                         'createdAt': now, 'tasks': {}, 'history': []})
            require(state['planHash'] == digest(self.plan), 'changed plan; create a new queue')
            leases = read(self.leases, {})
            rows = state['tasks']
            for tid in self.tasks:
                rows.setdefault(tid, {'status': 'pending', 'attempt': 0})
            result = None
            if operation == 'claim':
                require(isinstance(worker, str) and worker, 'worker required')
                require(not any(r['status'] == 'running' and r['worker'] == worker for r in rows.values()), 'worker already busy')
                done = {tid for tid, r in rows.items() if r['status'] == 'completed'}
                ready = [t for tid, t in self.tasks.items() if rows[tid]['status'] == 'pending'
                         and set(t.get('dependsOn', [])) <= done]
                ready.sort(key=lambda t: (-t.get('priority', 0), -t.get('estimatedSeconds', 1), t['taskId']))
                for task in ready:
                    kind = task['kind']
                    active = sum(r['status'] == 'running' and self.tasks[tid]['kind'] == kind for tid, r in rows.items())
                    if active >= self.plan[kind + 'Concurrency'] or any(r in leases for r in task.get('resources', [])):
                        continue
                    tid = task['taskId']
                    row = {'status': 'running', 'attempt': rows[tid]['attempt'] + 1,
                           'worker': worker, 'token': uuid.uuid4().hex, 'startedAt': now,
                           'deadline': now + task.get('timeoutSeconds', 600)}
                    rows[tid] = row
                    owner = {'runId': self.plan['runId'], 'taskId': tid, 'token': row['token']}
                    for resource in task.get('resources', []):
                        leases[resource] = owner
                    result = {**task, **row}
                    break
            elif operation in ('finish', 'retry'):
                require(task_id in rows, 'unknown task')
                row, task = rows[task_id], self.tasks[task_id]
                require(token and row.get('token') == token, 'stale attempt token')
                if operation == 'finish':
                    require(row['status'] == 'running' and stopped, 'confirm worker and session stopped before finishing')
                    require(outcome in ('completed', 'failed', 'cancelled') and evidence, 'outcome and evidence required')
                    require(now >= row['startedAt'], 'finish precedes start')
                    row.update(status=outcome, finishedAt=now, evidence=evidence, usage=usage or {})
                    state['history'].append({'taskId': task_id, **row})
                    for resource in task.get('resources', []):
                        owner = leases.get(resource)
                        require(owner and owner['token'] == token, 'resource ownership mismatch')
                        del leases[resource]
                else:
                    require(row['status'] == 'failed' and task.get('retrySafe', False), 'retry is not safe')
                    require(row['attempt'] < task.get('maxAttempts', 1), 'attempt budget exhausted')
                    row['status'] = 'pending'
                result = dict(row)
            elif operation == 'status':
                result = {'tasks': rows, 'overdue': [tid for tid, r in rows.items()
                          if r['status'] == 'running' and r['deadline'] <= now],
                          'history': state['history'], 'createdAt': state['createdAt']}
            else:
                raise ValueError('unknown operation')
            # Reserve before persisting claims; release after persisting completion.
            # A crash can leave a stale lease, never an unprotected running task.
            if operation == 'claim':
                save(self.leases, leases)
            save(self.state_path, state)
            if operation != 'claim':
                save(self.leases, leases)
            return result


def performance(plan, snapshot):
    """Reservation occupancy is not LLM compute utilization; report unknown costs explicitly."""
    tasks = {t['taskId']: t for t in plan['tasks']}
    result = {}
    for platform in ('web', 'mobile', 'shared'):
        rows = [r for r in snapshot['history'] if tasks[r['taskId']]['platform'] == platform]
        groups = {}
        for kind in ('generation', 'execution'):
            selected = [r for r in rows if tasks[r['taskId']]['kind'] == kind]
            ids = {tid for tid, t in tasks.items() if t['platform'] == platform and t['kind'] == kind}
            elapsed = sum(r['finishedAt'] - r['startedAt'] for r in selected)
            groups[kind] = {
                'tasks': len(ids), 'completed': sum(snapshot['tasks'][tid]['status'] == 'completed' for tid in ids),
                'attempts': len(selected), 'retries': sum(r['attempt'] > 1 for r in selected),
                'failures': sum(r['status'] == 'failed' for r in selected),
                'occupiedSeconds': elapsed if selected else 'unknown',
                'llmSeconds': 'unknown', 'toolSeconds': 'unknown', 'totalTokens': 'unknown',
                'queueSeconds': 'unknown', 'dependencyWaitSeconds': 'unknown',
                'resourceWaitSeconds': 'unknown', 'agentIdleSeconds': 'unknown'}
            for metric in ('llmSeconds', 'toolSeconds', 'totalTokens', 'queueSeconds',
                           'dependencyWaitSeconds', 'resourceWaitSeconds', 'agentIdleSeconds'):
                values = [r.get('usage', {}).get(metric) for r in selected]
                if values and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values):
                    groups[kind][metric] = sum(values)
        result[platform] = groups
    return {'platforms': result, 'note': 'Occupied time includes LLM/tool waits; costs and idle time require host telemetry.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan', type=Path)
    p.add_argument('state', type=Path)
    p.add_argument('operation', choices=('claim', 'finish', 'retry', 'status', 'report'))
    p.add_argument('--leases', type=Path, required=True, help='Shared host-wide resource registry')
    p.add_argument('--worker')
    p.add_argument('--task')
    p.add_argument('--token')
    p.add_argument('--outcome', choices=('completed', 'failed', 'cancelled'))
    p.add_argument('--stopped', action='store_true')
    p.add_argument('--evidence')
    p.add_argument('--usage', type=Path, help='Observed metrics JSON; omit unavailable fields')
    a = p.parse_args()
    try:
        queue = Queue(a.plan, a.state, a.leases)
        result = queue.operate('status' if a.operation == 'report' else a.operation, a.worker, a.task,
                    a.token, a.outcome, a.stopped, a.evidence, usage=read(a.usage, {}) if a.usage else None)
        if a.operation == 'report':
            result = performance(queue.plan, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
