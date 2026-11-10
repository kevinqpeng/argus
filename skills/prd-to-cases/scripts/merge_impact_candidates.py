#!/usr/bin/env python3
"""Collect current exploration receipts without making business judgments."""
import argparse
import json
import re
from pathlib import Path


GROUPS = {"sources": "sourceId", "sourceUnits": "unitId", "objects": "objectId",
          "checks": "checkId", "obligations": "obligationId", "handoffs": "handoffId"}
DIMENSIONS = {"data", "inflight", "lifecycle", "permission", "side_effect", "ordering"}
DISPOSITIONS = {"covered", "not_applicable", "unknown", "out_of_scope"}
FORBIDDEN_CHECK_KEYS = {"kind", "result", "testability"}
CODE_REF = re.compile(r"(.+):(\d+)(?:-(\d+))?$")
ID_RULES = {"sourceUnits": "-U", "checks": "-C", "obligations": "-OB-", "handoffs": "-H"}


def evidence_errors(value):
    if not isinstance(value, str) or not value.strip():
        return ["empty evidenceRef"]
    parts = [part.strip() for part in value.split(";")]
    errors = ["more than 2 evidence refs"] if len(parts) > 2 else []
    for part in parts:
        match = CODE_REF.fullmatch(part)
        if match is None:
            if len(part) > 120 or "\n" in part:
                errors.append(f"evidence ref too long: {part[:40]}")
            continue
        start, end = int(match.group(2)), int(match.group(3) or match.group(2))
        if end < start or end - start + 1 > 3:
            errors.append(f"evidence span > 3 lines: {part}")
    return errors


def identifier_ok(task_id, group, value):
    marker = ID_RULES.get(group)
    if marker is None:
        return True
    return re.fullmatch(re.escape(task_id + marker) + r"[1-9][0-9]*", value) is not None


def owners(tasks):
    owned = {}
    listed = [task.get("objectIds") for task in tasks]
    if all(ids is None for ids in listed):
        return {}
    if any(not isinstance(ids, list) or not ids for ids in listed):
        raise ValueError("every exploration task must list objectIds")
    for task, ids in zip(tasks, listed):
        for object_id in ids:
            if not isinstance(object_id, str) or object_id in owned:
                raise ValueError(f"object assigned twice or invalid: {object_id}")
            owned[object_id] = task["taskId"]
    return owned


def collect(plan, receipts):
    if plan.get("schemaVersion") != 1 or not isinstance(plan.get("inputHash"), str) or not plan["inputHash"]:
        raise ValueError("invalid plan inputHash or schemaVersion")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("missing tasks")
    expected = {}
    for task in tasks:
        tid, attempt = task.get("taskId"), task.get("attempt")
        if not isinstance(tid, str) or not tid.strip() or tid in expected or type(attempt) is not int or attempt < 1:
            raise ValueError("invalid or duplicate task")
        expected[tid] = attempt
    object_owner = owners(tasks)
    entries = {group: {} for group in GROUPS}
    errors = []
    seen = set()
    for receipt in receipts:
        tid = receipt.get("taskId")
        if tid not in expected or tid in seen or type(receipt.get("attempt")) is not int or receipt["attempt"] != expected.get(tid) or receipt.get("inputHash") != plan["inputHash"] or receipt.get("schemaVersion") != 1:
            errors.append(f"unexpected, duplicate or stale receipt: {tid}")
            continue
        seen.add(tid)
        status = receipt.get("status", "complete")
        if status not in {"complete", "partial"}:
            errors.append(f"{tid}: invalid receipt status")
        elif status == "partial":
            errors.append(f"partial receipt: {tid}")
        for group, key in GROUPS.items():
            rows = receipt.get(group)
            if not isinstance(rows, list):
                errors.append(f"{tid}: missing {group}")
                continue
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key].strip():
                    errors.append(f"{tid}: invalid {group} identifier")
                    continue
                if not identifier_ok(tid, group, row[key]):
                    errors.append(f"{tid}: {group} id must be {tid}{ID_RULES[group]}<n>: {row[key]}")
                if group == "sourceUnits" and row.get("disposition") not in DISPOSITIONS:
                    errors.append(f"{tid}: source unit {row[key]}: invalid disposition")
                if group == "sources" and row.get("kind") == "prd":
                    ref = row.get("sourceRef")
                    if not isinstance(ref, str) or not ref.startswith("run://"):
                        errors.append(f"{tid}: prd sourceRef must start with run://")
                if group == "objects" and object_owner and object_owner.get(row.get("objectId")) != tid:
                    errors.append(f"{tid}: object not assigned: {row.get('objectId')}")
                if group == "checks":
                    if row.get("dimension") not in DIMENSIONS:
                        errors.append(f"{tid}: check {row[key]}: invalid dimension")
                    forbidden = FORBIDDEN_CHECK_KEYS & row.keys()
                    if forbidden:
                        errors.append(f"{tid}: check {row[key]}: forbidden {','.join(sorted(forbidden))}")
                    for issue in evidence_errors(row.get("evidenceRef")):
                        errors.append(f"{tid}: check {row[key]}: {issue}")
                    owner = object_owner.get(row.get("objectId"))
                    if object_owner and owner != tid:
                        errors.append(f"{tid}: check object not assigned: {row.get('objectId')}")
                variants = entries[group].setdefault(row[key], [])
                match = next((v for v in variants if v["value"] == row), None)
                if match is None:
                    variants.append({"value": row, "tasks": [tid]})
                elif tid not in match["tasks"]:
                    match["tasks"].append(tid)
                else:
                    errors.append(f"{tid}: duplicate {group} identifier: {row[key]}")
    errors.extend(f"missing receipt: {tid}" for tid in sorted(set(expected) - seen))
    conflicts = [f"{group}: {ident}" for group, values in entries.items()
                 for ident, variants in values.items() if len(variants) > 1]
    return {"schemaVersion": 1, "inputHash": plan["inputHash"],
            "status": "BLOCKED" if errors or conflicts else "READY_FOR_REVIEW",
            "errors": sorted(errors), "conflicts": sorted(conflicts), "candidates": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("receipts", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        receipts = [json.loads(path.read_text(encoding="utf-8")) for path in args.receipts]
        result = collect(plan, receipts)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result = {"status": "BLOCKED", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "READY_FOR_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
