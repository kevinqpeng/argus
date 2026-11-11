#!/usr/bin/env python3
"""断言可观察性检查：模糊措辞、或然结论、路径清单与配置对照。

只做高精度规则：能确定是弱表述的直接阻断，需要业务判断的只给提示。
用法：python3 assertion_check.py <runDir>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VAGUE = re.compile(
    r'符合预期|如预期|功能正常|正常工作|一切正常|无异常|状态正常|显示正常|表现正常|正确地|'
    r'合理性|相关问题|记录保留|数据保留|等等|若干|适当|尽量|视情况|大概|可能是')
OR_EITHER = re.compile(r'(?<![或])或(?!者|许)|(?<![／/])/(?=[^\d])')
PATH_WORD = re.compile(r'路径|节点顺序|审批顺序|流转顺序')
PATH_EVIDENCE = re.compile(r'不含|不经过|没有|依次|、')
CONFIG_WORD = re.compile(r'保存|开关|配置项|发布|默认为')
SUITES = ('ui', 'api', 'performance', 'security')


def load(run_dir: Path, name: str):
    path = run_dir / 'cases' / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None


def check(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    cases = load(run_dir, 'cases.json')
    result = {'cases': 0, 'assertions': 0, 'gaps': [], 'warnings': []}
    if not isinstance(cases, list) or not cases:
        result['gaps'].append('缺少 cases/cases.json 或为空，无法检查断言')
        return result
    result['cases'] = len(cases)
    seen_text, suites, config_cases = {}, set(), []
    for case in cases:
        case_id = case.get('caseId') or '<no-id>'
        suites.add(case.get('suite'))
        steps = case.get('steps') or []
        actions = ' '.join(str(step.get('action') or '') for step in steps)
        if CONFIG_WORD.search(actions) or CONFIG_WORD.search(str(case.get('title') or '')):
            config_cases.append(case_id)
        assertions = case.get('assertions') or []
        if not assertions:
            result['gaps'].append(f'{case_id}: 用例没有断言')
            continue
        result['assertions'] += len(assertions)
        for index, assertion in enumerate(assertions, 1):
            text = str(assertion.get('expected') or '').strip()
            label = f'{case_id} 断言[{index}]'
            if not text:
                result['gaps'].append(f'{label}: 预期为空')
                continue
            match = VAGUE.search(text)
            if match:
                result['gaps'].append(f'{label}: 弱措辞「{match.group()}」，改写成可观察对象与值')
                continue
            match = OR_EITHER.search(text)
            if match:
                result['warnings'].append(f'{label}: 「{match.group()}」让结论不唯一，能确认唯一结果就改写')
            if PATH_WORD.search(text) and not PATH_EVIDENCE.search(text):
                result['warnings'].append(f'{label}: 提到路径但没给节点清单或「不含谁」，评审无法核对')
            seen_text.setdefault(text, []).append(case_id)
    for text, owners in seen_text.items():
        if len(set(owners)) >= 3:
            result['warnings'].append(
                f'断言「{text[:24]}…」在 {len(set(owners))} 条用例里逐字重复，核对是否缺少具体取值')
    if config_cases and 'api' not in suites:
        result['warnings'].append(
            f'配置类用例 {config_cases[:4]} 存在，但没有任何 suite=api 用例承接配置读写')
    unknown = sorted(s for s in suites if s is not None and s not in SUITES)
    if unknown:
        result['gaps'].append(f'非法 suite：{unknown}（只能是 {", ".join(SUITES)}）')
    if None in suites:
        result['warnings'].append('有用例没写 suite，执行阶段会拦；设计阶段先补，便于区分界面与接口断言')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    args = parser.parse_args()
    result = check(args.run_dir)
    summary = {k: result[k] for k in ('cases', 'assertions')}
    summary['warnings'] = len(result['warnings'])
    print('[assertions] ' + ('PASSED' if not result['gaps'] else 'BLOCKED') + ' '
          + json.dumps(summary, ensure_ascii=False))
    for gap in result['gaps']:
        print('  - ' + gap)
    for warning in result['warnings']:
        print('  ~ 提示：' + warning)
    return 0 if not result['gaps'] else 2


if __name__ == '__main__':
    sys.exit(main())
