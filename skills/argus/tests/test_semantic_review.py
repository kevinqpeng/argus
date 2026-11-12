import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from phase_gate import Gate, file_hash, semantic_contract, semantic_input_hash, semantic_subject_hashes


class SemanticReviewTests(unittest.TestCase):
    def test_source_content_reads_verified_snapshot_and_rejects_drift(self):
        from phase_gate import source_content
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = root / 'cases/sources/prd.md'
            path.parent.mkdir(parents=True)
            path.write_text('原始规则', encoding='utf-8')
            source = {'sourceRef': 'run://cases/sources/prd.md',
                      'sourceHash': sha256(path.read_bytes()).hexdigest(),
                      'sourceContent': '旧副本'}
            self.assertEqual(source_content(root, source), '原始规则')
            path.write_text('变化规则', encoding='utf-8')
            self.assertEqual(source_content(root, source), '')
            self.assertEqual(source_content(root, {'sourceContent': '旧快照'}), '旧快照')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'cases').mkdir()
        self.cases = [{'caseId': 'C1', 'fixtures': [], 'reviewStatus': 'draft',
                       'steps': [{'id': 'submit', 'action': 'Submit'}],
                       'assertions': [{'stepId': 'submit', 'expected': 'Mail arrives'}]}]
        self.write('cases/cases.json', self.cases)
        self.write('cases/impact-analysis.json', {'sources': [{'sourceId': 'P1', 'kind': 'prd'}],
                                                'checks': [{'checkId': 'I1'}]})
        self.write('cases/acs.json', [{'acId': 'A1', 'text': 'Mail arrives'}])
        self.write('cases/scenarios.json', [{'scenarioId': 'S1', 'acIds': ['A1']}])
        self.write('evidence.json', {'model': 'fixture', 'noul': 0.9})
        definitions = json.loads((Path(__file__).resolve().parents[2] /
                                 'decision-router/references/questions.json').read_text())
        verdict = {'final': 0.9, 'evidenceRef': 'evidence.json',
                   'evidenceHash': file_hash(self.root / 'evidence.json')}
        hashes = semantic_subject_hashes(self.root)
        self.review = {'model': 'fixture', 'questionSetVersion': definitions['questionSetVersion'],
                       'inputHash': semantic_input_hash(self.root)}
        for group, ident in [('sources', 'P1'), ('impacts', 'I1'), ('cases', 'C1'),
                             ('realization', 'C1'), ('ruleSupport', 'C1')]:
            item = copy.deepcopy(verdict)
            item['subjectHash'] = hashes[group][ident]
            self.review[group] = {ident: item}

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def gaps(self):
        self.write('cases/semantic-review.json', self.review)
        gate = Gate(self.root)
        gate.semantic()
        return gate.gaps

    def test_draft_design_needs_no_runner(self):
        self.assertEqual(self.gaps(), [])
        self.cases[0].update(reviewStatus='approved', semanticReview='approved',
                             automationRef='mail.spec.js::send')
        self.write('cases/cases.json', self.cases)
        self.assertEqual(self.gaps(), [])

    def test_high_mapping_score_cannot_replace_other_reviews(self):
        for group in ('sources', 'impacts', 'realization', 'ruleSupport'):
            original = self.review.pop(group)
            self.assertTrue(any(group in gap for gap in self.gaps()))
            self.review[group] = original

    def test_changed_input_and_receipt_are_rejected(self):
        self.cases[0]['fixtures'] = ['Changed recipient']
        self.write('cases/cases.json', self.cases)
        self.assertTrue(any('输入' in gap for gap in self.gaps()))
        self.review['inputHash'] = semantic_input_hash(self.root)
        self.write('evidence.json', {'changed': True})
        self.assertTrue(any('回执' in gap for gap in self.gaps()))

    def test_non_probabilities_or_self_lowered_threshold_cannot_pass(self):
        self.review['thresholds'] = {'pass': 0}
        for value in (None, True, '0.99', float('nan'), 2, 0.5):
            with self.subTest(value=value):
                self.review['realization']['C1']['final'] = value
                self.assertTrue(self.gaps())

    def test_unrelated_impact_metadata_does_not_invalidate_review(self):
        before = semantic_subject_hashes(self.root)
        impact = json.loads((self.root / 'cases/impact-analysis.json').read_text())
        impact['notes'] = 'observation summary'
        impact['sources'].append({'sourceId': 'OBS', 'kind': 'observation', 'summary': 'test env'})
        self.write('cases/impact-analysis.json', impact)
        self.assertEqual(semantic_subject_hashes(self.root), before)
        self.assertEqual(self.gaps(), [])

    def test_one_case_change_leaves_other_subjects(self):
        before = semantic_subject_hashes(self.root)
        self.cases.append({'caseId': 'C2', 'fixtures': [], 'steps': self.cases[0]['steps'],
                           'assertions': self.cases[0]['assertions']})
        self.write('cases/cases.json', self.cases)
        after = semantic_subject_hashes(self.root)
        self.assertEqual(after['cases']['C1'], before['cases']['C1'])
        self.assertEqual(after['sources'], before['sources'])
        self.assertNotEqual(semantic_input_hash(self.root), self.review['inputHash'])

    def test_contract_exposes_budget_without_reading_source(self):
        definitions = json.loads((Path(__file__).resolve().parents[2] /
                                 'decision-router/references/questions.json').read_text())
        contract = semantic_contract()
        self.assertEqual(contract['maxBytes'], definitions['textStateContract']['maxBytes'])
        self.assertEqual(set(contract['groups']), {'sources', 'impacts', 'cases', 'realization', 'ruleSupport'})
        self.assertIn('subjectHash', contract['verdict'])
        self.assertIn('语义审查', contract['order'])
        self.assertIn('人工评审', contract['order'])

    def test_wrong_question_version_and_missing_evidence_block(self):
        self.review['questionSetVersion'] = 'old'
        self.review['cases']['C1']['evidenceRef'] = 'missing.json'
        self.assertTrue(any('题集' in gap for gap in self.gaps()))
        self.assertTrue(any('回执' in gap for gap in self.gaps()))


if __name__ == '__main__':
    unittest.main()
