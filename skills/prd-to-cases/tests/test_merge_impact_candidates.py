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
            {"taskId": "A", "attempt": 2}, {"taskId": "B", "attempt": 1}]}
        self.a = {"schemaVersion": 1, "inputHash": "snapshot", "taskId": "A", "attempt": 2,
                  **{group: [] for group in merge.GROUPS}}
        self.b = {**self.a, "taskId": "B", "attempt": 1}

    def test_collects_identical_candidates_with_provenance(self):
        source = {"sourceId": "PRD", "kind": "prd", "sourceRef": "run://cases/sources/prd.md"}
        self.a["sources"] = [source]
        self.b["sources"] = [dict(source)]
        self.a["checks"] = [{"checkId": "A-C1", "dimension": "data", "evidenceRef": "file:1", "objectId": "O"}]
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


if __name__ == "__main__":
    unittest.main()
