#!/usr/bin/env python3
"""Validate generation shards and report ready tasks; the host owns dispatch."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import unicodedata


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


def validate_plan(plan):
    require(plan["schemaVersion"] == 1 and plan["runId"], "invalid generation identity")
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
        require(unique([c["caseId"] for c in shard_cases], "caseIds") == set(task["caseIds"]),
                "case assignment mismatch: " + tid)
        covered = set()
        for case in shard_cases:
            require(case["schemaVersion"] == 1 and case["reviewStatus"] == "draft"
                    and case["semanticReview"] == "draft", "workers must submit draft cases")
            require(case["suite"] in ("ui", "api", "performance", "security")
                    and case["priority"] in ("P0", "P1", "P2", "P3")
                    and isinstance(case["fixtures"], list), "invalid case fields")
            assigned = unique(case["scenarioIds"], "case scenarios")
            require(assigned and assigned <= set(task["scenarioIds"]), "case outside task scope")
            covered |= assigned
            step_ids = unique([s["id"] for s in case["steps"]], "step IDs")
            require(step_ids and all(isinstance(s["action"], str) and s["action"].strip()
                                     for s in case["steps"]), "missing steps")
            require(case["assertions"] and all(a["stepId"] in step_ids and
                    isinstance(a["expected"], str) and a["expected"].strip() for a in case["assertions"]),
                    "missing or unbound assertions")
            entry = case["automationRef"].split(":", 1)[0]
            require(entry in {f"{tid}/{attempts[tid]}/{n}" for n in receipt["files"]},
                    "automationRef outside owned files")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--root", type=Path, required=True, help="Private generation directory")
    parser.add_argument("--merge", type=Path, help="Write draft cases only when all shards are complete")
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.plan.read_text()), json.loads(args.state.read_text()), args.root)
        if args.merge:
            require(result["status"] == "PASSED", "cannot merge incomplete generation")
            content = json.dumps(result["cases"], ensure_ascii=False, indent=2) + "\n"
            # Publish complete bytes without overwriting reviewed cases.
            with tempfile.NamedTemporaryFile(dir=args.merge.parent, mode="w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
                os.link(output.name, args.merge)
        result.pop("cases")
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["status"] == "BLOCKED" else 0
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "BLOCKED", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
