#!/usr/bin/env python3
"""把评审表业务文案渲染成 cases.xlsx / cases.md / mapping.json（不判断业务，只做排版与映射）。

用法：
    render_review_table.py <runDir> [--content cases/presentation/1/content.json]
                                       [--out-dir cases] [--round 1]

`content.json` 是评审表 worker 的唯一业务产出：文案 + 分组；xlsx 版式、追踪表、
关联影响、待确认规则、Markdown 和 step/assertion 映射均由本脚本确定性生成，
因此映射不会漏项、正文不会与 JSON 漂移。缺少某个源步骤或断言的去向时报错退出（exit 2）。

content.json 结构：
{
  "title": "工作簿与 Markdown 标题",
  "cases": [{
    "caseId": "C-XXX-01",
    "group": "业务分组（同组连续，纵向合并）",
    "title": "用例标题（不含 caseId）",
    "fixtures": ["管理员已登录", ...],
    "steps": ["进入 工作流程 → 催办配置", ...],
    "expected": ["提示『保存成功』", ...],
    "stepSources": [["S1"], ["S2", "S3"]],        // 可选，默认按位置 1:1
    "expectedSources": [[0], [1]],                 // 可选，默认按位置 1:1
    "stepExemptions": {"S4": "并入前置条件列"},      // 可选，源步骤无展示去向时必须写明理由
    "assertionExemptions": {"2": "同上"}           // 可选，断言索引（0 基）无展示去向时写明理由
  }],
  "notes": [{"tag": "待确认 Q1", "text": "..."}]
}
"""
import argparse
import json
import pathlib
import sys
from math import ceil

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

RESULTS = ['未测试', '通过', '失败', '错误', '阻塞', '跳过', '不稳定', '取消']
HEAD_MAIN = ['场景名称', '用例标题', '前置条件', '操作步骤', '预期结果', '测试结果']
WIDTH_MAIN = [18, 44, 34, 42, 44, 14]
HEAD_TRACK = ['用例ID', '场景ID', '场景名称', 'AC ID', '测试类型', '优先级', '评审状态', '自动化入口']
WIDTH_TRACK = [14, 22, 30, 34, 12, 8, 12, 60]
HEAD_IMPACT = ['编号', '类别', '内容', '预期/来源', '覆盖用例', '状态']
WIDTH_IMPACT = [18, 16, 60, 46, 26, 12]
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill('solid', fgColor='1F3864')


def load_run(run_dir):
    cases_dir = run_dir / 'cases'
    read = lambda n: json.loads((cases_dir / n).read_text(encoding='utf-8'))
    data = {
        'cases': read('cases.json'),
        'scenarios': {s['scenarioId']: s for s in read('scenarios.json')},
        'acs': {a['acId']: a for a in read('acs.json')},
        'impact': read('impact-analysis.json'),
    }
    semantic = cases_dir / 'semantic-review.json'
    data['semantic'] = json.loads(semantic.read_text(encoding='utf-8')) if semantic.is_file() else None
    return data


def check_destination(label, source_ids, groups, exemptions):
    """每个源条目要么落到展示序号，要么带理由豁免；不能既落又豁免或凭空多出。"""
    flat = [sid for group in groups for sid in group]
    errors = []
    if sorted(set(flat)) != sorted(source_ids):
        missing = sorted(set(source_ids) - set(flat) - set(exemptions))
        extra = sorted(set(flat) - set(source_ids))
        both = sorted(set(flat) & set(exemptions))
        if missing:
            errors.append(f'{label}: 缺去向且无豁免理由 {missing}')
        if extra:
            errors.append(f'{label}: 出现未知来源 {extra}')
        if both:
            errors.append(f'{label}: 同时有去向和豁免 {both}')
    stray = sorted(set(exemptions) - set(source_ids))
    if stray:
        errors.append(f'{label}: 豁免引用了不存在的来源 {stray}')
    if len(flat) != len(set(flat)):
        errors.append(f'{label}: 同一来源被多个展示条目引用 {sorted({x for x in flat if flat.count(x) > 1})}')
    return errors, flat


def normalize(content, source, errors):
    """校验 caseId 集合/顺序与每步、每断言的去向，产出渲染单元。"""
    by_id = {c['caseId']: c for c in source['cases']}
    seen = [row['caseId'] for row in content['cases']]
    if seen != [c['caseId'] for c in source['cases']]:
        errors.append(f"caseId 顺序或集合与 cases.json 不一致：{seen} vs "
                      f"{[c['caseId'] for c in source['cases']]}")
    units = []
    for row in content['cases']:
        case = by_id.get(row['caseId'])
        if case is None:
            continue
        steps = case['steps']
        step_ids = [s['id'] for s in steps]
        step_ex = row.get('stepExemptions') or {}
        step_sources = row.get('stepSources') or [[sid] for sid in step_ids if sid not in step_ex]
        errs, _ = check_destination(f"{row['caseId']} 步骤", step_ids, step_sources, step_ex)
        errors += errs
        if len(row['steps']) != len(step_sources):
            errors.append(f"{row['caseId']}: steps 条数 {len(row['steps'])} 与 stepSources "
                          f"{len(step_sources)} 不一致")
        assertions = case['assertions']
        exp_ex = row.get('assertionExemptions') or {}
        indexes = list(range(len(assertions)))
        exp_sources = row.get('expectedSources') or [[i] for i in indexes if str(i) not in exp_ex]
        errs, _ = check_destination(f"{row['caseId']} 断言", indexes, exp_sources,
                                    {int(k): v for k, v in exp_ex.items()})
        errors += errs
        if len(row['expected']) != len(exp_sources):
            errors.append(f"{row['caseId']}: expected 条数 {len(row['expected'])} 与 expectedSources "
                          f"{len(exp_sources)} 不一致")
        units.append({'row': row, 'case': case, 'stepSources': step_sources,
                      'expectedSources': exp_sources,
                      'stepExemptions': step_ex,
                      'assertionExemptions': {int(k): v for k, v in exp_ex.items()}})
    return units


def numbered(items):
    return '\n'.join(f'{i}. {text}' for i, text in enumerate(items, 1))


def est_height(cells):
    lines = 1
    for text, width in cells:
        text = str(text or '')
        per_line = max(int(width * 1.05), 8)
        count = sum(max(1, ceil(len(seg) / per_line)) for seg in text.split('\n'))
        lines = max(lines, count)
    return min(max(28, lines * 15 + 6), 409)


def style_head(ws, width=None):
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF', size=11)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 26
    for idx, w in enumerate(width or [], 1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = w
    ws.freeze_panes = 'A2'


def body(ws, values, widths, center=()):
    ws.append(values)
    row = ws.max_row
    for col, width in enumerate(widths, 1):
        cell = ws.cell(row=row, column=col)
        cell.alignment = Alignment(vertical='top', wrap_text=True,
                                   horizontal='center' if col in center else 'left')
        cell.border = BORDER
    ws.row_dimensions[row].height = est_height(list(zip(values, widths)))
    return row


def render_main(wb, units, source):
    ws = wb.active
    ws.title = '用例主表'
    ws.append(HEAD_MAIN)
    style_head(ws, WIDTH_MAIN)
    rows = []
    for unit in units:
        row = unit['row']
        rows.append(body(ws, [row['group'], row['title'], numbered(row['fixtures']),
                              numbered(row['steps']), numbered(row['expected']), '未测试'],
                         WIDTH_MAIN, center=(1, 6)))
    dv = DataValidation(type='list', formula1='"' + ','.join(RESULTS) + '"', allow_blank=False,
                        showDropDown=False, errorStyle='stop', error='请选择测试结果',
                        errorTitle='测试结果', prompt='选择执行结果', promptTitle='测试结果')
    ws.add_data_validation(dv)
    dv.add(f'F2:F{ws.max_row}')
    start = 2
    for idx, unit in enumerate(units, 1):
        row = idx + 1
        if idx > 1 and unit['row']['group'] != units[idx - 2]['row']['group']:
            if row - 1 > start:
                ws.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
                ws.cell(start, 1).alignment = Alignment(horizontal='center', vertical='center',
                                                        wrap_text=True)
            start = row
    last = len(units) + 1
    if last > start:
        ws.merge_cells(start_row=start, start_column=1, end_row=last, end_column=1)
        ws.cell(start, 1).alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.auto_filter.ref = f'A1:F{ws.max_row}'
    return rows


def render_track(wb, units, source):
    ws = wb.create_sheet('用例追踪')
    ws.append(HEAD_TRACK)
    style_head(ws, WIDTH_TRACK)
    for unit in units:
        case, row = unit['case'], unit['row']
        ac_ids = sum((source['scenarios'][s]['acIds'] for s in case['scenarioIds']), [])
        body(ws, [case['caseId'], ','.join(case['scenarioIds']), row['group'],
                  ','.join(dict.fromkeys(ac_ids)), case['suite'], case['priority'],
                  case.get('reviewStatus', ''), case['automationRef']], WIDTH_TRACK, center=(5, 6, 7))
    return ws


def render_impact(wb, source, notes, low):
    ws = wb.create_sheet('关联影响与待确认')
    ws.append(HEAD_IMPACT)
    style_head(ws, WIDTH_IMPACT)
    owner = {}
    for case in source['cases']:
        for sid in case['scenarioIds']:
            for ac in source['scenarios'][sid]['acIds']:
                owner[ac] = case['caseId']
    for o in source['impact'].get('obligations', []):
        body(ws, [o['obligationId'], '测试义务', o['text'], o.get('expected', ''),
                  ','.join(sorted({owner[a] for a in o['acIds'] if a in owner})),
                  o.get('reviewStatus', '')], WIDTH_IMPACT, center=(6,))
    for note in notes:
        body(ws, [note['tag'], '待确认' if '待确认' in note['tag'] else '已知限制',
                  note['text'], '', '', ''], WIDTH_IMPACT)
    for line in low:
        body(ws, ['语义低分', '语义审查', line, '', '', ''], WIDTH_IMPACT)
    return ws


def low_scores(semantic):
    if not semantic:
        return []
    out = []
    for group in ('sources', 'impacts', 'cases', 'realization', 'ruleSupport'):
        for name, verdict in (semantic.get(group) or {}).items():
            if verdict.get('acceptedReason'):
                out.append(f'{group}/{name}（{verdict["final"]:.2f}）：{verdict["acceptedReason"]}')
    return out


def render_md(path, content, units, source, notes, low):
    md = [f'# {content["title"]}', '',
          f'- run: {path.parent.parent.name}', f'- 用例数: {len(units)}',
          f'- 场景数: {len(source["scenarios"])}', f'- AC 数: {len(source["acs"])}',
          f'- 语义低分: {len(low)} 条（见文末）', '']
    for unit in units:
        case, row = unit['case'], unit['row']
        ac_ids = sum((source['scenarios'][s]['acIds'] for s in case['scenarioIds']), [])
        md += [f"## {case['caseId']} {row['title']}", '',
               f"- 分组: {row['group']}",
               f"- 场景: {', '.join(case['scenarioIds'])}",
               f"- AC: {', '.join(dict.fromkeys(ac_ids))}",
               f"- suite: {case['suite']} / 优先级: {case['priority']}",
               f"- 自动化: `{case['automationRef']}`", '',
               '**前置条件**', '', numbered(row['fixtures']), '',
               '**操作步骤**', '', numbered(row['steps']), '',
               '**预期结果**', '', numbered(row['expected']), '',
               '**测试结果**: 未测试', '',
               '**追踪**', '',
               '- 步骤映射: ' + '; '.join(f"{i}→{'+'.join(g)}" for i, g in enumerate(unit['stepSources'], 1)),
               '- 预期映射: ' + '; '.join(f"{i}→{'+'.join(str(x) for x in g)}"
                                       for i, g in enumerate(unit['expectedSources'], 1)), '']
    md += ['## 待确认事项与已知限制', ''] + [f"- **{n['tag']}**：{n['text']}" for n in notes]
    md += ['', '## 语义审查低分项与处置', ''] + [f'- {line}' for line in low]
    path.write_text('\n'.join(md) + '\n', encoding='utf-8')
    return len(md)


def build_mapping(units, source, sheet_rows, round_dir):
    entries = []
    for unit, row_no in zip(units, sheet_rows):
        case = unit['case']
        step_pos = {s['id']: i for i, s in enumerate(case['steps'])}
        entries.append({
            'caseId': case['caseId'],
            'mainSheet': {'file': 'cases.xlsx', 'sheet': '用例主表', 'row': row_no},
            'group': unit['row']['group'],
            'title': unit['row']['title'],
            'fixtures': {'source': {'file': 'cases.json', 'field': f"cases[{case['caseId']}].fixtures"},
                         'displayIndexes': list(range(1, len(unit['row']['fixtures']) + 1))},
            'stepMapping': [
                {'displayIndex': i, 'sourceStepIds': group,
                 'sourceRefs': [f"cases.json:{case['caseId']}.steps[{step_pos[s]}]" for s in group]}
                for i, group in enumerate(unit['stepSources'], 1)],
            'assertionMapping': [
                {'displayIndex': i, 'sourceIndexes': group,
                 'sourceStepIds': [case['assertions'][x]['stepId'] for x in group]}
                for i, group in enumerate(unit['expectedSources'], 1)],
            'stepExemptions': unit['stepExemptions'],
            'assertionExemptions': {str(k): v for k, v in unit['assertionExemptions'].items()},
            'tracking': {'file': 'cases.xlsx', 'sheet': '用例追踪', 'row': row_no},
        })
    return {'schemaVersion': 1, 'generatedBy': 'render_review_table.py',
            'round': round_dir.name, 'caseCount': len(entries), 'cases': entries}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_dir', type=pathlib.Path)
    ap.add_argument('--content', type=pathlib.Path)
    ap.add_argument('--round', default='1')
    ap.add_argument('--out-dir', type=pathlib.Path)
    args = ap.parse_args()

    run_dir = args.run_dir.resolve()
    round_dir = run_dir / 'cases/presentation' / args.round
    content_path = args.content or round_dir / 'content.json'
    out_dir = (args.out_dir or run_dir / 'cases').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    content = json.loads(content_path.read_text(encoding='utf-8'))
    source = load_run(run_dir)
    errors = []
    units = normalize(content, source, errors)
    if errors:
        for line in errors:
            print(f'ERROR {line}', file=sys.stderr)
        sys.exit(2)

    notes = content.get('notes') or []
    low = low_scores(source['semantic'])
    wb = Workbook()
    sheet_rows = render_main(wb, units, source)
    render_track(wb, units, source)
    render_impact(wb, source, notes, low)
    xlsx = out_dir / 'cases.xlsx'
    wb.save(xlsx)
    md_lines = render_md(out_dir / 'cases.md', content, units, source, notes, low)
    mapping = build_mapping(units, source, sheet_rows, round_dir)
    (round_dir / 'mapping.json').write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{xlsx} cases={len(units)} rows={len(sheet_rows)}; cases.md lines={md_lines}; '
          f'mapping={round_dir / "mapping.json"}')


if __name__ == '__main__':
    main()
