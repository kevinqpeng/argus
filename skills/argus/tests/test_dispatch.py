import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dispatch.py"
sys.path.insert(0, str(SCRIPT.parent))
import dispatch


def snapshot(model="inherit-parent", effort="session", session_effort="high",
             host=None, session_model="unknown", session_provider="unknown",
             binding_provider=None):
    binding = {"mode": "delegate", "model": model, "effort": effort}
    if binding_provider:
        binding["provider"] = binding_provider
    frozen = {"runId": "run-1",
              "config": {"defaultEffort": "session", "roles": {"generation": binding}},
              "sessionEffective": {"model": session_model, "provider": session_provider,
                                  "effort": session_effort}}
    if host:
        frozen["host"] = host
    return frozen


def adjustment(revision, previous, digest, overrides, **extra):
    entry = {"revision": revision, "previousRevision": previous, "baseConfigHash": digest,
             "requestedAt": "2026-11-12T00:00:00Z", "requestRef": "chat-1",
             "decisionRef": "decisions/1.json", "capabilityEvidenceRef": "doctor.md",
             "effectiveFrom": f"revision-{revision}", "overrides": overrides}
    entry.update(extra)
    return entry


class DispatchTests(unittest.TestCase):
    def test_inherited_role_omits_model_and_agent_type(self):
        card = dispatch.resolve_role(snapshot(), "generation")
        self.assertEqual(card, {"role": "generation", "modelParameter": None,
                                "effortParameter": "high", "resolvedEffort": "high",
                                "agentType": None})

    def test_explicit_model_is_passed_through_and_aliases_are_rejected(self):
        card = dispatch.resolve_role(snapshot(model="grok-4", effort="xhigh"), "generation")
        self.assertEqual(card["modelParameter"], "grok-4")
        self.assertEqual(card["effortParameter"], "xhigh")
        self.assertIsNone(card["agentType"])
        with self.assertRaisesRegex(ValueError, "model"):
            dispatch.resolve_role(snapshot(model="https://example.test/m"), "generation")
        with self.assertRaisesRegex(ValueError, "effort"):
            dispatch.resolve_role(snapshot(effort="not an effort"), "generation")

    def test_pi_encodes_effort_as_a_model_suffix_and_drops_the_effort_argument(self):
        card = dispatch.resolve_role(
            snapshot(host="pi", effort="low", session_model="deepseek/model-a",
                     session_provider="UniToken"), "generation")
        self.assertEqual(card, {"role": "generation",
                                "modelParameter": "UniToken/deepseek/model-a:low",
                                "effortParameter": None, "resolvedEffort": "low",
                                "agentType": None})
        explicit = dispatch.resolve_role(
            snapshot(host="pi", model="vendor/model-b", effort="medium"), "generation")
        self.assertEqual(explicit["modelParameter"], "vendor/model-b:medium")
        self.assertIsNone(explicit["effortParameter"])
        qualified = dispatch.resolve_role(
            snapshot(host="pi", model="model-b", effort="medium", binding_provider="vendor"),
            "generation")
        self.assertEqual(qualified["modelParameter"], "vendor/model-b:medium")

    def test_pi_keeps_an_already_qualified_model(self):
        card = dispatch.resolve_role(
            snapshot(host="pi", model="vendor/model-b", effort="low",
                     binding_provider="vendor"), "generation")
        self.assertEqual(card["modelParameter"], "vendor/model-b:low")

    def test_pi_blocks_when_the_inherited_model_was_never_recorded(self):
        with self.assertRaisesRegex(ValueError, "session model is unknown"):
            dispatch.resolve_role(snapshot(host="pi", effort="medium"), "generation")

    def test_pi_leaves_an_unset_level_alone(self):
        card = dispatch.resolve_role(snapshot(host="pi", effort="model-default",
                                              model="vendor/model-b"), "generation")
        self.assertEqual(card["modelParameter"], "vendor/model-b")
        self.assertIsNone(card["effortParameter"])

    def test_cli_blocks_a_missing_role_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "argus-model.json"
            path.write_text(json.dumps({"config": {"roles": {}}}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(path), "generation"],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")


class AdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "argus-model.json"
        self.frozen = snapshot()
        self.raw = json.dumps(self.frozen).encode("utf-8")
        self.path.write_bytes(self.raw)
        self.digest = hashlib.sha256(self.raw).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, entries, **extra):
        document = {"schemaVersion": 1, "runId": "run-1", "adjustments": entries, **extra}
        (self.dir / "model-adjustments.json").write_text(
            json.dumps(document), encoding="utf-8")

    def card(self):
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.path), "generation"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stdout)

    def test_absent_adjustments_keep_the_frozen_revision(self):
        card = self.card()
        self.assertEqual(card["configRevision"], 0)
        self.assertNotIn("adjustmentRejection", card)

    def test_verified_adjustments_replace_whole_bindings_in_order(self):
        self.write([
            adjustment(1, 0, self.digest,
                       {"roles": {"generation": {"mode": "delegate",
                                                 "model": "vendor/m1", "effort": "low"}}}),
            adjustment(2, 1, self.digest,
                       {"roles": {"generation": {"mode": "delegate",
                                                 "model": "inherit-parent", "effort": "high"}},
                        "defaultEffort": "low"}),
        ])
        card = self.card()
        self.assertEqual(card["configRevision"], 2)
        self.assertEqual(card["resolvedEffort"], "high")
        self.assertIsNone(card["modelParameter"])

    def test_stale_base_hash_stops_the_chain_at_the_last_verified_revision(self):
        self.write([
            adjustment(1, 0, self.digest,
                       {"roles": {"generation": {"mode": "delegate",
                                                 "model": "vendor/m1", "effort": "low"}}}),
            adjustment(2, 1, "0" * 64,
                       {"roles": {"generation": {"mode": "delegate",
                                                 "model": "vendor/m2", "effort": "high"}}}),
        ])
        card = self.card()
        self.assertEqual(card["configRevision"], 1)
        self.assertEqual(card["modelParameter"], "vendor/m1")
        self.assertIn("baseConfigHash", card["adjustmentRejection"])

    def test_unverified_entries_are_rejected(self):
        cases = {
            "not consecutive": ([adjustment(2, 0, self.digest, {})], {}),
            "belongs to a different run": ([], {"runId": "run-2"}),
            "missing decisionRef": ([adjustment(1, 0, self.digest, {}, decisionRef=" ")], {}),
            "may only change roles and defaultEffort": (
                [adjustment(1, 0, self.digest, {"decision": {"maxCallsPerRun": 5}})], {}),
            "not a frozen role": (
                [adjustment(1, 0, self.digest, {"roles": {"reviewer": {"model": "m"}}})], {}),
            "unknown fields": ([adjustment(1, 0, self.digest, {}, extraField=1)], {}),
        }
        for expected, (entries, extra) in cases.items():
            with self.subTest(expected):
                self.write(entries, **extra)
                card = self.card()
                self.assertEqual(card["configRevision"], 0)
                self.assertIn(expected, card["adjustmentRejection"])

    def test_unreadable_document_falls_back_to_the_frozen_snapshot(self):
        (self.dir / "model-adjustments.json").write_text("{", encoding="utf-8")
        card = self.card()
        self.assertEqual(card["configRevision"], 0)
        self.assertIn("unreadable", card["adjustmentRejection"])


if __name__ == "__main__":
    unittest.main()
