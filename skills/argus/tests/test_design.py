import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import phase_gate


class DesignCompletionTest(unittest.TestCase):
    def test_design_rejects_wrong_queue_and_changed_source(self):
        import scheduler
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            run = Path(tmp).resolve()
            manifest = {"runId": "r", "gates": {"formalVerification": {
                "status": "N/A", "evidenceRef": "formal.md"}}, "timing": {}}
            (run / "formal.md").write_text("not applicable")
            for stage, ref in (("generation", "cases/generation/plan.json"),
                               ("presentation", "cases/presentation/1/plan.json")):
                source = ({"tasks": [{"taskId": "one", "outputFiles": ["cases.json"]}]}
                          if stage == "generation" else
                          {"slices": [{"name": "one", "output": "one.json"}]})
                source_path = run / ref
                source_path.parent.mkdir(parents=True)
                source_path.write_text(json.dumps(source))
                plan = scheduler.design_plan(source, "r", 1)
                plan.update(timingRun=str(run), timingStage=stage)
                plan_path, state_path = run / f"{stage}.json", run / f"{stage}.state.json"
                plan_path.write_text(json.dumps(plan))
                state_path.write_text(json.dumps({"planHash": scheduler.digest(plan), "tasks": {
                    "one": {"status": "completed", "startedAt": 100.1234567,
                            "finishedAt": 104.1234567}}, "history": []}))
                from datetime import datetime, timezone
                manifest["timing"][stage] = {
                    "startedAt": datetime.fromtimestamp(100.1234567, timezone.utc).isoformat(),
                    "endedAt": datetime.fromtimestamp(104.1234567, timezone.utc).isoformat(),
                    "queueStateRef": str(state_path), "queuePlanRef": str(plan_path)}
            for stage in ("input", "exploration"):
                manifest["timing"][stage] = {"startedAt": "2026-01-01T00:00:00+00:00",
                                              "endedAt": "2026-01-01T00:00:01+00:00"}
            (run / "manifest.json").write_text(json.dumps(manifest))
            stack.enter_context(patch("verify_all.draft_gaps", return_value=[]))
            stack.enter_context(patch("verify_all.case_gates", return_value={}))
            for method in ("dispatch", "semantic", "presentation_delivery"):
                stack.enter_context(patch.object(phase_gate.Gate, method, return_value=None))
            gate = phase_gate.Gate(run)
            gate.design()
            self.assertEqual(gate.gaps, [])
            state_path = run / "generation.state.json"
            state = json.loads(state_path.read_text())
            state["tasks"]["other"] = state["tasks"].pop("one")
            state_path.write_text(json.dumps(state))
            gate = phase_gate.Gate(run)
            gate.design()
            self.assertTrue(any("任务集合" in gap for gap in gate.gaps))
            source_path = run / "cases/presentation/1/plan.json"
            source_path.write_text(json.dumps({"slices": [{"name": "changed", "output": "two.json"}]}))
            gate = phase_gate.Gate(run)
            gate.design()
            self.assertTrue(any("当前分片计划" in gap for gap in gate.gaps))

    def test_completion_preserves_fields_and_blocks_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve()
            manifest = run / "manifest.json"
            original = {"state": "design_in_progress", "runId": "r"}
            manifest.write_text(json.dumps(original))
            with patch.object(phase_gate.Gate, "design", lambda gate: gate.gaps.append("missing evidence")):
                self.assertTrue(phase_gate.complete_design(run).gaps)
            self.assertEqual(json.loads(manifest.read_text()), original)
            with patch.object(phase_gate.Gate, "design", lambda gate: None):
                self.assertFalse(phase_gate.complete_design(run).gaps)
            saved = json.loads(manifest.read_text())
            self.assertEqual(saved["state"], "design_completed")
            self.assertEqual(saved["runId"], "r")
            self.assertEqual(saved["gates"]["design"]["status"], "PASSED")
