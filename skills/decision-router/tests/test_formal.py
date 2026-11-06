import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import decisions
import jev


class FormalRoutingTest(unittest.TestCase):
    def test_probability_boundaries(self):
        question = decisions.DEFINITIONS["questions"]["formal_verification"]
        for value, expected in [(0, "no"), (.2, "no"), (.2001, "host-analysis"),
                                (.5, "host-analysis"), (.7999, "host-analysis"),
                                (.8, "yes"), (1, "yes")]:
            with self.subTest(value=value):
                self.assertEqual(decisions.route(
                    {"noul": value, "probabilityKind": "calibrated"}, question
                )["route"], expected)
        self.assertEqual(decisions.route({"noul": 1}, question)["route"], "host-analysis")
        for value in [float("nan"), -1, 2, True]:
            with self.assertRaises(ValueError):
                decisions.route({"noul": value, "probabilityKind": "calibrated"}, question)

    def test_payload_uses_registered_criteria(self):
        question = decisions.DEFINITIONS["questions"]["formal_verification"]
        payload, _ = jev.build_request({
            "state": {"subjects": {"formal_verification": {"evidence": {"scenarioId": "S1", "acceptance": "one debit per request"}, "evidenceHash": jev.evidence_hash({"scenarioId": "S1", "acceptance": "one debit per request"})}}},
            "questions": {"formal_verification": question["instructions"] + " Subject: S1."},
        }, None)
        sent = payload["questions"]["formal_verification"]
        self.assertEqual(sent["type"], "noul")
        self.assertEqual(sent["criteria"], question["criteria"])
        self.assertIn("S1", sent["instructions"])

    def test_cli_rejects_stale_or_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.json"
            for version, answer in [("1.3.0", {"noul": .9}),
                                    (decisions.DEFINITIONS["questionSetVersion"], {})]:
                source.write_text(json.dumps({"schemaVersion": 1,
                    "questionSetVersion": version,
                    "answers": {"formal_verification": {**answer, "probabilityKind": "calibrated"}}}))
                result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/decisions.py"),
                                         str(source)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
