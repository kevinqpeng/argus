#!/usr/bin/env python3
"""检查断言非空及步骤关联；业务预期是否充分由来源核对、语义审查和人工评审判断。

用法：python3 assertion_check.py <runDir>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "argus/scripts"))
from case_contract import validate_steps

SUITES = ('ui', 'api', 'performance', 'security')


def check(run_dir: Path) -> dict:
    path = Path(run_dir) / 'cases/cases.json'
    result = {'cases': 0, 'assertions': 0, 'gaps': [], 'warnings': []}
    try:
        cases = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        result['gaps'].append(f'无法读取 cases/cases.json：{exc}')
        return result
    if not isinstance(cases, list) or not cases:
        result['gaps'].append('缺少 cases/cases.json 或为空，无法检查断言')
        return result
    result['cases'] = len(cases)
    for case in cases:
        if not isinstance(case, dict):
            result['gaps'].append('用例必须是对象')
            continue
        case_id = case.get('caseId') or '<no-id>'
        assertions = case.get('assertions')
        if isinstance(assertions, list):
            result['assertions'] += len(assertions)
        try:
            validate_steps(case.get('steps'), assertions)
        except ValueError as exc:
            result['gaps'].append(f'{case_id}: {exc}')
        if case.get('suite') is None:
            result['warnings'].append(f'{case_id}: 未声明 suite')
        elif case.get('suite') not in SUITES:
            result['gaps'].append(f'{case_id}: 非法 suite')
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
