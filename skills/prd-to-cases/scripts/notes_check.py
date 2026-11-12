#!/usr/bin/env python3
"""待确认事项内容检查：保留业务疑问、已查证据及对用例的影响。

读 cases/notes.json；没有该文件时退回本轮展示 content.json 的 notes 并按旧契约提示。
用法：python3 notes_check.py <runDir> [--baseline <基线工作树>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path



def read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None


def locate(run_dir: Path) -> tuple:
    """返回 (notes, 来源标签, 是否结构化)；优先结构化 notes.json。"""
    structured = read_json(run_dir / 'cases' / 'notes.json')
    if isinstance(structured, dict) and isinstance(structured.get('notes'), list):
        return structured['notes'], 'cases/notes.json', True
    manifest = read_json(run_dir / 'manifest.json') or {}
    round_id = str((manifest.get('presentation') or {}).get('round', '1'))
    content = read_json(run_dir / 'cases' / 'presentation' / round_id / 'content.json') or {}
    return content.get('notes') or [], f'cases/presentation/{round_id}/content.json（旧契约）', False


def has_text(value):
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)


def check(run_dir: Path, baseline=None, strict=None) -> dict:
    run_dir = Path(run_dir)
    notes, label, structured = locate(run_dir)
    strict = structured if strict is None else strict
    result = {'source': label, 'structured': structured, 'strict': strict,
              'notes': len(notes), 'counts': {}, 'gaps': [], 'warnings': []}
    if not notes:
        # 缺材料不算收口失败：新 run 仍应在生成阶段写 cases/notes.json（哪怕 notes: []），
        # 这里只提示，避免把尚未装配的 run 判成 BLOCKED。
        result['warnings'].append('没有找到待确认材料：新 run 需写 cases/notes.json（没有事项也写 notes: []）')
        return result
    cases = read_json(run_dir / 'cases' / 'cases.json') or []
    if isinstance(cases, dict):
        cases = cases.get('cases') or []
    case_ids = {case.get('caseId') for case in cases if isinstance(case, dict)}
    for index, note in enumerate(notes, 1):
        if not isinstance(note, dict):
            result['gaps'].append(f'notes[{index}]: 不是对象')
            continue
        label_text = str(note.get('category') or f'#{index}')
        disposition = note.get('disposition') or 'decision'
        result['counts'][disposition] = result['counts'].get(disposition, 0) + 1
        if not str(note.get('question') or note.get('conclusion') or '').strip():
            result['gaps'].append(f'{label_text}: 缺少业务疑问 question')
        evidence = (note.get('evidence') or note.get('evidenceRef') or
                    note.get('searchedRefs') or note.get('sourceRefs'))
        impact = (note.get('impact') or note.get('conclusion') or note.get('basis') or
                  note.get('action') or note.get('impactCaseIds'))
        if not has_text(evidence):
            result['gaps'].append(f'{label_text}: 请说明已查证据或检索结果')
        if not has_text(impact):
            result['gaps'].append(f'{label_text}: 请说明结论如何影响用例')
        impacted = note.get('impactCaseIds')
        if impacted is not None:
            if not isinstance(impacted, list):
                result['gaps'].append(f'{label_text}: impactCaseIds 应为数组')
            else:
                unknown = [cid for cid in impacted if cid not in case_ids]
                if unknown:
                    result['gaps'].append(f'{label_text}: impactCaseIds 引用未知用例 {unknown}')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--baseline', help='兼容旧调用；证据内容由来源复核确认')
    parser.add_argument('--no-baseline', action='store_true', help='兼容旧调用')
    parser.add_argument('--strict', action='store_true', help='兼容参数；所有输入使用相同内容检查')
    args = parser.parse_args()
    result = check(args.run_dir, None if args.no_baseline else args.baseline,
                   True if args.strict else None)
    summary = {'source': result['source'], 'notes': result['notes'], 'counts': result['counts'],
               'strict': result['strict'], 'warnings': len(result['warnings'])}
    print('[pending] ' + ('PASSED' if not result['gaps'] else 'BLOCKED') + ' '
          + json.dumps(summary, ensure_ascii=False))
    for gap in result['gaps']:
        print('  - ' + gap)
    for warning in result['warnings']:
        print('  ~ 提示：' + warning)
    return 0 if not result['gaps'] else 2


if __name__ == '__main__':
    sys.exit(main())
