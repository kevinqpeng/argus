import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/notes_check.py'


def run(root: Path, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), str(root), *extra], capture_output=True, text=True)


class NotesCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'cases').mkdir()
        (self.root / 'cases/cases.json').write_text(
            json.dumps([{'caseId': 'TC-1'}, {'caseId': 'TC-2'}]), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, notes):
        (self.root / 'cases/notes.json').write_text(
            json.dumps({'schemaVersion': 1, 'notes': notes}, ensure_ascii=False), encoding='utf-8')

    base = {'category': '驳回记录', 'question': '记录如何展示？', 'action': '请产品确认',
            'sourceRefs': ['cases/generation/G4/2/notes.md']}

    def test_minimal_note_needs_no_classification_or_repeated_refs(self):
        self.write([{'question': '谁处理？', 'evidence': '共享转办契约', 'impact': '决定待办归属'}])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_note_keeps_evidence_and_effect(self):
        self.write([{'question': '谁处理？'}])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('已查证据', result.stdout)
        self.assertIn('影响用例', result.stdout)

    def test_blank_evidence_is_not_content(self):
        self.write([{'conclusion': '沿用原行为', 'evidence': ['  '], 'impact': '断言保持不变'}])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('已查证据', result.stdout)

    def test_resolved_note_passes_and_counts(self):
        self.write([dict(self.base, disposition='resolved_by_code',
                         searchedRefs=['bpm/src/BpmCommentTypeEnum.java:12-20'],
                         evidenceRef='bpm/src/BpmCommentTypeEnum.java:12-20',
                         conclusion='流转记录沿用既有驳回动作名称，不新增独立枚举值。')])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('"resolved_by_code": 1', result.stdout)

    def test_unknown_impact_case_blocks(self):
        self.write([dict(self.base, disposition='decision', searchedRefs=['检索无命中：驳回方式'],
                         impactCaseIds=['TC-404'], blocking=True)])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('TC-404', result.stdout)

    def test_short_basis_needs_no_falsifier_or_minimum_length(self):
        self.write([dict(self.base, disposition='assumption', basis='短')])
        self.assertEqual(run(self.root).returncode, 0)

    def test_legacy_run_only_warns(self):
        (self.root / 'manifest.json').write_text(
            json.dumps({'presentation': {'round': '2'}}), encoding='utf-8')
        round_dir = self.root / 'cases/presentation/2'
        round_dir.mkdir(parents=True)
        (round_dir / 'content.json').write_text(json.dumps({'notes': [self.base]}, ensure_ascii=False),
                                                encoding='utf-8')
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('旧契约', result.stdout)
        strict = run(self.root, '--strict')
        self.assertEqual(strict.returncode, 0)


if __name__ == '__main__':
    unittest.main()
