#!/usr/bin/env python3
"""Publish validated specs and remove only task-owned scratch resources."""
import argparse
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prd-to-cases/scripts"))
from generation import digest, file_hash, path_names, publish_bytes, require, safe_path, unique, validate_case


@contextmanager
def locked(run):
    with safe_path(run, ".lifecycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read(run, name):
    return json.loads(safe_path(run, name).read_text())


def approved_cases(run, manifest):
    cases = read(run, "cases/cases.json")
    require(isinstance(cases, list) and cases, "missing approved cases")
    require(all(c.get("reviewStatus") == "approved" and c.get("semanticReview") == "approved"
                for c in cases), "cases must be approved before publication")
    for case in cases:
        validate_case(case, case["scenarioIds"], "approved")
    gate = manifest.get("gates", {}).get("humanCaseReview", {})
    config = read(run, "argus-model.json")
    require(config.get("runId") == manifest.get("runId"), "stale frozen configuration")
    enabled = config["config"].get("execution", {}).get("requireHumanCaseReview", True)
    require(type(enabled) is bool, "invalid review configuration")
    if enabled:
        require(gate.get("status") == "PASSED", "human review gate is not PASSED")
        for key in ("runId", "baselineHash", "inputHash"):
            require(manifest.get(key) and gate.get(key) == manifest[key], "stale review " + key)
        require(all(gate.get(k) for k in ("reviewer", "reviewedAt", "approvalRef")), "missing approval evidence")
        hashes = gate.get("artifactHashes", {})
        required = {"cases/" + n for n in ("impact-analysis.json", "requirements.json", "acs.json",
                    "scenarios.json", "cases.json", "cases.md", "cases.xlsx")}
        require(required <= hashes.keys(), "incomplete approval artifact hashes")
        for name, expected in hashes.items():
            require(file_hash(safe_path(run, name)) == expected, "changed approved artifact: " + name)
    else:
        require(gate.get("status") == "N/A" and gate.get("reason"), "disabled review requires N/A and reason")
    return cases


def publication_inputs(run, task_id):
    manifest = read(run, "manifest.json")
    cases = approved_cases(run, manifest)
    plan = read(run, "cases/scripts/plan.json")
    require(plan.get("schemaVersion") == 1 and plan.get("runId") == manifest["runId"], "stale script plan")
    require(plan.get("approvedCasesHash") == digest(cases), "changed approved cases")
    tasks = plan["tasks"]
    unique([t["taskId"] for t in tasks], "script task IDs")
    path_names([t["taskId"] for t in tasks])
    path_names([t["specFile"] for t in tasks])
    assigned = [cid for t in tasks for cid in t["caseIds"]]
    require(unique(assigned, "script case IDs") == unique([c["caseId"] for c in cases], "approved case IDs"),
            "script case ownership mismatch")
    for task in tasks:
        tid = task["taskId"]
        require(tid.isascii() and all(c.isalnum() or c in "_-" for c in tid), "invalid taskId")
        safe_path(run, "harness/tests/" + task["specFile"])
    selected = [t for t in tasks if t["taskId"] == task_id]
    require(len(selected) == 1, "unknown script task")
    task = selected[0]
    receipt = read(run, f"cases/scripts/{task_id}/result.json")
    require(receipt.get("runId") == manifest["runId"] and receipt.get("planHash") == digest(plan)
            and receipt.get("taskId") == task_id, "stale script receipt")
    require(unique(receipt["caseIds"], "receipt cases") == set(task["caseIds"]), "receipt case ownership mismatch")
    source = safe_path(run, f"cases/scripts/{task_id}/" + task["specFile"])
    content = source.read_bytes()
    import hashlib
    require(hashlib.sha256(content).hexdigest() == receipt["specFileHash"], "spec hash mismatch")
    titles = {}
    for case in cases:
        if case["caseId"] not in task["caseIds"]:
            continue
        ref = case.get("automationRef", "")
        require(ref.startswith("planned:"), "approved case requires planned automationRef")
        path, separator, title = ref[8:].partition("::")
        require(separator and title.strip() and path == task["specFile"], "automationRef outside script ownership")
        titles[case["caseId"]] = title
    require(receipt.get("discoveredTitles") == titles, "script title ownership mismatch")
    return plan, task, content


def event(run, operation, task, started, status, **details):
    record = {"schemaVersion": 1, "operation": operation, "taskId": task,
              "runDir": str(run.resolve()), "recordedAt": time.time(),
              "status": status, "durationMs": round((time.monotonic() - started) * 1000, 3), **details}
    with safe_path(run, "lifecycle.jsonl").open("a") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
    from metrics import record as measure
    measure(safe_path(run, "metrics.jsonl"), operation, started, status)
    return record


def publish(run, task_id):
    from phase_gate import Gate
    started = time.monotonic()
    with locked(run):
        try:
            gate = Gate(run)
            gate.publish(task_id)
            require(not gate.gaps, "; ".join(gate.gaps))
            plan, task, content = gate.publication
            target = safe_path(run, "harness/tests/" + task["specFile"])
            target.parent.mkdir(parents=True, exist_ok=True)
            publish_bytes(target, content)
            # Each spec commits independently; a later task failure does not regenerate it.
            receipt = {"schemaVersion": 1, "state": "Published", "runId": plan["runId"],
                       "planHash": digest(plan), "taskId": task_id, "specFile": task["specFile"],
                       "specFileHash": file_hash(target)}
            publish_bytes(safe_path(run, f"cases/scripts/{task_id}/published.json"),
                          (json.dumps(receipt, sort_keys=True) + "\n").encode())
            return event(run, "publish", task_id, started, "Published", specFile=task["specFile"])
        except (ValueError, OSError, KeyError, TypeError) as exc:
            event(run, "publish", task_id, started, "BLOCKED", errorType=type(exc).__name__)
            raise


def cleanup(run, task_id, attempt):
    started = time.monotonic()
    with locked(run):
        try:
            manifest = read(run, "manifest.json")
            plan = read(run, "cases/scripts/plan.json")
            require(plan["runId"] == manifest["runId"] and task_id in {t["taskId"] for t in plan["tasks"]},
                    "cleanup task ownership mismatch")
            require(task_id.isascii() and all(c.isalnum() or c in "_-" for c in task_id)
                    and type(attempt) is int and attempt > 0, "invalid cleanup identity")
            scratch = safe_path(run, f"tmp/{task_id}/{attempt}")
            removed = scratch.exists()
            if removed:
                owner = read(run, f"tmp/{task_id}/{attempt}/owner.json")
                require(owner == {"runId": manifest["runId"], "taskId": task_id, "attempt": attempt,
                                  "state": "stopped"}, "scratch owner must confirm stopped worker")
                require(not any(p.is_symlink() for p in scratch.rglob("*")), "symlink in scratch resources")
                shutil.rmtree(scratch)
            return event(run, "cleanup", task_id, started, "PASSED", attempt=attempt, removed=removed)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            event(run, "cleanup", task_id, started, "BLOCKED", errorType=type(exc).__name__)
            raise


def execution_cases(run):
    manifest = read(run, "manifest.json")
    cases = approved_cases(run, manifest)
    plan = read(run, "cases/scripts/plan.json")
    refs = {}
    for task in plan["tasks"]:
        publication_inputs(run, task["taskId"])
        receipt = read(run, f"cases/scripts/{task['taskId']}/published.json")
        target = "harness/tests/" + task["specFile"]
        require(receipt.get("state") == "Published" and receipt.get("planHash") == digest(plan)
                and receipt.get("runId") == manifest["runId"] and receipt.get("taskId") == task["taskId"]
                and receipt.get("specFile") == task["specFile"]
                and receipt.get("specFileHash") == file_hash(safe_path(run, target)), "invalid published spec")
        for cid in task["caseIds"]:
            refs[cid] = target
    return [{**c, "automationRef": refs[c["caseId"]] + "::" + c["automationRef"].split("::", 1)[1]}
            for c in cases]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("operation", choices=("publish", "cleanup", "bind"))
    parser.add_argument("task", nargs="?")
    parser.add_argument("--attempt", type=int)
    args = parser.parse_args()
    try:
        if args.operation == "bind":
            with locked(args.run):
                cases = execution_cases(args.run)
                publish_bytes(safe_path(args.run, "cases/execution-cases.json"),
                              (json.dumps(cases, ensure_ascii=False, indent=2) + "\n").encode())
            result = {"status": "PASSED", "cases": len(cases)}
        else:
            require(bool(args.task), "task is required")
            result = publish(args.run, args.task) if args.operation == "publish" else cleanup(args.run, args.task, args.attempt)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "retryable": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
