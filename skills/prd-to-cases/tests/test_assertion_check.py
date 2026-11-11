import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/assertion_check.py'


def run(root: Path):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True)


class AssertionCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'cases').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, cases):
        (self.root / 'cases/cases.json').write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')

    @staticmethod
    def case(case_id, expected, suite='ui', action='审批人驳回'):
        return {'caseId': case_id, 'suite': suite, 'title': case_id,
                'steps': [{'id': 'S1', 'kind': 'action', 'action': action}],
                'assertions': [{'stepId': 'S1', 'expected': expected}]}

    def test_vague_wording_blocks(self):
        self.write([self.case('T1', '驳回后状态正常')])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('弱措辞', result.stdout)

    def test_record_retention_phrase_blocks(self):
        self.write([self.case('T1', '原来的审批记录保留')])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)

    def test_concrete_assertions_pass_and_ambiguous_or_only_warns(self):
        self.write([self.case('T1', '流转记录共 5 条，新增提交记录排在两条旧记录之后'),
                    self.case('T2', '弹窗不显示节点或人员下拉框')])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('让结论不唯一', result.stdout)

    def test_config_cases_without_api_suite_warn(self):
        self.write([self.case('T1', '重新进入页面后开关仍为开启', action='保存开关配置')])
        result = run(self.root)
        self.assertEqual(result.returncode, 0)
        self.assertIn('suite=api', result.stdout)

    def test_missing_cases_file_blocks(self):
        result = run(self.root)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
