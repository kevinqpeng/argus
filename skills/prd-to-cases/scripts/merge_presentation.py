#!/usr/bin/env python3
"""Merge presentation shards in frozen case order without changing their content."""
import argparse
import json
import re
from pathlib import Path

SLICE_WORD = re.compile(r'本片|本用例片')


def reject_slice_wording(value, path):
    if isinstance(value, str):
        found = SLICE_WORD.search(value)
        if found:
            raise ValueError(f'分片措辞进入交付正文 {path}: {found.group(0)}')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_slice_wording(item, f'{path}[{index}]')
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != 'caseId':
                reject_slice_wording(item, f'{path}.{key}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('shards', type=Path, nargs='+')
    args = parser.parse_args()
    if args.output.resolve() in {path.resolve() for path in [args.cases, *args.shards]}:
        raise ValueError('output must not replace an input')
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    ids = [case['caseId'] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate frozen caseId')
    rows, notes, title = {}, [], None
    for path in args.shards:
        part = json.loads(path.read_text(encoding='utf-8'))
        if title is None:
            title = part['title']
        if not isinstance(title, str) or not title.strip() or part['title'] != title:
            raise ValueError('shard titles must match and be nonempty')
        reject_slice_wording(part, path.name)
        for row in part['cases']:
            case_id = row['caseId']
            if case_id not in ids or case_id in rows:
                raise ValueError(f'unknown or duplicate caseId: {case_id}')
            rows[case_id] = row
        notes.extend(part['notes'])
    if set(rows) != set(ids):
        raise ValueError(f'missing caseIds: {sorted(set(ids) - set(rows))}')
    output = {'title': title, 'cases': [rows[case_id] for case_id in ids], 'notes': notes}
    content = json.dumps(output, ensure_ascii=False, indent=2) + '\n'
    if args.output.exists() and args.output.read_text(encoding='utf-8') != content:
        raise ValueError('output exists with different content')
    args.output.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    main()
