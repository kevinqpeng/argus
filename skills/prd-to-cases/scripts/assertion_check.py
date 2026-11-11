#!/usr/bin/env python3
"""断言可观察性检查：模糊措辞、或然结论、节点锚点、记录四件套与配置对照。

只做高精度规则：能确定是弱表述的直接阻断，需要业务判断的只给提示。路径断言只用代词
指代节点、来源给了 anchors.start 却没用、记录追加类不给条数——这三类直接阻断。
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
# 路径类断言说的是「从哪开始、经过谁、跳过谁」，必须落到节点身份上。
PATH_SIGNAL = re.compile(r'路径|新一轮|重新审批|重新发起后|换版|重新开始|起点|从.{1,6}开始')
NODE_TOKEN = re.compile(r'[「"]?([A-Za-z][A-Za-z0-9]{0,2})[」"]?\s*节点|节点\s*(?:为|是|：|:)?\s*[「"]?([A-Za-z][A-Za-z0-9]{0,2})')
PRONOUN_ANCHOR = re.compile(r'流程起点|路径起点|起点|原驳回节点|原节点|原审批人|该节点|当前节点|发起人')
ORDER_WORD = re.compile(r'依次|顺序|→|->|>|之后|排在')
RECORD_WORD = re.compile(r'流转记录|审批记录|处理记录')
RECORD_GROW = re.compile(r'保留|追加|新增|增加|仍在|原有|历史')
RECORD_COUNT = re.compile(r'条数|记录数|\d+\s*条')
RECORD_ORDER = re.compile(r'依次|顺序|排在|之后|之前|逆序|置顶|最上|末尾')
RECORD_KEEP = re.compile(r'未变|没变|不变|相同|一致|保持|不被改写|未改写|无变化')
RECORD_CURRENT = re.compile(r'当前节点')
OBLIGATION_PATH = re.compile(r'节点|路径|分支')
OBLIGATION_CHANGE = re.compile(r'重新|变更|变化|调整|换版|开始|不含|不经过|加签|转办|继续')
MIN_WAIVER = 20
SUITES = ('ui', 'api', 'performance', 'security')


def load(run_dir: Path, name: str):
    path = run_dir / 'cases' / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None


def node_tokens(text: str) -> set:
    tokens = set()
    for left, right in NODE_TOKEN.findall(text):
        if left or right:
            tokens.add(left or right)
    return tokens


def anchor_gaps(run_dir: Path, cases: list) -> tuple:
    """来源给了节点身份，断言就必须用它；只写「起点/发起人」等于没锚定。"""
    impact = load(run_dir, 'impact-analysis.json')
    gaps, warnings = [], []
    if not isinstance(impact, dict):
        warnings.append('缺少 cases/impact-analysis.json，路径类义务没有节点锚点可对照')
        return gaps, warnings, 0
    scenarios = load(run_dir, 'scenarios.json') or []
    scenario_acs = {s.get('scenarioId'): [a for a in s.get('acIds') or [] if a]
                    for s in scenarios if isinstance(s, dict)}
    bodies, case_acs = {}, {}
    for case in cases:
        case_id = case.get('caseId') or '<no-id>'
        bodies[case_id] = ' '.join(str(a.get('expected') or '') for a in case.get('assertions') or [])
        acs = set()
        for scenario_id in case.get('scenarioIds') or []:
            acs.update(scenario_acs.get(scenario_id) or [])
        case_acs[case_id] = acs
    anchors = 0
    for duty in impact.get('obligations') or []:
        if not isinstance(duty, dict):
            continue
        text = ' '.join(str(duty.get(key) or '') for key in ('text', 'expected'))
        if not (OBLIGATION_PATH.search(text) and OBLIGATION_CHANGE.search(text)):
            continue
        obligation_id = duty.get('obligationId') or '<no-id>'
        declared = duty.get('anchors') if isinstance(duty.get('anchors'), dict) else {}
        start = str(declared.get('start') or '').strip()
        waiver = str(declared.get('waiver') or '').strip()
        if not start and len(waiver) < MIN_WAIVER:
            gaps.append(f'{obligation_id}: 路径类义务没有节点锚点，写 anchors.start（变更后从哪个节点开始）'
                        f'或 {MIN_WAIVER} 字以上 anchors.waiver 说明来源确实没给节点身份')
            continue
        if not start:
            continue
        anchors += 1
        acs = {a for a in duty.get('acIds') or [] if a}
        covering = [case_id for case_id, case_acs_ids in case_acs.items() if acs and case_acs_ids & acs]
        if not any(start in bodies.get(case_id, '') for case_id in covering):
            gaps.append(f'{obligation_id}: 覆盖它的用例断言都没有出现来源给定的新路径起点「{start}」'
                        f'（{", ".join(covering[:4]) or "没有用例承接"}）')
    return gaps, warnings, anchors


def check(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    cases = load(run_dir, 'cases.json')
    result = {'cases': 0, 'assertions': 0, 'anchors': 0, 'gaps': [], 'warnings': []}
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
        texts = [str(a.get('expected') or '').strip() for a in assertions]
        path_texts = [text for text in texts if PATH_SIGNAL.search(text)]
        record_texts = [text for text in texts if RECORD_WORD.search(text)]
        if path_texts and not node_tokens(' '.join(path_texts)):
            if any(PRONOUN_ANCHOR.search(text) for text in path_texts):
                result['gaps'].append(
                    f'{case_id}: 路径断言只写了「起点/发起人」这类代词，没有具体节点标识；'
                    '从哪个节点开始必须写成节点身份')
        elif path_texts:
            listed = any(len(node_tokens(text)) >= 3
                         or (len(node_tokens(text)) >= 2 and ORDER_WORD.search(text))
                         for text in texts)
            if not listed:
                result['warnings'].append(
                    f'{case_id}: 路径断言没给节点清单，评审无法核对从谁开始、跳过谁；'
                    '写成「依次为 A、C、D」并说明不含谁')
        if record_texts and any(RECORD_GROW.search(text) for text in record_texts):
            if not any(RECORD_COUNT.search(text) for text in record_texts):
                result['gaps'].append(
                    f'{case_id}: 记录追加类断言没有条数或增量，先读基线再写差值')
            missing = [label for label, pattern in (('顺序', RECORD_ORDER), ('旧行未被改写', RECORD_KEEP),
                                                    ('当前节点标记', RECORD_CURRENT))
                       if not any(pattern.search(text) for text in record_texts)]
            if missing:
                result['warnings'].append(f'{case_id}: 记录四件套缺{"、".join(missing)}')
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
    impact_gaps, impact_warnings, anchors = anchor_gaps(run_dir, cases)
    result['gaps'].extend(impact_gaps)
    result['warnings'].extend(impact_warnings)
    result['anchors'] = anchors
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    args = parser.parse_args()
    result = check(args.run_dir)
    summary = {k: result[k] for k in ('cases', 'assertions', 'anchors')}
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
