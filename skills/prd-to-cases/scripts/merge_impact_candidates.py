#!/usr/bin/env python3
"""Collect current exploration receipts without making business judgments."""
import argparse
import json
from pathlib import Path


GROUPS = {"sources": "sourceId", "sourceUnits": "unitId", "objects": "objectId",
          "checks": "checkId", "obligations": "obligationId", "handoffs": "handoffId"}


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
    entries = {group: {} for group in GROUPS}
    errors = []
    seen = set()
    for receipt in receipts:
        tid = receipt.get("taskId")
        if tid not in expected or tid in seen or type(receipt.get("attempt")) is not int or receipt["attempt"] != expected.get(tid) or receipt.get("inputHash") != plan["inputHash"] or receipt.get("schemaVersion") != 1:
            errors.append(f"unexpected, duplicate or stale receipt: {tid}")
            continue
        seen.add(tid)
        for group, key in GROUPS.items():
            rows = receipt.get(group)
            if not isinstance(rows, list):
                errors.append(f"{tid}: missing {group}")
                continue
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key].strip():
                    errors.append(f"{tid}: invalid {group} identifier")
                    continue
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
