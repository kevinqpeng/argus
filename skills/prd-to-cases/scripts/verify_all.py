#!/usr/bin/env python3
"""Run the delivery checks in one process.

  verify_all.py <runDir> --draft [--source-id ID]
      Before the generation plan is frozen.

  verify_all.py <runDir> --cases [--baseline DIR]
      After cases are merged, before review slices are dispatched.

  verify_all.py <runDir> [--round 1] [--feature NAME] [--source-id ID] [--baseline DIR]
      After the workbook is rendered. --feature adds the publish precheck
      and does not publish.

Exit 0 when every section is clean, otherwise 2. The three case gates are the
same functions coverage_check, assertion_check and notes_check already use.
"""
import argparse
import hashlib
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VERSION_REASONS = {
    "unparseable": "版本历史无法解析",
    "ambiguous": "版本历史不唯一",
    "mismatch": "记录版本与文档不一致",
    "hash": "需求文档快照哈希不一致",
    "bad-ref": "sourceRef 不是 run://",
    "binary": "需求快照不是文本，无法读取版本历史",
    "missing-file": "sourceRef 指向的快照不存在",
    "no-hash": "需求快照缺少 sourceHash",
    "error": "无法读取影响分析",
}


def read_json(path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def section(gaps):
    return {"status": "PASSED" if not gaps else "BLOCKED", "gaps": gaps}


def case_gates(run, baseline, previous=None):
    from assertion_check import check as assertion_check
    from coverage_check import check as coverage_check
    from notes_check import check as notes_check

    sections = {}
    for name, call in (
        ("coverage", lambda: coverage_check(run, previous)),
        ("assertion", lambda: assertion_check(run)),
        ("notes", lambda: notes_check(run, baseline)),
    ):
        captured = io.StringIO()
        try:
            with redirect_stdout(captured):
                result = call()
            sections[name] = section(list(result.get("gaps") or []))
        except SystemExit:
            text = captured.getvalue().strip() or f"{name} 未完成"
            sections[name] = section([text])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            sections[name] = section([str(exc)])
    return sections


def draft_gaps(run, source_id):
    from decision_table import TABLE, blocking, load
    from merge_impact_candidates import evidence_errors
    from publish_review import version_status

    gaps = []
    impact = read_json(run / "cases/impact-analysis.json")
    units = []
    standalone = read_json(run / "cases/sources/units.json")
    if isinstance(standalone, list):
        units.extend(standalone)
    if isinstance(impact, dict):
        units.extend(impact.get("sourceUnits") or [])
    if not units:
        gaps.append("还没有来源单元")
    for unit in units:
        if not isinstance(unit, dict):
            gaps.append("来源单元不是对象")
            continue
        for issue in evidence_errors(unit.get("evidenceRef")):
            gaps.append(f"{unit.get('unitId')}: {issue}")
    acs = read_json(run / "cases/acs.json")
    catalog = {row.get("acId") for row in acs} if isinstance(acs, list) else set()
    if not catalog:
        gaps.append("缺少 cases/acs.json，义务还不能写 acIds")
    for duty in (impact or {}).get("obligations") or []:
        if not isinstance(duty, dict):
            continue
        label = duty.get("obligationId") or "<no-id>"
        ac_ids = duty.get("acIds") or []
        clarifying = (duty.get("reviewStatus") == "needs_clarification"
                      and not str(duty.get("expected") or "").strip())
        if catalog and not clarifying and not ac_ids:
            gaps.append(f"{label}: 义务要当场写 acIds")
        unknown = [item for item in ac_ids if item not in catalog]
        if unknown:
            gaps.append(f"{label}: acIds 不在验收条件里：{unknown}")
    try:
        gaps.extend(blocking(load(run, TABLE), run))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        gaps.append(str(exc))
    try:
        status = version_status(run, source_id)
        if status.get("reason") in VERSION_REASONS:
            gaps.append(VERSION_REASONS[status["reason"]])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        gaps.append("版本：" + str(exc))
    return gaps


def workbook_issues(path):
    from openpyxl import load_workbook
    from render_review_table import HEAD_MAIN, HEAD_NOTES, RESULTS

    book = load_workbook(path)
    issues = []
    if "用例主表" not in book.sheetnames:
        return ["缺少工作表「用例主表」"]
    sheet = book["用例主表"]
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1, max_col=6))]
    if headers != HEAD_MAIN:
        issues.append(f"主表表头应为 {HEAD_MAIN}，实际 {headers}")
    expected = '"' + ",".join(RESULTS) + '"'
    formulas = [item.formula1 for item in sheet.data_validations.dataValidation]
    if expected not in formulas:
        issues.append("测试结果下拉与约定选项不一致")
    for row in range(2, (sheet.max_row or 1) + 1):
        if sheet.cell(row, 6).value != "未测试":
            issues.append(f"第 {row} 行测试结果不是「未测试」")
            break
    for row in range(1, (sheet.max_row or 1) + 1):
        height = sheet.row_dimensions[row].height
        if height is not None and height > 409:
            issues.append(f"第 {row} 行行高 {height} 超过 409")
    for merge in sheet.merged_cells.ranges:
        if merge.min_col != 1 or merge.max_col != 1:
            issues.append(f"合并超出分组列：{merge.coord}")
    if "待确认事项" in book.sheetnames:
        notes = book["待确认事项"]
        got = [cell.value for cell in next(notes.iter_rows(min_row=1, max_row=1, max_col=3))]
        if got != HEAD_NOTES:
            issues.append(f"待确认事项表头应为 {HEAD_NOTES}，实际 {got}")
    return issues


def workbook_gaps(run, round_id):
    gaps = []
    paths = [run / "cases/cases.xlsx", run / "cases/presentation" / round_id / "cases.xlsx"]
    for path in paths:
        rel = path.relative_to(run).as_posix()
        if not path.is_file():
            gaps.append("缺少 " + rel)
            continue
        gaps.extend(f"{rel}: {issue}" for issue in workbook_issues(path))
    return gaps


def mapping_gaps(run, round_id):
    base = run / "cases/presentation" / round_id
    mapping = read_json(base / "mapping.json")
    content = read_json(base / "content.json")
    raw = read_json(run / "cases/cases.json")
    if not isinstance(mapping, dict) or not isinstance(content, dict) or raw is None:
        return ["缺少 mapping.json、content.json 或 cases.json"]
    cases = raw.get("cases") if isinstance(raw, dict) else raw
    by_id = {row.get("caseId"): row for row in cases if isinstance(row, dict)}
    gaps = []
    mapped = [row.get("caseId") for row in mapping.get("cases") or []]
    content_ids = [row.get("caseId") for row in content.get("cases") or []]
    if mapped != content_ids:
        gaps.append("mapping 与 content.json 的 caseId 顺序不一致")
    for row in mapping.get("cases") or []:
        case = by_id.get(row.get("caseId"))
        if case is None:
            gaps.append(f"mapping 有未知用例 {row.get('caseId')}")
            continue
        step_ids = [step.get("id") for step in case.get("steps") or []]
        covered = [sid for group in row.get("stepMapping") or [] for sid in group.get("sourceStepIds") or []]
        exempt = set(row.get("stepExemptions") or {})
        if set(covered) | exempt != set(step_ids) or set(covered) & exempt:
            gaps.append(f"{case.get('caseId')} 步骤映射与用例不一致")
        indexes = list(range(len(case.get("assertions") or [])))
        covered_assertions = [item for group in row.get("assertionMapping") or []
                              for item in group.get("sourceIndexes") or []]
        exempt_assertions = set()
        for key in row.get("assertionExemptions") or {}:
            try:
                exempt_assertions.add(int(key))
            except (TypeError, ValueError):
                gaps.append(f"{case.get('caseId')} 断言豁免键不是序号：{key}")
        if set(covered_assertions) | exempt_assertions != set(indexes) or set(covered_assertions) & exempt_assertions:
            gaps.append(f"{case.get('caseId')} 断言映射与用例不一致")
    return gaps


def hash_section(run, round_id):
    from render_review_table import artifact_refs

    missing, mismatch, hashes = [], [], {}
    for ref in artifact_refs(round_id):
        path = run / ref
        if not path.is_file():
            missing.append(ref)
            continue
        hashes[ref] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = read_json(run / "cases/presentation" / round_id / "receipt.json") or {}
    recorded = receipt.get("artifactHashes") or {}
    for ref, digest in hashes.items():
        if ref in recorded and recorded[ref] != digest:
            mismatch.append(ref)
    gaps = [f"缺少 {ref}" for ref in missing] + [f"哈希不一致 {ref}" for ref in mismatch]
    return {"status": "PASSED" if not gaps else "BLOCKED", "gaps": gaps, "hashes": hashes}


def emit(report):
    gaps = [f"{name}: {gap}" for name, section in report["sections"].items() for gap in section.get("gaps") or []]
    report["status"] = "PASSED" if not gaps else "BLOCKED"
    report["gaps"] = gaps
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not gaps else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--cases", action="store_true")
    parser.add_argument("--round", default="1")
    parser.add_argument("--feature")
    parser.add_argument("--source-id")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--previous", help="历史评审表 .xlsx，传给覆盖差集")
    args = parser.parse_args()
    if args.draft and (args.cases or args.feature):
        parser.error("--draft 不和 --cases、--feature 一起用")
    run = args.run.resolve()
    if args.draft:
        return emit({"mode": "draft", "sections": {"draft": section(draft_gaps(run, args.source_id))}})
    sections = case_gates(run, args.baseline, args.previous)
    if args.cases:
        return emit({"mode": "cases", "sections": sections})
    sections["xlsx"] = section(workbook_gaps(run, args.round))
    sections["mapping"] = section(mapping_gaps(run, args.round))
    sections["hashes"] = hash_section(run, args.round)
    if args.feature:
        from publish_review import precheck
        try:
            report = precheck(run, args.feature, args.source_id)
            sections["precheck"] = section(list(report.get("issues") or []))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            sections["precheck"] = section([str(exc)])
    return emit({"mode": "delivery", "sections": sections})


if __name__ == "__main__":
    sys.exit(main())
