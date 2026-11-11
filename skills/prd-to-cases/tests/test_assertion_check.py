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

    def write_impact(self, obligations, ac_ids=('AC-1',)):
        (self.root / 'cases/scenarios.json').write_text(json.dumps(
            [{'scenarioId': 'SCN-1', 'acIds': list(ac_ids)}], ensure_ascii=False), encoding='utf-8')
        (self.root / 'cases/impact-analysis.json').write_text(json.dumps(
            {'obligations': obligations}, ensure_ascii=False), encoding='utf-8')

    @staticmethod
    def case(case_id, expected, suite='ui', action='审批人驳回', scenario_ids=('SCN-1',)):
        return {'caseId': case_id, 'suite': suite, 'title': case_id, 'scenarioIds': list(scenario_ids),
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

    def test_path_assertion_with_only_a_pronoun_blocks(self):
        self.write([self.case('T1', '发起人再次提交后从流程起点重新审批')])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('代词', result.stdout)

    def test_path_assertion_with_node_chain_passes(self):
        self.write([self.case('T1', '再次提交后新一轮审批依次为 A、C、D，节点清单中没有 B')])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_path_assertion_without_a_node_list_warns(self):
        self.write([self.case('T1', 'A 节点审批人待办列表出现该申请的新一轮待办，节点为 A')])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('节点清单', result.stdout)

    def test_record_increment_without_a_count_blocks(self):
        self.write([self.case('T1', '流转记录新增一条驳回记录')])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('条数或增量', result.stdout)

    def test_declared_start_must_appear_in_the_assertions(self):
        self.write([self.case('T1', '再次提交后待办出现在 A 节点')])
        self.write_impact([{'obligationId': 'OBL-1', 'acIds': ['AC-1'],
                            'text': '分支变化后从新路径起点重新审批',
                            'anchors': {'start': 'B0'}}])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('B0', result.stdout)

    def test_path_obligation_without_an_anchor_blocks(self):
        self.write([self.case('T1', '再次提交后新一轮审批依次为 A、C、D')])
        self.write_impact([{'obligationId': 'OBL-1', 'acIds': ['AC-1'],
                            'text': '驳回后从当前节点继续审批，节点不存在时重新开始'}])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('节点锚点', result.stdout)

    def test_path_obligation_waiver_passes(self):
        self.write([self.case('T1', '再次提交后新一轮审批依次为 A、C、D')])
        self.write_impact([{'obligationId': 'OBL-1', 'acIds': ['AC-1'],
                            'text': '驳回后从当前节点继续审批，节点不存在时重新开始',
                            'anchors': {'waiver': 'PRD 只说流程起点，没有给节点身份，由合同审批模型节点清单 A/B/C/D 确定'}}])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == '__main__':
    unittest.main()
