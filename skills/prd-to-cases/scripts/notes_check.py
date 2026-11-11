#!/usr/bin/env python3
"""待确认事项三分法检查：先自查再提问，只让需要裁决的进评审表。

读 cases/notes.json；没有该文件时退回本轮展示 content.json 的 notes 并按旧契约提示。
用法：python3 notes_check.py <runDir> [--baseline <基线工作树>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DISPOSITIONS = ('resolved_by_code', 'assumption', 'decision')
LOCATOR = re.compile(r'^[^\s:]+:\d+(-\d+)?$')
NO_HIT = '检索无命中'
MIN_TEXT = 20


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


def baseline_root(run_dir: Path, explicit):
    if explicit:
        return Path(explicit)
    manifest = read_json(run_dir / 'manifest.json') or {}
    version = str(((manifest.get('versions') or {}).get('baselineVersion')) or '')
    if not version:
        return None
    # run 目录结构：<state>/projects/<project>/runs/<runId>
    project = run_dir.parent.parent.name
    root = Path.home() / '.local' / 'state' / 'argus' / 'baselines' / project / version
    return root if root.is_dir() else None


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
    cases = read_json(run_dir / 'cases' / 'cases.json')
    case_ids = {case.get('caseId') for case in cases or [] if isinstance(case, dict)}
    root = baseline_root(run_dir, baseline) if baseline is not False else None
    for index, note in enumerate(notes, 1):
        if not isinstance(note, dict):
            result['gaps'].append(f'notes[{index}]: 不是对象')
            continue
        label_text = str(note.get('category') or f'#{index}')
        disposition = note.get('disposition') or 'decision'
        if disposition not in DISPOSITIONS:
            result['gaps'].append(f'{label_text}: disposition={disposition!r} 非法'
                                  f'（只能是 {", ".join(DISPOSITIONS)}）')
            continue
        result['counts'][disposition] = result['counts'].get(disposition, 0) + 1
        for field in ('category', 'question', 'action'):
            if not str(note.get(field) or '').strip():
                result['gaps'].append(f'{label_text}: 缺少 {field}')
        if not isinstance(note.get('sourceRefs'), list) or not note['sourceRefs']:
            result['gaps'].append(f'{label_text}: 缺少 sourceRefs')
        searched = note.get('searchedRefs')
        if not isinstance(searched, list) or not searched:
            result['gaps'].append(f'{label_text}: 缺 searchedRefs——提问前必须先查基线并写明查过哪里')
            searched = []
        for ref in searched:
            text = str(ref or '')
            if not text.strip():
                result['gaps'].append(f'{label_text}: searchedRefs 有空项')
            elif not (LOCATOR.match(text) or text.startswith(NO_HIT)):
                result['gaps'].append(f'{label_text}: searchedRefs 需为「文件:行」或「{NO_HIT}：关键词」，收到 {text!r}')
            elif root is not None and LOCATOR.match(text) and not (root / text.split(':')[0]).is_file():
                result['gaps'].append(f'{label_text}: searchedRefs 指向不存在文件 {text}')
        if disposition == 'resolved_by_code':
            evidence = str(note.get('evidenceRef') or '').strip()
            conclusion = str(note.get('conclusion') or '').strip()
            if not LOCATOR.match(evidence):
                result['gaps'].append(f'{label_text}: resolved_by_code 需要 evidenceRef「文件:行」，收到 {evidence!r}')
            elif root is not None and not (root / evidence.split(':')[0]).is_file():
                result['gaps'].append(f'{label_text}: evidenceRef 指向不存在文件 {evidence}')
            if len(conclusion) < MIN_TEXT:
                result['gaps'].append(f'{label_text}: resolved_by_code 需 ≥{MIN_TEXT} 字结论说明查到什么')
            if not any(conclusion[:12] in str(case.get('assertions') or []) for case in cases or [] if isinstance(case, dict)) \
                    and conclusion:
                result['warnings'].append(f'{label_text}: 结论尚未落进断言，确认是否已写进用例')
        elif disposition == 'assumption':
            if len(str(note.get('basis') or '')) < MIN_TEXT:
                result['gaps'].append(f'{label_text}: assumption 需 ≥{MIN_TEXT} 字依据 basis')
            if not str(note.get('falsifier') or '').strip():
                result['gaps'].append(f'{label_text}: assumption 需写 falsifier——什么现象会推翻它')
        else:
            if not isinstance(note.get('blocking'), bool):
                result['gaps'].append(f'{label_text}: decision 需用布尔 blocking 标明不裁决能否先写断言')
            impact = note.get('impactCaseIds')
            if not isinstance(impact, list) or not impact:
                result['gaps'].append(f'{label_text}: decision 需列 impactCaseIds')
            else:
                unknown = sorted(set(impact) - case_ids)
                if unknown:
                    result['gaps'].append(f'{label_text}: impactCaseIds 引用未知用例 {unknown}')
            if all(str(ref or '').startswith(NO_HIT) for ref in searched) and searched:
                result['warnings'].append(f'{label_text}: 已记录检索无命中，可保留为待裁决')
    if not strict:
        # 已交付的旧 run 没写过三分法字段，只提示不回退其门禁状态；新 run 写
        # cases/notes.json 后自动转严格模式。
        gap_count = len(result['gaps'])
        result['gaps'] = []
        result['warnings'] = [f'旧契约待确认事项 {len(notes)} 条未按三分法标注（{gap_count} 项缺字段）；'
                              f'新 run 必须先写 cases/notes.json 再出评审表']
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--baseline', help='基线工作树，用于核对 evidenceRef 与 searchedRefs 的文件存在')
    parser.add_argument('--no-baseline', action='store_true', help='跳过文件存在核对，只查格式')
    parser.add_argument('--strict', action='store_true', help='旧 run 也按三分法阻断')
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
