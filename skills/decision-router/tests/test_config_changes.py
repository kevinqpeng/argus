import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import decisions
import jev


class ConfigChangeRoutingTest(unittest.TestCase):
    def test_complete_diff_reaches_registered_question(self):
        evidence = {"diff": {"roles.generation.model": ["old", "new"]},
                    "businessUnchanged": True, "capabilitiesVerified": True}
        payload, _ = jev.build_request({
            "questions": {"change1": {"questionId": "run_config_change", "subject": "change1"}},
            "state": {"subjects": {"change1": {
                "evidence": evidence, "evidenceHash": jev.evidence_hash(evidence)}}},
        })
        self.assertEqual(payload["questions"]["change1"]["criteria"],
                         decisions.DEFINITIONS["questions"]["run_config_change"]["criteria"])
        self.assertIn("new", str(payload["state"]))

    def test_uncertainty_never_routes_to_business_impact(self):
        question = decisions.DEFINITIONS["questions"]["run_config_change"]
        for probabilities, expected in [
            ([.9, .05, .05], "retain_run"),
            ([.05, .9, .05], "business_impact"),
            ([.05, .05, .9], "insufficient_evidence"),
            ([.49, .5, .01], "host-analysis"),
            ([.79, .2, .01], "host-analysis"),
        ]:
            distribution = dict(zip(question["criteria"], probabilities))
            answer = {"choice": max(distribution, key=distribution.get),
                      "probabilities": distribution, "probabilityKind": "calibrated"}
            with self.subTest(probabilities=probabilities):
                self.assertEqual(decisions.route(answer, question)["route"], expected)
        self.assertEqual(decisions.route({}, question)["route"], "host-analysis")


if __name__ == "__main__":
    unittest.main()
