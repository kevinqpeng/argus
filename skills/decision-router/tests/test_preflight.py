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
    def request(self):
        evidence = {"acceptance": "submission creates exactly one order",
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
