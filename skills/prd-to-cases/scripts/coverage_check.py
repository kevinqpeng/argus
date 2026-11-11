#!/usr/bin/env python3
"""用例覆盖自检：AC→场景→用例→断言、PRD 规则→义务→用例、通用形态轴、组合矩阵定论、
量词双向口径、与历史评审表差集，并算出加权覆盖率。

在展示门禁之前运行；发现问题退出码 2（BLOCKED）。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'argus' / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_contract import validate_steps
import decision_table

NUMBERED = re.compile(r'^\s*(\d+(?:\.(\d+))*)[.、]\s+(\S.*)$')
REF = re.compile(r'^(.{1,300}?):(\d+)(?:-(\d+))?$')
ACCEPTED = {'covered', 'not_applicable', 'out_of_scope', 'unknown', 'gap'}
SETTLED = {'covered', 'not_applicable', 'out_of_scope'}
RULE_DISPOSITIONS = {'covered_elsewhere', 'duplicate', 'not_applicable', 'out_of_scope'}
PERSON = ('人', '甲', '乙', '丙', '丁', '戊', '己')
ALL_PEOPLE = ('均可', '都可', '都能', '均能', '双方', '两人', '任何人', '所有人', '每位', '每人', '全体',
              '都可见', '都可看到', '都可以')
# 或然量词：断言只承诺「其中一人」，来源或基线可能要求「全部人」，与 ALL_PEOPLE 构成反向缺口。
VAGUE_PEOPLE = ('其中一个', '其中一名', '其中一位', '其中之一', '任意一人', '之一')
CLAUSE = re.compile(r'[。；;，,\n]')
MIN_REASON = 20
HARD_CELLS = 400
ALLOW_LARGE_FLAG = '--allow-large-matrix'

# 通用形态轴：与业务无关的组合维度目录。任何需求都要对每族作出声明或豁免，
# 否则某些形态（代理主体、定义变更、时点反向、量词集合…）会整族缺席而门禁仍然 PASS。
AXIS_FAMILIES = {
    'subject': '主体形态（直接操作人、代理代办：转办/加签/委托/管理员代操作、系统主体）',
    'lifecycle': '生命周期时点（变更前已存在、变更中在途、变更后新建，且正反两向）',
    'trigger': '触发来源（用户操作、业务字段变化、配置变更、版本与定义变更、外部回调、批量）',
    'target': '目标状态（起始态、中间态、终态、回退或回滚态）',
    'quantity': '量词与集合（单/多主体、单/多条、全部/部分成功、成员集合）',
    'permission': '权限与数据范围（有权、无权、越权、跨租户、跨数据范围）',
    'repeat': '重复与并发（一次、重复提交、并发、重试、超时、幂等）',
    'boundary': '边界（空、最小、最大、超长、非法输入）',
}
DEFAULT_THRESHOLDS = {'ruleCoverage': 0.9, 'matrixCoverage': 0.9, 'baselineCoverage': 0.9,
                      'score': 0.9}
PART_LABELS = {'rule': '规则-义务', 'matrix': '组合矩阵', 'baseline': '基线差集'}


def load(path, label, required=True):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        if required:
            print(f'BLOCKED: 缺少 {label}（{path.name}）')
            raise SystemExit(2)
        return None


def rules(path):
    """抽取编号规则行；跳过无句读的章节标题。"""
    found = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        match = NUMBERED.match(line)
        if not match:
            continue
        text = match.group(3).strip()
        if len(text) < 20 and not re.search(r'[。；]', text):
            continue
        found.append((number, f'{match.group(1)} {text}'))
    return found


def refs(unit):
    """evidenceRef → [(start, end)]，只保留指向 PRD 快照的引用。"""
    spans = []
    for part in str(unit.get('evidenceRef') or '').split(';'):
        match = REF.match(part.strip())
        if not match or 'prd' not in match.group(1):
            continue
        start = int(match.group(2))
        spans.append((start, int(match.group(3) or start)))
    return spans


def title_key(text):
    text = re.sub(r'（续）\s*$', '', str(text or ''))
    return re.sub(r'\s+', '', text)


def review_titles(path):
    """历史评审表的用例标题：兼容「用例主表」与「主表」两种表名。"""
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True)
    name = next((n for n in ('用例主表', '主表') if n in book.sheetnames), book.sheetnames[0])
    rows = list(book[name].iter_rows(min_row=2, values_only=True))
    header = book[name].iter_rows(min_row=1, max_row=1, values_only=True)
    column = 1
    for index, value in enumerate(next(header, ()) or ()):
        if str(value or '').strip() in ('用例标题', '标题'):
            column = index
    return [r[column] for r in rows if r and len(r) > column and r[column]]


def expand_cells(data, names):
    """把 coveredBy 紧凑写法展开成 cells：一条用例只需声明它覆盖的取值组合。

    支持 `{"caseIds": ["CASE-X"], "values": {"维度": ["取值A", "取值B"], ...}}`，
    未列出的维度视为取全部取值；多个条目命中同一格时合并 caseIds。
    """
    cells, problems = [], []
    merged = {}
    for group in data.get('coveredBy') or []:
        owners = group.get('caseIds') or []
        values = group.get('values') or {}
        if not owners:
            problems.append('coveredBy 条目缺 caseIds')
            continue
        unknown = [name for name in values if name not in names]
        if unknown:
            problems.append(f'coveredBy 引用了未声明的维度 {unknown}')
            continue
        picks = []
        for name in names:
            picked = values.get(name)
            if picked is None:
                picks.append([d['values'] for d in data['dimensions'] if d['name'] == name][0])
            else:
                picks.append([picked] if isinstance(picked, str) else list(picked))
        for combo in _product(picks):
            merged.setdefault(tuple(combo), []).extend(owners)
    for combo, owners in merged.items():
        cells.append({'values': dict(zip(names, combo)), 'status': 'covered', 'caseIds': sorted(set(owners))})
    cells.extend(data.get('cells') or [])
    return cells, problems


def quantifier_gaps(cases, acs, scenarios, exclusions):
    """两个方向的量词缺口。

    正向：断言写「双方都能处理」，AC 却只说「由其中一名处理」，属于断言强于来源。
    反向：断言写「其中一个/任意一人」，AC 或基线要求全员，真值不同，必须裁决后才能出表。
    记录条目数（一条/两条）不算人员量词；「二者均可被选中」这类非人物主语也跳过。
    """
    gaps = []
    ac_text = {a['acId']: str(a.get('text') or '') for a in acs}
    scenario_acs = {s['scenarioId']: s.get('acIds') or [] for s in scenarios}
    for case in cases:
        pool = ' '.join(ac_text.get(a, '') for sid in case.get('scenarioIds') or []
                        for a in scenario_acs.get(sid, []))
        for assertion in case.get('assertions') or []:
            for clause in CLAUSE.split(str(assertion.get('expected') or '')):
                token = next((t for t in ALL_PEOPLE if t in clause), None)
                if token and any(p in clause for p in PERSON):
                    if (case['caseId'], token) in exclusions:
                        continue
                    if not any(t in pool for t in ALL_PEOPLE):
                        gaps.append(f'{case["caseId"]} 断言用了人员量词「{token}」，对应 AC 只给单人/分配口径：'
                                    f'{clause.strip()[:40]}')
                    continue
                vague = next((t for t in VAGUE_PEOPLE if t in clause), None)
                if not vague or not any(p in clause for p in PERSON):
                    continue
                if (case['caseId'], vague) in exclusions:
                    continue
                if any(t in pool for t in ALL_PEOPLE):
                    gaps.append(f'{case["caseId"]} 断言用或然量词「{vague}」，来源是全员口径：'
                                f'{clause.strip()[:40]}；先裁决待办接收范围再出表')
    return gaps


def check(run, previous=None, matrix=True, lenient_rules=False, quantifier=True, allow_large=False):
    cases_dir = run / 'cases'
    acs = load(cases_dir / 'acs.json', 'acs.json')
    scenarios = load(cases_dir / 'scenarios.json', 'scenarios.json')
    merged = load(cases_dir / 'cases.json', 'cases.json')
    cases = merged['cases'] if isinstance(merged, dict) else merged
    impact = load(cases_dir / 'impact-analysis.json', 'impact-analysis.json')
    gaps = []
    warnings = []

    ac_ids = [a['acId'] for a in acs]
    scenario_ids = [s['scenarioId'] for s in scenarios]
    case_ids = [c['caseId'] for c in cases]

    for ac in acs:
        if not any(ac['acId'] in (s.get('acIds') or []) for s in scenarios):
            gaps.append(f'验收标准没有场景：{ac["acId"]}')
    for scenario in scenarios:
        if not any(scenario['scenarioId'] in (c.get('scenarioIds') or []) for c in cases):
            gaps.append(f'场景没有用例：{scenario["scenarioId"]}')
    for case in cases:
        try:
            validate_steps(case.get('steps'), case.get('assertions'))
        except ValueError as exc:
            gaps.append(f'{case["caseId"]} 步骤/断言不完整：{exc}')
        for sid in case.get('scenarioIds') or []:
            if sid not in scenario_ids:
                gaps.append(f'{case["caseId"]} 引用未知场景：{sid}')

    if quantifier:
        recorded = (load(cases_dir / 'quantifier-exclusions.json', 'cases/quantifier-exclusions.json',
                         required=False) or {}).get('items') or []
        for item in recorded:
            if not (item.get('reason') or '').strip():
                gaps.append(f'quantifier-exclusions.json 缺理由：{item.get("caseId")} {item.get("token")}')
            elif item.get('token') in VAGUE_PEOPLE and not str(item.get('pendingRef') or '').strip():
                gaps.append(f'quantifier-exclusions.json 或然量词豁免缺少待确认引用 pendingRef：'
                            f'{item.get("caseId")} {item.get("token")}')
        gaps.extend(quantifier_gaps(cases, acs, scenarios,
                                    {(i.get('caseId'), i.get('token')): i for i in recorded}))

    rule_notes = (load(cases_dir / 'rule-coverage.json', 'cases/rule-coverage.json', required=False) or {}).get('lines') or {}
    for line, note in rule_notes.items():
        if not str(line).isdigit():
            gaps.append(f'rule-coverage.json 行号非法：{line}')
            continue
        if note.get('disposition') not in RULE_DISPOSITIONS:
            gaps.append(f'rule-coverage.json 处置非法：prd.md:{line} disposition={note.get("disposition")}')
        elif not (note.get('reason') or '').strip():
            gaps.append(f'rule-coverage.json 缺理由：prd.md:{line}')

    units = impact['sourceUnits']
    obligations = {o['obligationId']: o for o in impact['obligations']}

    prd_paths = sorted(cases_dir.glob('sources/prd*.md'))
    if prd_paths:
        for line, text in rules(prd_paths[0]):
            if str(line) in rule_notes:
                continue
            sink = warnings if lenient_rules else gaps
            covering = [u for u in units if any(a <= line <= b for a, b in refs(u))]
            if not covering:
                sink.append(f'规则行未被来源单元覆盖：prd.md:{line} {text[:40]}')
                continue
            if not any(u.get('disposition') in SETTLED for u in covering):
                sink.append(f'规则行未定论（{covering[0].get("disposition")}）：prd.md:{line} {text[:40]}')
                continue
            if not any(u.get('disposition') == 'covered' for u in covering):
                continue
            linked = [o for u in covering for o in (u.get('obligationIds') or [])]
            if not linked:
                sink.append(f'规则已覆盖但无义务：prd.md:{line} {text[:40]}')
                continue
            for obligation_id in linked:
                obligation = obligations.get(obligation_id)
                if obligation is None:
                    sink.append(f'来源单元引用未知义务 {obligation_id}：prd.md:{line}')
                elif not [a for a in obligation.get('acIds') or [] if a in ac_ids]:
                    sink.append(f'{obligation_id} 没有验收标准：prd.md:{line} {text[:40]}')

    if matrix:
        stats = matrix_report(cases_dir, cases, gaps, warnings, allow_large)
    else:
        stats = None

    rule_ratio, rule_holes = rule_coverage(obligations.values(), scenarios, cases)
    gaps.extend(f'规则-义务未落到带断言的用例：{hole}' for hole in rule_holes)

    diff_note = None
    baseline_ratio = None
    if previous:
        try:
            old = {title_key(t) for t in review_titles(Path(previous))}
        except Exception as exc:  # noqa: BLE001 - 缺文件、缺表名、格式异常都按读取失败处理
            gaps.append(f'无法读取历史评审表 {previous}：{exc}')
            old = set()
        if old:
            new = {title_key(c['title']) for c in cases}
            note = cases_dir / 'history-diff.md'
            diff_note = {'onlyPrevious': sorted(old - new), 'onlyCurrent': sorted(new - old)}
            if (old - new or new - old) and not note.is_file():
                gaps.append(f'与历史评审表存在差异（仅历史 {len(old - new)} 条 / 仅当前 {len(new - old)} 条），'
                            f'需记录到 cases/history-diff.md 并分类缺失/粒度/多余/冲突')
            baseline_ratio = baseline_coverage(diff_note, note)
    else:
        warnings.append('未提供 --previous 历史评审表：外部基线锤点缺失，覆盖率只由内部分母计算')

    coverage = coverage_report(rule_ratio, stats, baseline_ratio, thresholds(run))
    gaps.extend(coverage.pop('gaps'))

    return {
        'acs': len(acs), 'scenarios': len(scenarios), 'cases': len(cases),
        'sourceUnits': len(units), 'obligations': len(obligations),
        'gaps': gaps, 'warnings': warnings, 'historyDiff': diff_note, 'coverage': coverage,
    }


def thresholds(run):
    """覆盖率阈值来自 argus-model.json；未配置时用 DEFAULT_THRESHOLDS（均为 0.9）。"""
    try:
        model = json.loads((run / 'argus-model.json').read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        model = {}
    configured = (model.get('coverage') or {}) if isinstance(model, dict) else {}
    limits = dict(DEFAULT_THRESHOLDS)
    for key in limits:
        value = configured.get(key)
        if isinstance(value, (int, float)) and 0 < value <= 1:
            limits[key] = float(value)
    return limits


def rule_coverage(obligations, scenarios, cases):
    """义务 → AC → 场景 → 用例（带断言）：任一段断掉就算该义务未覆盖。"""
    realized = {ac for case in cases if case.get('assertions')
                for sid in case.get('scenarioIds') or []
                for ac in next((s.get('acIds') or [] for s in scenarios if s['scenarioId'] == sid), [])}
    obligations = list(obligations)
    if not obligations:
        return 1.0, []
    holes = []
    for obligation in obligations:
        acs = [a for a in obligation.get('acIds') or [] if a]
        if not acs:
            holes.append(f'{obligation["obligationId"]} 没有验收标准')
            continue
        missing = [a for a in acs if a not in realized]
        if missing:
            holes.append(f'{obligation["obligationId"]} 的验收标准没有落到带断言的用例：{missing}')
    return (len(obligations) - len(holes)) / len(obligations), holes


def baseline_coverage(diff, note):
    """历史差集锤点：仅历史条目必须在 history-diff.md 里逐条分类。"""
    only = (diff or {}).get('onlyPrevious') or []
    if not only:
        return 1.0
    text = title_key(note.read_text(encoding='utf-8', errors='ignore')) if note.is_file() else ''
    return sum(1 for title in only if title_key(title) in text) / len(only)


def coverage_report(rule_ratio, stats, baseline_ratio, limits):
    """综合覆盖率 = 规则-义务 0.5 + 组合矩阵 0.3 + 基线差集 0.2；缺失分量按权重归一。"""
    matrix_ratio = None
    if stats:
        # 分母只扣真正不适用的单元：越界与待确认都属于「该验而未验」，仍算未覆盖，
        # 否则把格子推给 out_of_scope 或 unknown 就能抬高覆盖率。
        denominator = stats['total'] - stats['notApplicable']
        matrix_ratio = 1.0 if denominator <= 0 else stats['covered'] / denominator
    parts = [('rule', rule_ratio, 0.5, limits['ruleCoverage'])]
    if matrix_ratio is not None:
        parts.append(('matrix', matrix_ratio, 0.3, limits['matrixCoverage']))
    if baseline_ratio is not None:
        parts.append(('baseline', baseline_ratio, 0.2, limits['baselineCoverage']))
    weight = sum(part[2] for part in parts)
    score = sum(ratio * share for _, ratio, share, _ in parts) / weight if weight else 0.0
    gaps = []
    short = [f'{PART_LABELS[key]} {ratio:.0%}（要求 {limit:.0%}）'
             for key, ratio, _, limit in parts if ratio < limit]
    if score < limits['score']:
        short.append(f'综合 {score:.0%}（要求 {limits["score"]:.0%}）')
    if short:
        gaps.append('覆盖率未达阈值：' + ' / '.join(short))
    report = {f'{key}Ratio': round(ratio, 3) for key, ratio, _, _ in parts}
    report['score'] = round(score, 3)
    report['thresholds'] = limits
    report['gaps'] = gaps
    if stats:
        report.update(stats)
    return report


def axis_gaps(data, dimensions):
    """通用形态轴：每一族都要写成维度，或写明不适用理由。"""
    declared = {}
    for dim in dimensions:
        if dim.get('family'):
            declared.setdefault(str(dim['family']), []).append(dim.get('name'))
    axes = data.get('axes') or {}
    problems = []
    if not isinstance(axes, dict):
        return ['组合矩阵 axes 必须是对象：{形态族: {dimension|waiver}}']
    for family, spec in axes.items():
        if family not in AXIS_FAMILIES:
            problems.append(f'组合矩阵 axes 出现未知形态族：{family}（可用：{", ".join(AXIS_FAMILIES)}）')
            continue
        if not isinstance(spec, dict):
            problems.append(f'组合矩阵 axes.{family} 必须是对象')
            continue
        dimension = spec.get('dimension')
        waiver = str(spec.get('waiver') or '').strip()
        if dimension:
            declared.setdefault(family, []).append(dimension)
            if dimension not in {d.get('name') for d in dimensions}:
                problems.append(f'组合矩阵 axes.{family} 指向未声明的维度：{dimension}')
        elif len(waiver) < MIN_REASON:
            problems.append(f'组合矩阵 axes.{family} 缺豁免理由（≥{MIN_REASON} 字）')
        else:
            declared.setdefault(family, ['豁免'])
    for family, hint in AXIS_FAMILIES.items():
        if family not in declared:
            problems.append(f'组合矩阵缺少形态轴「{family}」：{hint}；在 dimensions[].family 或 axes 中声明，'
                            f'或写明本需求不适用的理由')
    return problems


def matrix_report(cases_dir, cases, gaps, warnings, allow_large):
    """组合矩阵：单元结论、out_of_scope 签名、二维组合定论、取值有用例、P0 必入矩阵。"""
    data = load(cases_dir / 'coverage-matrix.json', 'cases/coverage-matrix.json（组合矩阵）')
    case_ids = [case['caseId'] for case in cases]
    table = decision_table.load(cases_dir.parent, 'cases/decision-table.json', required=False)
    dimensions = data.get('dimensions') or []
    names = [d.get('name') for d in dimensions]
    if not dimensions or any(not n or not d.get('values') for n, d in zip(names, dimensions)):
        gaps.append('组合矩阵缺少维度或维度取值')
        return None
    # 决策表登记过的 unknown 有 risk 和合并口径，由表负责，不再算矩阵未定论。
    decided_unknown = decision_table.unknown_combos(table, names) if table else set()
    gaps.extend(axis_gaps(data, dimensions))
    expected = {tuple(combo) for combo in _product([d['values'] for d in dimensions])}
    stats = {'total': len(expected), 'covered': 0, 'notApplicable': 0, 'outOfScope': 0, 'unknown': 0}
    if len(expected) > HARD_CELLS and not allow_large:
        gaps.append(f'组合矩阵单元数 {len(expected)} 超过硬上限 {HARD_CELLS}：先用等价类收敛取值，'
                    f'或显式 {ALLOW_LARGE_FLAG} 并说明依据')
        return stats
    for group in data.get('coveredBy') or []:
        if isinstance(group, dict):
            omitted = [n for n in names if n not in (group.get('values') or {})]
            if len(omitted) >= 2:
                warnings.append(f'组合矩阵 coveredBy 一条声明覆盖多格：{group.get("caseIds")} 未写明 {omitted}')
    expanded, problems = expand_cells(data, names)
    gaps.extend(problems)
    seen, settled, statuses = set(), set(), {}
    unsigned, short = [], []
    for cell in expanded:
        combo = tuple((cell.get('values') or {}).get(name) for name in names)
        label = dict(zip(names, combo))
        if combo in seen:
            gaps.append(f'组合矩阵重复单元：{label}')
            continue
        seen.add(combo)
        if combo not in expected:
            gaps.append(f'组合矩阵存在未知取值组合：{label}')
            continue
        status = cell.get('status')
        reason = str(cell.get('reason') or '').strip()
        if status not in ACCEPTED:
            gaps.append(f'组合矩阵状态非法：{label} status={status}')
            continue
        statuses[combo] = status
        if status == 'covered':
            stats['covered'] += 1
            owners = cell.get('caseIds') or []
            if not owners:
                gaps.append(f'组合矩阵标 covered 但无用例：{label}')
            for case_id in owners:
                if case_id not in case_ids:
                    gaps.append(f'组合矩阵引用未知用例 {case_id}')
            settled.add(combo)
        elif status in ('unknown', 'gap'):
            stats['unknown'] += 1
            if status == 'unknown' and combo in decided_unknown:
                settled.add(combo)
                continue
            gaps.append(f'组合矩阵单元{"未定论" if status == "unknown" else "缺口"}：'
                        f'{label} {reason or "缺理由"}')
        elif status == 'not_applicable':
            stats['notApplicable'] += 1
            if len(reason) < MIN_REASON:
                gaps.append(f'组合矩阵 not_applicable 缺理由（≥{MIN_REASON} 字）：{label}')
            else:
                settled.add(combo)
        else:
            stats['outOfScope'] += 1
            missing = [key for key in ('reason', 'reviewer', 'approvalRef')
                       if not str(cell.get(key) or '').strip()]
            if missing:
                unsigned.append((label, missing))
            elif len(reason) < MIN_REASON:
                short.append(label)
            else:
                settled.add(combo)
    for label, missing in unsigned[:4]:
        gaps.append(f'组合矩阵 out_of_scope 未经签署（缺 {missing}）：{label}；'
                    f'越界结论必须有理由、评审人与批准引用')
    if len(unsigned) > 4:
        gaps.append(f'组合矩阵 out_of_scope 未经签署：…另有 {len(unsigned) - 4} 个越界单元缺签署')
    for label in short[:4]:
        gaps.append(f'组合矩阵 out_of_scope 理由过短（≥{MIN_REASON} 字）：{label}')
    if len(short) > 4:
        gaps.append(f'组合矩阵 out_of_scope 理由过短：…另有 {len(short) - 4} 个单元')
    missing_cells = sorted(expected - seen)
    for combo in missing_cells[:6]:
        gaps.append(f'组合矩阵缺单元：{dict(zip(names, combo))}')
    if len(missing_cells) > 6:
        gaps.append(f'组合矩阵缺单元：…另有 {len(missing_cells) - 6} 个取值组合没有任何结论')
    owned = {combo for combo in seen if combo and statuses.get(combo) in ('covered', 'not_applicable')}
    owned |= decided_unknown & seen
    for index, name in enumerate(names):
        for value in dimensions[index]['values']:
            if not any(combo[index] == value for combo in owned):
                owners = {statuses.get(combo) for combo in seen if combo and combo[index] == value}
                gaps.append(f'组合矩阵取值没有任何用例或不适用说明：{name}={value}'
                            f'（现有结论 {sorted(o for o in owners if o)}）')
    mapped = {case_id for cell in expanded for case_id in cell.get('caseIds') or []}
    stray = sorted({case['caseId'] for case in cases if case.get('priority') == 'P0'} - mapped)
    if stray:
        gaps.append(f'P0 用例没有落在组合矩阵任何单元：{stray}；P0 不参与等价类抽样')
    for left, right in _pairs(range(len(names))):
        need = {(a, b) for a in dimensions[left]['values'] for b in dimensions[right]['values']}
        have = {(combo[left], combo[right]) for combo in settled if combo in seen}
        missing = sorted(need - have)
        for value_left, value_right in missing[:5]:
            gaps.append(f'组合矩阵二维组合未定论：{names[left]}={value_left} × {names[right]}={value_right}')
        if len(missing) > 5:
            gaps.append(f'组合矩阵二维组合未定论：…另有 {len(missing) - 5} 个组合缺少定论单元')
    gaps.extend(decision_gaps(cases_dir, cases_dir.parent, data, names, expanded, warnings))
    return stats


def decision_gaps(cases_dir, run, data, names, expanded, warnings=None):
    """决策表存在时，矩阵必须就是它的展开：防止两张表变成两份事实源。"""
    table = decision_table.load(Path(run), 'cases/decision-table.json', required=False)
    if not table:
        if warnings is not None and expanded:
            warnings.append('未写决策表 cases/decision-table.json：组合口径要等用例出来才补，'
                            '未规定的组合容易顺手写成 out_of_scope')
        return []
    gaps = [f'决策表：{error}' for error in decision_table.blocking(table, run)]
    axes = decision_table.axes_of(table)
    if [axis.get('values') for axis in axes] != [d.get('values') for d in data.get('dimensions') or []]:
        gaps.append('决策表与组合矩阵的维度或取值不一致：矩阵必须是决策表的展开，不能两份各写一套')
    table_names = [axis['name'] for axis in axes]
    declared = {(cell['status'], tuple(cell['values'].get(n) for n in table_names))
                for cell in decision_table.cells_of(table)[0]}
    for cell in expanded:
        combo = tuple((cell.get('values') or {}).get(n) for n in table_names)
        if (cell.get('status'), combo) not in declared:
            gaps.append(f'决策表没有登记该格：{dict(zip(table_names, combo))} status={cell.get("status")}')
    return gaps


def _pairs(indexes):
    indexes = list(indexes)
    return [(indexes[i], indexes[j]) for i in range(len(indexes)) for j in range(i + 1, len(indexes))]


def _product(values):
    combos = [[]]
    for group in values:
        combos = [combo + [value] for combo in combos for value in group]
    return combos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--previous', help='历史评审表 .xlsx，用于差集核对')
    parser.add_argument('--no-matrix', action='store_true', help='跳过组合矩阵检查（仅限已记录理由的旧 run）')
    parser.add_argument('--lenient-rules', action='store_true', help='编号规则链未闭合只提示（默认阻断）')
    parser.add_argument('--no-quantifier', action='store_true', help='跳过断言人员量词检查（仅限已记录理由的旧 run）')
    parser.add_argument('--allow-large-matrix', action='store_true',
                        help=f'允许组合矩阵超过 {HARD_CELLS} 格（需在矩阵 note 里说明收敛依据）')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    result = check(run, args.previous, not args.no_matrix, args.lenient_rules, not args.no_quantifier,
                   args.allow_large_matrix)
    summary = {k: v for k, v in result.items() if k not in ('gaps', 'warnings', 'historyDiff')}
    summary['warnings'] = len(result['warnings'])
    print('[coverage] ' + ('PASSED' if not result['gaps'] else 'BLOCKED') + ' ' + json.dumps(summary, ensure_ascii=False))
    coverage = result.get('coverage') or {}
    if coverage:
        print('  覆盖率：综合 ' + f'{coverage.get("score", 0):.0%}' + ''.join(
            f' / {PART_LABELS[key]} ' + f'{coverage[f"{key}Ratio"]:.0%}'
            for key in PART_LABELS if f'{key}Ratio' in coverage))
    if result['historyDiff']:
        print('  历史差集（标题集合）：仅历史 ' + str(len(result['historyDiff']['onlyPrevious']))
              + ' 条 / 仅当前 ' + str(len(result['historyDiff']['onlyCurrent']))
              + ' 条，按缺失/粒度/多余/冲突分类写入 cases/history-diff.md')
    for gap in result['gaps']:
        print('  - ' + gap)
    for warning in result['warnings']:
        print('  ~ 提示：' + warning)
    return 0 if not result['gaps'] else 2


if __name__ == '__main__':
    sys.exit(main())
