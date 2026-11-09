import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/render_review_table.py'


class ReviewTableTest(unittest.TestCase):
    def test_review_workbook_and_internal_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / 'cases'
            presentation = cases / 'presentation/1'
            presentation.mkdir(parents=True)
            case = {'caseId': 'C1', 'scenarioIds': ['S'], 'suite': 'ui', 'priority': 'P1',
                    'automationRef': 'spec', 'fixtures': ['已登录'],
                    'steps': [{'id': 'S1', 'action': '打开页面'}],
                    'assertions': [{'stepId': 'S1', 'expected': '显示成功'}]}
            data = {'cases.json': [case], 'scenarios.json': [{'scenarioId': 'S', 'acIds': ['A']}],
                    'acs.json': [{'acId': 'A'}], 'impact-analysis.json': {'obligations': [{'text': '原始影响'}]}}
            for name, value in data.items():
                (cases / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            content = {'title': '评审', 'cases': [
                {'caseId': 'C1', 'group': '审批', 'title': '提交', 'fixtures': ['已登录'],
                 'steps': ['打开页面'], 'expected': ['显示成功']}],
                'notes': [{'category': '审批规则', 'question': '再次提交使用哪个版本？',
                           'action': '请产品确认', 'sourceRefs': ['impact-analysis.json:obligations[0]']}]}
            file = presentation / 'content.json'
            file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
            subprocess.run([sys.executable, str(SCRIPT), str(root)], check=True, capture_output=True)
            workbook = load_workbook(cases / 'cases.xlsx')
            self.assertEqual(workbook.sheetnames, ['用例主表', '待确认事项'])
            self.assertEqual([c.value for c in workbook['待确认事项'][1]], ['类别', '待确认内容', '建议处理'])
            self.assertEqual(workbook['待确认事项']['B2'].value, '再次提交使用哪个版本？')
            self.assertEqual(workbook['用例主表']['D2'].value, '1. 打开页面')
            self.assertEqual(workbook['用例主表']['F2'].value, '未测试')
            self.assertEqual(workbook['用例主表']['G2'].value, 'C1')
            self.assertTrue(workbook['用例主表'].column_dimensions['G'].hidden)
            self.assertEqual(len(workbook['用例主表'].data_validations.dataValidation), 1)
            mapping = json.loads((presentation / 'mapping.json').read_text(encoding='utf-8'))
            self.assertEqual(mapping['cases'][0]['tracking']['file'], 'cases.md')
            self.assertEqual(mapping['notes'][0]['sourceRefs'], content['notes'][0]['sourceRefs'])
            md = (cases / 'cases.md').read_text(encoding='utf-8')
            self.assertIn('原始影响', md)
            self.assertIn('"automationRef": "spec"', md)

            reviewed = root / 'reviewed.xlsx'
            workbook['用例主表']['D2'] = '1. 打开新页面'
            workbook.save(reviewed)
            report = root / 'changes.json'
            command = [sys.executable, str(SCRIPT.with_name('reconcile_review.py')),
                       str(cases / 'cases.xlsx'), str(reviewed), str(presentation / 'mapping.json'), str(report)]
            subprocess.run(command, check=True, capture_output=True)
            changes = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(changes['changes'][0]['status'], 'modified')
            self.assertEqual(changes['changes'][0]['changedColumns'], ['操作步骤'])
            workbook['用例主表']['G2'] = None
            workbook.save(reviewed)
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['identityStatus'], 'blocked')

            content['notes'] = [{'tag': 'Q1', 'text': '不完整'}]
            file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('sourceRefs', result.stderr)

    def test_reconcile_moves_edits_deletes_and_ambiguous_rows(self):
        spec = importlib.util.spec_from_file_location('reconcile', SCRIPT.with_name('reconcile_review.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = [{'row': 2, 'caseId': 'C1', 'values': ['组', '标题1', '条件', '步骤', '结果']},
               {'row': 3, 'caseId': 'C2', 'values': ['组', '标题2', '条件', '步骤', '结果']}]
        mapping = {'cases': [{'caseId': c['caseId'], 'mainSheet': {'row': c['row']}} for c in old]}
        reviewed = [{'row': 2, 'caseId': 'C2', 'values': old[1]['values']},
                    {'row': 3, 'caseId': 'C1', 'values': ['组', '新标题', '条件', '步骤', '结果']}]
        result = module.compare(old, reviewed, mapping)
        self.assertEqual([r['status'] for r in result], ['unchanged', 'modified'])
        self.assertEqual(result[1]['changedColumns'], ['用例标题'])
        self.assertEqual(module.compare(old, reviewed[:1], mapping)[1]['status'], 'deleted')
        reviewed.append({'row': 4, 'caseId': '', 'values': ['组', '新增', '', '', '']})
        self.assertEqual(module.compare(old, reviewed, mapping)[2]['status'], 'added')
        ambiguous = module.compare(old, reviewed[1:], mapping)
        self.assertEqual([r['status'] for r in ambiguous], ['modified', 'unidentified', 'missing_unresolved'])
        reviewed[2]['caseId'] = 'C1'
        self.assertTrue(any(r['status'] == 'identity_conflict' for r in module.compare(old, reviewed, mapping)))


if __name__ == '__main__':
    unittest.main()
