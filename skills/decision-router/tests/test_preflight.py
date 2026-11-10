import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jev
from metrics import summarize


class PreflightTests(unittest.TestCase):
    def test_design_regression_examples_build_with_complete_evidence(self):
        examples = json.loads((Path(__file__).with_name('design_review_cases.json')).read_text())
        questions, subjects = {}, {}
        for example in examples:
            ident, evidence = example['id'], example['evidence']
            self.assertNotIn(ident, questions)
            questions[ident] = {'questionId': example['questionId'], 'subject': ident.replace('_', ' ')}
            subjects[ident] = {'evidence': evidence, 'evidenceHash': jev.evidence_hash(evidence)}
        payload, _ = jev.build_request({'questions': questions, 'state': {'subjects': subjects}})
        self.assertEqual(set(payload['questions']), set(questions))
        self.assertEqual(payload['state']['subjects'], subjects)

    def request(self):
        evidence = {"acceptance": "submission creates exactly one order", "fixtures": [],
                    "steps": [{"id": "submit", "action": "submit twice with same key"}],
                    "assertions": [{"stepId": "submit", "expected": "one persisted order"}]}
        return {"questions": {"C1": {"questionId": "mapping", "subject": "case C1"}},
                "state": {"subjects": {"C1": {"evidence": evidence, "evidenceHash": jev.evidence_hash(evidence)}}}}

    def invoke(self, data, failure=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.json"
            source.write_text(json.dumps(data))
            output = root / "normalized.json"
            argv = ["jev.py", str(source), "--raw", str(root / "raw.json"), "--output", str(output)]
            with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "fake"}), \
                 mock.patch("urllib.request.urlopen", side_effect=failure) as http, \
                 mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(jev.main(), 2)
            self.assertFalse(output.exists())
            metrics = summarize(output.with_suffix(".metrics.jsonl"))
            return http.call_count, json.loads(stderr.getvalue()), metrics

    def test_missing_fields_rejected_before_network(self):
        for field in ("acceptance", "steps", "assertions"):
            data = self.request()
            record = data["state"]["subjects"]["C1"]
            del record["evidence"][field]
            record["evidenceHash"] = jev.evidence_hash(record["evidence"])
            calls, error, metrics = self.invoke(data)
            self.assertEqual(calls, 0)
            self.assertFalse(error["retryable"])
            self.assertEqual(error["errorKind"], "input_contract")
            self.assertEqual(metrics["stages"]["jev"]["preflightRejected"], 1)
        for state in (None, "{truncated", {}, {"subjects": {}}):
            calls, _, _ = self.invoke({**self.request(), "state": state})
            self.assertEqual(calls, 0)

    def test_changed_content_rejected_without_network(self):
        data = self.request()
        data["state"]["subjects"]["C1"]["evidence"]["assertions"][0]["expected"] = "one"
        calls, error, _ = self.invoke(data)
        self.assertEqual(calls, 0)
        self.assertIn("truncated", error["reason"])

    def test_mapping_cannot_drop_fixtures_or_unasserted_business_steps(self):
        for change in ('fixtures', 'step'):
            data = self.request()
            record = data['state']['subjects']['C1']
            record['evidence']['fixtures'] = []
            if change == 'fixtures':
                del record['evidence']['fixtures']
            else:
                record['evidence']['steps'].append({'id': 'empty', 'action': 'Submit empty content'})
            record['evidenceHash'] = jev.evidence_hash(record['evidence'])
            calls, error, _ = self.invoke(data)
            self.assertEqual(calls, 0, error)

    def test_source_inventory_requires_original_source_not_only_generated_items(self):
        evidence = {'sourceUnits': [{'unitId': 'U1'}], 'obligations': []}
        data = {'questions': {'source': {'questionId': 'source_inventory', 'subject': 'source A'}},
                'state': {'subjects': {'source': {'evidence': evidence,
                          'evidenceHash': jev.evidence_hash(evidence)}}}}
        calls, error, _ = self.invoke(data)
        self.assertEqual(calls, 0)
        self.assertIn('sourceContent', error['reason'])

    def test_empty_inventory_is_evidence_for_semantic_review_not_local_pass(self):
        evidence = {'sourceContent': 'Deleted templates fall back to default.',
                    'sourceUnits': [], 'obligations': []}
        data = {'questions': {'source': {'questionId': 'source_inventory', 'subject': 'source A'}},
                'state': {'subjects': {'source': {'evidence': evidence,
                          'evidenceHash': jev.evidence_hash(evidence)}}}}
        payload, _ = jev.build_request(data)
        self.assertEqual(payload['state']['subjects']['source']['evidence'], evidence)
        self.assertIn('every distinct', payload['questions']['source']['instructions'])
        record = data['state']['subjects']['source']
        evidence['sourceUnits'] = [{'sourceExcerpt': 'Templates always fail.'}]
        record['evidenceHash'] = jev.evidence_hash(evidence)
        with self.assertRaisesRegex(ValueError, 'excerpt absent'):
            jev.build_request(data)
        evidence['sourceUnits'][0]['sourceExcerpt'] = evidence['sourceContent']
        record['evidenceHash'] = jev.evidence_hash(evidence)
        jev.build_request(data)

    def test_large_state_prunes_only_unused_records_and_preserves_complete_evidence(self):
        data = self.request()
        original = copy.deepcopy(data["state"]["subjects"]["C1"])
        data["state"]["diagnostics"] = "x" * 1_000_000
        data["state"]["subjects"]["unused"] = {"irrelevant": "y" * 1_000_000}
        payload, _ = jev.build_request(data)
        self.assertEqual(payload["state"], {"subjects": {"C1": original}})
        self.assertIn("Evidence: state.subjects", payload["questions"]["C1"]["instructions"])

    def test_oversized_required_evidence_fails_instead_of_truncating(self):
        data = self.request()
        record = data["state"]["subjects"]["C1"]
        record["evidence"]["acceptance"] = "x" * 300_000
        record["evidenceHash"] = jev.evidence_hash(record["evidence"])
        calls, error, _ = self.invoke(data)
        self.assertEqual(calls, 0)
        self.assertIn("never truncate", error["reason"])

    def test_full_request_body_reaches_the_byte_budget(self):
        data = self.request()
        payload, _ = jev.build_request(data)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        contract = jev.DEFINITIONS["textStateContract"]
        original = contract["maxBytes"]
        try:
            contract["maxBytes"] = len(body)
            with self.assertRaisesRegex(ValueError, "never truncate"):
                jev.build_request(data)
            contract["maxBytes"] = len(body) + 1
            jev.build_request(data)
        finally:
            contract["maxBytes"] = original

    def test_transport_failures_classified_without_automatic_retries(self):
        for code, retryable in ((400, False), (401, False), (429, True), (503, True)):
            calls, error, metrics = self.invoke(self.request(), urllib.error.HTTPError("url", code, "failure", {}, None))
            self.assertEqual(calls, 1)
            self.assertEqual(error["retryable"], retryable)
            self.assertEqual(metrics["stages"]["jev"]["failures"], 1)
            self.assertEqual(metrics["contractErrorRetries"], 0)
            self.assertEqual(metrics["stages"]["jev"]["totalTokens"], "unknown")


if __name__ == "__main__":
    unittest.main()
