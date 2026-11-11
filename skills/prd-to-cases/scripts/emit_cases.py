#!/usr/bin/env python3
"""Write cases.json from a step TSV. validate_steps runs before any file is written."""
import argparse
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "argus/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_contract import validate_steps
from generation import contract_traps, safe_path

HEADERS = ("caseId", "scenarioId", "title", "priority", "suite", "spec",
           "fixtures", "stepId", "kind", "action", "expected", "exemption")
SUITES = {"ui", "api", "performance", "security"}
PRIORITIES = {"P0", "P1", "P2", "P3"}
KINDS = {"action", "setup", "cleanup"}
META = ("title", "priority", "suite", "spec", "scenarioId")


def cell(row, key):
    return (row.get(key) or "").strip()


def parts(value):
    return [part.strip() for part in value.split("|") if part.strip()]


def convert(text, ac_ids=None):
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames != list(HEADERS):
        raise ValueError("TSV header must be " + "\t".join(HEADERS))
    order, groups = [], {}
    for raw in reader:
        case_id = cell(raw, "caseId")
        if not case_id:
            raise ValueError("missing caseId")
        if case_id not in groups:
            order.append(case_id)
            groups[case_id] = []
        groups[case_id].append(raw)
    if not order:
        raise ValueError("用例表为空")
    return [build(groups[case_id], ac_ids) for case_id in order]


def build(rows, ac_ids):
    first = rows[0]
    case_id = cell(first, "caseId")
    title = cell(first, "title")
    spec = cell(first, "spec")
    if (not title or not spec or "::" in spec or cell(first, "priority") not in PRIORITIES
            or cell(first, "suite") not in SUITES or not parts(cell(first, "scenarioId"))):
        raise ValueError("invalid case fields: " + case_id)
    safe_path(Path("/generation-check"), spec)
    steps, assertions = [], []
    for row in rows:
        for key in META:
            value = cell(row, key)
            if value and value != cell(first, key):
                raise ValueError(f"{case_id}: {key} differs across steps")
        if row is not first and cell(row, "fixtures") and cell(row, "fixtures") != cell(first, "fixtures"):
            raise ValueError(f"{case_id}: fixtures differ across steps")
        step_id = cell(row, "stepId")
        action = (row.get("action") or "").strip()
        kind = cell(row, "kind") or "action"
        expected = (row.get("expected") or "").strip()
        exemption = (row.get("exemption") or "").strip()
        if not step_id or not action or kind not in KINDS:
            raise ValueError("invalid step: " + case_id)
        if expected and exemption:
            raise ValueError(f"{case_id}: {step_id} has both expected and exemption")
        step = {"id": step_id, "action": action}
        if kind != "action":
            step["kind"] = kind
        if exemption:
            step["assertionExemption"] = exemption
        steps.append(step)
        if expected:
            assertions.append({"stepId": step_id, "expected": expected})
    case = {
        "schemaVersion": 1, "caseId": case_id, "scenarioIds": parts(cell(first, "scenarioId")),
        "title": title, "suite": cell(first, "suite"), "priority": cell(first, "priority"),
        "fixtures": parts(cell(first, "fixtures")), "steps": steps, "assertions": assertions,
        "reviewStatus": "draft", "automationRef": f"planned:{spec}::{title}",
    }
    validate_steps(case["steps"], case["assertions"])
    task = {"scenarioIds": case["scenarioIds"]}
    if ac_ids is not None:
        task["acIds"] = ac_ids
    contract_traps(case, task)
    return case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tsv", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--ac-ids", help="Comma-separated AC ids owned by this task")
    parser.add_argument("--check", action="store_true", help="Validate only; write nothing")
    args = parser.parse_args()
    if not args.check and args.out is None:
        parser.error("--out is required unless --check")
    ac_ids = [part.strip() for part in args.ac_ids.split(",") if part.strip()] if args.ac_ids else None
    try:
        cases = convert(args.tsv.read_text(encoding="utf-8"), ac_ids)
    except (OSError, ValueError, csv.Error, KeyError, TypeError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    steps = sum(len(case["steps"]) for case in cases)
    print(json.dumps({"schemaVersion": 1, "status": "PASSED", "cases": len(cases), "steps": steps},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
