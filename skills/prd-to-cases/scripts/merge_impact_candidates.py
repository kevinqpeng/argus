#!/usr/bin/env python3
"""Collect current exploration receipts without making business judgments."""
import argparse
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path


GROUPS = {"sources": "sourceId", "sourceUnits": "unitId", "objects": "objectId",
          "checks": "checkId", "obligations": "obligationId", "handoffs": "handoffId"}
BASE_DIMENSIONS = ("data", "inflight", "lifecycle", "permission", "side_effect", "ordering")
DIMENSION_ORDER = BASE_DIMENSIONS
DIMENSIONS = set(BASE_DIMENSIONS)
EXTRA_QUESTION_MIN = 10
DISPOSITIONS = {"covered", "not_applicable", "unknown", "out_of_scope"}
DECIDED = {"covered", "unknown"}
UNDECIDED = {"not_applicable", "out_of_scope"}
MIN_REASON = 20
FILLER = ("不适用", "无需", "无影响", "无关", "none", "n/a", "na", "无")
NO_BUNDLE = {"data", "permission"}
BUNDLE_MAX = 3
GAP_LIMIT = 1 / 3
WINDOW_LINES = (4, 6)
WINDOW = re.compile(r"(.+):(\d+)-(\d+)$")
FORBIDDEN_CHECK_KEYS = {"kind", "result", "testability"}
CODE_REF = re.compile(r"(.+):(\d+)(?:-(\d+))?$")
ID_RULES = {"sourceUnits": "-U", "checks": "-C", "obligations": "-OB-", "handoffs": "-H"}
# assemble_baseline.impact 用同一份必填清单。schemaVersion 由调用方先补 1。
IMPACT_REQUIRED = {
    "sources": ("sourceId", "kind", "sourceRef", "sourceHash"),
    "sourceUnits": ("unitId", "sourceIds", "sourceExcerpt", "evidenceRef", "disposition", "reason"),
    "objects": ("objectId", "description"),
    "checks": ("checkId", "objectId", "sourceIds", "evidenceRef", "disposition", "reason"),
    "obligations": ("obligationId", "sourceIds", "text"),
    "handoffs": ("handoffId", "producer", "path", "evidenceRef", "question"),
}
EMPTY_FIELDS = (
    {"field": "reviewRef", "groups": ["sourceUnits", "checks"], "value": "",
     "reason": "本轮没有人工评审引用。留空字符串，不要为此询问主 Agent。"},
    {"field": "approvalRef", "groups": ["obligations"], "value": "",
     "reason": "候选义务尚未批准。留空字符串，不要为此询问主 Agent。"},
    {"field": "expected", "groups": ["obligations"], "value": "",
     "reason": "预期未冻结时留空，reviewStatus 写 needs_clarification。"},
    {"field": "obligationIds", "groups": ["sourceUnits", "checks"], "value": [],
     "reason": "还没有义务时用空数组。"},
    {"field": "acIds", "groups": ["obligations"], "value": [],
     "reason": "场景尚未冻结时用空数组。"},
    {"field": "receiver", "groups": ["handoffs"], "value": "",
     "reason": "没有接收任务时留空，并在 question 写未决问题。"},
)
EMPTY_FIELD_NAMES = frozenset(row["field"] for row in EMPTY_FIELDS)
EVIDENCE_RULE = "evidenceRef 是字符串。多条用英文分号 ';' 拼接，最多 2 条。每条是 文件:行 或 文件:起-止，含首尾最多 3 行。不要写成 JSON 数组。"


def evidence_errors(value):
    if isinstance(value, list):
        return ["evidenceRef 必须是分号拼接的字符串，不能是 JSON 数组"]
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


def explained(reason):
    text = str(reason or "").strip()
    for filler in FILLER:
        text = text.replace(filler, " ")
    return len(str(reason or "").strip()) >= MIN_REASON and bool(text.strip("。. ;；,，:：、 \n"))


def plan_dimensions(plan):
    """基线六维是分母下限；计划只能往上加，不能减也不能改名。"""
    extra = plan.get("extraDimensions")
    if extra is None:
        return list(BASE_DIMENSIONS), []
    if not isinstance(extra, list):
        return list(BASE_DIMENSIONS), ["extraDimensions 必须是数组"]
    keys, errors = [], []
    for row in extra:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str) or not row["key"].strip():
            errors.append("extraDimensions 每项需要 key 与 question")
            continue
        key, question = row["key"].strip(), row.get("question")
        if key in BASE_DIMENSIONS or key in keys:
            errors.append(f"扩展维度不能重复或覆盖基线六维：{key}")
            continue
        if not isinstance(question, str) or len(question.strip()) < EXTRA_QUESTION_MIN:
            errors.append(f"扩展维度 {key} 要写清它回答什么问题（{EXTRA_QUESTION_MIN} 字以上）")
            continue
        keys.append(key)
    return list(BASE_DIMENSIONS) + keys, errors


def dims_of(row):
    """A check may conclude several unrelated dimensions at once, but not data/permission."""
    listed = row.get("dimensions")
    return listed if isinstance(listed, list) else [row.get("dimension")]


def bundle_errors(check_id, row, dimensions=DIMENSION_ORDER):
    listed = row.get("dimensions")
    if listed is None:
        return []
    if not isinstance(listed, list) or not listed:
        return [f"check {check_id}: dimensions must be a non-empty list"]
    errors = []
    if len(set(listed)) != len(listed):
        errors.append(f"check {check_id}: dimensions 有重复项")
    for dimension in listed:
        if dimension not in dimensions:
            errors.append(f"check {check_id}: invalid dimension {dimension}")
        elif dimension in NO_BUNDLE:
            errors.append(f"check {check_id}: {dimension} 不能和别的维度合并，必须单独给结论")
    if len(listed) > BUNDLE_MAX:
        errors.append(f"check {check_id}: dimensions 一次最多 {BUNDLE_MAX} 个")
    if row.get("disposition") in UNDECIDED and not explained(row.get("reason")):
        errors.append(f"check {check_id}: 合并豁免必须写清理由（需 {MIN_REASON} 字以上）")
    return errors


def matrix_gaps(object_ids, checks, dimensions=DIMENSION_ORDER):
    """Blank cells are allowed, but each one becomes a named gap to register."""
    gaps = []
    for object_id in object_ids:
        for dimension in dimensions:
            if not [row for row in checks if row.get("objectId") == object_id
                    and dimension in dims_of(row)]:
                gaps.append({"gapId": f"GAP-{object_id}-{dimension}",
                             "objectId": object_id, "dimension": dimension,
                             "question": f"{object_id} 的 {dimension} 维度本轮未核查，是否影响本次需求？"})
    return gaps


def matrix_errors(object_ids, checks, dimensions=DIMENSION_ORDER):
    """Blank cells are counted, not forbidden: only an unusable matrix blocks."""
    errors = []
    for object_id in object_ids:
        for dimension in dimensions:
            cell = [row for row in checks if row.get("objectId") == object_id
                    and dimension in dims_of(row)]
            if not cell:
                continue
            label = f"{object_id} × {dimension}"
            if any(row.get("disposition") in DECIDED for row in cell):
                continue
            for row in cell:
                if not explained(row.get("reason")):
                    errors.append(f"六维结论不成立 {label}：{row.get('disposition')} 理由不足 "
                                  f"（需 {MIN_REASON} 字以上并写清为什么该维度不受影响）")
    gaps = matrix_gaps(object_ids, checks, dimensions)
    total = len(object_ids) * len(dimensions)
    if total and len(gaps) / total > GAP_LIMIT:
        errors.append(f"六维留空 {len(gaps)}/{total} 超过三分之一：先把变更对象查完，"
                      f"剩下的空格才能登记为待确认")
    return errors


def matrix_summary(object_ids, checks, dimensions=DIMENSION_ORDER):
    counts = {name: 0 for name in ("covered", "unknown", "notApplicable", "outOfScope", "empty")}
    matrix = {}
    for object_id in object_ids:
        matrix[object_id] = {}
        for dimension in dimensions:
            cell = [row for row in checks if row.get("objectId") == object_id
                    and dimension in dims_of(row)]
            dispositions = [row.get("disposition") for row in cell]
            matrix[object_id][dimension] = {
                "checkIds": [row.get("checkId") for row in cell], "dispositions": dispositions}
            if not cell:
                counts["empty"] += 1
            for disposition in dispositions:
                key = {"not_applicable": "notApplicable", "out_of_scope": "outOfScope"}.get(disposition, disposition)
                if key in counts:
                    counts[key] += 1
    return {"schemaVersion": 1, "dimensions": list(dimensions), "objects": len(object_ids),
            "cells": len(object_ids) * len(dimensions), "counts": counts, "matrix": matrix}


def merged_hints(object_ids, checks, dimensions=DIMENSION_ORDER):
    """An object that concludes nothing at all is probably not a change object."""
    hints = []
    for object_id in object_ids:
        cells = [row for row in checks
                 if row.get("objectId") == object_id and any(d in dimensions for d in dims_of(row))]
        if cells and all(row.get("disposition") in UNDECIDED for row in cells):
            hints.append(f"{object_id} 所有维度都是 not_applicable/out_of_scope："
                         f"它可能不是本次变更对象，考虑合并或删除")
    return hints


def window_errors(plan):
    """The index must hand every object 4-6 resolvable 3-line windows before dispatch."""
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["探索计划没有任务"]
    errors = []
    low, high = WINDOW_LINES
    for task in tasks:
        task_id = task.get("taskId")
        object_ids = task.get("objectIds")
        if not isinstance(object_ids, list) or not object_ids:
            errors.append(f"{task_id}: 计划未写 objectIds（六维无法逐对象核对）")
            continue
        windows = task.get("windows")
        if not isinstance(windows, list) or not windows:
            errors.append(f"{task_id}: 缺少 windows（每个对象写 {low} 到 {high} 个 文件:起始-结束）")
            continue
        by_object = {}
        for row in windows:
            if not isinstance(row, dict):
                errors.append(f"{task_id}: windows 每项必须是 objectId/refs 对象")
                continue
            object_id, refs = row.get("objectId"), row.get("refs")
            if object_id in by_object:
                errors.append(f"{task_id}: 对象窗口重复 {object_id}")
                continue
            by_object[object_id] = refs
        for object_id in object_ids:
            refs = by_object.get(object_id)
            if not isinstance(refs, list) or not low <= len(refs) <= high:
                errors.append(f"{task_id}: {object_id} 应有 {low} 到 {high} 个窗口，实际 "
                              f"{len(refs) if isinstance(refs, list) else 0}")
                continue
            for ref in refs:
                match = WINDOW.fullmatch(ref.strip()) if isinstance(ref, str) else None
                if match is None:
                    errors.append(f"{task_id}: {object_id} 窗口必须写成 文件:起始-结束：{str(ref)[:40]}")
                    continue
                start, end = int(match.group(2)), int(match.group(3))
                if end < start or end - start + 1 > 3:
                    errors.append(f"{task_id}: {object_id} 窗口跨度超过 3 行：{ref}")
        for extra in sorted(set(by_object) - set(object_ids)):
            errors.append(f"{task_id}: 窗口对象未分配给本任务：{extra}")
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
    dimensions, dimension_errors = plan_dimensions(plan)
    object_owner = owners(tasks)
    entries = {group: {} for group in GROUPS}
    errors = list(dimension_errors)
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
                    if not isinstance(row.get("dimensions"), list) and row.get("dimension") not in set(dimensions):
                        errors.append(f"{tid}: check {row[key]}: invalid dimension")
                    errors.extend(f"{tid}: {issue}" for issue in bundle_errors(row[key], row, dimensions))
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
    errors.extend(window_errors(plan))
    errors.extend(f"missing receipt: {tid}" for tid in sorted(set(expected) - seen))
    object_ids = sorted({*(object_owner or {}), *entries["objects"]})
    flat_checks = [variant["value"] for variants in entries["checks"].values() for variant in variants]
    errors.extend(matrix_errors(object_ids, flat_checks, dimensions))
    gaps = matrix_gaps(object_ids, flat_checks, dimensions)
    conflicts = [f"{group}: {ident}" for group, values in entries.items()
                 for ident, variants in values.items() if len(variants) > 1]
    return {"schemaVersion": 1, "inputHash": plan["inputHash"],
            "status": "BLOCKED" if errors or conflicts else "READY_FOR_REVIEW",
            "errors": sorted(errors), "conflicts": sorted(conflicts), "candidates": entries,
            "gaps": gaps, "hints": merged_hints(object_ids, flat_checks, dimensions),
            "dimensions": list(dimensions),
            "matrix": {**matrix_summary(object_ids, flat_checks, dimensions), "gaps": gaps}}


def find_task(plan, task_id):
    if plan.get("schemaVersion") != 1 or not isinstance(plan.get("inputHash"), str) or not plan["inputHash"]:
        raise ValueError("invalid plan inputHash or schemaVersion")
    matches = [task for task in plan.get("tasks") or [] if task.get("taskId") == task_id]
    if len(matches) != 1:
        raise ValueError(f"unknown task: {task_id}")
    task = matches[0]
    if type(task.get("attempt")) is not int or task["attempt"] < 1:
        raise ValueError("invalid attempt")
    return task


def receipt_rel(task):
    return f"cases/exploration/{task['taskId']}/{task['attempt']}.json"


def inside(root, rel):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        raise ValueError("path must stay inside the root")
    parts = Path(rel).parts
    if ".." in parts or parts[0] == "":
        raise ValueError("path must stay inside the root")
    path = (root.resolve() / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path must stay inside the root")
    return path


def read_window(baseline, ref, object_id):
    match = WINDOW.fullmatch(ref.strip()) if isinstance(ref, str) else None
    if match is None:
        raise ValueError(f"窗口必须写成 文件:起始-结束：{str(ref)[:40]}")
    rel, start, end = match.group(1), int(match.group(2)), int(match.group(3))
    path = inside(baseline, rel)
    if not path.is_file():
        raise ValueError(f"窗口文件不存在：{ref}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if start < 1 or end > len(lines):
        raise ValueError(f"窗口行号越界：{ref}")
    return {"objectId": object_id, "ref": ref, "evidenceRef": ref,
            "text": "\n".join(lines[start - 1:end])}


def frozen_prd_source(run, prd_rel, source_id):
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("missing source id")
    path = inside(run, prd_rel)
    if not path.is_file():
        raise ValueError("冻结 PRD 不存在")
    return {"schemaVersion": 1, "sourceId": source_id, "kind": "prd",
            "sourceRef": "run://" + prd_rel.replace("\\", "/").lstrip("/"),
            "sourceHash": hashlib.sha256(path.read_bytes()).hexdigest()}


def minimal_sample(task, plan, source, windows):
    task_id, object_id = task["taskId"], task["objectIds"][0]
    ref = windows[0]["evidenceRef"]
    return {
        "schemaVersion": 1, "taskId": task_id, "attempt": task["attempt"],
        "inputHash": plan["inputHash"], "status": "complete", "sources": [source],
        "sourceUnits": [{
            "schemaVersion": 1, "unitId": f"{task_id}-U1", "sourceIds": [source["sourceId"]],
            "sourceExcerpt": "替换成冻结来源中的原文。", "evidenceRef": "cases/sources/prd.md:1",
            "disposition": "covered", "obligationIds": [], "reviewRef": "",
            "reason": "样例只示范字段形状，正式回执写成这条来源的处置理由。",
        }],
        "objects": [{"schemaVersion": 1, "objectId": object_id, "description": "替换成该对象的变更前后。"}],
        "checks": [{
            "schemaVersion": 1, "checkId": f"{task_id}-C1", "objectId": object_id,
            "dimension": "data", "sourceIds": [source["sourceId"]], "evidenceRef": ref,
            "disposition": "unknown", "obligationIds": [], "reviewRef": "",
            "reason": "样例：evidenceRef 是分号拼接的字符串，不是 JSON 数组。",
        }],
        "obligations": [{
            "schemaVersion": 1, "obligationId": f"{task_id}-OB-1", "sourceIds": [source["sourceId"]],
            "text": "替换成候选义务。", "expected": "", "acIds": [],
            "reviewStatus": "needs_clarification", "approvalRef": "",
        }],
        "handoffs": [{
            "schemaVersion": 1, "handoffId": f"{task_id}-H1", "producer": task_id,
            "receiver": "", "path": "替换成跨任务的调用链", "evidenceRef": ref,
            "question": "样例：跨任务未决问题。没有接收任务时 receiver 留空。",
        }],
    }


def build_packet(plan, task_id, *, baseline, run, prd_rel, source_id, packet_path):
    """Render one exploration brief from the same constants the checkers use."""
    task = find_task(plan, task_id)
    errors = window_errors({"tasks": [task]})
    if errors:
        raise ValueError("；".join(errors))
    windows = [read_window(baseline, ref, row["objectId"])
               for row in task["windows"] for ref in row["refs"]]
    source = frozen_prd_source(run, prd_rel, source_id)
    dimensions, dimension_errors = plan_dimensions(plan)
    if dimension_errors:
        raise ValueError("；".join(dimension_errors))
    catalog = ac_catalog(run)
    output = (run.resolve() / receipt_rel(task)).resolve()
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "--task", task_id,
                           "--check", str(output), "--packet", str(Path(packet_path).resolve())])
    sample = minimal_sample(task, plan, source, windows)
    empty_fields = [dict(row) for row in EMPTY_FIELDS]
    rules = [
        "回执只写 output 指向的文件。写完立刻运行 checkCommand，失败只改这份回执。",
        "sources 原样复制 copyVerbatim.sources，不要另写一份。",
        "evidenceRef 使用 windows[].evidenceRef 的字面量，类型是字符串。sourceUnits 同样最多 2 段、每段最多 3 行；更长就拆成两条单元。",
        "windows[].text 已是不超过 3 行的原文。这些窗口不要再打开仓库检索。",
    ]
    if catalog:
        sample["obligations"][0]["acIds"] = [catalog[0]["acId"]]
        sample["obligations"][0]["reviewStatus"] = "draft"
        for row in empty_fields:
            if row["field"] == "acIds":
                row["reason"] = "从 acCatalog 选择并写入。只有预期未明且 reviewStatus 为 needs_clarification 时才用空数组。"
        rules.append("预期已经明确的义务，acIds 从 acCatalog 里选，写在本回执里。不要留空留给主 Agent 回填。")
        rules.append("emptyFields 里除 acIds 以外的留空是合法的，不要询问主 Agent。")
    else:
        rules.append("emptyFields 中的留空是合法的，不要询问主 Agent。")
    return {
        "schemaVersion": 1, "taskId": task_id, "attempt": task["attempt"],
        "inputHash": plan["inputHash"], "objectIds": list(task["objectIds"]),
        "requiredFields": {group: ["schemaVersion", *fields] for group, fields in IMPACT_REQUIRED.items()},
        "idRules": {group: f"{task_id}{suffix}<n>" for group, suffix in ID_RULES.items()},
        "evidenceRef": EVIDENCE_RULE,
        "acCatalog": catalog,
        "dimensionRules": {"allowed": list(dimensions), "noBundle": sorted(NO_BUNDLE),
                           "bundleMax": BUNDLE_MAX,
                           "note": "data 和 permission 必须单独给结论。dimensions 一次最多 3 个。"},
        "emptyFields": empty_fields,
        "copyVerbatim": {"sources": [source]},
        "windows": windows,
        "sample": sample,
        "output": str(output),
        "checkCommand": command,
        "workerRules": rules,
    }


def ac_catalog(run):
    """AC already extracted for this run. Empty when exploration starts before that file exists."""
    path = Path(run) / "cases" / "acs.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("acs.json 必须是数组")
    catalog = []
    for row in data:
        if not isinstance(row, dict) or not str(row.get("acId") or "").strip():
            raise ValueError("acs.json 每项需要 acId")
        catalog.append({"acId": row["acId"], "text": row.get("text") or "",
                        "sourceRef": row.get("sourceRef") or ""})
    return catalog


def obligation_ac_errors(packet, row, label):
    """When the packet carries the AC list, an obligation names its ACs in the same write."""
    catalog = [item.get("acId") for item in packet.get("acCatalog") or [] if isinstance(item, dict)]
    if not catalog:
        return []
    ac_ids = row.get("acIds")
    if not isinstance(ac_ids, list) or any(not isinstance(item, str) for item in ac_ids):
        return [f"{label}: acIds 必须是字符串数组"]
    unknown = [item for item in ac_ids if item not in catalog]
    if unknown:
        return [f"{label}: acIds 不在 acCatalog 中：{unknown}"]
    clarifying = row.get("reviewStatus") == "needs_clarification" and not str(row.get("expected") or "").strip()
    if not clarifying and not ac_ids:
        return [f"{label}: 义务要当场写 acIds，从 acCatalog 选择"]
    return []


def filled(row, field):
    return field in row and row[field] not in ("", None, [])


def check_receipt(packet, receipt):
    """Shape-check one receipt. Merge still owns the cross-task matrix."""
    errors = []
    task_id = packet.get("taskId")
    if receipt.get("schemaVersion") != 1:
        errors.append("missing schemaVersion")
    if receipt.get("taskId") != task_id:
        errors.append("taskId mismatch")
    if receipt.get("attempt") != packet.get("attempt") or receipt.get("inputHash") != packet.get("inputHash"):
        errors.append("attempt or inputHash mismatch")
    if receipt.get("status") not in {"complete", "partial"}:
        errors.append("status must be complete or partial")
    if receipt.get("sources") != (packet.get("copyVerbatim") or {}).get("sources"):
        errors.append("sources 必须原样复制 packet.copyVerbatim.sources")
    allowed = set((packet.get("dimensionRules") or {}).get("allowed") or BASE_DIMENSIONS)
    assigned = set(packet.get("objectIds") or [])
    for group, key in GROUPS.items():
        rows = receipt.get(group)
        if not isinstance(rows, list):
            errors.append(f"missing {group}")
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                errors.append(f"invalid {group} row")
                continue
            if row.get("schemaVersion") != 1:
                errors.append(f"{group}: missing schemaVersion")
            label = row.get(key) if isinstance(row.get(key), str) else group
            missing = [field for field in IMPACT_REQUIRED[group] if not filled(row, field)]
            if missing:
                errors.append(f"{label} 缺少 {', '.join(missing)}")
            if isinstance(row.get(key), str):
                if row[key] in seen:
                    errors.append(f"duplicate {group} identifier: {row[key]}")
                seen.add(row[key])
                if not identifier_ok(task_id, group, row[key]):
                    errors.append(f"{group} id must be {task_id}{ID_RULES[group]}<n>: {row[key]}")
            if group in {"sourceUnits", "checks", "handoffs"}:
                for issue in evidence_errors(row.get("evidenceRef")):
                    errors.append(f"{label}: {issue}")
            if group == "sourceUnits" and row.get("disposition") not in DISPOSITIONS:
                errors.append(f"{label}: invalid disposition")
            if group == "objects" and row.get("objectId") not in assigned:
                errors.append(f"object not assigned: {row.get('objectId')}")
            if group == "obligations":
                errors.extend(obligation_ac_errors(packet, row, str(label)))
            if group == "checks":
                if not isinstance(row.get("dimensions"), list) and row.get("dimension") not in allowed:
                    errors.append(f"{label}: invalid dimension")
                errors.extend(bundle_errors(str(label), row, list(allowed)))
                forbidden = FORBIDDEN_CHECK_KEYS & row.keys()
                if forbidden:
                    errors.append(f"{label}: forbidden {','.join(sorted(forbidden))}")
                if assigned and row.get("objectId") not in assigned:
                    errors.append(f"check object not assigned: {row.get('objectId')}")
    return errors


def stat_receipts(run, plan):
    missing, empty, ok = [], [], []
    for task in plan.get("tasks") or []:
        rel = receipt_rel(task)
        path = run / rel
        if not path.is_file():
            missing.append(rel)
        elif path.stat().st_size == 0:
            empty.append(rel)
        else:
            ok.append(rel)
    return {"status": "READY" if not missing and not empty else "REDISPATCH",
            "missing": missing, "empty": empty, "ok": ok}


def check_main(argv):
    parser = argparse.ArgumentParser(description="Check one exploration receipt against its packet")
    parser.add_argument("--task", required=True)
    parser.add_argument("--check", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    args = parser.parse_args(argv)
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    receipt = json.loads(args.check.read_text(encoding="utf-8"))
    if packet.get("taskId") != args.task or receipt.get("taskId") != args.task:
        errors = [f"task id must be {args.task}"]
    else:
        errors = check_receipt(packet, receipt)
    status = "PASSED" if not errors else "BLOCKED"
    print(json.dumps({"status": status, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if status == "PASSED" else 2


def stat_main(argv):
    parser = argparse.ArgumentParser(description="Stat exploration receipts after workers return")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    result = stat_receipts(args.run, plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "READY" else 2


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--stat"]:
        try:
            return stat_main(argv[1:])
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "REDISPATCH", "errors": [str(exc)]}, ensure_ascii=False))
            return 2
    if "--check" in argv:
        try:
            return check_main(argv)
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "BLOCKED", "errors": [str(exc)]}, ensure_ascii=False))
            return 2
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
