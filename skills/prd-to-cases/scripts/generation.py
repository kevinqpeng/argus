#!/usr/bin/env python3
"""Validate generation shards and report ready tasks; the host owns dispatch."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import unicodedata
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "argus/scripts"))
from case_contract import validate_steps


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(values, label):
    require(isinstance(values, list) and all(isinstance(v, str) and v for v in values), label)
    require(len(values) == len(set(values)), "duplicate " + label)
    return set(values)


def path_names(values):
    canonical = [unicodedata.normalize("NFC", v).casefold() for v in values]
    require(len(set(canonical)) == len(canonical), "aliased filesystem paths")
    require(not any(b.startswith(a + "/") for a in canonical for b in canonical if a != b),
            "file and directory overlap")


def safe_path(root, name):
    require(isinstance(name, str) and name, "empty path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == name
            and name != "." and "\\" not in name, "unsafe path: " + name)
    target = root.resolve()
    for part in path.parts:
        target = target / part
        require(not target.is_symlink(), "symlink path: " + name)
    require(target.resolve().is_relative_to(root.resolve()), "path escapes root")
    return target


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_bytes(target, content):
    """Atomic create; an identical retry is safe, a conflicting write is not."""
    require(not target.is_symlink(), "symlink publication target")
    with tempfile.NamedTemporaryFile(dir=target.parent) as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
        try:
            os.link(output.name, target)
        except FileExistsError:
            require(not target.is_symlink() and target.read_bytes() == content,
                    "publication conflicts with existing artifact: " + str(target))


def validate_case(case, scenario_ids, review_status):
    require(case["schemaVersion"] == 1 and case["reviewStatus"] == review_status
            and case["semanticReview"] == review_status, "case review statuses must be " + review_status)
    require(case["suite"] in ("ui", "api", "performance", "security")
            and case["priority"] in ("P0", "P1", "P2", "P3")
            and isinstance(case["fixtures"], list), "invalid case fields")
    assigned = unique(case["scenarioIds"], "case scenarios")
    require(assigned and assigned <= set(scenario_ids), "case outside task scope")
    validate_steps(case["steps"], case["assertions"])
    return assigned


def validate_automation_ref(case, task, attempt, files, phase):
    ref = case.get("automationRef")
    require(isinstance(ref, str) and ref.strip(), "missing automationRef: " + case["caseId"])
    if phase == "cases":
        require(ref.startswith("planned:"), "case generation requires planned automationRef")
        path, separator, title = ref[len("planned:"):].partition("::")
        require(separator and title.strip(), "planned automationRef requires file::title")
        safe_path(Path("/generation-check"), path)
    else:
        entry, separator, title = ref.partition(":")
        require(separator and title.strip(), "automationRef requires an entry title")
        require(entry in {f"{task['taskId']}/{attempt}/{n}" for n in files},
                "automationRef outside owned files")


def validate_plan(plan):
    require(plan["schemaVersion"] == 1 and plan["runId"], "invalid generation identity")
    require(plan.get("phase", "legacy") in ("legacy", "cases"), "unknown generation phase")
    for key in ("inputHash", "baselineHash"):
        value = plan[key]
        require(isinstance(value, str) and len(value) == 64 and
                all(c in "0123456789abcdef" for c in value), "invalid " + key)
    scenarios = unique(plan["scenarioIds"], "scenarioIds")
    require(scenarios and plan["tasks"], "empty generation plan")
    ids = unique([t["taskId"] for t in plan["tasks"]], "taskIds")
    path_names(ids)
    tasks, owned_scenarios, owned_cases = {}, set(), set()
    for task in plan["tasks"]:
        tid = task["taskId"]
        require(tid.isascii() and all(c.isalnum() or c in "_-" for c in tid), "invalid taskId")
        assigned = unique(task["scenarioIds"], "assigned scenarios")
        cases = unique(task["caseIds"], "assigned cases")
        deps = unique(task["dependsOn"], "dependencies")
        files = unique(task["outputFiles"], "outputFiles")
        path_names([*files, "result.json"])
        require(assigned and cases and files, "empty task")
        require(not assigned & owned_scenarios and not cases & owned_cases, "duplicate ownership")
        require(deps <= ids and tid not in deps, "invalid dependency")
        for name in files:
            safe_path(Path("/generation-check"), name)
            require(name != "result.json", "reserved result.json")
        owned_scenarios |= assigned
        owned_cases |= cases
        tasks[tid] = task
    require(owned_scenarios == scenarios, "scenario assignment mismatch")
    remaining, ordered = dict(tasks), []
    while remaining:
        ready = sorted(tid for tid, t in remaining.items() if set(t["dependsOn"]) <= set(ordered))
        require(ready, "cyclic generation dependencies")
        for tid in ready:
            ordered.append(tid)
            del remaining[tid]
    require(isinstance(plan["inputArtifacts"], dict) and plan["inputArtifacts"], "missing frozen inputs")
    path_names(plan["inputArtifacts"])
    for name in plan["inputArtifacts"]:
        safe_path(Path("/generation-check"), name)
        require(PurePosixPath(name).parts[0].casefold() not in {tid.casefold() for tid in ids},
                "input overlaps worker directory")
    return tasks, ordered


def evaluate(plan, state, root):
    tasks, ordered = validate_plan(plan)
    plan_hash = digest(plan)
    require(state["schemaVersion"] == 1 and state["planHash"] == plan_hash, "stale generation state")
    attempts = state["attempts"]
    require(set(attempts) == set(tasks) and all(type(n) is int and n > 0 for n in attempts.values()),
            "invalid attempt assignments")
    running = unique(state["running"], "running tasks")
    failed = unique(state["failed"], "failed tasks")
    require(running <= tasks.keys() and failed <= tasks.keys() and not running & failed,
            "invalid active tasks")
    for name, expected in plan["inputArtifacts"].items():
        require(file_hash(safe_path(root, name)) == expected, "changed frozen input: " + name)
    completed, cases, artifacts = {}, [], {}
    for tid in ordered:
        task = tasks[tid]
        directory = safe_path(root, f"{tid}/{attempts[tid]}")
        receipt_path = directory / "result.json"
        if tid in failed | running or not receipt_path.exists():
            continue
        safe_path(root, f"{tid}/{attempts[tid]}/result.json")
        receipt = json.loads(receipt_path.read_text())
        require(receipt["schemaVersion"] == 1 and receipt["runId"] == plan["runId"]
                and receipt["planHash"] == plan_hash and receipt["taskId"] == tid
                and type(receipt["attempt"]) is int and receipt["attempt"] == attempts[tid]
                and receipt["state"] == "completed", "stale or incomplete receipt: " + tid)
        require(set(task["dependsOn"]) <= completed.keys(), "dependency not completed: " + tid)
        require(receipt["dependencyHashes"] == {d: completed[d] for d in task["dependsOn"]},
                "stale dependency artifacts: " + tid)
        require(receipt["resultHash"] == digest({k: v for k, v in receipt.items() if k != "resultHash"}),
                "receipt hash mismatch: " + tid)
        gaps = receipt.get("unresolvedObligations")
        require(isinstance(gaps, list), "missing unresolvedObligations receipt: " + tid)
        require(not gaps, "unresolved obligations in " + tid + ": " + json.dumps(gaps, ensure_ascii=False))
        require(set(receipt["files"]) == set(task["outputFiles"]), "output ownership mismatch: " + tid)
        actual_files = set()
        for path in directory.rglob("*"):
            require(not path.is_symlink(), "symlink in worker output")
            if path.is_file():
                actual_files.add(path.relative_to(directory).as_posix())
        require(actual_files == set(task["outputFiles"]) | {"result.json"}, "unexpected worker files: " + tid)
        for name, expected in receipt["files"].items():
            path = safe_path(directory, name)
            require(file_hash(path) == expected, "artifact hash mismatch: " + tid + "/" + name)
            artifacts[f"{tid}/{attempts[tid]}/{name}"] = expected
        shard_cases = receipt["cases"]
        if plan.get("phase") == "cases":
            require("cases.json" in receipt["files"], "case phase requires cases.json")
            require(json.loads(safe_path(directory, "cases.json").read_text()) == shard_cases,
                    "cases.json differs from receipt cases: " + tid)
        require(unique([c["caseId"] for c in shard_cases], "caseIds") == set(task["caseIds"]),
                "case assignment mismatch: " + tid)
        covered = set()
        for case in shard_cases:
            covered |= validate_case(case, task["scenarioIds"], "draft")
            validate_automation_ref(case, task, attempts[tid], receipt["files"], plan.get("phase", "legacy"))
        require(covered == set(task["scenarioIds"]), "unrepresented assigned scenarios: " + tid)
        completed[tid] = receipt["resultHash"]
        cases.extend(shard_cases)
    ready = [tid for tid in ordered if tid not in completed and tid not in running | failed
             and set(tasks[tid]["dependsOn"]) <= completed.keys()]
    pending = sorted(set(tasks) - completed.keys())
    return {"schemaVersion": 1, "planHash": plan_hash,
            "status": "PASSED" if not pending else "BLOCKED" if failed else "PENDING",
            "completed": completed, "ready": ready, "pending": pending, "failed": sorted(failed),
            "artifacts": artifacts, "cases": sorted(cases, key=lambda c: c["caseId"])}


def seal(plan, state, root, task_id, gaps):
    """Write result.json from files the worker already put in its attempt directory."""
    tasks, _ = validate_plan(plan)
    require(plan.get("phase") == "cases", "seal supports the cases phase")
    require(task_id in tasks, "unknown task")
    task = tasks[task_id]
    require("cases.json" in task["outputFiles"], "cases phase requires cases.json output")
    plan_hash = digest(plan)
    require(state["schemaVersion"] == 1 and state["planHash"] == plan_hash, "stale generation state")
    attempt = state["attempts"][task_id]
    require(type(attempt) is int and attempt > 0, "invalid attempt")
    require(isinstance(gaps, list), "unresolvedObligations must be a list")
    for item in gaps:
        require(isinstance(item, dict) and isinstance(item.get("obligationId"), str) and item["obligationId"]
                and isinstance(item.get("reason"), str) and item["reason"].strip(),
                "invalid unresolved obligation")
    directory = safe_path(root, f"{task_id}/{attempt}")
    files = {}
    for name in task["outputFiles"]:
        path = safe_path(directory, name)
        require(path.is_file(), "missing worker output: " + name)
        files[name] = file_hash(path)
    shard_cases = json.loads(safe_path(directory, "cases.json").read_text(encoding="utf-8"))
    require(unique([case["caseId"] for case in shard_cases], "caseIds") == set(task["caseIds"]),
            "case assignment mismatch")
    covered = set()
    for case in shard_cases:
        covered |= validate_case(case, task["scenarioIds"], "draft")
        validate_automation_ref(case, task, attempt, files, "cases")
    require(covered == set(task["scenarioIds"]), "unrepresented assigned scenarios")
    dependency_hashes = {}
    for dep in task["dependsOn"]:
        dep_attempt = state["attempts"][dep]
        dep_receipt = json.loads(safe_path(root, f"{dep}/{dep_attempt}/result.json").read_text(encoding="utf-8"))
        dependency_hashes[dep] = dep_receipt["resultHash"]
    receipt = {"schemaVersion": 1, "runId": plan["runId"], "planHash": plan_hash, "taskId": task_id,
               "attempt": attempt, "state": "completed", "dependencyHashes": dependency_hashes,
               "files": files, "cases": shard_cases, "unresolvedObligations": gaps}
    receipt["resultHash"] = digest({key: value for key, value in receipt.items() if key != "resultHash"})
    publish_bytes(directory / "result.json", (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
    return {"taskId": task_id, "attempt": attempt, "resultHash": receipt["resultHash"]}


def seal_ready(plan, state, root, gaps_by_task):
    """Seal every finished cases-phase task that does not yet have a receipt."""
    tasks, ordered = validate_plan(plan)
    require(isinstance(gaps_by_task, dict), "gaps file must be an object")
    require(set(gaps_by_task) <= set(tasks), "unknown task in gaps file")
    sealed = []
    for task_id in ordered:
        task = tasks[task_id]
        attempt = state["attempts"][task_id]
        directory = safe_path(root, f"{task_id}/{attempt}")
        if (directory / "result.json").is_file():
            continue
        if any(not safe_path(directory, name).is_file() for name in task["outputFiles"]):
            continue
        if any(not (safe_path(root, f"{dep}/{state['attempts'][dep]}") / "result.json").is_file()
               for dep in task["dependsOn"]):
            continue
        sealed.append(seal(plan, state, root, task_id, gaps_by_task.get(task_id, [])))
    return sealed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--root", type=Path, required=True, help="Private generation directory")
    parser.add_argument("--merge", type=Path, help="Write draft cases only when all shards are complete")
    parser.add_argument("--seal", help="Write result.json for this cases-phase task from its output files")
    parser.add_argument("--seal-ready", action="store_true",
                        help="Seal every finished cases-phase task that has no receipt")
    parser.add_argument("--gaps", default="[]", help="JSON array of unresolved obligations for --seal")
    parser.add_argument("--gaps-file", type=Path,
                        help="taskId to unresolvedObligations object for --seal-ready")
    parser.add_argument("--metrics", type=Path, help="Run metrics.jsonl")
    args = parser.parse_args()
    if args.seal and args.merge:
        parser.error("seal does not merge")
    if args.seal_ready and (args.seal or args.merge):
        parser.error("seal-ready does not combine with seal or merge")
    if args.gaps_file and not args.seal_ready:
        parser.error("--gaps-file requires --seal-ready")
    started = time.monotonic()
    status = "BLOCKED"
    try:
        plan = json.loads(args.plan.read_text())
        state = json.loads(args.state.read_text())
        if args.seal or args.seal_ready:
            if args.seal:
                result = seal(plan, state, args.root, args.seal, json.loads(args.gaps))
            else:
                gaps_by_task = json.loads(args.gaps_file.read_text()) if args.gaps_file else {}
                result = seal_ready(plan, state, args.root, gaps_by_task)
            status = "PASSED"
            print(json.dumps(result, ensure_ascii=False))
            return 0
        result = evaluate(plan, state, args.root)
        if args.merge:
            require(result["status"] == "PASSED", "cannot merge incomplete generation")
            content = json.dumps(result["cases"], ensure_ascii=False, indent=2) + "\n"
            publish_bytes(args.merge, content.encode("utf-8"))
        result.pop("cases")
        status = result["status"]
        result["durationMs"] = round((time.monotonic() - started) * 1000, 3)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["status"] == "BLOCKED" else 0
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "BLOCKED", "reason": str(exc)}))
        return 2
    finally:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "argus/scripts"))
        from metrics import record
        record(args.metrics or args.root / "validation.metrics.jsonl", "validation", started, status,
               contractErrorRetries=0)


if __name__ == "__main__":
    raise SystemExit(main())
