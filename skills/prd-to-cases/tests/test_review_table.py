import json
import importlib.util
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/render_review_table.py'
SKILLS = SCRIPT.parents[2]


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
            check = [sys.executable, str(SCRIPT), str(root), '--check-only',
                     '--out-dir', str(root / 'unused'), '--case-ids', 'C1']
            result = subprocess.run(check, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / 'unused').exists())
            self.assertFalse((cases / 'cases.xlsx').exists())
            self.assertFalse((presentation / 'mapping.json').exists())
            row = content['cases'][0]
            for lines, expected_code in ((26, 0), (27, 2)):
                row['steps'] = ['打开页面' + '\n继续操作' * (lines - 1)]
                file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
                result = subprocess.run(check, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected_code, result.stderr)
                if expected_code:
                    self.assertIn('C1 操作步骤', result.stderr)
            row['steps'] = ['打开页面']
            content['padding'] = '中' * 9000
            file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(check, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('25 KiB', result.stderr)
            del content['padding']
            file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(check + ['C2'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            result = subprocess.run(check + ['C1'] * 8, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('最多 8 条', result.stderr)
            result = subprocess.run([x for x in check if x != '--check-only'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
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

    def test_presentation_flags_internal_details_without_blocking_api_cases(self):
        spec = importlib.util.spec_from_file_location('render', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = {'cases': [{'caseId': 'C1', 'suite': 'ui'}, {'caseId': 'C2', 'suite': 'api'}]}
        content = {'cases': [
            {'caseId': 'C1', 'group': '配置', 'title': '保存',
             'fixtures': ['FA-ADMIN 新建 AR-930a49fc-C-CFG-01-model'],
             'steps': ['GET /bpm/model/get 读取 metaInfo'], 'expected': ['状态不变']},
            {'caseId': 'C2', 'group': '接口', 'title': '鉴权', 'fixtures': [],
             'steps': ['GET /bpm/model/get'], 'expected': ['HTTP 403']}],
            'notes': [{'category': '规则', 'question': 'C-CFG-02 由哪个版本生效？', 'action': '请产品确认'}]}
        issues = module.presentation_issues(content, source)
        self.assertTrue(any('FA-ADMIN' in s for s in issues))
        self.assertTrue(any('AR-930a49fc' in s for s in issues))
        self.assertTrue(any('GET /bpm/model/get' in s for s in issues))
        self.assertTrue(any('notes[1]' in s for s in issues))
        self.assertFalse(any('C2 ' in s for s in issues))
        content['cases'][0].update(fixtures=['管理员已登录'], steps=['进入模型设置，查看开关'])
        content['notes'][0]['question'] = '再次提交时使用哪个版本？'
        self.assertEqual(module.presentation_issues(content, source), [])
        content['cases'][0]['steps'] = ['GET /bpm/model/get']
        content['cases'][0]['technicalExceptions'] = [{'field': 'steps', 'index': 1,
            'token': 'GET /bpm/model/get', 'reason': '接口为测试对象', 'evidenceRef': 'cases.json:C1'}]
        self.assertEqual(module.presentation_issues(content, source), [])
        content['cases'][0]['technicalExceptions'][0]['evidenceRef'] = ''
        self.assertTrue(module.presentation_issues(content, source))

    def test_presentation_gate_binds_prompt_artifacts_and_quality_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'cases/presentation/1'
            base.mkdir(parents=True)
            def write(ref, data):
                path = root / ref
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data, encoding='utf-8')
            def sha(ref):
                return hashlib.sha256((root / ref).read_bytes()).hexdigest()
            write('manifest.json', json.dumps({'presentation': {'round': 1}}))
            files = {'cases.json': [{'caseId': 'C1', 'suite': 'ui'}],
                     'scenarios.json': [], 'acs.json': [], 'impact-analysis.json': {}, 'semantic-review.json': {}}
            for name, data in files.items():
                write('cases/' + name, json.dumps(data))
            content = {'cases': [{'caseId': 'C1', 'group': '配置', 'title': '保存',
                                  'fixtures': [], 'steps': ['打开页面'], 'expected': ['保存成功']}], 'notes': []}
            write('cases/presentation/1/content.json', json.dumps(content, ensure_ascii=False))
            for name in ('cases.xlsx', 'cases.md', 'mapping.json'):
                write('cases/presentation/1/' + name, 'fixture')
            write('cases/presentation/1/jev.json', 'evidence')
            write('cases/presentation/1/quality-review.json', json.dumps({'status': 'PASSED',
                'contentHash': sha('cases/presentation/1/content.json'),
                'evidence': [{'ref': 'cases/presentation/1/jev.json', 'sha256': sha('cases/presentation/1/jev.json')}]}))
            refs = ['cases/' + name for name in files] + ['cases/presentation/1/' + name for name in
                    ('content.json', 'cases.xlsx', 'cases.md', 'mapping.json', 'quality-review.json')]
            contract = (SKILLS / 'prd-to-cases/references/review-table.md').read_text(encoding='utf-8')
            prompt = contract.split('## 固定提示词（原样派发）\n', 1)[1].split('\n## 主 Agent 验收', 1)[0].strip()
            receipt = {'agentId': 'agent', 'model': 'model', 'effort': 'high', 'isolation': 'fresh',
                       'promptHash': hashlib.sha256(prompt.encode()).hexdigest(),
                       'artifactHashes': {ref: sha(ref) for ref in refs}}
            write('cases/presentation/1/receipt.json', json.dumps(receipt))
            command = [sys.executable, str(SKILLS / 'argus/scripts/phase_gate.py'), str(root), '--phase', 'presentation']
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            write('cases/presentation/1/jev.json', 'modified')
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)



if __name__ == '__main__':
    unittest.main()
