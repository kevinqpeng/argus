import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/assertion_check.py'


class AssertionCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'cases').mkdir()

    def check(self, cases):
        (self.root / 'cases/cases.json').write_text(json.dumps(cases, ensure_ascii=False))
        return subprocess.run([sys.executable, str(SCRIPT), str(self.root)], capture_output=True, text=True)

    @staticmethod
    def case(expected):
        return {'caseId': 'C1', 'suite': 'ui',
                'steps': [{'id': 's1', 'action': '提交申请'}],
                'assertions': [{'stepId': 's1', 'expected': expected}]}

    def test_business_expressions_do_not_need_fixed_tokens(self):
        for expected in ('从流程起点重新审批', '原来的审批记录保留', '流转记录新增一条驳回记录',
                         '重新进入页面后开关仍为开启', '路径符合预期', '弹窗不显示节点或人员下拉框'):
            with self.subTest(expected=expected):
                result = self.check([self.case(expected)])
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('提示', result.stdout)

    def test_declared_anchor_does_not_require_literal_copy(self):
        (self.root / 'cases/impact-analysis.json').write_text(json.dumps({'obligations': [
            {'text': '改变路径', 'anchors': {'start': 'B0'}}]}))
        result = self.check([self.case('待办回到部门审批')])
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_empty_and_unbound_assertions_still_block(self):
        for change in ('empty', 'unbound', 'missing', 'uncovered'):
            case = self.case('申请进入审批')
            if change == 'empty':
                case['assertions'][0]['expected'] = ' '
            elif change == 'unbound':
                case['assertions'][0]['stepId'] = 'other'
            elif change == 'missing':
                case['assertions'] = []
            else:
                case['steps'].append({'id': 's2', 'action': '再次提交'})
            with self.subTest(change=change):
                self.assertEqual(self.check([case]).returncode, 2)

    def test_setup_exemption_and_api_suite_are_structural_choices(self):
        case = self.case('申请进入审批')
        case['suite'] = 'api'
        case['steps'].insert(0, {'id': 'prep', 'action': '准备申请', 'kind': 'setup',
                                 'assertionExemption': '前置准备'})
        self.assertEqual(self.check([case]).returncode, 0)

    def test_missing_or_invalid_cases_block(self):
        for cases in ([], {}, [None]):
            with self.subTest(cases=cases):
                self.assertEqual(self.check(cases).returncode, 2)
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.root / 'missing')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
