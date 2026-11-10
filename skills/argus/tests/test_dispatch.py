import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/dispatch.py"
sys.path.insert(0, str(SCRIPT.parent))
import dispatch


def snapshot(model="inherit-parent", effort="session", session_effort="high"):
    return {"config": {"defaultEffort": "session", "roles": {
        "generation": {"mode": "delegate", "model": model, "effort": effort}}},
            "sessionEffective": {"effort": session_effort}}


class DispatchTests(unittest.TestCase):
    def test_inherited_role_omits_model_and_agent_type(self):
        card = dispatch.resolve_role(snapshot(), "generation")
        self.assertEqual(card, {"role": "generation", "modelParameter": None,
                                "effortParameter": "high", "agentType": None})

    def test_explicit_model_is_passed_through_and_aliases_are_rejected(self):
        card = dispatch.resolve_role(snapshot(model="grok-4", effort="xhigh"), "generation")
        self.assertEqual(card["modelParameter"], "grok-4")
        self.assertEqual(card["effortParameter"], "xhigh")
        self.assertIsNone(card["agentType"])
        with self.assertRaisesRegex(ValueError, "model"):
            dispatch.resolve_role(snapshot(model="https://example.test/m"), "generation")
        with self.assertRaisesRegex(ValueError, "effort"):
            dispatch.resolve_role(snapshot(effort="not an effort"), "generation")

    def test_cli_blocks_a_missing_role_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "argus-model.json"
            path.write_text(json.dumps({"config": {"roles": {}}}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(path), "generation"],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
