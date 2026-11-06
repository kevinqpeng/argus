import concurrent.futures
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import lifecycle
from phase_gate import Gate
from metrics import summarize


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.manifest = {"runId": "R1", "gates": {"humanCaseReview": {"status": "N/A", "reason": "frozen option"}}}
        self.write("manifest.json", self.manifest)
        self.write("argus-model.json", {"runId": "R1", "config": {"execution": {"requireHumanCaseReview": False}}})
        self.cases = [{"schemaVersion": 1, "scenarioIds": [f"S{i}"], "suite": "api", "priority": "P0",
                       "fixtures": [], "steps": [{"id": "1", "action": "submit"}],
                       "assertions": [{"stepId": "1", "expected": "success"}], "caseId": f"C{i}", "reviewStatus": "approved", "semanticReview": "approved",
                       "automationRef": f"planned:{i}.spec.ts::title {i}"} for i in (1, 2)]
        self.write("cases/cases.json", self.cases)
        self.plan = {"schemaVersion": 1, "runId": "R1", "approvedCasesHash": lifecycle.digest(self.cases),
                     "tasks": [{"taskId": f"T{i}", "caseIds": [f"C{i}"], "specFile": f"{i}.spec.ts"} for i in (1, 2)]}
        self.write("cases/scripts/plan.json", self.plan)
        for i in (1, 2):
            source = self.run / f"cases/scripts/T{i}/{i}.spec.ts"
            source.parent.mkdir(parents=True)
            source.write_text(f"// spec {i}\n")
            self.write(f"cases/scripts/T{i}/result.json", {"runId": "R1", "planHash": lifecycle.digest(self.plan),
                       "taskId": f"T{i}", "caseIds": [f"C{i}"], "specFileHash": lifecycle.file_hash(source),
                       "discoveredTitles": {f"C{i}": f"title {i}"}})
            self.write(f"tmp/T{i}/1/owner.json", {"runId": "R1", "taskId": f"T{i}", "attempt": 1, "state": "stopped"})
            (self.run / f"tmp/T{i}/1/cache").write_text("temporary")

    def write(self, name, data):
        p = self.run / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))

    def test_publish_cleanup_and_retry_preserve_specs_and_other_tasks(self):
        lifecycle.publish(self.run, "T1")
        published = self.run / "harness/tests/1.spec.ts"
        before = published.read_bytes()
        lifecycle.cleanup(self.run, "T1", 1)
        self.assertFalse(lifecycle.cleanup(self.run, "T1", 1)["removed"])
        lifecycle.publish(self.run, "T1")
        self.assertEqual(published.read_bytes(), before)
        self.assertTrue((self.run / "tmp/T2/1/cache").exists())
        self.assertTrue((self.run / "cases/scripts/T1/1.spec.ts").exists())
        events = [json.loads(x) for x in (self.run / "lifecycle.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), 4)
        self.assertTrue(all("durationMs" in e for e in events))

    def test_publication_is_copy_and_rejects_conflicting_existing_bytes(self):
        lifecycle.publish(self.run, "T1")
        source = self.run / "cases/scripts/T1/1.spec.ts"
        published = self.run / "harness/tests/1.spec.ts"
        source.write_text("worker late mutation")
        self.assertNotEqual(source.read_bytes(), published.read_bytes())
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            lifecycle.publish(self.run, "T1")
        self.assertNotEqual(published.read_text(), "worker late mutation")

    def test_draft_wrong_reference_and_approval_changes_block_before_write(self):
        for field, value in [("reviewStatus", "draft"), ("automationRef", "planned:other.spec.ts::title 1")]:
            changed = copy.deepcopy(self.cases)
            changed[0][field] = value
            self.write("cases/cases.json", changed)
            with self.assertRaises(ValueError):
                lifecycle.publish(self.run, "T1")
            self.assertFalse((self.run / "harness/tests/1.spec.ts").exists())

    def test_cleanup_refuses_other_owner_symlink_and_active_worker(self):
        owner = {"runId": "other", "taskId": "T1", "attempt": 1, "state": "stopped"}
        self.write("tmp/T1/1/owner.json", owner)
        with self.assertRaisesRegex(ValueError, "owner"):
            lifecycle.cleanup(self.run, "T1", 1)
        owner.update(runId="R1", state="running")
        self.write("tmp/T1/1/owner.json", owner)
        with self.assertRaisesRegex(ValueError, "stopped"):
            lifecycle.cleanup(self.run, "T1", 1)
        owner["state"] = "stopped"
        self.write("tmp/T1/1/owner.json", owner)
        (self.run / "tmp/T1/1/link").symlink_to(self.run / "cases")
        with self.assertRaisesRegex(ValueError, "symlink"):
            lifecycle.cleanup(self.run, "T1", 1)
        self.assertTrue((self.run / "cases/cases.json").exists())
        with self.assertRaises(ValueError):
            lifecycle.cleanup(self.run, "../T2", 1)

    def test_concurrent_process_publication_and_cleanup(self):
        def invoke(operation, task):
            command = [sys.executable, "-B", str(SCRIPTS / "lifecycle.py"), str(self.run), operation, task]
            if operation == "cleanup":
                command += ["--attempt", "1"]
            return subprocess.run(command, capture_output=True, text=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda args: invoke(*args),
                           [(op, task) for task in ("T1", "T2") for op in ("publish", "cleanup", "publish", "cleanup")]))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(lifecycle.execution_cases(self.run)), 2)
        self.assertEqual(summarize(self.run / "metrics.jsonl")["stages"]["publish"]["count"], 4)

    def test_binding_preserves_approved_cases_and_resume_after_later_error(self):
        approved = (self.run / "cases/cases.json").read_bytes()
        lifecycle.publish(self.run, "T1")
        receipt = self.run / "cases/scripts/T2/result.json"
        original = receipt.read_bytes()
        receipt.write_text("{}")
        with self.assertRaises(ValueError):
            lifecycle.publish(self.run, "T2")
        first = (self.run / "harness/tests/1.spec.ts").stat().st_mtime_ns
        receipt.write_bytes(original)
        lifecycle.publish(self.run, "T2")
        bound = lifecycle.execution_cases(self.run)
        self.assertEqual(bound[0]["automationRef"], "harness/tests/1.spec.ts::title 1")
        self.assertEqual((self.run / "cases/cases.json").read_bytes(), approved)
        self.assertEqual((self.run / "harness/tests/1.spec.ts").stat().st_mtime_ns, first)

    def test_metrics_wrapper_and_unknown_costs(self):
        log = self.run / "measured.jsonl"
        for stage, code in (("generation", 0), ("playwright", 3)):
            result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "metrics.py"), "--stage", stage,
                                     str(log), "--", sys.executable, "-c", f"raise SystemExit({code})"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
        report = summarize(log)
        self.assertEqual(report["stages"]["generation"]["count"], 1)
        self.assertIsInstance(report["stages"]["playwright"]["durationMs"], float)
        self.assertEqual(report["stages"]["jev"]["totalTokens"], "unknown")
        self.assertEqual(report["comparableReworkSeconds"], "unknown")

    def test_published_gate_checks_real_file_and_immutable_binding(self):
        for tid in ("T1", "T2"):
            lifecycle.publish(self.run, tid)
        self.write("cases/execution-cases.json", lifecycle.execution_cases(self.run))
        self.write("cases/discovered.txt", [{"caseId": c["caseId"], "automationRef": c["automationRef"]}
                                           for c in lifecycle.execution_cases(self.run)])
        self.manifest.update(timing={"review": {"approvedAt": "2026-11-06T10:00:00+08:00"}},
                             scripts={"generatedAfter": "2026-11-06T10:01:00+08:00"})
        self.write("manifest.json", self.manifest)
        gate = Gate(self.run)
        gate.scripts()
        self.assertFalse(gate.gaps, gate.gaps)
        (self.run / "harness/tests/1.spec.ts").unlink()
        gate = Gate(self.run)
        gate.scripts()
        self.assertTrue(gate.gaps)

    def test_crash_after_spec_before_receipt_recovers_without_rewrite(self):
        target = self.run / "harness/tests/1.spec.ts"
        target.parent.mkdir(parents=True)
        target.write_bytes((self.run / "cases/scripts/T1/1.spec.ts").read_bytes())
        before = target.stat().st_mtime_ns
        lifecycle.publish(self.run, "T1")
        self.assertEqual(target.stat().st_mtime_ns, before)
        self.assertTrue((self.run / "cases/scripts/T1/published.json").exists())

    def test_approval_hashes_pass_then_detect_changed_frozen_material(self):
        self.write("argus-model.json", {"runId": "R1", "config": {"execution": {"requireHumanCaseReview": True}}})
        names = ["cases/" + name for name in ("impact-analysis.json", "requirements.json", "acs.json",
                 "scenarios.json", "cases.json", "cases.md", "cases.xlsx")]
        for name in names:
            if name != "cases/cases.json":
                (self.run / name).write_text("frozen")
        self.manifest.update(inputHash="a" * 64, baselineHash="b" * 64)
        self.manifest["gates"]["humanCaseReview"] = {"status": "PASSED", "runId": "R1",
            "inputHash": "a" * 64, "baselineHash": "b" * 64, "reviewer": "human",
            "reviewedAt": "2026-11-06T10:00:00+08:00", "approvalRef": "approval-message",
            "artifactHashes": {name: lifecycle.file_hash(self.run / name) for name in names}}
        self.write("manifest.json", self.manifest)
        lifecycle.publish(self.run, "T1")
        (self.run / "cases/cases.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed approved artifact"):
            lifecycle.publish(self.run, "T2")
        self.assertFalse((self.run / "harness/tests/2.spec.ts").exists())

    def test_dispatch_receipt_existence_does_not_bypass_contract(self):
        self.manifest["generation"] = {"planRef": "cases/generation/plan.json"}
        self.write("manifest.json", self.manifest)
        (self.run / "cases/exploration.md").write_text("exploration")
        self.write("cases/generation/plan.json", {"tasks": [{"taskId": "G1"}]})
        self.write("cases/generation/state.json", {})
        self.write("cases/generation/G1/1/result.json", {"state": "completed"})
        gate = Gate(self.run)
        gate.dispatch()
        self.assertTrue(any("生成契约错误" in gap for gap in gate.gaps))

    def test_review_enabled_cannot_use_disabled_receipt(self):
        self.write("argus-model.json", {"config": {"execution": {"requireHumanCaseReview": True}}})
        gate = Gate(self.run)
        gate.publish("T1")
        self.assertTrue(gate.gaps)
        self.assertFalse((self.run / "harness").exists())


if __name__ == "__main__":
    unittest.main()
