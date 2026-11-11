#!/usr/bin/env python3
"""Adapt a frozen PRD and exploration receipts into baseline JSON.

The model fills a bindings TSV and the exploration receipts. This script does
the conversion. Do not rewrite it under /tmp.

  assemble_baseline.py rows   --run RUN --prd cases/sources/prd.md
  assemble_baseline.py ac     --run RUN --prd cases/sources/prd.md --source-id ID --bindings rows.tsv
  assemble_baseline.py cover  --run RUN --prd cases/sources/prd.md --acs cases/acs.json
  assemble_baseline.py impact --run RUN --prd cases/sources/prd.md --source-id ID --candidates merged.json
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generation import file_hash, publish_bytes, safe_path
from merge_impact_candidates import (BASE_DIMENSIONS, EMPTY_FIELD_NAMES, IMPACT_REQUIRED, bundle_errors,
                                     matrix_errors, matrix_gaps, matrix_summary, merged_hints)

PRIORITIES = {"P0", "P1", "P2", "P3"}
DISPOSITIONS = {"covered", "not_applicable", "unknown", "out_of_scope"}
BINDINGS = ("rowRef", "acId", "requirementId", "text", "priority", "scenarioId", "title", "expected")
SKIP_CELL = re.compile(r"非必填|不必填|不必须|选填|非必须|可选")
MUST_CELL = re.compile(r"(?i)(?<!非)(?<!不)(?:必填|必须|校验)|(?<![A-Za-z])Must(?![A-Za-z])")
HEADER_MUST = re.compile(r"必填|校验|(?i:must)")
CELL_YES = re.compile(r"(?i)^(是|y|yes|must|必填|校验|必须)$")
KINDS = {"prd", "business_rule", "contract", "history", "code", "observation"}
DIMENSIONS = set(BASE_DIMENSIONS)
HASH = re.compile(r"^[a-f0-9]{64}$")


def load_rows(text, prd_name):
    lines = text.splitlines()
    heading = ""
    rows = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            index += 1
            continue
        parsed = pipe_table(lines, index)
        if parsed is None:
            parsed = aligned_table(lines, index)
        if parsed is None:
            index += 1
            continue
        header, data, nxt = parsed
        for line_no, cells in data:
            rows.append({"ref": f"{prd_name}:{line_no}", "line": line_no, "heading": heading,
                         "must": is_must(header, cells), "cells": cells})
        index = nxt
    return rows


def pipe_table(lines, index):
    if not lines[index].lstrip().startswith("|"):
        return None
    header = cells_of(lines[index])
    if index + 1 >= len(lines) or not lines[index + 1].lstrip().startswith("|"):
        return None
    data = []
    cursor = index + 2
    while cursor < len(lines) and lines[cursor].lstrip().startswith("|"):
        data.append((cursor + 1, cells_of(lines[cursor])))
        cursor += 1
    return header, data, cursor


def aligned_table(lines, index):
    header = aligned_cells(lines[index])
    if header is None:
        return None
    data = []
    cursor = index + 1
    while cursor < len(lines):
        cells = aligned_cells(lines[cursor])
        if cells is None:
            break
        data.append((cursor + 1, cells))
        cursor += 1
    if not data:
        return None
    return header, data, cursor


def cells_of(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def aligned_cells(line):
    if line.lstrip().startswith("|") or not line.strip():
        return None
    if "\t" not in line and not re.search(r"\s{2,}", line):
        return None
    cells = [part.strip() for part in re.split(r"\t+|\s{2,}", line.strip()) if part.strip()]
    return cells if len(cells) >= 2 else None


def is_must(header, cells):
    for head, cell in zip(header, cells):
        if HEADER_MUST.search(head) and CELL_YES.fullmatch(cell):
            return True
    return any(cell and not SKIP_CELL.search(cell) and MUST_CELL.search(cell) for cell in cells)


def prd_file(run, name):
    path = safe_path(run, name)
    if not path.is_file():
        raise ValueError(f"找不到冻结 PRD: {name}")
    return path


def write_json(path, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    publish_bytes(path, payload)


def read_bindings(path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != list(BINDINGS):
            raise ValueError("绑定表表头必须是 " + "\t".join(BINDINGS))
        rows = list(reader)
    if not rows:
        raise ValueError("绑定表为空")
    return rows


def command_rows(run, prd):
    path = prd_file(run, prd)
    print(json.dumps({"rows": load_rows(path.read_text(encoding="utf-8"), prd)}, ensure_ascii=False, indent=2))


def source_ref(prd, ref):
    prefix = prd + ":"
    if not ref.startswith(prefix) or not ref[len(prefix):].isdigit():
        raise ValueError(f"rowRef 必须来自 rows 输出: {ref}")
    return "run://" + ref


def command_ac(run, prd, source_id, bindings, baseline):
    path = prd_file(run, prd)
    known = {row["ref"] for row in load_rows(path.read_text(encoding="utf-8"), prd)}
    digest = file_hash(path)
    grouped = {}
    requirements = {}
    for raw in read_bindings(bindings):
        ref = (raw.get("rowRef") or "").strip()
        if ref not in known:
            raise ValueError(f"绑定了不存在的表行: {ref}")
        ac_id = raw["acId"].strip()
        requirement_id = raw["requirementId"].strip()
        priority = raw["priority"].strip()
        if not ac_id or not requirement_id or priority not in PRIORITIES or not raw["text"].strip():
            raise ValueError(f"绑定行无效: {ref}")
        if not raw["scenarioId"].strip() or not raw["title"].strip() or not raw["expected"].strip():
            raise ValueError(f"绑定行无效: {ref}")
        bucket = grouped.setdefault(ac_id, [])
        bucket.append(raw)
        previous = requirements.get(requirement_id)
        if previous and previous != raw["text"].strip():
            raise ValueError(f"同一需求的正文不一致: {requirement_id}")
        requirements[requirement_id] = raw["text"].strip()
    acs, scenarios = [], []
    for ac_id, items in grouped.items():
        priorities = {item["priority"].strip() for item in items}
        scenarios_ids = {item["scenarioId"].strip() for item in items}
        titles = {item["title"].strip() for item in items}
        expected = {item["expected"].strip() for item in items}
        requirement_ids = {item["requirementId"].strip() for item in items}
        texts = {item["text"].strip() for item in items}
        if len(priorities) != 1 or len(scenarios_ids) != 1 or len(titles) != 1 or len(expected) != 1 or len(requirement_ids) != 1 or len(texts) != 1:
            raise ValueError(f"同一 AC 的场景或优先级不一致: {ac_id}")
        acs.append({
            "schemaVersion": 1, "acId": ac_id, "requirementId": items[0]["requirementId"].strip(),
            "sourceId": source_id, "sourceHash": digest, "text": items[0]["text"].strip(),
            "sourceRef": ";".join(source_ref(prd, item["rowRef"].strip()) for item in items),
            "priority": items[0]["priority"].strip(), "reviewStatus": "draft"})
        scenarios.append({
            "schemaVersion": 1, "scenarioId": items[0]["scenarioId"].strip(), "title": items[0]["title"].strip(),
            "expected": items[0]["expected"].strip(), "baselineVersion": baseline,
            "acIds": [ac_id], "preconditions": [], "priority": items[0]["priority"].strip(),
            "reviewStatus": "draft"})
    first_ref = {}
    for items in grouped.values():
        requirement_id = items[0]["requirementId"].strip()
        first_ref.setdefault(requirement_id, source_ref(prd, items[0]["rowRef"].strip()))
    requirement_rows = [{"schemaVersion": 1, "requirementId": requirement_id, "sourceId": source_id,
                         "sourceHash": digest, "text": text, "sourceRef": first_ref[requirement_id]}
                        for requirement_id, text in requirements.items()]
    cases = safe_path(run, "cases")
    write_json(cases / "requirements.json", requirement_rows)
    write_json(cases / "acs.json", acs)
    write_json(cases / "scenarios.json", scenarios)
    print(json.dumps({"requirements": len(requirement_rows), "acs": len(acs), "scenarios": len(scenarios)}, ensure_ascii=False))


def command_cover(run, prd, acs_name):
    path = prd_file(run, prd)
    must = [row["ref"] for row in load_rows(path.read_text(encoding="utf-8"), prd) if row["must"]]
    acs = json.loads(safe_path(run, acs_name).read_text(encoding="utf-8"))
    cited = set()
    for item in acs if isinstance(acs, list) else []:
        for part in str(item.get("sourceRef", "")).split(";"):
            cited.add(part.strip().removeprefix("run://"))
    missing = [ref for ref in must if ref not in cited]
    if missing:
        raise ValueError("Must/校验行没有 AC: " + ", ".join(missing))
    print(json.dumps({"must": len(must), "status": "PASSED"}, ensure_ascii=False))


def one(candidates, group):
    rows = []
    for ident, variants in (candidates.get(group) or {}).items():
        if len(variants) != 1:
            raise ValueError(f"未解决的候选冲突: {group} {ident}")
        rows.append(variants[0]["value"])
    return rows


def require_fields(row, fields, label):
    missing = [field for field in fields
               if field not in EMPTY_FIELD_NAMES and (field not in row or row[field] in ("", None, []))]
    if missing:
        raise ValueError(f"{label} 缺少 {', '.join(missing)}")


def command_impact(run, prd, source_id, candidates_path):
    path = prd_file(run, prd)
    merged = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    if merged.get("status") != "READY_FOR_REVIEW":
        raise ValueError("探索候选还不是 READY_FOR_REVIEW")
    dimensions = merged.get("dimensions") or list(BASE_DIMENSIONS)
    if not isinstance(dimensions, list) or tuple(dimensions[:len(BASE_DIMENSIONS)]) != BASE_DIMENSIONS or len(set(dimensions)) != len(dimensions):
        raise ValueError("维度不能少于或替换基线六维，只能在其后追加")
    candidates = merged.get("candidates") or {}
    sources = one(candidates, "sources")
    units = one(candidates, "sourceUnits")
    objects = one(candidates, "objects")
    checks = one(candidates, "checks")
    obligations = one(candidates, "obligations")
    digest = file_hash(path)
    seen = False
    for source in sources:
        source.setdefault("schemaVersion", 1)
        require_fields(source, IMPACT_REQUIRED["sources"], source.get("sourceId", "source"))
        if source["kind"] not in KINDS:
            raise ValueError(f"来源类型无效: {source['sourceId']}")
        if source["kind"] == "prd" and source.get("sourceId") == source_id:
            seen = True
            if not str(source.get("sourceRef", "")).startswith("run://"):
                raise ValueError("prd sourceRef 必须是 run://")
            if source.get("sourceHash") != digest:
                raise ValueError("prd sourceHash 与冻结快照不一致")
    if not seen:
        raise ValueError(f"候选里没有来源 {source_id}")
    for unit in units:
        unit.setdefault("schemaVersion", 1)
        unit.setdefault("reviewRef", "")
        require_fields(unit, IMPACT_REQUIRED["sourceUnits"], unit.get("unitId", "unit"))
        if unit.get("disposition") not in DISPOSITIONS:
            raise ValueError(f"disposition 无效: {unit.get('unitId')}")
        unit.setdefault("obligationIds", [])
    for item in objects:
        item.setdefault("schemaVersion", 1)
        require_fields(item, IMPACT_REQUIRED["objects"], item.get("objectId", "object"))
    for check in checks:
        check.setdefault("schemaVersion", 1)
        check.setdefault("reviewRef", "")
        check.setdefault("obligationIds", [])
        require_fields(check, IMPACT_REQUIRED["checks"], check.get("checkId", "check"))
        listed = check.get("dimensions")
        if listed is None:
            if check.get("dimension") not in set(dimensions):
                raise ValueError(f"dimension 无效: {check.get('checkId')}")
        else:
            issues = bundle_errors(str(check.get("checkId")), check, dimensions)
            if issues:
                raise ValueError("；".join(issues))
        if check.get("disposition") not in DISPOSITIONS:
            raise ValueError(f"disposition 无效: {check.get('checkId')}")
    for duty in obligations:
        duty.setdefault("schemaVersion", 1)
        duty.setdefault("expected", "")
        duty.setdefault("acIds", [])
        duty.setdefault("reviewStatus", "needs_clarification")
        duty.setdefault("approvalRef", "")
        require_fields(duty, IMPACT_REQUIRED["obligations"], duty.get("obligationId", "obligation"))
    if not HASH.fullmatch(digest):
        raise ValueError("快照哈希无效")
    object_ids = [item["objectId"] for item in objects]
    errors = matrix_errors(object_ids, checks, dimensions)
    if errors:
        raise ValueError("六维矩阵不可用：" + "；".join(errors[:6]))
    document = {"schemaVersion": 1, "dimensions": list(dimensions), "sources": sources,
                "sourceUnits": units, "objects": objects, "checks": checks, "obligations": obligations}
    target = safe_path(run, "cases/impact-analysis.json")
    write_json(target, document)
    gaps = matrix_gaps(object_ids, checks, dimensions)
    write_json(safe_path(run, "cases/exploration/impact-matrix.json"),
               {**matrix_summary(object_ids, checks, dimensions), "gaps": gaps})
    write_json(safe_path(run, "cases/exploration/impact-gaps.json"),
               {"schemaVersion": 1, "gaps": gaps})
    print(json.dumps({"path": "cases/impact-analysis.json", "sourceUnits": len(units),
                      "obligations": len(obligations), "cells": len(object_ids) * len(dimensions),
                      "gaps": len(gaps), "hints": merged_hints(object_ids, checks, dimensions)},
                     ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("rows", "ac", "cover", "impact"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prd", required=True, help="相对 run 的冻结 PRD 路径")
    parser.add_argument("--source-id")
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--acs", default="cases/acs.json")
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--baseline", default="draft")
    args = parser.parse_args()
    try:
        run = args.run.resolve()
        if args.mode == "rows":
            command_rows(run, args.prd)
        elif args.mode == "ac":
            if not args.source_id or not args.bindings:
                parser.error("ac 需要 --source-id 和 --bindings")
            command_ac(run, args.prd, args.source_id, args.bindings, args.baseline)
        elif args.mode == "cover":
            command_cover(run, args.prd, args.acs)
        else:
            if not args.source_id or not args.candidates:
                parser.error("impact 需要 --source-id 和 --candidates")
            command_impact(run, args.prd, args.source_id, args.candidates)
    except (OSError, ValueError, KeyError, TypeError, csv.Error, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
