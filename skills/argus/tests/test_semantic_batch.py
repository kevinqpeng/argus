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
            review['cases']['C']['acceptedReason'] = 'checked against the source'
            write('cases/semantic-review.json', review)
            sb.assemble(root)
            kept = json.loads((root / 'cases/semantic-review.json').read_text())
            self.assertEqual(kept['cases']['C']['acceptedReason'], 'checked against the source')
            key = next(iter(result['answers']))
            result['answers'][key]['noul'] = 0.99
            write('cases/semantic-batch/0001.out.json', result)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                sb.assemble(root)
            listed = sb.report(root)
            self.assertEqual(listed['status'], 'REPORTED')
            self.assertTrue(any(item['final'] == 0.4 for item in listed['below']))
            with self.assertRaisesRegex(ValueError, 'already recorded'):
                sb.prepare(root, root / 'subjects.json')


    def test_prepare_splits_on_the_full_request_body(self):
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
            subjects = [{'group': g, 'id': 'C', 'subject': 'C', 'evidence': evidence}
                        for g in ('cases', 'realization', 'ruleSupport')]
            write('subjects.json', subjects)
            one = {'questions': {'q000001': {'questionId': 'mapping', 'subject': 'C'}},
                   'state': {'subjects': {'q000001': {'evidence': evidence,
                                                     'evidenceHash': sb.jev.evidence_hash(evidence)}}}}
            payload, _ = sb.jev.build_request(one)
            body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
            contract = sb.jev.DEFINITIONS['textStateContract']
            original = contract['maxBytes']
            contract['maxBytes'] = len(body) + 1
            try:
                sb.prepare(root, root / 'subjects.json')
            finally:
                contract['maxBytes'] = original
            plan = json.loads((root / 'cases/semantic-batch/plan.json').read_text())
            self.assertEqual(len(plan['batches']), len(subjects))

    def test_reprepare_reuses_unchanged_batch_and_subjects_are_judgeable(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import semantic_batch as sb
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'cases').mkdir()
            steps = [{'id': 's', 'action': 'submit'}]
            assertions = [{'stepId': 's', 'expected': 'done'}]

            def write(name, value):
                (root / name).write_text(json.dumps(value), encoding='utf-8')

            write('cases/impact-analysis.json', {
                'sources': [{'sourceId': 'P', 'kind': 'prd', 'sourceContent': 'rule: mail arrives'}],
                'sourceUnits': [{'unitId': 'U', 'sourceIds': ['P'], 'sourceExcerpt': 'mail arrives', 'obligationIds': ['O']}],
                'objects': [{'objectId': 'OBJ', 'description': 'mail'}],
                'checks': [{'checkId': 'I', 'objectId': 'OBJ', 'dimension': 'data', 'reason': 'path', 'evidenceRef': 'e'}],
                'obligations': [{'obligationId': 'O', 'sourceIds': ['P'], 'acIds': ['A'], 'text': 'mail arrives', 'expected': 'inbox'}],
            })
            write('cases/acs.json', [{'acId': 'A', 'text': 'mail arrives'}])
            write('cases/scenarios.json', [{'scenarioId': 'S', 'acIds': ['A'], 'expected': 'inbox'}])
            write('cases/cases.json', [
                {'caseId': 'A', 'scenarioIds': ['S'], 'fixtures': [], 'steps': steps, 'assertions': assertions},
                {'caseId': 'B', 'scenarioIds': ['S'], 'fixtures': [], 'steps': steps, 'assertions': assertions},
            ])
            subjects = root / 'subjects.json'
            sb.write_subjects(root, subjects)
            largest = 0
            for item in json.loads(subjects.read_text()):
                payload, _ = sb.jev.build_request({
                    'questions': {'only': {'questionId': sb.GROUPS[item['group']], 'subject': item['subject']}},
                    'state': {'subjects': {'only': {'evidence': item['evidence'], 'evidenceHash': sb.jev.evidence_hash(item['evidence'])}}}})
                largest = max(largest, len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()))
            contract = sb.jev.DEFINITIONS['textStateContract']
            original = contract['maxBytes']
            contract['maxBytes'] = largest + 512
            try:
                sb.prepare(root, subjects)
                directory = root / 'cases/semantic-batch'
                plan = json.loads((directory / 'plan.json').read_text())
                self.assertGreater(len(plan['batches']), 1)
                for batch in plan['batches']:
                    request, _ = sb.jev.build_request(json.loads((directory / f"{batch['name']}.input.json").read_text()))
                    raw = {'model': 'fixture', 'answers': {key: {'type': 'noul', 'noul': 0.9} for key in request['questions']}}
                    (directory / f"{batch['name']}.raw.json").write_text(json.dumps(raw), encoding='utf-8')
                    sb.request(root, batch['name'], record=True)
                kept_name = next(batch['name'] for batch in plan['batches']
                                 if all(item['id'] == 'A' for item in batch['mapping'].values()))
                kept_raw = (directory / f'{kept_name}.raw.json').read_bytes()
                cases = json.loads((root / 'cases/cases.json').read_text())
                cases[1]['assertions'] = [{'stepId': 's', 'expected': 'changed'}]
                write('cases/cases.json', cases)
                with self.assertRaisesRegex(ValueError, 'subjects stale'):
                    sb.prepare(root, subjects)
                self.assertEqual((directory / f'{kept_name}.raw.json').read_bytes(), kept_raw)
                sb.write_subjects(root, subjects)
                sb.prepare(root, subjects)
            finally:
                contract['maxBytes'] = original
            plan = json.loads((directory / 'plan.json').read_text())
            reused = [batch for batch in plan['batches'] if (directory / f"{batch['name']}.raw.json").is_file()]
            rewritten = [batch for batch in plan['batches'] if not (directory / f"{batch['name']}.raw.json").is_file()]
            self.assertTrue(reused)
            self.assertTrue(rewritten)
            self.assertTrue(all(all(item['id'] != 'B' for item in batch['mapping'].values()) for batch in reused))
            self.assertTrue(any(any(item['id'] == 'B' for item in batch['mapping'].values()) for batch in rewritten))


if __name__ == '__main__':
    unittest.main()
