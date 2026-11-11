import importlib.util
from pathlib import Path
import unittest


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
