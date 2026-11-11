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
    def test_partition_input_preserves_evidence_and_rejects_invalid_scope(self):
        spec = importlib.util.spec_from_file_location('render', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'cases').mkdir()
            cases = [{'caseId': f'C{i}', 'scenarioIds': [f'S{i}'],
                      'fixtures': ['边界 0/1/多'], 'steps': [{'id': 's', 'action': '并发提交'}],
                      'assertions': [{'stepId': 's', 'expected': '仅一次副作用'}]}
                     for i in range(1, 4)]
            files = {'cases.json': cases,
                     'scenarios.json': [{'scenarioId': f'S{i}', 'acIds': [f'A{i}']} for i in range(1, 4)],
                     'acs.json': [{'acId': f'A{i}', 'text': '完整原文'} for i in range(1, 4)],
                     'impact-analysis.json': {'unknown': ['跨组规则待确认'], 'obligations': ['全部义务']},
                     'semantic-review.json': {'status': 'BLOCKED', 'reason': '完整保留，不代替门禁'}}
            for name, value in files.items():
                (root / 'cases' / name).write_text(json.dumps(value, ensure_ascii=False))
            command = [sys.executable, str(SCRIPT), str(root), '--print-input', '--case-ids']
            result = subprocess.run(command + ['C1', 'C3'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertEqual(packet['cases'], [cases[0], cases[2]])
            self.assertEqual(packet['acs'], [files['acs.json'][0], files['acs.json'][2]])
            self.assertEqual(packet['scenarios'], [files['scenarios.json'][0], files['scenarios.json'][2]])
            self.assertEqual(packet['impact'], files['impact-analysis.json'])
            self.assertEqual(packet['semantic'], files['semantic-review.json'])
            self.assertEqual(len(packet['caseIndex']), 3)
            for ref, digest in packet['sourceArtifacts'].items():
                self.assertEqual(digest, hashlib.sha256((root / ref).read_bytes()).hexdigest())
            self.assertEqual(sorted(p.name for p in (root / 'cases').iterdir()), sorted(files))
            for ids in (['C3', 'C1'], ['C1', 'C1'], ['unknown'], ['C1'] * 9, []):
                result = subprocess.run(command + ids, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
            source = module.load_run(root)
            del source['acs']['A1']
            with self.assertRaises(KeyError):
                module.presentation_input(source, ['C1'])
            (root / 'cases/semantic-review.json').unlink()
            result = subprocess.run(command + ['C1'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(json.loads(result.stdout)['semantic'])

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
                row['densityExceptions'] = {'steps': {'display': list(row['steps']),
                    'reason': '布局边界测试', 'sourceRefs': ['cases.json:C1.steps']}}
                file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
                result = subprocess.run(check, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected_code, result.stderr)
                if expected_code:
                    self.assertIn('C1 操作步骤', result.stderr)
            row['steps'] = ['打开页面']
            content['padding'] = '中' * 9000
            file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(check, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            del content['padding']
            file.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(check + ['C2'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            result = subprocess.run(check + ['C1'] * 8, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('caseId', result.stderr)
            result = subprocess.run([x for x in check if x != '--check-only'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            subprocess.run([sys.executable, str(SCRIPT), str(root)], check=True, capture_output=True)
            # 展示门禁与发布脚本要求本轮目录与 cases/ 的同一份字节，避免二次渲染漂移
            self.assertEqual((presentation / 'cases.xlsx').read_bytes(), (cases / 'cases.xlsx').read_bytes())
            self.assertEqual((presentation / 'cases.md').read_bytes(), (cases / 'cases.md').read_bytes())
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

    def test_presentation_hides_protocol_and_sample_ids_from_reviewers(self):
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
        self.assertTrue(any('C1 ' in s and 'GET /bpm/model/get' in s for s in issues))
        self.assertTrue(any('C2 ' in s and 'GET /bpm/model/get' in s for s in issues))
        self.assertTrue(any('notes[1]' in s for s in issues))
        content['cases'][0].update(fixtures=['管理员已登录'], steps=['进入模型设置，查看开关'])
        content['cases'][1]['steps'] = ['未登录时查看该模型']
        content['notes'][0]['question'] = '再次提交时使用哪个版本？'
        self.assertEqual(module.presentation_issues(content, source), [])
        content['cases'][0]['steps'] = ['使用 FA-ADMIN 查看开关']
        content['cases'][0]['technicalExceptions'] = [{'field': 'steps', 'index': 1,
            'token': 'FA-ADMIN', 'reason': '页面上原样展示该账号', 'evidenceRef': 'cases.json:C1'}]
        self.assertEqual(module.presentation_issues(content, source), [])
        content['cases'][0]['technicalExceptions'][0]['evidenceRef'] = ''
        self.assertTrue(module.presentation_issues(content, source))
        content['cases'][1]['steps'] = ['GET /bpm/model/get']
        content['cases'][1]['technicalExceptions'] = [{'field': 'steps', 'index': 1,
            'token': 'GET /bpm/model/get', 'reason': '接口为测试对象', 'evidenceRef': 'cases.json:C2'}]
        self.assertTrue(any('C2 ' in s and 'GET /bpm/model/get' in s
                            for s in module.presentation_issues(content, source)))
        content['cases'][1]['steps'] = [
            '提交申请 SUB-07',
            '调用时传入 {"id":"<taskId>","rejectMethod":"CONTINUE_FROM_CURRENT_NODE"}']
        leaked = module.presentation_issues(content, source)
        self.assertTrue(any('SUB-07' in s for s in leaked))
        self.assertTrue(any('{"id":' in s for s in leaked))
        self.assertTrue(any('CONTINUE_FROM_CURRENT_NODE' in s for s in leaked))
        self.assertTrue(any('taskId' in s or 'rejectMethod' in s for s in leaked))

    def test_review_density_rejects_walkthrough_but_keeps_any_one_approval_rule(self):
        spec = importlib.util.spec_from_file_location('render', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = {'cases': [{'caseId': 'C1', 'suite': 'ui'}]}
        row = {'caseId': 'C1', 'group': '多人审批人待办',
               'title': '多人节点继续审批：待办只到一名审批人，任一人通过即可',
               'fixtures': ['配置人已开启并发布目标流程的「允许驳回后继续审批」，节点 C 审批人为甲、乙且任一人通过即可',
                            '发起人已发起一张申请，审批人 A、B 已依次通过，待办停在节点 C 并由审批人甲持有'],
               'steps': ['审批人甲在「审批中心 > 我的待办」打开该申请的节点 C 待办，点击「驳回」，填写审批意见「请补充合同金额」，选择「驳回后从当前节点继续审批」并确定',
                         '发起人在「审批中心 > 我的发起」打开该申请详情，点击「再次提交」，仅把申请说明改为「已补充合同金额」后提交',
                         '分别查看审批人甲、审批人乙的待办中该申请在节点 C 的待办归属',
                         '本轮待办持有人打开该待办，填写审批意见「同意」并通过',
                         '再次查看审批人甲、审批人乙的待办与该申请的流转记录'],
               'expected': ['驳回后审批人甲的待办中不再出现该申请；流转记录新增驳回记录，动作为「驳回至发起人」与「驳回后从当前节点继续审批」，处理人为审批人甲',
                            '再次提交后仍停留在该申请详情，我的发起条数不变、未新建申请，该申请仍为审批中',
                            '节点 C 本轮只有一条待办，由审批人甲或审批人乙其中一人持有，不因甲是本次驳回人而固定给甲',
                            '持有人通过后其待办中不再出现该申请；流转记录在驳回记录之后追加节点 C 的「已通过」，处理人为该持有人',
                            '审批人甲、审批人乙均无该申请在节点 C 的待办，节点 C 本轮只有一条「已通过」记录；原 A 通过、B 通过、甲驳回记录仍在，处理人与意见保持原值']}
        issues = module.presentation_issues({'cases': [row]}, source)
        for field in ('steps', 'expected'):
            self.assertTrue(any(f'C1 {field}: 评审密度' in issue for issue in issues), issues)
        source['cases'][0].update(
            steps=[{'id': f'S{i}', 'action': action} for i, action in enumerate(row['steps'], 1)],
            assertions=[{'stepId': f'S{i}', 'expected': result}
                        for i, result in enumerate(row['expected'], 1)])
        source['cases'][0]['assertions'][2]['expected'] = '甲、乙均收到节点 C 待办，任一人通过即可'
        row.update(title='甲、乙各有待办，任一人通过即可',
                   fixtures=['开关已开启并发布；节点 C 配置甲、乙，任一人通过即可',
                             'A、B 已通过；节点 C 待办由甲持有'],
                   steps=['甲选择「从当前节点继续审批」驳回',
                          '发起人仅修改申请说明后再次提交',
                          '查询甲、乙待办，由本轮持有人审批通过',
                          '核对甲、乙待办与流转记录'],
                   expected=['驳回后甲无待办，追加甲的驳回记录',
                             '再次提交仍为原申请，状态审批中、我的发起条数不变',
                             '节点 C 的待办分别发给甲、乙，任一人通过即可',
                             '通过后甲、乙均无节点 C 待办，仅追加本轮持有人的一条通过记录',
                             '原 A、B 通过及甲驳回记录保留，处理人与意见不变'])
        self.assertEqual(module.presentation_issues({'cases': [row]}, source), [])
        row['stepSources'] = [['S1'], ['S2'], ['S3', 'S4'], ['S5']]
        errors = []
        units = module.normalize({'cases': [row]}, source, errors)
        self.assertEqual(errors, [])
        self.assertEqual(units[0]['expectedSources'], [[i] for i in range(5)])
        self.assertEqual(len(source['cases'][0]['steps']), 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.xlsx'
            wb = module.Workbook()
            rows = module.render_main(wb, units, source)
            mapping = module.build_mapping(units, source, rows, Path(tmp))
            wb.save(path)
            rendered = load_workbook(path)
            sheet = rendered['用例主表']
            self.assertEqual(sheet['D2'].value, module.numbered(row['steps']))
            self.assertEqual(sheet['E2'].value, module.numbered(row['expected']))
            self.assertIn('分别发给甲、乙', sheet['E2'].value)
            self.assertEqual(sheet['F2'].value, '未测试')
            self.assertEqual(mapping['cases'][0]['stepMapping'][2]['sourceStepIds'], ['S3', 'S4'])
            rendered.close()

    def test_density_exception_needs_specific_source_evidence(self):
        spec = importlib.util.spec_from_file_location('render', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = {'cases': [{'caseId': 'C1', 'suite': 'api'}]}
        row = {'caseId': 'C1', 'group': '并发', 'title': '并发重试', 'fixtures': [],
               'steps': ['三名审批人按固定时序重试；' + '并发时序边界' * 50], 'expected': []}
        self.assertTrue(module.presentation_issues({'cases': [row]}, source))
        row['densityExceptions'] = {'steps': {'reason': '必要步骤', 'sourceRefs': []}}
        self.assertTrue(module.presentation_issues({'cases': [row]}, source))
        row['densityExceptions']['steps'] = {
            'reason': '三个参与者的交错顺序和重试次数均为验收边界，不能合并',
            'sourceRefs': ['acs.json:A1.text'], 'display': list(row['steps'])}
        self.assertEqual(module.presentation_issues({'cases': [row]}, source), [])
        # Evidence belongs to the exact current text; an edit requires re-review.
        row['steps'][0] += '修改后的时序'
        self.assertTrue(module.presentation_issues({'cases': [row]}, source))

    def test_presentation_gate_binds_prompt_and_artifacts_without_readability_scores(self):
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
                     'scenarios.json': [], 'acs.json': [], 'impact-analysis.json': {}}
            for name, data in files.items():
                write('cases/' + name, json.dumps(data))
            content = {'cases': [{'caseId': 'C1', 'group': '配置', 'title': '保存',
                                  'fixtures': [], 'steps': ['打开页面'], 'expected': ['保存成功']}],
                       'notes': [{'category': '审批规则', 'question': '再次提交使用哪个版本？',
                                  'action': '请产品确认', 'sourceRefs': ['impact-analysis.json:obligations[0]']}]}
            write('cases/presentation/1/content.json', json.dumps(content, ensure_ascii=False))
            for name in ('cases.xlsx', 'cases.md', 'mapping.json'):
                write('cases/presentation/1/' + name, 'fixture')
            refs = ['cases/' + name for name in files] + ['cases/presentation/1/' + name for name in
                    ('content.json', 'cases.xlsx', 'cases.md', 'mapping.json')]
            contract = (SKILLS / 'prd-to-cases/references/review-table.md').read_text(encoding='utf-8')
            prompt = contract.split('## 固定提示词（原样派发）\n', 1)[1].split('\n## 主 Agent 验收', 1)[0].strip()
            receipt = {'agentId': 'agent', 'model': 'model', 'effort': 'high', 'isolation': 'fresh',
                       'promptHash': hashlib.sha256(prompt.encode()).hexdigest(),
                       'artifactHashes': {ref: sha(ref) for ref in refs}}
            write('cases/presentation/1/receipt.json', json.dumps(receipt))
            write('cases/cases.xlsx', 'workbook')
            write('deliverables/审批驳回后重新发起用例(V0.3).xlsx', 'workbook')
            filled = subprocess.run([sys.executable, str(SCRIPT), str(root), '--fill-receipt'], capture_output=True, text=True)
            self.assertEqual(filled.returncode, 0, filled.stderr)
            command = [sys.executable, str(SKILLS / 'argus/scripts/phase_gate.py'), str(root), '--phase', 'presentation']
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            content['cases'][0]['title'] = '修改后的标题'
            write('cases/presentation/1/content.json', json.dumps(content, ensure_ascii=False))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('哈希不一致', result.stdout)

    def test_more_than_one_slice_cannot_be_one_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'cases').mkdir(parents=True)
            cases = [{'caseId': f'C{index}'} for index in range(9)]
            (root / 'cases/cases.json').write_text(json.dumps(cases), encoding='utf-8')
            (root / 'manifest.json').write_text(json.dumps({'presentation': {'round': 1}}), encoding='utf-8')
            base = root / 'cases/presentation/1'
            base.mkdir(parents=True)
            receipt = {'agentId': 'agent', 'model': 'model', 'effort': 'high', 'isolation': 'fresh'}
            (base / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
            command = [sys.executable, str(SKILLS / 'argus/scripts/phase_gate.py'), str(root), '--phase', 'presentation']
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 2)
            self.assertIn('逐片派发', first.stdout)
            plan = subprocess.run([sys.executable, str(SCRIPT), str(root), '--plan-slices'],
                                  capture_output=True, text=True)
            self.assertEqual(plan.returncode, 0, plan.stderr)
            body = json.loads((base / 'plan.json').read_text(encoding='utf-8'))
            slices = body['slices']
            self.assertEqual([len(row['caseIds']) for row in slices], [8, 1])
            self.assertEqual(body['lineBudgets']['fixtures'], 6)
            self.assertEqual(body['cap'], 8)
            receipt['shards'] = [{'agentId': 'agent', 'caseIds': row['caseIds'], 'status': 'completed'}
                                 for row in slices]
            (base / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertNotIn('逐片派发', second.stdout)
            self.assertIn('缺失', second.stdout)
            receipt['shards'] = [{'agentId': f'agent-{i}', 'caseIds': ids, 'status': 'completed'}
                                 for i, ids in enumerate([slices[0]['caseIds'][:4], slices[0]['caseIds'][4:], slices[1]['caseIds']])]
            (base / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
            retry = subprocess.run(command, capture_output=True, text=True)
            self.assertNotIn('逐片派发', retry.stdout)
            receipt['shards'][1]['caseIds'].pop()
            (base / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
            incomplete = subprocess.run(command, capture_output=True, text=True)
            self.assertIn('逐片派发', incomplete.stdout)

    def test_long_cases_make_shorter_slices(self):
        spec = importlib.util.spec_from_file_location('render_slice', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        heavy = {'steps': [{'id': str(i), 'action': '步骤'} for i in range(6)],
                 'assertions': [{'stepId': str(i), 'expected': '结果'} for i in range(6)]}
        cases = [{'caseId': f'C{i}', **heavy} for i in range(9)]
        slices = module.slice_case_ids(cases)
        self.assertEqual([len(group) for group in slices], [4, 4, 1])
        self.assertEqual([item for group in slices for item in group], [f'C{i}' for i in range(9)])
        light = [{'caseId': f'C{i}'} for i in range(9)]
        self.assertEqual([len(group) for group in module.slice_case_ids(light)], [8, 1])


if __name__ == '__main__':
    unittest.main()
