import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from phase_gate import Gate, file_hash, semantic_input_hash


class SemanticReviewTests(unittest.TestCase):
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
        self.review = {'model': 'fixture', 'questionSetVersion': definitions['questionSetVersion'],
                       'inputHash': semantic_input_hash(self.root)}
        for group, ident in [('sources', 'P1'), ('impacts', 'I1'), ('cases', 'C1'),
                             ('realization', 'C1'), ('ruleSupport', 'C1')]:
            self.review[group] = {ident: copy.deepcopy(verdict)}

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

    def test_wrong_question_version_and_missing_evidence_block(self):
        self.review['questionSetVersion'] = 'old'
        self.review['cases']['C1']['evidenceRef'] = 'missing.json'
        self.assertTrue(any('题集' in gap for gap in self.gaps()))
        self.assertTrue(any('回执' in gap for gap in self.gaps()))


if __name__ == '__main__':
    unittest.main()
