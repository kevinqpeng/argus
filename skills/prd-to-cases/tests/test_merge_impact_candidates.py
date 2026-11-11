import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "merge_impact_candidates", Path(__file__).resolve().parents[1] / "scripts/merge_impact_candidates.py")
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.plan = {"schemaVersion": 1, "inputHash": "snapshot", "tasks": [
            {"taskId": "A", "attempt": 2, "objectIds": ["O1"], "windows": [self.window("A", "O1")]},
            {"taskId": "B", "attempt": 1, "objectIds": ["O2"], "windows": [self.window("B", "O2")]}]}
        self.a = {"schemaVersion": 1, "inputHash": "snapshot", "taskId": "A", "attempt": 2,
                  **{group: [] for group in merge.GROUPS}}
        self.b = {**self.a, "taskId": "B", "attempt": 1, "checks": []}

    @staticmethod
    def window(task_id, object_id):
        return {"objectId": object_id, "refs": [f"{task_id}.java:{index}-{index + 1}"
                                                for index in (1, 3, 5, 7)]}

    @staticmethod
    def coverage(task_id, object_id):
        return [{"checkId": f"{task_id}-C{index}", "objectId": object_id, "dimension": dimension,
                 "disposition": "covered", "evidenceRef": f"{task_id}.java:{index}"}
                for index, dimension in enumerate(merge.DIMENSION_ORDER, start=1)]

    def test_collects_identical_candidates_with_provenance(self):
        source = {"sourceId": "PRD", "kind": "prd", "sourceRef": "run://cases/sources/prd.md"}
        self.a["sources"] = [source]
        self.b["sources"] = [dict(source)]
        self.a["checks"] = self.coverage("A", "O1")
        self.b["checks"] = self.coverage("B", "O2")
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertEqual(result["status"], "READY_FOR_REVIEW", result["errors"])
        self.assertEqual(result["candidates"]["sources"]["PRD"][0]["tasks"], ["A", "B"])
        self.assertEqual(result["candidates"]["checks"]["A-C1"][0]["tasks"], ["A"])

    def test_task_prefixed_ids_and_prd_ref(self):
        self.a["sourceUnits"] = [{"unitId": "U1", "disposition": "covered"}]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertTrue(any("A-U<n>" in item for item in result["errors"]))
        self.a["sourceUnits"] = [{"unitId": "A-U1", "disposition": "maybe"}]
        self.a["sources"] = [{"sourceId": "PRD", "kind": "prd", "sourceRef": "cases/sources/prd.md"}]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertTrue(any("invalid disposition" in item for item in result["errors"]))
        self.assertTrue(any("run://" in item for item in result["errors"]))

    def test_conflicts_and_missing_receipts_do_not_disappear(self):
        self.a["checks"] = [{"checkId": "C", "reason": "one"}]
        self.b["checks"] = [{"checkId": "C", "reason": "two"}]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["candidates"]["checks"]["C"]), 2)
        errors = merge.collect(self.plan, [self.a])["errors"]
        self.assertIn("missing receipt: B", errors)
        self.assertTrue(any("invalid dimension" in item for item in errors))

    def test_stale_attempt_and_missing_group_block(self):
        self.a["attempt"] = 1
        del self.b["handoffs"]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("stale" in e for e in result["errors"]))
        self.assertIn("B: missing handoffs", result["errors"])

    def test_rejects_wide_span_wrong_dimension_and_foreign_object(self):
        self.plan["tasks"][0]["objectIds"] = ["O1"]
        self.plan["tasks"][1]["objectIds"] = ["O2"]
        self.a["checks"] = [{"checkId": "C", "objectId": "O2", "dimension": "testability",
                             "kind": "code", "evidenceRef": "A.java:10-13; B.java:1; C.java:2"}]
        self.a["status"] = "partial"
        self.b["checks"] = [{"checkId": "D", "objectId": "O2", "dimension": "ordering",
                             "evidenceRef": "B.java:4-6"}]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("invalid dimension" in item for item in result["errors"]))
        self.assertTrue(any("forbidden kind" in item for item in result["errors"]))
        self.assertTrue(any("span > 3" in item for item in result["errors"]))
        self.assertTrue(any("more than 2" in item for item in result["errors"]))
        self.assertTrue(any("not assigned" in item for item in result["errors"]))
        self.assertIn("partial receipt: A", result["errors"])
        self.assertEqual(result["candidates"]["checks"]["D"][0]["tasks"], ["B"])

    def test_matrix_must_conclude_every_object_dimension(self):
        plan = {"schemaVersion": 1, "inputHash": "snapshot",
                "tasks": [{"taskId": "A", "attempt": 1, "objectIds": ["OBJ-01"],
                           "windows": [{"objectId": "OBJ-01", "refs": [f"A.java:{i}-{i + 1}" for i in range(1, 5)]}]}]}
        receipt = {"schemaVersion": 1, "inputHash": "snapshot", "taskId": "A", "attempt": 1,
                   "sources": [], "sourceUnits": [], "objects": [{"objectId": "OBJ-01", "description": "d"}],
                   "checks": [{"checkId": "A-C1", "objectId": "OBJ-01", "dimension": "data",
                              "disposition": "covered", "reason": "字段新增，读写路径一致",
                              "evidenceRef": "A.java:1-2"}], "obligations": [], "handoffs": []}
        result = merge.collect(plan, [receipt])
        self.assertEqual([gap["gapId"] for gap in result["gaps"]],
                         [f"GAP-OBJ-01-{dimension}" for dimension in merge.DIMENSION_ORDER if dimension != "data"])
        self.assertEqual(result["matrix"]["cells"], len(merge.DIMENSIONS))
        self.assertTrue(any("六维留空 5/6 超过三分之一" in item for item in result["errors"]))
        self.assertFalse([item for item in result["errors"] if "六维结论不成立" in item])

    def test_few_blank_cells_pass_and_stay_named_gaps(self):
        plan = {"schemaVersion": 1, "inputHash": "snapshot",
                "tasks": [{"taskId": "A", "attempt": 1, "objectIds": ["OBJ-01"],
                           "windows": [{"objectId": "OBJ-01", "refs": [f"A.java:{i}-{i + 1}" for i in range(1, 5)]}]}]}
        checks = [{"checkId": f"A-C{index}", "objectId": "OBJ-01", "dimension": dimension,
                   "disposition": "covered", "evidenceRef": "A.java:1-2"}
                  for index, dimension in enumerate(("data", "inflight", "lifecycle", "permission"), start=1)]
        checks.append({"checkId": "A-C5", "objectId": "OBJ-01", "dimensions": ["side_effect", "ordering"],
                       "disposition": "not_applicable",
                       "reason": "该对象是流程模型配置项，不产生外部调用、消息或并发写入，顺序与副作用无从谈起",
                       "evidenceRef": "A.java:3-4"})
        receipt = {"schemaVersion": 1, "inputHash": "snapshot", "taskId": "A", "attempt": 1,
                   "sources": [], "sourceUnits": [], "objects": [{"objectId": "OBJ-01", "description": "d"}],
                   "checks": checks, "obligations": [], "handoffs": []}
        result = merge.collect(plan, [receipt])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["matrix"]["counts"]["notApplicable"], 2)

    def test_plan_may_add_dimensions_but_not_replace_base(self):
        plan = {"schemaVersion": 1, "inputHash": "snapshot",
                "extraDimensions": [{"key": "compat", "question": "历史数据与老客户端是否兼容"}],
                "tasks": [{"taskId": "A", "attempt": 1, "objectIds": ["OBJ-01"],
                           "windows": [{"objectId": "OBJ-01", "refs": [f"A.java:{i}-{i + 1}" for i in range(1, 5)]}]}]}
        dimensions, errors = merge.plan_dimensions(plan)
        self.assertEqual(errors, [])
        self.assertEqual(dimensions, [*merge.BASE_DIMENSIONS, "compat"])
        self.assertEqual(merge.matrix_summary(["OBJ-01"], [], dimensions)["cells"], len(dimensions))
        self.assertEqual(len(merge.matrix_gaps(["OBJ-01"], [], dimensions)), len(dimensions))
        plan["extraDimensions"] = [{"key": "data", "question": "不允许覆盖基线维度"}]
        self.assertTrue(merge.plan_dimensions(plan)[1])
        plan["extraDimensions"] = [{"key": "compat", "question": "太短"}]
        self.assertTrue(merge.plan_dimensions(plan)[1])

    def test_object_with_only_na_conclusions_is_flagged_not_blocked(self):
        checks = [{"checkId": f"C{index}", "objectId": "OBJ-01", "dimension": dimension,
                   "disposition": "not_applicable",
                   "reason": "该对象只是模型配置字段，不产生外部调用、消息或并发写入，顺序也无从谈起"}
                  for index, dimension in enumerate(merge.BASE_DIMENSIONS, start=1)]
        self.assertEqual(merge.matrix_errors(["OBJ-01"], checks), [])
        hints = merge.merged_hints(["OBJ-01"], checks)
        self.assertEqual(len(hints), 1)
        self.assertIn("可能不是本次变更对象", hints[0])

    def test_bundle_cannot_swallow_data_or_permission(self):
        checks = [{"checkId": "A-C1", "objectId": "OBJ-01", "dimensions": ["data", "side_effect"],
                   "disposition": "not_applicable",
                   "reason": "该对象是流程模型配置项，不产生外部调用、消息或并发写入，顺序与副作用无从谈起"}]
        errors = merge.bundle_errors("A-C1", checks[0])
        self.assertTrue(any("data 不能和别的维度合并" in item for item in errors))
        checks[0]["dimensions"] = ["inflight", "lifecycle", "ordering", "side_effect"]
        self.assertTrue(any("一次最多 3 个" in item for item in merge.bundle_errors("A-C1", checks[0])))
        checks[0]["dimensions"] = ["inflight", "lifecycle"]
        checks[0]["reason"] = "不适用"
        self.assertTrue(any("合并豁免必须写清理由" in item for item in merge.bundle_errors("A-C1", checks[0])))

    def test_matrix_rejects_filler_not_applicable_reason(self):
        objects = ["OBJ-01"]
        checks = [{"checkId": "A-C1", "objectId": "OBJ-01", "dimension": dimension,
                   "disposition": "not_applicable", "reason": "不适用"} for dimension in merge.DIMENSION_ORDER]
        self.assertEqual(len(merge.matrix_errors(objects, checks)), len(merge.DIMENSIONS))
        for check in checks:
            check["reason"] = "该对象是请求体 DTO，没有进行中实例、定时任务和并发写入者，仅承载字段透传"
        self.assertEqual(merge.matrix_errors(objects, checks), [])
        checks[0]["disposition"] = "unknown"
        self.assertEqual(merge.matrix_errors(objects, checks), [])

    def test_packet_renders_contract_window_text_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            run = root / "run"
            baseline.mkdir()
            (run / "cases/sources").mkdir(parents=True)
            (baseline / "A.java").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            (run / "cases/sources/prd.md").write_text("规则原文\n", encoding="utf-8")
            plan = {"schemaVersion": 1, "inputHash": "snapshot", "tasks": [{
                "taskId": "A", "attempt": 1, "objectIds": ["O1"],
                "windows": [{"objectId": "O1", "refs": [f"A.java:{index}-{index}" for index in (1, 2, 3, 4)]}]}]}
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            packet_path = run / "cases/exploration/packets/A.json"
            command = [sys.executable, str(Path(merge.__file__).with_name("generation.py")), "packet",
                       "--task", "A", "--plan", str(plan_path), "--baseline", str(baseline),
                       "--run", str(run), "--prd", "cases/sources/prd.md", "--source-id", "PRD",
                       "--out", str(packet_path)]
            created = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(created.returncode, 0, created.stderr)
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertIn("schemaVersion", packet["requiredFields"]["sourceUnits"])
            self.assertIn("sourceIds", packet["requiredFields"]["sourceUnits"])
            self.assertIn("reason", packet["requiredFields"]["checks"])
            self.assertEqual(packet["windows"][0]["text"], "one")
            self.assertEqual(packet["windows"][0]["evidenceRef"], "A.java:1-1")
            self.assertIn(";", packet["evidenceRef"])
            self.assertIn("JSON 数组", packet["evidenceRef"])
            fields = {row["field"] for row in packet["emptyFields"]}
            self.assertIn("reviewRef", fields)
            self.assertIn("approvalRef", fields)
            self.assertEqual(merge.check_receipt(packet, packet["sample"]), [])
            receipt_path = Path(packet["output"])
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            sample = json.loads(json.dumps(packet["sample"]))
            sample["checks"][0]["evidenceRef"] = ["A.java:1-1"]
            receipt_path.write_text(json.dumps(sample), encoding="utf-8")
            blocked = subprocess.run([sys.executable, str(Path(merge.__file__)), "--task", "A",
                                      "--check", str(receipt_path), "--packet", str(packet_path)],
                                     capture_output=True, text=True)
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("JSON 数组", blocked.stdout)
            sample["checks"][0]["evidenceRef"] = "A.java:1-1"
            del sample["checks"][0]["reason"]
            receipt_path.write_text(json.dumps(sample), encoding="utf-8")
            missing = subprocess.run([sys.executable, str(Path(merge.__file__)), "--task", "A",
                                      "--check", str(receipt_path), "--packet", str(packet_path)],
                                     capture_output=True, text=True)
            self.assertIn("reason", missing.stdout)
            receipt_path.write_text(json.dumps(packet["sample"]), encoding="utf-8")
            passed = subprocess.run(shlex.split(packet["checkCommand"]), capture_output=True, text=True)
            self.assertEqual(passed.returncode, 0, passed.stdout)
            receipt_path.write_bytes(b"")
            empty = subprocess.run([sys.executable, str(Path(merge.__file__)), "--stat",
                                    "--plan", str(plan_path), "--run", str(run)],
                                   capture_output=True, text=True)
            self.assertEqual(empty.returncode, 2)
            self.assertEqual(json.loads(empty.stdout)["empty"], ["cases/exploration/A/1.json"])

    def test_plan_needs_resolvable_windows_before_dispatch(self):
        plan = {"schemaVersion": 1, "inputHash": "h", "tasks": [{"taskId": "A", "attempt": 1,
                                                                   "objectIds": ["OBJ-01"]}]}
        self.assertTrue(any("缺少 windows" in item for item in merge.window_errors(plan)))
        plan["tasks"][0]["windows"] = [{"objectId": "OBJ-01", "refs": ["A.java:1-2"] * 3}]
        errors = merge.window_errors(plan)
        self.assertTrue(any("应有 4 到 6 个窗口" in item for item in errors))
        plan["tasks"][0]["windows"] = [{"objectId": "OBJ-01", "refs": ["A.java:1-2", "A.java:3-5",
                                                                        "A.java:7-9", "A.java:11-13"]}]
        self.assertEqual(merge.window_errors(plan), [])
        plan["tasks"][0]["windows"][0]["refs"] = ["A.java:1-5"] + ["A.java:7-9"] * 3
        self.assertTrue(any("跨度超过 3 行" in item for item in merge.window_errors(plan)))



if __name__ == "__main__":
    unittest.main()
