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
        self.manifest.update(state="ready", requiredMatrix=["desktop"],
                             runConfigHash=lifecycle.file_hash(self.run / "argus-model.json"))
        self.evidence("disable-review", "disable-human-review", {
            "runId": "R1", "runConfigHash": self.manifest["runConfigHash"]})
        self.manifest["gates"]["humanCaseReview"].update(
            authorizationRef="evidence/disable-review.json",
            authorizationHash=lifecycle.file_hash(self.run / "evidence/disable-review.json"))
        self.write("manifest.json", self.manifest)
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

    def evidence(self, name, decision, scope):
        self.write("evidence/" + name + "-raw.json", {
            "role": "user", "text": "Explicit human authorization", "id": name})
        self.write("evidence/" + name + ".json", {
            "schemaVersion": 1, "sourceRef": "host-message:" + name,
            "actorType": "human", "actor": "human", "decision": decision,
            "recordedAt": "2026-11-06T10:00:00+08:00",
            "rawRef": "evidence/" + name + "-raw.json",
            "rawHash": lifecycle.file_hash(self.run / "evidence" / (name + "-raw.json")),
            "scope": scope})

    def approve(self):
        self.write("argus-model.json", {"runId": "R1", "config": {"execution": {"requireHumanCaseReview": True}}})
        names = ["cases/" + name for name in ("impact-analysis.json", "requirements.json", "acs.json",
                 "scenarios.json", "cases.json", "cases.md", "cases.xlsx")]
        for name in names:
            if name == "cases/cases.json":
                continue
            if name == "cases/impact-analysis.json":
                self.write(name, {"schemaVersion": 1, "objects": [], "checks": []})
                continue
            (self.run / name).write_text("frozen")
        self.write("cases/exploration/evidence-check.json", {"status": "PASSED", "failed": 0, "failures": []})
        self.manifest.update(inputHash="a" * 64, baselineHash="b" * 64,
                             runConfigHash=lifecycle.file_hash(self.run / "argus-model.json"))
        scope = {k: self.manifest[k] for k in ("runId", "inputHash", "baselineHash", "runConfigHash", "requiredMatrix")}
        scope.update(caseIds=["C1", "C2"], artifactHashes={name: lifecycle.file_hash(self.run / name) for name in names})
        self.evidence("approval", "approve-cases-and-validate", scope)
        self.manifest["gates"]["humanCaseReview"] = {
            **scope, "status": "PASSED", "reviewer": "human",
            "reviewedAt": "2026-11-06T10:00:00+08:00", "approvalRef": "evidence/approval.json",
            "approvalHash": lifecycle.file_hash(self.run / "evidence/approval.json")}
        self.write("manifest.json", self.manifest)

    def test_human_approval_does_not_require_semantic_review(self):
        for case in self.cases:
            case.pop("semanticReview", None)
        self.write("cases/cases.json", self.cases)
        self.approve()
        self.assertEqual(lifecycle.approved_cases(self.run, self.manifest), self.cases)
        (self.run / "cases/discovered.txt").write_text("discovered")
        gate = Gate(self.run)
        gate.cases()
        self.assertEqual(gate.gaps, [])
        self.manifest["gates"]["humanCaseReview"]["status"] = "BLOCKED"
        with self.assertRaises(ValueError):
            lifecycle.approved_cases(self.run, self.manifest)

    def test_unverifiable_approval_and_pending_state_block(self):
        self.approve()
        original = copy.deepcopy(self.manifest)
        for field, value in [("approvalRef", "nonexistent-reply"), ("reviewedAt", "not-a-date")]:
            self.manifest = copy.deepcopy(original)
            self.manifest["gates"]["humanCaseReview"][field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, OSError)):
                lifecycle.approved_cases(self.run, self.manifest)
        self.manifest = original
        self.manifest["state"] = "awaiting_case_review"
        with self.assertRaises(ValueError):
            lifecycle.approved_cases(self.run, self.manifest)

    def test_changed_config_or_missing_disable_authorization_blocks(self):
        self.manifest["gates"]["humanCaseReview"].pop("authorizationRef")
        with self.assertRaises((ValueError, KeyError)):
            lifecycle.approved_cases(self.run, self.manifest)
        self.approve()
        self.write("argus-model.json", {"runId": "R1", "config": {"execution": {"requireHumanCaseReview": False}}})
        self.manifest["gates"]["humanCaseReview"] = {"status": "N/A", "reason": "arbitrary"}
        with self.assertRaises(ValueError):
            lifecycle.approved_cases(self.run, self.manifest)

    def test_matrix_and_raw_approval_changes_block(self):
        self.approve()
        self.manifest["requiredMatrix"].append("mobile")
        with self.assertRaises(ValueError):
            lifecycle.approved_cases(self.run, self.manifest)
        self.approve()
        self.write("evidence/approval-raw.json", {"role": "assistant", "text": "approved"})
        with self.assertRaises(ValueError):
            lifecycle.approved_cases(self.run, self.manifest)

    def test_final_review_source_is_bound_and_later_edits_block(self):
        self.approve()
        refs = ["cases/review/1/final.json", "cases/import-map.json"]
        self.write(refs[0], {"cases": self.cases, "version": "reviewed-v2"})
        self.write(refs[1], {"rows": [{"caseId": c["caseId"], "sourceRef": refs[0]} for c in self.cases]})
        self.manifest["reviewImport"] = {"artifactRefs": refs}
        with self.assertRaisesRegex(ValueError, "import artifacts"):
            lifecycle.approved_cases(self.run, self.manifest)
        gate = self.manifest["gates"]["humanCaseReview"]
        gate["artifactHashes"].update({ref: lifecycle.file_hash(self.run / ref) for ref in refs})
        record = lifecycle.read(self.run, gate["approvalRef"])
        record["scope"]["artifactHashes"] = gate["artifactHashes"]
        self.write(gate["approvalRef"], record)
        gate["approvalHash"] = lifecycle.file_hash(self.run / gate["approvalRef"])
        self.assertEqual(lifecycle.approved_cases(self.run, self.manifest), self.cases)
        self.write(refs[0], {"version": "later-edit"})
        with self.assertRaisesRegex(ValueError, "changed approved artifact"):
            lifecycle.approved_cases(self.run, self.manifest)

    def test_nonhuman_partial_and_generic_continue_evidence_blocks(self):
        for field, value in [("actorType", "agent"), ("decision", "continue"),
                             ("recordedAt", "2026-11-06T10:00:00"), ("scope", {})]:
            with self.subTest(field=field):
                self.approve()
                gate = self.manifest["gates"]["humanCaseReview"]
                record = lifecycle.read(self.run, gate["approvalRef"])
                record[field] = value
                self.write(gate["approvalRef"], record)
                gate["approvalHash"] = lifecycle.file_hash(self.run / gate["approvalRef"])
                with self.assertRaises(ValueError):
                    lifecycle.approved_cases(self.run, self.manifest)

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
        self.approve()
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

    def test_dispatch_requires_index_windows_per_object(self):
        self.manifest["generation"] = {"planRef": "cases/generation/plan.json"}
        self.write("manifest.json", self.manifest)
        (self.run / "cases/exploration.md").write_text("exploration")
        self.write("cases/generation/plan.json", {"runId": "R1", "tasks": [{"taskId": "G1"}]})
        self.write("cases/generation/state.json", {})
        gate = Gate(self.run)
        gate.dispatch()
        self.assertTrue(any("缺少 cases/exploration/plan.json" in gap for gap in gate.gaps))
        self.write("cases/exploration/plan.json", {"schemaVersion": 1, "inputHash": "h", "tasks": [
            {"taskId": "T1", "attempt": 1, "objectIds": ["OBJ-01"]}]})
        gate = Gate(self.run)
        gate.dispatch()
        self.assertTrue(any("探索计划缺索引窗口" in gap for gap in gate.gaps))
        self.write("cases/exploration/plan.json", {"schemaVersion": 1, "inputHash": "h", "tasks": [
            {"taskId": "T1", "attempt": 1, "objectIds": ["OBJ-01"],
             "windows": [{"objectId": "OBJ-01", "refs": ["A.java:1-2", "A.java:4-6",
                                                        "A.java:8-10", "A.java:12-14"]}]}]})
        gate = Gate(self.run)
        gate.dispatch()
        self.assertFalse([gap for gap in gate.gaps if "探索计划缺索引窗口" in gap], gate.gaps)

    def test_cases_gate_requires_full_matrix_and_verified_evidence(self):
        self.approve()
        (self.run / "cases/discovered.txt").write_text("[]")
        self.write("cases/impact-analysis.json", {"schemaVersion": 1,
                                                  "objects": [{"objectId": "OBJ-01"}], "checks": []})
        self.write("cases/exploration/evidence-check.json", {
            "status": "FAILED", "failed": 1,
            "failures": [{"id": "I1", "reason": "文件不存在", "evidenceRef": "x.java:1"}]})
        gate = Gate(self.run)
        gate.cases()
        self.assertTrue(any("六维缺格 OBJ-01 × inflight" in gap for gap in gate.gaps), gate.gaps)
        self.assertTrue(any("证据真实性" in gap for gap in gate.gaps), gate.gaps)
        self.write("cases/exploration/evidence-check.json", {"status": "PASSED", "failed": 0, "failures": []})
        self.write("cases/impact-analysis.json", {"schemaVersion": 1, "objects": [], "checks": []})
        gate = Gate(self.run)
        gate.cases()
        self.assertFalse([gap for gap in gate.gaps if "六维缺格" in gap or "证据" in gap], gate.gaps)

    def test_presentation_gate_carries_matrix_and_evidence_gaps(self):
        self.write("cases/impact-analysis.json", {"schemaVersion": 1,
                                                  "objects": [{"objectId": "OBJ-01"}], "checks": []})
        gate = Gate(self.run)
        gate.presentation()
        self.assertTrue(any("六维缺格 OBJ-01 × data" in gap for gap in gate.gaps), gate.gaps)
        self.assertTrue(any("evidence-check.json" in gap for gap in gate.gaps), gate.gaps)
        self.write("cases/impact-analysis.json", {"schemaVersion": 1, "objects": [], "checks": []})
        self.write("cases/exploration/evidence-check.json", {"status": "PASSED", "failed": 0, "failures": []})
        gate = Gate(self.run)
        gate.presentation()
        self.assertFalse([gap for gap in gate.gaps if "六维缺格" in gap or "evidence-check" in gap], gate.gaps)

    def test_review_enabled_cannot_use_disabled_receipt(self):
        self.write("argus-model.json", {"config": {"execution": {"requireHumanCaseReview": True}}})
        gate = Gate(self.run)
        gate.publish("T1")
        self.assertTrue(gate.gaps)
        self.assertFalse((self.run / "harness").exists())


if __name__ == "__main__":
    unittest.main()
