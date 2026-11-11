#!/usr/bin/env python3
"""用例覆盖自检：AC→场景→用例→断言、PRD 规则→义务→用例、组合矩阵、与历史评审表差集。

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
CLAUSE = re.compile(r'[。；;，,\n]')
MAX_CELLS = 60


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
    """断言里的人员量词（双方/均可/每人…）必须有对应 AC 的同类口径。

    只查单向：断言写「双方都能处理」，AC 却只说「由其中一名处理」，属于断言强于来源。
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
                if not token or not any(p in clause for p in PERSON):
                    continue
                if (case['caseId'], token) in exclusions:
                    continue
                if not any(t in pool for t in ALL_PEOPLE):
                    gaps.append(f'{case["caseId"]} 断言用了人员量词「{token}」，对应 AC 只给单人/分配口径：'
                                f'{clause.strip()[:40]}')
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
        gaps.extend(quantifier_gaps(cases, acs, scenarios,
                                    {(i.get('caseId'), i.get('token')) for i in recorded}))

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
        path = cases_dir / 'coverage-matrix.json'
        data = load(path, 'cases/coverage-matrix.json（组合矩阵）')
        dimensions = data.get('dimensions') or []
        names = [d.get('name') for d in dimensions]
        if not dimensions or any(not n or not d.get('values') for n, d in zip(names, dimensions)):
            gaps.append('组合矩阵缺少维度或维度取值')
        else:
            expected = {tuple(combo) for combo in _product([d['values'] for d in dimensions])}
            large = len(expected) > MAX_CELLS and not allow_large
            if large:
                gaps.append(f'组合矩阵单元数 {len(expected)} 超过上限 {MAX_CELLS}：减少维度取值、'
                            f'拆分矩阵，或显式允许后说明依据')
            expanded, problems = ([], []) if large else expand_cells(data, names)
            gaps.extend(problems)
            # 决策表登记过的 unknown 有 risk 和合并口径，由表负责，不再算矩阵未定论。
            decided_unknown = decision_table.unknown_combos(
                json.loads((cases_dir / 'decision-table.json').read_text(encoding='utf-8')), names) \
                if (cases_dir / 'decision-table.json').is_file() else set()
            seen = set()
            for cell in expanded:
                values = cell.get('values') or {}
                combo = tuple(values.get(name) for name in names)
                if combo in seen:
                    gaps.append(f'组合矩阵重复单元：{dict(zip(names, combo))}')
                    continue
                seen.add(combo)
                if combo not in expected:
                    gaps.append(f'组合矩阵存在未知取值组合：{dict(zip(names, combo))}')
                    continue
                status = cell.get('status')
                if status not in ACCEPTED:
                    gaps.append(f'组合矩阵状态非法：{dict(zip(names, combo))} status={status}')
                elif status == 'covered':
                    owners = cell.get('caseIds') or []
                    if not owners:
                        gaps.append(f'组合矩阵标 covered 但无用例：{dict(zip(names, combo))}')
                    for case_id in owners:
                        if case_id not in case_ids:
                            gaps.append(f'组合矩阵引用未知用例 {case_id}')
                elif status == 'unknown' and combo in decided_unknown:
                    continue
                elif status in ('unknown', 'gap'):
                    gaps.append(f'组合矩阵单元{"未定论" if status == "unknown" else "缺口"}：'
                                f'{dict(zip(names, combo))} {(cell.get("reason") or "缺理由").strip()}')
                elif not (cell.get('reason') or '').strip():
                    gaps.append(f'组合矩阵 {status} 缺理由：{dict(zip(names, combo))}')
            if not large:
                for combo in sorted(expected - seen):
                    gaps.append(f'组合矩阵缺单元：{dict(zip(names, combo))}')
            gaps.extend(decision_gaps(cases_dir, run, data, names, expanded, warnings))

    diff_note = None
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

    return {
        'acs': len(acs), 'scenarios': len(scenarios), 'cases': len(cases),
        'sourceUnits': len(units), 'obligations': len(obligations),
        'gaps': gaps, 'warnings': warnings, 'historyDiff': diff_note,
    }


def decision_gaps(cases_dir, run, data, names, expanded, warnings=None):
    """决策表存在时，矩阵必须就是它的展开：防止两张表变成两份事实源。"""
    path = cases_dir / 'decision-table.json'
    if not path.is_file():
        if warnings is not None and expanded:
            warnings.append('未写决策表 cases/decision-table.json：组合口径要等用例出来才补，'
                            '未规定的组合容易顺手写成 out_of_scope')
        return []
    table = json.loads(path.read_text(encoding='utf-8'))
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
                        help=f'允许组合矩阵超过 {MAX_CELLS} 格（需在矩阵 note 里说明拆分依据）')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    result = check(run, args.previous, not args.no_matrix, args.lenient_rules, not args.no_quantifier,
                   args.allow_large_matrix)
    summary = {k: v for k, v in result.items() if k not in ('gaps', 'warnings', 'historyDiff')}
    summary['warnings'] = len(result['warnings'])
    print('[coverage] ' + ('PASSED' if not result['gaps'] else 'BLOCKED') + ' ' + json.dumps(summary, ensure_ascii=False))
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
