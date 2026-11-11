#!/usr/bin/env python3
"""Write the one business file a generation or review worker reads.

  slice_inputs.py <runDir> --plan <plan.json>

A review plan writes ``slices/<name>/input.md`` next to each planned content.json.
A generation plan writes ``inputs/<taskId>.md`` next to the plan. A generation
task with more than 7 caseIds is refused so the plan is split before it is frozen.
Exploration tasks stay on ``generation.py packet``.
"""
import argparse
import json
import sys
from pathlib import Path

MAX_CASES = 7
CASE_FILES = ("cases.json", "scenarios.json", "acs.json", "impact-analysis.json")


def load_json(path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cases_of(data):
    if isinstance(data, dict):
        data = data.get("cases")
    if not isinstance(data, list):
        raise ValueError("cases.json 必须是用例数组")
    return data


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def by_id(rows, key):
    found = {}
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get(key), str):
            found[row[key]] = row
    return found


def pick(rows, key, wanted):
    found = by_id(rows, key)
    missing = [item for item in wanted if item not in found]
    if missing:
        raise ValueError(f"计划引用了不存在的 {key}：{missing}")
    return [found[item] for item in wanted]


def slice_records(run, case_ids):
    cases = pick(cases_of(load_json(run / "cases/cases.json")), "caseId", case_ids)
    scenario_ids = []
    for case in cases:
        for scenario_id in case.get("scenarioIds") or []:
            if scenario_id not in scenario_ids:
                scenario_ids.append(scenario_id)
    scenarios = pick(load_json(run / "cases/scenarios.json"), "scenarioId", scenario_ids)
    ac_ids = []
    for scenario in scenarios:
        for ac_id in scenario.get("acIds") or []:
            if ac_id not in ac_ids:
                ac_ids.append(ac_id)
    acs = pick(load_json(run / "cases/acs.json"), "acId", ac_ids) if ac_ids else []
    impact = load_json(run / "cases/impact-analysis.json") or {}
    wanted = set(ac_ids)
    obligations = [row for row in impact.get("obligations") or []
                   if wanted.intersection(row.get("acIds") or [])]
    obligation_ids = {row.get("obligationId") for row in obligations}
    units = [row for row in impact.get("sourceUnits") or []
             if obligation_ids.intersection(row.get("obligationIds") or [])]
    return cases, scenarios, acs, obligations, units


def constraint_block():
    return "\n".join([
        "## 写的时候就定稿",
        "- evidenceRef 最多 2 段，每段含首尾最多 3 行，用英文分号连接。",
        "- 义务的 acIds 只从本文件的 AC 清单里选，和义务同一笔写上。预期未明才留空，并标 needs_clarification。",
        "- 路径义务如果给了 anchors.start，断言里写这个节点身份，不要只写「起点」。记录有追加时写条数。",
        "",
    ])


def business_markdown(title, check, cases, scenarios, acs, obligations, units):
    parts = [
        f"# {title}",
        "",
        "只读本文件。不要打开 cases.json、scenarios.json、acs.json、impact-analysis.json、PRD 或仓库。",
        "",
        constraint_block(),
    ]
    if check:
        parts += ["自检命令：", "", "```", check, "```", ""]
    starts = sorted({str(row.get("anchors", {}).get("start")).strip()
                     for row in obligations
                     if isinstance(row.get("anchors"), dict) and str(row["anchors"].get("start") or "").strip()})
    if starts:
        parts += [f"断言必须出现的节点身份：{'、'.join(starts)}", ""]
    sections = [("本片用例", cases), ("场景", scenarios), ("验收条件", acs),
                ("相关义务", obligations), ("来源单元", units)]
    for heading, rows in sections:
        parts += [f"## {heading}", "", "```json", dump(rows), "```", ""]
    return "\n".join(parts)


def notes_markdown(run, check):
    notes_file = load_json(run / "cases/notes.json") or {"notes": []}
    gaps = load_json(run / "cases/exploration/impact-gaps.json") or {"gaps": []}
    parts = [
        "# 待确认事项",
        "",
        "只读本文件。不要打开 impact-analysis.json 或其他用例 JSON。",
        "",
        constraint_block(),
    ]
    if check:
        parts += ["自检命令：", "", "```", check, "```", ""]
    parts += ["## 待确认事项", "", "```json", dump(notes_file), "```", "",
              "## 已登记缺口", "", "```json", dump(gaps), "```", ""]
    return "\n".join(parts)


def generation_markdown(run, task, cases, scenarios, acs, obligations, units):
    root = Path(__file__).resolve().parents[1] / "references"
    contract = (root / "shared-contract.md").read_text(encoding="utf-8").strip()
    quality = (root / "assertion-quality.md").read_text(encoding="utf-8").strip()
    body = business_markdown(
        f"生成任务 {task['taskId']}", "", cases, scenarios, acs, obligations, units)
    pending = "按本文件的场景、验收条件和义务写分配的 caseId，不要加任务以外的用例。"
    if not cases:
        pending = "用例还没写。" + pending
    assigned = "\n".join([
        "",
        f"本任务 caseIds：{', '.join(task['caseIds'])}",
        f"本任务 acIds：{', '.join(task.get('acIds') or [row['acId'] for row in acs])}",
        pending,
        "",
        "## 共享契约",
        "",
        contract,
        "",
        "## 断言质量",
        "",
        "下面是写法。assertion_check 由主 Agent 在合并后运行，本任务不跑它。",
        "",
        quality,
        "",
    ])
    return body + assigned


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "bytes": len(text.encode("utf-8"))}


def review_plan(run, plan, plan_path):
    written = []
    for row in plan.get("slices") or []:
        output = row.get("output")
        if not isinstance(output, str):
            raise ValueError("评审分片缺少 output")
        target = (run / output).parent / "input.md"
        if row.get("kind") == "notes":
            text = notes_markdown(run, row.get("checkCommand") or "")
        else:
            cases, scenarios, acs, obligations, units = slice_records(run, row.get("caseIds") or [])
            text = business_markdown(f"评审分片 {row.get('name')}", row.get("checkCommand") or "",
                                     cases, scenarios, acs, obligations, units)
        written.append(write(target, text))
    if not written:
        raise ValueError("评审计划没有分片")
    return {"kind": "review", "plan": str(plan_path), "files": written}


def generation_plan(run, plan, plan_path):
    tasks = plan.get("tasks") or []
    oversized = [task.get("taskId") for task in tasks
                 if isinstance(task.get("caseIds"), list) and len(task["caseIds"]) > MAX_CASES]
    if oversized:
        raise ValueError(f"生成任务超过 {MAX_CASES} 条，先拆开再冻结计划：{oversized}")
    if not (run / "cases/impact-analysis.json").is_file():
        raise ValueError("生成输入包需要 cases/impact-analysis.json")
    written = []
    for task in tasks:
        case_ids = task.get("caseIds") or []
        scenario_ids = task.get("scenarioIds") or []
        scenarios = pick(load_json(run / "cases/scenarios.json"), "scenarioId", scenario_ids)
        ac_ids = list(task.get("acIds") or [])
        for scenario in scenarios:
            for ac_id in scenario.get("acIds") or []:
                if ac_id not in ac_ids:
                    ac_ids.append(ac_id)
        acs = pick(load_json(run / "cases/acs.json"), "acId", ac_ids) if ac_ids else []
        impact = load_json(run / "cases/impact-analysis.json") or {}
        wanted = set(ac_ids)
        obligations = [row for row in impact.get("obligations") or []
                       if wanted.intersection(row.get("acIds") or [])]
        obligation_ids = {row.get("obligationId") for row in obligations}
        units = [row for row in impact.get("sourceUnits") or []
                 if obligation_ids.intersection(row.get("obligationIds") or [])]
        cases = []
        case_file = load_json(run / "cases/cases.json")
        if case_file is not None and case_ids:
            cases = pick(cases_of(case_file), "caseId", case_ids)
        text = generation_markdown(run, task, cases, scenarios, acs, obligations, units)
        written.append(write(plan_path.parent / "inputs" / f"{task['taskId']}.md", text))
    if not written:
        raise ValueError("生成计划没有任务")
    return {"kind": "generation", "plan": str(plan_path), "files": written}


def build(run, plan_path):
    plan = load_json(plan_path)
    if not isinstance(plan, dict):
        raise ValueError("计划不是 JSON 对象")
    if plan.get("slices"):
        return review_plan(run, plan, plan_path)
    tasks = plan.get("tasks") or []
    if tasks and all(isinstance(task, dict) and "caseIds" in task for task in tasks):
        return generation_plan(run, plan, plan_path)
    if tasks and any("objectIds" in task for task in tasks if isinstance(task, dict)):
        raise ValueError("探索任务用 generation.py packet，不用 slice_inputs")
    raise ValueError("认不出计划：评审计划要有 slices，生成计划的任务要有 caseIds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.run.resolve(), args.plan.resolve())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    report["status"] = "PASSED"
    report["closed"] = list(CASE_FILES)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
