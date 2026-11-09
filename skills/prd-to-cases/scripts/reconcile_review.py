#!/usr/bin/env python3
"""比较评审前后的主表，保留行级证据；识别不确定时不猜测用例身份。"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

HEADERS = ['场景名称', '用例标题', '前置条件', '操作步骤', '预期结果', '测试结果', '内部用例ID']


def read_sheet(path):
    book = load_workbook(path, data_only=False)
    if '用例主表' not in book:
        raise ValueError('缺少用例主表')
    sheet = book['用例主表']
    if [sheet.cell(1, i).value for i in range(1, 8)] != HEADERS:
        raise ValueError('主表列结构或内部用例ID列不完整')
    rows = []
    for number in range(2, sheet.max_row + 1):
        values = [sheet.cell(number, i).value for i in range(1, 8)]
        if not any(v is not None and str(v).strip() for v in values[1:5] + values[6:7]):
            continue
        for merged in sheet.merged_cells.ranges:
            if merged.min_col == 1 and merged.max_col == 1 and merged.min_row <= number <= merged.max_row:
                values[0] = sheet.cell(merged.min_row, 1).value
                break
        rows.append({'row': number, 'caseId': str(values[6]).strip() if values[6] is not None else '',
                     'values': values[:5], 'result': values[5]})
    return rows


def compare(baseline, reviewed, mapping):
    expected = {c['caseId']: c['mainSheet']['row'] for c in mapping['cases']}
    if len(expected) != len(mapping['cases']):
        raise ValueError('基线映射中的用例ID不唯一')
    originals = {row['caseId']: row for row in baseline}
    if (len(originals) != len(baseline) or set(originals) != set(expected) or
            any(originals[cid]['row'] != row for cid, row in expected.items())):
        raise ValueError('基线工作簿与映射不一致')
    counts = Counter(row['caseId'] for row in reviewed if row['caseId'])
    duplicate = {cid for cid, count in counts.items() if count > 1}
    all_originals_present = all(counts[cid] == 1 for cid in originals)
    unknown_ids = set(counts) - set(originals)
    changes = []
    ambiguous = False
    matched = set()
    for row in reviewed:
        cid = row['caseId']
        entry = {'reviewRow': row['row'], 'caseId': cid or None, 'values': row['values']}
        if row['values'][0] is None or not str(row['values'][0]).strip():
            entry['groupIssue'] = '场景为空，请核对合并单元格或填写场景'
        if not cid:
            entry['status'] = 'added' if all_originals_present and not duplicate and not unknown_ids else 'unidentified'
            ambiguous |= entry['status'] == 'unidentified'
        elif cid in duplicate or cid not in originals:
            entry['status'] = 'identity_conflict'
            ambiguous = True
        else:
            old = originals[cid]
            matched.add(cid)
            entry['baselineRow'] = old['row']
            entry['before'] = old['values']
            entry['changedColumns'] = [HEADERS[i] for i in range(5) if old['values'][i] != row['values'][i]]
            entry['status'] = 'modified' if entry['changedColumns'] else 'unchanged'
        changes.append(entry)
    for cid, old in originals.items():
        if cid not in matched:
            changes.append({'caseId': cid, 'baselineRow': old['row'], 'before': old['values'],
                            'status': 'missing_unresolved' if ambiguous else 'deleted'})
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('reviewed', type=Path)
    parser.add_argument('mapping', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text(encoding='utf-8'))
    changes = compare(read_sheet(args.baseline), read_sheet(args.reviewed), mapping)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    report = {'schemaVersion': 1, 'baselineHash': digest(args.baseline),
              'reviewedHash': digest(args.reviewed), 'mappingHash': digest(args.mapping),
              'identityStatus': 'blocked' if any(c.get('groupIssue') or c['status'] in
                  ('unidentified', 'identity_conflict', 'missing_unresolved') for c in changes) else 'resolved',
              'changes': changes}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"身份映射：{report['identityStatus']}；结果：{args.output}")
    return 2 if report['identityStatus'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
