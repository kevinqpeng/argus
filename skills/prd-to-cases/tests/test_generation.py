import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/generation.py"
spec = importlib.util.spec_from_file_location("generation", SCRIPT)
generation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generation)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "baseline.json").write_text('["S1", "S2"]')
        self.plan = {"schemaVersion": 1, "runId": "run-1", "inputHash": "a" * 64,
                     "baselineHash": "b" * 64, "scenarioIds": ["S1", "S2"],
                     "inputArtifacts": {"baseline.json": generation.file_hash(self.root / "baseline.json")},
                     "tasks": [{"taskId": f"G{i}", "scenarioIds": [f"S{i}"], "caseIds": [f"C{i}"],
                                "dependsOn": [], "outputFiles": ["test.py"]} for i in (1, 2)]}
        self.reset_state()

    def reset_state(self):
        self.state = {"schemaVersion": 1, "planHash": generation.digest(self.plan),
                      "attempts": {"G1": 1, "G2": 1}, "running": [], "failed": []}

    def evaluate(self):
        return generation.evaluate(self.plan, self.state, self.root)

    def complete(self, i, dependencies=None):
        tid = f"G{i}"
        attempt = self.state["attempts"][tid]
        directory = self.root / tid / str(attempt)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "test.py").write_text(f"def test_case_{i}():\n    assert True\n")
        case = {"schemaVersion": 1, "caseId": f"C{i}", "scenarioIds": [f"S{i}"],
                "suite": "api", "priority": "P0", "fixtures": [],
                "steps": [{"id": "step1", "action": "Submit request"}],
                "assertions": [{"stepId": "step1", "expected": "Accepted"}],
                "reviewStatus": "draft", "semanticReview": "draft",
                "automationRef": f"{tid}/{attempt}/test.py:test_case_{i}"}
        receipt = {"schemaVersion": 1, "runId": "run-1", "planHash": generation.digest(self.plan),
                   "taskId": tid, "attempt": attempt, "state": "completed", "dependencyHashes": dependencies or {},
                   "files": {"test.py": generation.file_hash(directory / "test.py")}, "cases": [case],
                   "unresolvedObligations": []}
        self.write_receipt(i, receipt)
        return receipt

    def write_receipt(self, i, receipt):
        if self.plan.get("phase") == "cases":
            directory = self.root / f"G{i}" / str(self.state["attempts"][f"G{i}"])
            (directory / "test.py").unlink(missing_ok=True)
            (directory / "cases.json").write_text(json.dumps(receipt["cases"]))
            receipt["files"] = {"cases.json": generation.file_hash(directory / "cases.json")}
        receipt["resultHash"] = generation.digest({k: v for k, v in receipt.items() if k != "resultHash"})
        path = self.root / f"G{i}" / str(self.state["attempts"][f"G{i}"]) / "result.json"
        path.write_text(json.dumps(receipt))

    def test_independent_ready_and_deterministic_merge(self):
        self.assertEqual(self.evaluate()["ready"], ["G1", "G2"])
        self.complete(2)
        self.complete(1)
        result = self.evaluate()
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual([c["caseId"] for c in result["cases"]], ["C1", "C2"])

    def test_dependency_wait_and_unlock(self):
        self.plan["tasks"][1]["dependsOn"] = ["G1"]
        self.reset_state()
        self.assertEqual(self.evaluate()["ready"], ["G1"])
        receipt = self.complete(1)
        self.assertEqual(self.evaluate()["ready"], ["G2"])
        self.complete(2, {"G1": receipt["resultHash"]})
        self.assertEqual(self.evaluate()["status"], "PASSED")

    def test_cycle_unknown_dependency_and_duplicate_ownership(self):
        original = copy.deepcopy(self.plan)
        for dependencies in (["G2"], ["missing"]):
            self.plan = copy.deepcopy(original)
            self.plan["tasks"][0]["dependsOn"] = dependencies
            self.plan["tasks"][1]["dependsOn"] = ["G1"]
            self.reset_state()
            with self.assertRaises(ValueError):
                self.evaluate()
        self.plan = copy.deepcopy(original)
        self.plan["tasks"][1]["caseIds"] = ["C1"]
        self.reset_state()
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.evaluate()

    def test_missing_and_failed_shards_never_pass(self):
        self.complete(1)
        self.assertEqual(self.evaluate()["pending"], ["G2"])
        self.state["failed"] = ["G2"]
        result = self.evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["ready"], [])

    def test_running_not_dispatched_twice(self):
        self.state["running"] = ["G1"]
        self.complete(1)
        self.assertEqual(self.evaluate()["ready"], ["G2"])
        self.assertNotIn("G1", self.evaluate()["completed"])

    def test_filesystem_aliases_rejected(self):
        self.plan["tasks"][1]["taskId"] = "g1"
        with self.assertRaisesRegex(ValueError, "aliased"):
            generation.validate_plan(self.plan)
        self.plan["tasks"][1]["taskId"] = "G2"
        self.plan["tasks"][0]["outputFiles"] = ["Test.py", "test.py"]
        with self.assertRaisesRegex(ValueError, "aliased"):
            generation.validate_plan(self.plan)

    def test_retry_ignores_late_old_receipt(self):
        self.complete(1)
        self.state["attempts"]["G1"] = 2
        self.assertEqual(self.evaluate()["ready"], ["G1", "G2"])
        self.complete(1)
        (self.root / "G1/1/test.py").write_text("late obsolete write")
        self.assertIn("G1", self.evaluate()["completed"])

    def test_retry_invalidates_consumers(self):
        self.plan["tasks"][1]["dependsOn"] = ["G1"]
        self.reset_state()
        first = self.complete(1)
        self.complete(2, {"G1": first["resultHash"]})
        self.state["attempts"]["G1"] = 2
        self.complete(1)
        with self.assertRaisesRegex(ValueError, "stale dependency"):
            self.evaluate()

    def test_changed_input_and_artifact(self):
        self.complete(1)
        (self.root / "G1/1/test.py").write_text("changed")
        with self.assertRaisesRegex(ValueError, "artifact hash"):
            self.evaluate()
        self.complete(1)
        (self.root / "baseline.json").write_text("changed")
        with self.assertRaisesRegex(ValueError, "frozen input"):
            self.evaluate()

    def test_stale_receipt_and_wrong_case(self):
        receipt = self.complete(1)
        receipt["planHash"] = "c" * 64
        self.write_receipt(1, receipt)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.evaluate()
        receipt = self.complete(1)
        receipt["cases"][0]["caseId"] = "C2"
        self.write_receipt(1, receipt)
        with self.assertRaisesRegex(ValueError, "assignment"):
            self.evaluate()

    def test_path_escape_symlink_and_extra_file(self):
        self.plan["tasks"][0]["outputFiles"] = ["../test.py"]
        self.reset_state()
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            self.evaluate()
        self.plan["tasks"][0]["outputFiles"] = ["test.py"]
        self.reset_state()
        self.complete(1)
        (self.root / "G1/1/extra.py").write_text("extra")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.evaluate()
        (self.root / "G1/1/extra.py").unlink()
        (self.root / "G1/1/test.py").unlink()
        (self.root / "G1/1/test.py").symlink_to(self.root / "baseline.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.evaluate()

    def test_draft_and_reference_contracts(self):
        for field, value, error in [("reviewStatus", "approved", "draft"),
                                    ("semanticReview", "approved", "draft"),
                                    ("automationRef", "", "missing automationRef"),
                                    ("automationRef", "G2/1/test.py:other", "outside owned")]:
            receipt = self.complete(1)
            receipt["cases"][0][field] = value
            self.write_receipt(1, receipt)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                self.evaluate()

    def test_business_step_without_assertion_cannot_merge(self):
        receipt = self.complete(1)
        receipt['cases'][0]['steps'].append({'id': 'empty', 'action': 'Clear content and submit'})
        self.write_receipt(1, receipt)
        with self.assertRaisesRegex(ValueError, 'empty.*assertion'):
            self.evaluate()

    def test_only_explained_setup_and_cleanup_may_omit_assertions(self):
        receipt = self.complete(1)
        step = {'id': 'prepare', 'action': 'Open the page', 'kind': 'setup',
                'assertionExemption': 'Navigation only; submit asserts the result'}
        receipt['cases'][0]['steps'].insert(0, step)
        self.write_receipt(1, receipt)
        self.assertIn('G1', self.evaluate()['completed'])
        step['kind'] = 'action'
        self.write_receipt(1, receipt)
        with self.assertRaisesRegex(ValueError, 'prepare.*assertion'):
            self.evaluate()

    def test_handoff_in_notes_cannot_close_unresolved_obligation(self):
        receipt = self.complete(1)
        receipt['unresolvedObligations'] = [{'obligationId': 'O-CC',
                                            'reason': 'Recipient editing belongs to modal group'}]
        self.write_receipt(1, receipt)
        with self.assertRaisesRegex(ValueError, 'unresolved.*O-CC'):
            self.evaluate()

    def test_missing_obligation_receipt_is_not_an_empty_gap_list(self):
        receipt = self.complete(1)
        receipt.pop('unresolvedObligations', None)
        self.write_receipt(1, receipt)
        with self.assertRaisesRegex(ValueError, 'unresolvedObligations'):
            self.evaluate()

    def test_case_only_planned_reference_and_partial_reuse(self):
        self.plan["phase"] = "cases"
        for task in self.plan["tasks"]:
            task["outputFiles"] = ["cases.json"]
        self.reset_state()
        for i in (1, 2):
            receipt = self.complete(i)
            receipt["cases"][0]["automationRef"] = f"planned:group{i}.spec.ts::case {i}"
            self.write_receipt(i, receipt)
        self.assertEqual(self.evaluate()["status"], "PASSED")
        self.state["failed"] = ["G2"]
        result = self.evaluate()
        self.assertEqual(set(result["completed"]), {"G1"})
        self.assertNotIn("G1", result["ready"])
        receipt["cases"][0]["automationRef"] = "planned:../outside.spec.ts::case 2"
        self.write_receipt(2, receipt)
        self.state["failed"] = []
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            self.evaluate()

    def test_cli_merge_refuses_partial_and_overwrite(self):
        plan = self.root / "plan.json"
        state = self.root / "state.json"
        output = self.root / "cases.json"
        plan.write_text(json.dumps(self.plan))
        state.write_text(json.dumps(self.state))
        command = [sys.executable, str(SCRIPT), str(plan), str(state), "--root", str(self.root), "--merge", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        self.complete(1)
        self.complete(2)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        original = output.read_bytes()
        self.assertEqual(len(json.loads(original)), 2)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), original)
        output.write_text("reviewed content")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output.read_text(), "reviewed content")


if __name__ == "__main__":
    unittest.main()
