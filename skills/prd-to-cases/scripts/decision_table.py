#!/usr/bin/env python3
"""决策表：先定组合分母，再冻结场景。

  decision_table.py check  RUN    # 场景冻结前的分母门禁（不写文件）
  decision_table.py expand RUN    # 决策表 → cases/coverage-matrix.json

决策表只登记来源真正裁定过的组合；没裁定的组合按维度级 defaults 继承，
仍继承不到的才阻塞。这样「未规定」不会事后悄悄变成 out_of_scope。

行里 status=unknown 表示来源确实没给规则。为了不把每个未规定项都推给评审，
unknown 必须写 risk：只有 risk=high 进入待确认事项；medium/low 必须写
assumption（本轮采用的合并口径），由用例按该口径执行，不占用评审名单。

行长什么样：
  {"values": {"触发角色": "转办接手人", ...},
   "status": "covered|not_applicable|out_of_scope|unknown",
   "scenarios": ["SCN-1"],              # status=covered 必填，一条以上
   "expected": "页面上能看到的判定结果",  # status=covered 必填
   "sideEffects": [], "notExpectedToChange": [], "illegalTransitions": [],
   "reason": "为什么 Not applicable / out_of_scope / unknown",
   "risk": "high", "assumption": "medium/low 时的合并口径"}

expansions 按实际来源选填 surface、subtype、both-arms、identity。
不适用的类别可以省略。

values 可以只写部分轴，少写就是「管这一片」：{"重新审批方式": "驳回后重新发起"}
一条顶掉这条轴上所有角色的格子，格子的数量由轴相乘得出，不靠人手写。
一格同时命中多条时，限定的轴越多的越具体、优先；两条同级口径同时命中
就是没裁定，阻断而不是按书写顺序取一条。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_impact_candidates import explained  # noqa: E402

DIMENSIONS = "dimensions"
TABLE = "cases/decision-table.json"
MATRIX = "cases/coverage-matrix.json"
ASSUMPTIONS = "cases/assumptions.md"
STATUSES = ("covered", "not_applicable", "out_of_scope", "unknown")
MAX_CELLS = 400
RISKS = ("high", "medium", "low")
# 组合级字段：covered 行必须能独立判定，负面含义的字段允许留空。
JUDGEMENT = ("sideEffects", "notExpectedToChange", "illegalTransitions")
EXPANSIONS = ("surface", "subtype", "both-arms", "identity")


def load(run, name, required=True):
    path = Path(run) / name
    if not path.is_file():
        if required:
            raise ValueError(f"缺少 {name}")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def names_of(table):
    dimensions = table.get(DIMENSIONS) or []
    return [row.get("name") for row in dimensions]


def axes_of(table):
    """决策表的维度定义，顺序即组合里的键顺序。"""
    return [{"name": axis.get("name"), "values": list(axis.get("values") or [])}
            for axis in (table.get(DIMENSIONS) or [])]


def product(dimensions):
    combos = [{}]
    for axis in dimensions:
        combos = [dict(combo, **{axis["name"]: value}) for combo in combos for value in axis["values"]]
    return combos


def axis_errors(table):
    dimensions = table.get(DIMENSIONS) or []
    if not dimensions:
        return ["决策表没有维度"]
    errors = []
    seen = set()
    for axis in dimensions:
        name = axis.get("name")
        values = axis.get("values") or []
        if not name:
            errors.append("决策表维度缺 name")
            continue
        if name in seen:
            errors.append(f"决策表维度重名：{name}")
        seen.add(name)
        if len(values) < 2:
            errors.append(f"决策表维度 {name} 少于两个取值：单值维度不该作为轴")
        if len(set(values)) != len(values):
            errors.append(f"决策表维度 {name} 取值重复")
    cells = 1
    for axis in dimensions:
        cells *= max(1, len(axis.get("values") or []))
    if cells > MAX_CELLS:
        errors.append(f"决策表组合数 {cells} 超过硬上限 {MAX_CELLS}：先用等价类收敛取值，或拆表")
    return errors


def row_errors(row, names, index):
    label = f"决策表第 {index + 1} 行"
    errors = []
    values = row.get("values") or {}
    if not values:
        errors.append(f"{label} 没有 values：至少限定一个轴取值，否则与 defaults 无异")
    unknown = [name for name in values if name not in names]
    if unknown:
        errors.append(f"{label} 引用了未声明的轴：{unknown}")
    status = row.get("status")
    if status not in STATUSES:
        errors.append(f"{label} status 非法：{status}")
        return errors
    if "coveredBy" in row:
        errors.append(f"{label} 没有 coveredBy 字段，覆盖关系写 scenarios（场景 ID）")
    if status == "covered":
        if not (row.get("scenarios") or []):
            errors.append(f"{label} 标 covered 但没有场景")
        if not (row.get("expected") or "").strip():
            errors.append(f"{label} 标 covered 但没有 expected：组合的判定结果必须可观察")
    elif not explained(row.get("reason")):
        errors.append(f"{label} {status} 理由不足（写清为什么该组合不受影响）")
    if status == "unknown":
        risk = row.get("risk")
        if risk not in RISKS:
            errors.append(f"{label} unknown 必须写 risk ∈ {list(RISKS)}："
                          f"只有 high 才进待确认，其余用 assumption 合并口径")
        elif risk != "high" and not (row.get("assumption") or "").strip():
            errors.append(f"{label} unknown risk={risk} 必须写 assumption："
                          f"影响业务结果不大的疑点按合并口径处理，不占用评审名单")
    for field in JUDGEMENT:
        value = row.get(field)
        if value in (None, "", []):
            continue
        if not isinstance(value, list):
            errors.append(f"{label} {field} 必须是数组")
    return errors


def default_errors(default, names, index):
    values = default.get("values") or {}
    errors = []
    if not values:
        errors.append(f"默认口径第 {index + 1} 条没有 values：至少限定一个轴取值")
    if [name for name in values if name not in names]:
        errors.append(f"默认口径第 {index + 1} 条引用了未声明的轴："
                      f"{[name for name in values if name not in names]}")
    if default.get("status") not in STATUSES:
        errors.append(f"默认口径第 {index + 1} 条 status 非法：{default.get('status')}")
    elif not explained(default.get("reason")):
        errors.append(f"默认口径第 {index + 1} 条理由不足")
    return errors


def table_errors(table, cases=None):
    errors = axis_errors(table)
    names = names_of(table)
    if errors:
        return errors
    rows = table.get("rows") or []
    defaults = table.get("defaults") or []
    if not rows and not defaults:
        errors.append("决策表既没有行也没有默认口径")
    for index, row in enumerate(rows):
        errors.extend(row_errors(row, names, index))
    for index, default in enumerate(defaults):
        errors.extend(default_errors(default, names, index))
    combos = product(table.get(DIMENSIONS) or [])
    valid = {axis["name"]: axis["values"] for axis in table.get(DIMENSIONS) or []}
    candidates = rows + defaults
    seen = {}
    for index, row in enumerate(rows):
        key = tuple(sorted((row.get("values") or {}).items()))
        if key in seen:
            errors.append(f"决策表行重复：{dict(key)}（第 {seen[key] + 1} 行与第 {index + 1} 行）")
        seen[key] = index
        for name, value in (row.get("values") or {}).items():
            if name in valid and value not in valid[name]:
                errors.append(f"决策表第 {index + 1} 行取值不在轴上：{name}={value}")
    for combo in combos:
        if pick(combo, candidates) is not None:
            continue
        tied = rivals(combo, candidates)
        if tied:
            errors.append(f"决策表组合有多个同级口径：{combo} → "
                          f"{[item.get('values') for item in tied]}；补一条更具体的行")
        else:
            errors.append(f"决策表组合未裁定：{combo}；写一行，或用 defaults 给维度级口径")
    errors.extend(expansion_errors(table, cases))
    errors.extend(borrowed_coverage(table))
    covered = scenarios_of(table)
    if cases is not None:
        for scenario_id in sorted(covered):
            if not [row for row in cases if scenario_id in (row.get("scenarioIds")
                                                            or ([row["scenarioId"]] if row.get("scenarioId") else []))]:
                errors.append(f"决策表标 covered 的场景没有用例：{scenario_id}")
    return errors


def matches(combo, item):
    values = item.get("values") or {}
    return bool(values) and all(combo.get(name) == value for name, value in values.items())


def pick(combo, items):
    """少写轴就是管这一片：命中多条时更具体的优先，同级命中视为歧义。"""
    picked, best, ties = None, 0, 0
    for item in items:
        if not matches(combo, item):
            continue
        size = len(item.get("values") or {})
        if size > best:
            picked, best, ties = item, size, 1
        elif size == best:
            ties += 1
    return picked if ties == 1 else None


def rivals(combo, items):
    """命中最具体的那几条，用来解释「为什么这一格没裁定」。"""
    hit = [item for item in items if matches(combo, item)]
    if not hit:
        return []
    best = max(len(item.get("values") or {}) for item in hit)
    return [item for item in hit if len(item.get("values") or {}) == best]


def cells_of(table):
    """展开成完整笛卡尔积：最具体的行优先，未登记的格子由 defaults 兜底。"""
    candidates = list(table.get("rows") or []) + list(table.get("defaults") or [])
    cells, uncovered = [], []
    for combo in product(table.get(DIMENSIONS) or []):
        source = pick(combo, candidates)
        if source is None:
            uncovered.append(combo)
            continue
        cell = {"values": combo, "status": source.get("status")}
        if source.get("reason"):
            cell["reason"] = source["reason"]
        if source.get("risk"):
            cell["risk"] = source["risk"]
        if source.get("assumption"):
            cell["assumption"] = source["assumption"]
        cells.append(cell)
    return cells, uncovered


def unknown_combos(table, names):
    """决策表已登记的未定论组合：它们有 risk/assumption，由表负责，不算矩阵缺口。"""
    cells, _ = cells_of(table)
    return {tuple(cell["values"].get(name) for name in names)
            for cell in cells if cell["status"] == "unknown"}


def value_homes(table):
    """取值出现在哪些轴上。展开项点名的取值必须在轴上，才会进入分母。"""
    homes = {}
    for axis in table.get(DIMENSIONS) or []:
        for value in axis.get("values") or []:
            homes.setdefault(value, set()).add(axis.get("name"))
    return homes


def expansion_errors(table, cases=None):
    """检查实际声明的展开项；未声明类别不要求补充。"""
    entries = table.get("expansions")
    if entries is None:
        return []
    if not isinstance(entries, list):
        return ["expansions 必须是数组"]
    by_kind, errors = {}, []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"展开项第 {index + 1} 条不是对象")
            continue
        kind = entry.get("kind")
        if kind not in EXPANSIONS:
            errors.append(f"展开项第 {index + 1} 条 kind 非法：{kind}（可用：{'、'.join(EXPANSIONS)}）")
            continue
        if kind in by_kind:
            errors.append(f"展开项重复：{kind}")
            continue
        by_kind[kind] = entry
    homes = value_homes(table)
    known = scenario_ids(cases) if cases is not None else None
    checkers = {"surface": lambda entry: surface_errors(entry, known),
                "subtype": lambda entry: subtype_errors(entry, homes),
                "both-arms": lambda entry: both_arms_errors(entry, table),
                "identity": lambda entry: identity_errors(entry, homes)}
    for kind, entry in by_kind.items():
        waiver = entry.get("waiver")
        if explained(waiver):
            continue
        if str(waiver or "").strip():
            errors.append(f"展开项 {kind}：豁免未说明依据，写清查过什么；"
                          f"要展开就删掉 waiver，改成这一类的取值")
            continue
        errors.extend(checkers[kind](entry))
    return errors


def scenario_ids(cases):
    found = set()
    for row in cases or []:
        found.update(row.get("scenarioIds") or ([row["scenarioId"]] if row.get("scenarioId") else []))
    return found


def surface_errors(entry, known):
    """可见项各占一个场景，不乘进笛卡尔积。"""
    label = str(entry.get("object") or "").strip()
    items = entry.get("items")
    if not label:
        return ["展开项 surface：写 object，点名这个页面、弹窗或表单"]
    if not isinstance(items, list) or not items:
        return [f"展开项 surface：{label} 的每条文案、说明、可填字段、字数和按钮各写 {{name, scenario}}"]
    errors, seen = [], {}
    for index, item in enumerate(items):
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        scenario = str(item.get("scenario") or "").strip() if isinstance(item, dict) else ""
        if not name or not scenario:
            errors.append(f"展开项 surface：{label} 第 {index + 1} 项要写 name 和 scenario")
            continue
        seen.setdefault(scenario, []).append(name)
        if known is not None and scenario not in known:
            errors.append(f"展开项 surface：场景 {scenario} 没有用例（{name}）")
    for scenario, names in sorted(seen.items()):
        if len(names) > 1:
            errors.append(f"展开项 surface：场景 {scenario} 同时负责 {'、'.join(names)}；每一项单独一个场景")
    return errors


def subtype_errors(entry, homes):
    parent = str(entry.get("parent") or "").strip()
    evidence = str(entry.get("evidence") or "").strip()
    values = list(entry.get("values") or [])
    who = parent or "未命名"
    if not parent:
        return ["展开项 subtype：写 parent，点名被拆开的那个对象"]
    if not evidence:
        return [f"展开项 subtype：{who} 写 evidence，文件:行号，或用「检索无命中」开头并写检索范围"]
    if evidence.startswith("检索无命中"):
        present = values or [parent]
        return [f"展开项 subtype：取值不在任何维度上：{value}；把它留在轴上"
                for value in present if value not in homes]
    if not _file_ref(evidence):
        return [f"展开项 subtype：{who} 的 evidence 要写 文件:行号，或用「检索无命中」开头"]
    if len(values) < 2:
        if explained(entry.get("sameOutcome")):
            return []
        return [f"展开项 subtype：{who} 的代码已拆开，values 至少两个并写上轴；"
                f"结果确实相同就写 sameOutcome"]
    return [f"展开项 subtype：取值不在任何维度上：{value}；加进轴，每个取值单独写场景"
            for value in values if value not in homes]


def _file_ref(evidence):
    path, _, line = evidence.rpartition(":")
    return bool(path.strip()) and line.strip().isdigit()


def both_arms_errors(entry, table):
    valid = {axis.get("name"): set(axis.get("values") or []) for axis in table.get(DIMENSIONS) or []}
    choice = entry.get("choice")
    cause = entry.get("cause")
    arms = list(entry.get("arms") or [])
    if choice not in valid:
        return [f"展开项 both-arms：choice 要是一条轴的名字：{choice}"]
    missing = [arm for arm in arms if arm not in valid[choice]]
    errors = []
    if len(arms) < 2 or missing:
        errors.append(f"展开项 both-arms：arms 要是轴「{choice}」上至少两个取值"
                      + (f"，缺：{'、'.join(missing)}" if missing else ""))
    cause_axes = [name for name, values in valid.items() if cause in values and name != choice]
    if not cause_axes:
        errors.append(f"展开项 both-arms：cause「{cause}」要写在与「{choice}」不同的轴上，"
                      f"选择和原因才会交叉出格子")
    return errors


def identity_errors(entry, homes):
    target = str(entry.get("target") or "").strip()
    values = list(entry.get("values") or [])
    who = target or "未命名"
    errors = []
    if not target:
        errors.append("展开项 identity：写 target，点名被找回的原对象")
    if len(values) < 3:
        errors.append(f"展开项 identity：{who} 要有三个取值：仍能找到并沿用、标识改了找不到、定义换了找不到")
    errors.extend(f"展开项 identity：取值不在任何维度上：{value}；加进轴，每个取值单独写场景"
                  for value in values if value not in homes)
    return errors


def borrowed_coverage(table):
    """一个场景只能证明一个取值。把它同时标到互斥取值上，格子会显示已覆盖，用例里却没有这个人。"""
    cells, _ = cells_of(table)
    candidates = list(table.get("rows") or []) + list(table.get("defaults") or [])
    by_scenario = {}
    for cell in cells:
        if cell.get("status") != "covered":
            continue
        source = pick(cell["values"], candidates) or {}
        for scenario_id in source.get("scenarios") or []:
            by_scenario.setdefault(scenario_id, []).append(cell["values"])
    errors = []
    for scenario_id, combos in sorted(by_scenario.items()):
        for name in names_of(table):
            present = sorted({combo.get(name) for combo in combos if combo.get(name) is not None})
            if len(present) <= 1:
                continue
            errors.append(
                f"场景 {scenario_id} 同时覆盖互斥取值 {name}={'、'.join(present)}；"
                f"每个取值单独写场景，用例里要做这个取值对应的操作和结果")
            break
    return errors


def scenarios_of(table):
    found = set()
    for row in table.get("rows") or []:
        found.update(row.get("scenarios") or [])
    return found


def case_map(run):
    path = Path(run) / "cases/cases.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    mapping = {}
    for case in cases or []:
        listed = case.get("scenarioIds") or ([case["scenarioId"]] if case.get("scenarioId") else [])
        for scenario_id in listed:
            mapping.setdefault(scenario_id, []).append(case["caseId"])
    return mapping


def matrix(table, mapping):
    """把裁定的结果投影成矩阵；covered 格附上真正引用这些场景的用例。"""
    cells, uncovered = cells_of(table)
    candidates = list(table.get("rows") or []) + list(table.get("defaults") or [])
    for cell in cells:
        if cell["status"] != "covered":
            continue
        source = pick(cell["values"], candidates) or {}
        cell["caseIds"] = sorted({case_id
                                  for scenario_id in source.get("scenarios") or []
                                  for case_id in (mapping or {}).get(scenario_id, [])})
    fingerprint = hashlib.sha256(json.dumps(table, sort_keys=True, ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    # 形态族声明随投影走：矩阵是决策表的影子，family/axes 只能从表上读。
    dimensions = []
    for axis in table.get(DIMENSIONS) or []:
        item = {"name": axis["name"], "values": axis["values"]}
        if axis.get("family"):
            item["family"] = axis["family"]
        dimensions.append(item)
    return {
        "schemaVersion": 1,
        "dimensions": dimensions,
        "axes": table.get("axes") or {},
        "note": f"由 {TABLE} 展开，sha256={fingerprint[:16]}；不要手改，改决策表后重新 expand",
        "cells": cells,
    }, uncovered


def blocking(table, run=None):
    cases = None
    path = Path(run) / "cases/cases.json" if run else None
    if path is not None and path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        cases = data.get("cases") if isinstance(data, dict) else data
    return table_errors(table, cases)


def command_check(run):
    table = load(run, TABLE)
    errors = blocking(table, run)
    cells, uncovered = cells_of(table)
    counts = {status: len([cell for cell in cells if cell["status"] == status]) for status in STATUSES}
    unknowns = [cell for cell in cells if cell["status"] == "unknown"]
    report = {
        "status": "BLOCKED" if errors else "PASSED",
        "cells": len(cells) + len(uncovered),
        "counts": counts,
        "unregistered": uncovered,
        "pendingReview": [{"values": cell["values"], "reason": cell["reason"]}
                          for cell in unknowns if cell.get("risk") == "high"],
        "folded": [{"values": cell["values"], "assumption": cell.get("assumption"),
                    "reason": cell.get("reason")}
                   for cell in unknowns if cell.get("risk") != "high"],
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def assumptions_note(cells):
    """medium/low 的未规定项不占评审预算，但口径要留成可反驳的文字。"""
    folded = [cell for cell in cells if cell.get("assumption")]
    lines = ["# 合并口径", "",
             f"由 `{TABLE}` 生成，不手改。这些组合的来源没有给出规则，按下列口径合并执行；"
             "它们不改变本轮验收结论，因此不进待确认事项，评审时可以直接反驳。", ""]
    if not folded:
        return lines + ["本轮没有需要合并口径的未规定组合。", ""]
    lines += ["| 组合 | 采用口径 | 来源未规定的原因 |", "| --- | --- | --- |"]
    for cell in folded:
        combo = " × ".join(str(value) for value in cell["values"].values())
        lines.append(f"| {combo} | {cell['assumption']} | {cell.get('reason') or ''} |")
    return lines + [""]


def write_assumptions(run, cells):
    path = Path(run) / ASSUMPTIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(assumptions_note(cells)), encoding="utf-8")
    return path


def command_expand(run):
    table = load(run, TABLE)
    errors = blocking(table, run)
    mapping = case_map(run)
    if mapping is None:
        errors.append("缺少 cases/cases.json：先按决策表生成用例，再展开覆盖矩阵")
    if errors:
        print(json.dumps({"status": "BLOCKED", "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    produced, _ = matrix(table, mapping)
    empty = [cell["values"] for cell in produced["cells"]
             if cell["status"] == "covered" and not cell.get("caseIds")]
    if empty:
        print(json.dumps({"status": "BLOCKED",
                          "errors": [f"covered 组合没有用例：{values}" for values in empty]},
                         ensure_ascii=False, indent=2))
        return 2
    path = Path(run) / MATRIX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    note = write_assumptions(run, cells_of(table)[0])
    print(json.dumps({"status": "PASSED", "matrix": str(path), "cells": len(produced["cells"]),
                      "assumptions": str(note)}, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "expand"))
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    try:
        if args.command == "check":
            return command_check(run)
        return command_expand(run)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "errors": [str(exc)]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
