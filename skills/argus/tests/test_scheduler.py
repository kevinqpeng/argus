import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import subprocess
from unittest.mock import patch
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import scheduler
from scheduler import Queue, performance, validate


def task(tid, platform='web', kind='generation', **extra):
    extra.setdefault('outputs', [tid + '.py'] if kind == 'generation' else [])
    return dict(taskId=tid, platform=platform, kind=kind, taskType='script' if kind == 'generation' else 'test', **extra)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.leases = self.root / 'leases.json'

    def queue(self, tasks, name='run', generation=2, execution=2):
        plan = dict(schemaVersion=1, runId=name, generationConcurrency=generation,
                    executionConcurrency=execution, inputHash="a" * 64, tasks=tasks)
        path = self.root / (name + '.plan.json')
        path.write_text(json.dumps(plan))
        return Queue(path, self.root / (name + '.state.json'), self.leases)

    def finish(self, q, claim, outcome='completed', now=None):
        return q.operate('finish', task_id=claim['taskId'], token=claim['token'], outcome=outcome,
                         stopped=True, evidence='receipt.json', now=now)

    def test_longest_ready_first_and_immediate_refill_with_dependency(self):
        q = self.queue([task('short', estimatedSeconds=1), task('long', estimatedSeconds=10),
                        task('next', dependsOn=['short'], estimatedSeconds=3)])
        a = q.operate('claim', worker='A', now=0)
        b = q.operate('claim', worker='B', now=0)
        self.assertEqual(a['taskId'], 'long')
        self.assertEqual(b['taskId'], 'short')
        self.assertIsNone(q.operate('claim', worker='C', now=1))
        self.finish(q, b, now=2)
        self.assertEqual(q.operate('claim', worker='B', now=2)['taskId'], 'next')

    def test_generation_independent_of_single_device_execution(self):
        q = self.queue([task('g1', 'mobile'), task('g2', 'mobile'),
                        task('e1', 'mobile', 'execution', resources=['device:A']),
                        task('e2', 'mobile', 'execution', resources=['device:A'])], execution=1)
        claims = [q.operate('claim', worker=str(i)) for i in range(3)]
        self.assertEqual({c['taskId'] for c in claims}, {'g1', 'g2', 'e1'})
        self.assertIsNone(q.operate('claim', worker='4'))

    def test_cross_run_device_and_port_conflicts_multi_device_release(self):
        q = self.queue([task('one', 'mobile', 'execution', resources=['device:A', 'port:host:8200']),
                        task('two', 'mobile', 'execution', resources=['device:B', 'port:host:8201'])])
        a = q.operate('claim', worker='A')
        b = q.operate('claim', worker='B')
        self.assertIsNotNone(b)
        other = self.queue([task('three', 'mobile', 'execution', resources=['device:A'])], name='other')
        self.assertIsNone(other.operate('claim', worker='C'))
        port = self.queue([task('four', 'mobile', 'execution', resources=['device:C', 'port:host:8200'])], name='port')
        self.assertIsNone(port.operate('claim', worker='D'))
        self.finish(q, a)
        self.assertIsNotNone(other.operate('claim', worker='C'))
        self.assertIsNotNone(port.operate('claim', worker='D'))

    def test_timeout_keeps_lease_until_confirmed_stopped_and_retry_is_fenced(self):
        q = self.queue([task('one', 'mobile', 'execution', resources=['device:A'], timeoutSeconds=1,
                             retrySafe=True, maxAttempts=2)])
        a = q.operate('claim', worker='A', now=0)
        self.assertEqual(q.operate('status', now=2)['overdue'], ['one'])
        with self.assertRaises(ValueError):
            q.operate('finish', task_id='one', token=a['token'], outcome='failed', evidence='error')
        self.finish(q, a, 'failed', now=3)
        q.operate('retry', task_id='one', token=a['token'])
        b = q.operate('claim', worker='B', now=4)
        with self.assertRaises(ValueError):
            self.finish(q, a)
        self.assertEqual(b['attempt'], 2)
        self.finish(q, b, 'failed', now=5)
        with self.assertRaises(ValueError):
            q.operate('retry', task_id='one', token=b['token'])

    def test_no_automatic_retry_for_business_side_effects(self):
        q = self.queue([task('one')])
        a = q.operate('claim', worker='A')
        self.finish(q, a, 'failed')
        with self.assertRaises(ValueError):
            q.operate('retry', task_id='one', token=a['token'])

    def test_concurrent_claims_and_resume_do_not_duplicate(self):
        q = self.queue([task(str(i)) for i in range(8)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda i: q.operate('claim', worker=str(i)), range(8)))
        self.assertEqual(len([c for c in claims if c]), 2)
        resumed = Queue(self.root / 'run.plan.json', self.root / 'run.state.json', self.leases)
        self.assertIsNone(resumed.operate('claim', worker='new'))
        self.assertEqual(len({c['token'] for c in claims if c}), 2)

    def test_cycles_conflicts_invalid_budget_and_stale_plan(self):
        base = self.queue([task('a')]).plan
        bad_tasks = [[task('a', dependsOn=['b']), task('b', dependsOn=['a'])],
                     [task('a', outputs=['Page.py']), task('b', outputs=['page.py'])],
                     [task('a', 'mobile', 'execution')], [task('a', timeoutSeconds=float('nan'))]]
        for tasks in bad_tasks:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                validate({**base, 'tasks': tasks})
        q = self.queue([task('a')])
        q.operate('status')
        changed = self.queue([task('b')])
        with self.assertRaises(ValueError):
            changed.operate('claim', worker='A')

    def test_metrics_separate_platforms_and_keep_unknown_costs(self):
        q = self.queue([task('web'), task('mobile', 'mobile')])
        a = q.operate('claim', worker='A', now=0)
        self.finish(q, a, now=4)
        report = performance(q.plan, q.operate('status', now=5))
        row = report['platforms'][a['platform']]['generation']
        self.assertEqual(row['occupiedSeconds'], 4)
        self.assertEqual(row['totalTokens'], 'unknown')
        self.assertEqual(row['completed'], 1)

    def test_crash_during_claim_retains_device_reservation(self):
        q = self.queue([task('one', 'mobile', 'execution', resources=['device:A'])])
        original = scheduler.save
        def fail_state(path, value):
            if path == q.state_path:
                raise OSError('simulated interruption')
            original(path, value)
        with patch.object(scheduler, 'save', side_effect=fail_state), self.assertRaises(OSError):
            q.operate('claim', worker='A')
        other = self.queue([task('other', 'mobile', 'execution', resources=['device:A'])], name='other')
        self.assertIsNone(other.operate('claim', worker='B'))

    def test_queue_paths_cannot_overwrite_plan_or_registry(self):
        q = self.queue([task('one')])
        plan = self.root / 'run.plan.json'
        with self.assertRaises(ValueError):
            Queue(plan, plan, self.leases)
        with self.assertRaises(ValueError):
            Queue(plan, q.state_path, q.state_path)

    def test_cli_processes_obey_limit_and_report(self):
        q = self.queue([task(str(i)) for i in range(5)])
        command = [sys.executable, '-B', str(Path(scheduler.__file__)),
                   str(self.root / 'run.plan.json'), str(q.state_path)]
        def claim(i):
            result = subprocess.run(command + ['claim', '--leases', str(self.leases), '--worker', str(i)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return json.loads(result.stdout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            claims = list(pool.map(claim, range(5)))
        self.assertEqual(sum(c is not None for c in claims), 2)
        result = subprocess.run(command + ['report', '--leases', str(self.leases)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['platforms']['web']['generation']['completed'], 0)


if __name__ == '__main__':
    unittest.main()
