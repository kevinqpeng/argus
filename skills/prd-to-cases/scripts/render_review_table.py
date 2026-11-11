#!/usr/bin/env python3
"""把评审表业务文案渲染成 cases.xlsx / cases.md / mapping.json（不判断业务，只做排版与映射）。

用法：
    render_review_table.py <runDir> [--content cases/presentation/1/content.json]
                                       [--out-dir cases] [--round 1]
    分片自检：同上加 --check-only --case-ids C1 C2（按冻结顺序）

`content.json` 是评审表 worker 的唯一业务产出：文案 + 分组；xlsx 版式、
待确认事项、内部 Markdown 和 step/assertion 映射均由本脚本确定性生成，
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
    "assertionExemptions": {"2": "同上"},          // 可选，断言索引（0 基）无展示去向时写明理由
    "densityExceptions": {"steps": {"display": ["必要的完整时序"],
                                      "reason": "必须逐项核对的并发顺序",
                                      "sourceRefs": ["acs.json:A1.text"]}},
    "technicalExceptions": [{"field": "steps", "index": 1, "token": "GET /api/x",
                               "reason": "接口本身是测试对象", "evidenceRef": "cases.json:..."}]
  }],
  "notes": [{"category": "规则适用版本", "question": "再次提交时按哪个版本审批？",
             "action": "请产品确认适用版本", "sourceRefs": ["impact-analysis.json:..."]}]
}
"""
import argparse
import hashlib
import json
import pathlib
import re
import shlex
import shutil
import sys
import unicodedata
from math import ceil

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

RESULTS = ['未测试', '通过', '失败', '错误', '阻塞', '跳过', '不稳定', '取消']
HEAD_MAIN = ['场景名称', '用例标题', '前置条件', '操作步骤', '预期结果', '测试结果']
WIDTH_MAIN = [18, 44, 34, 42, 44, 14]
HEAD_NOTES = ['类别', '待确认内容', '建议处理']
WIDTH_NOTES = [22, 64, 50]
# 待确认事项三分法：只有 decision 进 Excel，其余写 cases.md 附录。
NOTE_DISPOSITIONS = ('decision', 'resolved_by_code', 'assumption')
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill('solid', fgColor='1F3864')
SLICE_CAP = 4
# 条数硬上限之外的内容预算：四条短用例仍是一片；步骤和断言多的用例提前换片。
SLICE_WEIGHT = 24
# 评审密度独立于 Excel 的 409 pt 容量；超预算需来源支持的逐字段例外。
REVIEW_LINE_BUDGET = {
    'title': (WIDTH_MAIN[1], 2), 'fixtures': (WIDTH_MAIN[2], 6),
    'steps': (WIDTH_MAIN[3], 10), 'expected': (WIDTH_MAIN[4], 12),
}

# 人员评审表里一律不出现的执行细节；technicalExceptions 不能放行。
ALWAYS_HIDDEN = re.compile(
    r'\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[\w/{}<>?=&.-]+'
    r'|(?<![A-Za-z0-9])(?!AR-)[A-Z]{2,}-\d+\b'
    r'|\{[^{}\n]{0,180}?["\']?[A-Za-z_][A-Za-z0-9_]*["\']?\s*:'
    r'|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}\b'
    r'|\b[a-z]{2,}(?:[A-Z][a-z0-9]+)+\b'
)
# 疑似内部记号；页面上必须原样核对时，可用 technicalExceptions 放行。
TECHNICAL = re.compile(
    r'\b(?:FA-(?:ADMIN|STARTER|APPROVER(?:_[A-Z]+)?|OUTSIDER))\b'
    r'|\bAR-[0-9a-fA-F]{8}(?:-[\w-]+)?\b'
    r'|\b(?:[a-z]+:[a-z]+:[a-z]+|tenant-id|aria-checked)\b'
    r'|\b(?:cases|semantic-review|impact-analysis)\.(?:json|md)\b'
    r'|\b(?:AC|OB|SC|C)-[A-Z0-9]+(?:-[A-Z0-9]+)+\b'
)


def presentation_issues(content, source):
    """检查评审密度和疑似技术泄漏；业务必要性由主 Agent 对照来源复核。

    source 保留给调用方传入的冻结用例；主表不因 suite 放行接口细节。
    """
    del source
    issues = []
    for row in content['cases']:
        density = row.get('densityExceptions') or {}
        if not isinstance(density, dict):
            issues.append(f"{row['caseId']}: densityExceptions 必须按字段记录")
            density = {}
        for field, (width, limit) in REVIEW_LINE_BUDGET.items():
            value = row.get(field, [] if field != 'title' else '')
            text = numbered(value) if isinstance(value, list) else value
            lines = wrapped_lines(text, width)
            if lines <= limit:
                continue
            exception = density.get(field) or {}
            if not isinstance(exception, dict):
                exception = {}
            refs = exception.get('sourceRefs')
            if not (exception.get('display') == value and
                    isinstance(exception.get('reason'), str) and exception['reason'].strip() and
                    isinstance(refs, list) and refs and
                    all(isinstance(ref, str) and ref.strip() for ref in refs)):
                issues.append(f"{row['caseId']} {field}: 评审密度 {lines} 行超过预算 {limit}；"
                              '请合并重复背景和常规操作，必要边界须记录绑定当前文案的 densityExceptions')
        exceptions = row.get('technicalExceptions', [])
        for field in ('group', 'title', 'fixtures', 'steps', 'expected'):
            values = row.get(field, [])
            values = values if isinstance(values, list) else [values]
            for index, value in enumerate(values, 1):
                text = str(value)
                for match in ALWAYS_HIDDEN.finditer(text):
                    issues.append(f"{row['caseId']} {field}[{index}]: {match.group()}")
                for match in TECHNICAL.finditer(text):
                    token = match.group()
                    allowed = any(e.get('field') == field and e.get('index') == index and
                                  e.get('token') == token and str(e.get('reason', '')).strip() and
                                  str(e.get('evidenceRef', '')).strip()
                                  for e in exceptions if isinstance(e, dict))
                    if not allowed:
                        issues.append(f"{row['caseId']} {field}[{index}]: {token}")
    for index, note in enumerate(content.get('notes') or [], 1):
        for field in ('category', 'question', 'action'):
            text = str(note.get(field, ''))
            for match in ALWAYS_HIDDEN.finditer(text):
                issues.append(f"notes[{index}] {field}: {match.group()}")
            for match in TECHNICAL.finditer(text):
                issues.append(f"notes[{index}] {field}: {match.group()}")
    return issues


def load_run(run_dir):
    cases_dir = run_dir / 'cases'
    artifacts = {}
    def read(name):
        raw = (cases_dir / name).read_bytes()
        artifacts['cases/' + name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    data = {
        'cases': read('cases.json'),
        'scenarios': {s['scenarioId']: s for s in read('scenarios.json')},
        'acs': {a['acId']: a for a in read('acs.json')},
        'impact': read('impact-analysis.json'),
    }
    semantic = cases_dir / 'semantic-review.json'
    data['semantic'] = read('semantic-review.json') if semantic.is_file() else None
    data['sourceArtifacts'] = artifacts
    return data


def case_weight(case):
    """One unit per step or assertion. Long text adds a unit per extra 80 characters."""
    if not isinstance(case, dict):
        return 1
    weight = 0
    for key, field in (("steps", "action"), ("assertions", "expected")):
        rows = case.get(key) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            text = row.get(field) if isinstance(row, dict) else ""
            weight += max(1, (len(text) + 79) // 80) if isinstance(text, str) and text else 1
    return max(1, weight)


def slice_case_ids(cases, cap=SLICE_CAP, weight_budget=SLICE_WEIGHT):
    """Source order. cap is the hard count. Weight splits slices that hold long cases."""
    if not isinstance(cases, list) or not cases:
        raise ValueError('用例 ID 无效')
    items = []
    for item in cases:
        if isinstance(item, str) and item:
            items.append((item, 1))
        elif isinstance(item, dict) and isinstance(item.get("caseId"), str) and item["caseId"]:
            items.append((item["caseId"], case_weight(item)))
        else:
            raise ValueError('用例 ID 无效')
    if len(items) != len({case_id for case_id, _ in items}):
        raise ValueError('用例 ID 无效')
    if len(items) <= cap and sum(weight for _, weight in items) <= weight_budget:
        return [[case_id for case_id, _ in items]]
    slices, current, current_weight = [], [], 0
    for case_id, weight in items:
        if current and (len(current) >= cap or current_weight + weight > weight_budget):
            slices.append(current)
            current, current_weight = [], 0
        current.append(case_id)
        current_weight += weight
    if current:
        slices.append(current)
    return slices


def slice_commands(run_dir, round_id, output_rel, case_ids, kind):
    content = str((run_dir / output_rel).resolve())
    argv = [sys.executable, str(pathlib.Path(__file__).resolve()), str(run_dir.resolve()),
            '--round', round_id, '--content', content, '--check-only']
    if kind == 'notes':
        argv.append('--notes-only')
    else:
        argv.extend(['--cases-only', '--case-ids', *case_ids])
    return shlex.join(argv), shlex.join(argv + ['--parse-only'])


def write_slice_plan(run_dir, round_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', round_id):
        raise ValueError('展示轮次无效')
    cases = json.loads((run_dir / 'cases/cases.json').read_text(encoding='utf-8'))
    slices = []
    for index, chunk in enumerate(slice_case_ids(cases), 1):
        name = f'{index:02d}'
        output = f'cases/presentation/{round_id}/slices/{name}/content.json'
        check, probe = slice_commands(run_dir, round_id, output, chunk, 'cases')
        slices.append({'name': name, 'kind': 'cases', 'caseIds': chunk, 'ownsNotes': False,
                       'output': output, 'checkCommand': check, 'probeCommand': probe})
    notes_output = f'cases/presentation/{round_id}/slices/notes/content.json'
    check, probe = slice_commands(run_dir, round_id, notes_output, [], 'notes')
    slices.append({'name': 'notes', 'kind': 'notes', 'caseIds': [], 'ownsNotes': True,
                   'output': notes_output, 'checkCommand': check, 'probeCommand': probe})
    plan = {
        'schemaVersion': 1, 'cap': SLICE_CAP, 'weightBudget': SLICE_WEIGHT,
        'lineBudgets': {name: limit for name, (_, limit) in REVIEW_LINE_BUDGET.items()},
        'slices': slices}
    path = run_dir / 'cases/presentation' / round_id / 'plan.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(plan, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') != data:
        raise ValueError('分片计划已存在且不一致')
    path.write_text(data, encoding='utf-8')
    return plan


def prompt_text():
    contract = (pathlib.Path(__file__).resolve().parents[1] / 'references/review-table.md').read_text(encoding='utf-8')
    return contract.split('## 固定提示词（原样派发）\n', 1)[1].split('\n## 主 Agent 验收', 1)[0].strip()


def stat_slices(run_dir, round_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', round_id):
        raise ValueError('展示轮次无效')
    plan = json.loads((run_dir / 'cases/presentation' / round_id / 'plan.json').read_text(encoding='utf-8'))
    missing, empty, ok = [], [], []
    for row in plan.get('slices') or []:
        rel = row.get('output')
        path = run_dir / rel if isinstance(rel, str) else None
        if path is None or not path.is_file():
            missing.append(rel)
        elif path.stat().st_size == 0:
            empty.append(rel)
        else:
            ok.append(rel)
    return {'status': 'READY' if not missing and not empty else 'REDISPATCH',
            'missing': missing, 'empty': empty, 'ok': ok}


def fill_receipt(run_dir, round_id):
    """Fill prompt and artifact hashes. Identity fields stay as the dispatcher wrote them."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', round_id):
        raise ValueError('展示轮次无效')
    base = run_dir / 'cases/presentation' / round_id
    path = base / 'receipt.json'
    receipt = json.loads(path.read_text(encoding='utf-8'))
    for key in ('agentId', 'model', 'effort', 'isolation'):
        if not isinstance(receipt.get(key), str) or not receipt[key].strip():
            raise ValueError('展示回执缺少 ' + key)
    names = ('content.json', 'cases.xlsx', 'cases.md', 'mapping.json')
    refs = [f'cases/{name}.json' for name in ('cases', 'scenarios', 'acs', 'impact-analysis')]
    refs += [f'cases/presentation/{round_id}/{name}' for name in names]
    hashes = {}
    for ref in refs:
        artifact = run_dir / ref
        if not artifact.is_file():
            raise ValueError('缺少 ' + ref)
        hashes[ref] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    receipt['promptHash'] = hashlib.sha256(prompt_text().encode('utf-8')).hexdigest()
    receipt['artifactHashes'] = hashes
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'status': 'FILLED', 'artifacts': len(hashes)}


def presentation_input(source, case_ids):
    """保留分配用例原文及全局影响证据，不用摘要替代业务输入。"""
    assigned = set(case_ids)
    cases = [case for case in source['cases'] if case['caseId'] in assigned]
    if (not case_ids or
            [case['caseId'] for case in cases] != case_ids):
        raise ValueError('分片 caseIds 必须为唯一、存在且保持源顺序的 ID')
    scenario_ids = set(sid for case in cases for sid in case['scenarioIds'])
    scenarios = [source['scenarios'][sid] for sid in sorted(scenario_ids)]
    ac_ids = set(aid for scenario in scenarios for aid in scenario['acIds'])
    return {
        'schemaVersion': 1, 'caseIds': case_ids,
        'sourceArtifacts': source['sourceArtifacts'],
        'caseIndex': [{key: case[key] for key in ('caseId', 'scenarioIds')}
                      for case in source['cases']],
        'cases': cases, 'scenarios': scenarios,
        'acs': [source['acs'][aid] for aid in sorted(ac_ids)],
        'impact': source['impact'], 'semantic': source['semantic'],
    }


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


def wrapped_lines(text, width):
    return sum(max(1, ceil(sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1
                              for ch in seg) / max(width - 2, 8)))
               for seg in str(text or '').split('\n'))


def est_height(cells):
    lines = max([1] + [wrapped_lines(text, width) for text, width in cells])
    height = max(28, lines * 15 + 6)
    if height > 409:
        raise ValueError('单行内容超过 Excel 行高上限：请在不遗漏业务义务的前提下精简文案')
    return height


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
        row_no = body(ws, [row['group'], row['title'], numbered(row['fixtures']),
                              numbered(row['steps']), numbered(row['expected']), '未测试'],
                         WIDTH_MAIN, center=(1, 6))
        ws.cell(row_no, 7, unit['case']['caseId'])
        rows.append(row_no)
    ws.cell(1, 7, '内部用例ID')
    ws.column_dimensions['G'].hidden = True
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


def render_notes(wb, notes):
    ws = wb.create_sheet('待确认事项')
    ws.append(HEAD_NOTES)
    style_head(ws, WIDTH_NOTES)
    for note in notes:
        body(ws, [note['category'], note['question'], note['action']], WIDTH_NOTES)
    return ws


def disposition(note):
    return note.get('disposition') or 'decision'


def note_dispositions(run_dir):
    """生成阶段的 cases/notes.json 是三分法事实源；旧 run 无此文件时全部为 decision。"""
    path = run_dir / 'cases/notes.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        print('ERROR cases/notes.json 不是合法 JSON，无法回填待确认类型', file=sys.stderr)
        sys.exit(2)
    rows = data.get('notes') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        print('ERROR cases/notes.json 需为 {notes: [...]}', file=sys.stderr)
        sys.exit(2)
    return {str(row['category']): row.get('disposition') or 'decision'
            for row in rows if isinstance(row, dict) and row.get('category')}


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
                                       for i, g in enumerate(unit['expectedSources'], 1)), '',
               '**源用例数据**', '', '```json',
               json.dumps(case, ensure_ascii=False, indent=2), '```', '']
    pending = [n for n in notes if disposition(n) == 'decision']
    settled = [n for n in notes if disposition(n) != 'decision']
    md += ['## 待确认事项与已知限制', ''] + [
        f"- **{n['category']}**：{n['question']}；{n['action']}（来源：{', '.join(n['sourceRefs'])}）"
        for n in pending]
    if settled:
        md += ['', '## 已自查结论与假设（不进评审表）', '']
        for note in settled:
            kind = '已按代码确定' if disposition(note) == 'resolved_by_code' else '假设'
            line = f"- **{note['category']}**（{kind}）：{note.get('conclusion') or note.get('basis') or note['question']}"
            if note.get('evidenceRef'):
                line += f"；依据 {note['evidenceRef']}"
            if note.get('falsifier'):
                line += f"；若 {note['falsifier']} 则不成立"
            md.append(line)
    md += ['', '## 影响分析与追踪原始数据', '', '```json',
           json.dumps(source['impact'], ensure_ascii=False, indent=2), '```']
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
            'technicalExceptions': unit['row'].get('technicalExceptions', []),
            'densityExceptions': unit['row'].get('densityExceptions', {}),
            'tracking': {'file': 'cases.md', 'heading': f"{case['caseId']} {unit['row']['title']}"},
        })
    return {'schemaVersion': 1, 'generatedBy': 'render_review_table.py',
            'round': round_dir.name, 'caseCount': len(entries), 'cases': entries}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_dir', type=pathlib.Path)
    ap.add_argument('--content', type=pathlib.Path)
    ap.add_argument('--check-only', action='store_true', help='只校验，不写文件')
    ap.add_argument('--parse-only', action='store_true', help='只解析参数，不读产物')
    ap.add_argument('--notes-only', action='store_true', help='待确认片：只校验 notes，cases 必须为空')
    ap.add_argument('--cases-only', action='store_true', help='业务片：notes 必须为空数组')
    ap.add_argument('--stat-slices', action='store_true', help='核对分片产物存在且非空')
    ap.add_argument('--print-input', action='store_true', help='输出无损分片输入 JSON，不写文件或批准门禁')
    ap.add_argument('--case-ids', nargs='+', help='自检或输入提取：本分片分配的有序 caseId')
    ap.add_argument('--round', default='1')
    ap.add_argument('--out-dir', type=pathlib.Path)
    ap.add_argument('--plan-slices', action='store_true', help='按源顺序和内容体量写出展示分片计划')
    ap.add_argument('--fill-receipt', action='store_true', help='写入固定提示词和产物哈希')
    args = ap.parse_args()
    if args.parse_only:
        print(json.dumps({'status': 'PARSED'}, ensure_ascii=False))
        return
    if args.notes_only and (args.case_ids or args.cases_only):
        ap.error('--notes-only 不能和用例分片参数一起使用')
    if args.case_ids and not (args.check_only or args.print_input):
        ap.error('--case-ids 只能与 --check-only 或 --print-input 一起使用')
    if args.print_input and (not args.case_ids or args.check_only or args.content or args.out_dir or args.plan_slices or args.fill_receipt):
        ap.error('--print-input 需要 --case-ids，不能与自检、文案或输出目录参数混用')
    if (args.plan_slices or args.fill_receipt or args.stat_slices) and (args.check_only or args.print_input or args.content or args.case_ids or args.out_dir or args.notes_only or args.cases_only):
        ap.error('分片计划和回执哈希与渲染参数分开使用')
    if sum(bool(flag) for flag in (args.plan_slices, args.fill_receipt, args.stat_slices)) > 1:
        ap.error('分片计划、回执哈希和产物检查分开运行')

    run_dir = args.run_dir.resolve()
    if args.stat_slices:
        try:
            result = stat_slices(run_dir, args.round)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            ap.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        if result['status'] != 'READY':
            sys.exit(2)
        return
    if args.plan_slices or args.fill_receipt:
        try:
            result = write_slice_plan(run_dir, args.round) if args.plan_slices else fill_receipt(run_dir, args.round)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            ap.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.print_input:
        try:
            packet = presentation_input(load_run(run_dir), args.case_ids)
        except (ValueError, KeyError) as exc:
            ap.error(str(exc))
        print(json.dumps(packet, ensure_ascii=False, separators=(',', ':')))
        return
    round_dir = run_dir / 'cases/presentation' / args.round
    content_path = args.content or round_dir / 'content.json'
    out_dir = (args.out_dir or run_dir / 'cases').resolve()

    content = json.loads(content_path.read_text(encoding='utf-8'))
    if args.notes_only:
        if content.get('cases'):
            print('ERROR 待确认片的 cases 必须是空数组', file=sys.stderr)
            sys.exit(2)
        content['cases'] = []
    errors = []
    source = {'cases': []} if args.notes_only else load_run(run_dir)
    if args.case_ids:
        assigned = set(args.case_ids)
        source['cases'] = [c for c in source['cases'] if c['caseId'] in assigned]
        if [c['caseId'] for c in source['cases']] != args.case_ids:
            errors.append('分片 caseIds 必须唯一、存在且保持源顺序')
        if len(args.case_ids) > SLICE_CAP:
            errors.append(f'分片最多 {SLICE_CAP} 条用例，请拆分任务')
    units = normalize(content, source, errors)
    if errors:
        for line in errors:
            print(f'ERROR {line}', file=sys.stderr)
        sys.exit(2)

    notes = content.get('notes') or []
    for note in notes:
        if isinstance(note, dict) and not note.get('disposition'):
            note['disposition'] = note_dispositions(run_dir).get(note.get('category'), 'decision')
    for i, note in enumerate(notes, 1):
        if (not isinstance(note, dict) or
                any(not isinstance(note.get(k), str) or not note[k].strip()
                    for k in ('category', 'question', 'action')) or
                not isinstance(note.get('sourceRefs'), list) or not note['sourceRefs'] or
                any(not isinstance(ref, str) or not ref.strip() for ref in note['sourceRefs'])):
            print(f'ERROR notes[{i}]: 需提供 category、question、action 和非空 sourceRefs', file=sys.stderr)
            sys.exit(2)
        if note.get('disposition') is not None and note['disposition'] not in NOTE_DISPOSITIONS:
            print(f"ERROR notes[{i}]: disposition 只能是 {', '.join(NOTE_DISPOSITIONS)}，"
                  f"收到 {note['disposition']!r}", file=sys.stderr)
            sys.exit(2)
    if args.cases_only and notes:
        print('ERROR 业务片的 notes 必须是空数组，待确认事项由 notes 片写入', file=sys.stderr)
        sys.exit(2)
    if args.notes_only:
        issues = presentation_issues({'cases': [], 'notes': notes}, None)
        if issues:
            for issue in issues:
                print(f'ERROR 评审文案待处理：{issue}', file=sys.stderr)
            sys.exit(2)
        if args.check_only:
            print(f'PASSED notes={len(notes)}')
            return
        print('ERROR 待确认片只支持 --check-only', file=sys.stderr)
        sys.exit(2)
    issues = presentation_issues(content, source)
    if issues:
        for issue in issues:
            print(f'ERROR 评审文案待处理：{issue}', file=sys.stderr)
        print('请按评审表契约精简业务表述；必要技术信息或复杂边界记录逐项例外并复核。', file=sys.stderr)
        sys.exit(2)
    layout_errors = []
    for row in content['cases']:
        values = [row['group'], row['title'], numbered(row['fixtures']),
                  numbered(row['steps']), numbered(row['expected']), '未测试']
        for field, value, width in zip(HEAD_MAIN, values, WIDTH_MAIN):
            try:
                est_height([(value, width)])
            except ValueError as exc:
                layout_errors.append(f"{row['caseId']} {field}: {exc}")
    for i, note in enumerate(notes, 1):
        for field, width in zip(('category', 'question', 'action'), WIDTH_NOTES):
            try:
                est_height([(note[field], width)])
            except ValueError as exc:
                layout_errors.append(f'notes[{i}] {field}: {exc}')
    if layout_errors:
        for error in layout_errors:
            print(f'ERROR {error}', file=sys.stderr)
        sys.exit(2)
    if args.check_only:
        print(f'PASSED cases={len(units)}; 映射、文案与行高预检通过；仍需语义和视觉核验')
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    low = low_scores(source['semantic'])
    wb = Workbook()
    sheet_rows = render_main(wb, units, source)
    pending = [note for note in notes if disposition(note) == 'decision']
    render_notes(wb, pending)
    xlsx = out_dir / 'cases.xlsx'
    wb.save(xlsx)
    md_lines = render_md(out_dir / 'cases.md', content, units, source, notes, low)
    # 展示门禁与 publish_review.py 要求 cases/ 与本轮目录的同一份字节；openpyxl 写入
    # docProps 时戳，分别渲染会得到不同字节，因此这里只做一次字节拷贝。
    round_dir.mkdir(parents=True, exist_ok=True)
    for name in ('cases.xlsx', 'cases.md'):
        rendered, mirrored = (out_dir / name).resolve(), round_dir / name
        if rendered != mirrored.resolve():
            shutil.copyfile(rendered, mirrored)
    mapping = build_mapping(units, source, sheet_rows, round_dir)
    mapping['notes'] = []
    row = 2
    for note in notes:
        entry = {'disposition': disposition(note), 'category': note['category'],
                 'sourceRefs': note['sourceRefs']}
        if disposition(note) == 'decision':
            entry.update({'sheet': '待确认事项', 'row': row})
            row += 1
        else:
            entry.update({'file': 'cases.md', 'section': '已自查结论与假设'})
        mapping['notes'].append(entry)
    (round_dir / 'mapping.json').write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{xlsx} cases={len(units)} rows={len(sheet_rows)}; cases.md lines={md_lines}; '
          f'mapping={round_dir / "mapping.json"}')


if __name__ == '__main__':
    main()
