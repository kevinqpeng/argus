import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/semantic_batch.py'


class SemanticBatchTest(unittest.TestCase):
    def test_prepare_and_reject_incomplete_review(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'cases').mkdir()
            def write(name, value):
                (root / name).write_text(json.dumps(value), encoding='utf-8')
            write('cases/impact-analysis.json', {'sources': [], 'checks': []})
            write('cases/acs.json', [])
            write('cases/scenarios.json', [])
            steps = [{'id': 's', 'action': 'submit'}]
            assertions = [{'stepId': 's', 'expected': 'done'}]
            write('cases/cases.json', [{'caseId': 'C', 'fixtures': [], 'steps': steps, 'assertions': assertions}])
            write('subjects.json', [{'group': 'cases', 'id': 'C', 'subject': 'C',
                                     'evidence': {'acceptance': {'text': 'criterion'}, 'fixtures': [],
                                                  'steps': steps, 'assertions': assertions}}])
            result = subprocess.run([sys.executable, str(SCRIPT), str(root), 'prepare',
                                     str(root / 'subjects.json')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('semantic subjects incomplete', result.stderr)
            self.assertFalse((root / 'cases/semantic-review.json').exists())

    def test_real_receipts_are_bound_to_requests_and_low_score_is_not_accepted(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import semantic_batch as sb
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'cases').mkdir()
            def write(name, value):
                (root / name).write_text(json.dumps(value), encoding='utf-8')
            steps = [{'id': 's', 'action': 'submit'}]
            assertions = [{'stepId': 's', 'expected': 'done'}]
            write('cases/impact-analysis.json', {'sources': [], 'checks': []})
            write('cases/acs.json', [])
            write('cases/scenarios.json', [])
            write('cases/cases.json', [{'caseId': 'C', 'steps': steps, 'assertions': assertions}])
            evidence = {'acceptance': {'text': 'done'}, 'fixtures': [], 'steps': steps, 'assertions': assertions}
            write('subjects.json', [{'group': g, 'id': 'C', 'subject': 'C', 'evidence': evidence}
                                    for g in ('cases', 'realization', 'ruleSupport')])
            sb.prepare(root, root / 'subjects.json')
            directory = root / 'cases/semantic-batch'
            payload = json.loads((directory / '0001.input.json').read_text())
            request, _ = sb.jev.build_request(payload)
            raw = {'model': 'fixture', 'answers': {k: {'type': 'noul', 'noul': 0.4}
                                                   for k in request['questions']}}
            write('cases/semantic-batch/0001.raw.json', raw)
            sb.request(root, '0001', record=True)
            result = json.loads((directory / '0001.out.json').read_text())
            sb.assemble(root)
            review = json.loads((root / 'cases/semantic-review.json').read_text())
            self.assertEqual(review['cases']['C']['final'], 0.4)
            self.assertNotIn('acceptedReason', review['cases']['C'])
            (root / 'cases/semantic-review.json').unlink()
            result['answers']['q000001']['noul'] = 0.99
            write('cases/semantic-batch/0001.out.json', result)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                sb.assemble(root)


if __name__ == '__main__':
    unittest.main()
