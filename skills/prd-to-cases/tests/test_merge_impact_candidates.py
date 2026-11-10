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
        self.a["checks"] = [{"checkId": "C", "evidenceRef": "file:1"}]
        self.b["checks"] = [{"checkId": "C", "evidenceRef": "file:1"}]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertEqual(result["status"], "READY_FOR_REVIEW")
        self.assertEqual(result["candidates"]["checks"]["C"][0]["tasks"], ["A", "B"])

    def test_conflicts_and_missing_receipts_do_not_disappear(self):
        self.a["checks"] = [{"checkId": "C", "reason": "one"}]
        self.b["checks"] = [{"checkId": "C", "reason": "two"}]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["candidates"]["checks"]["C"]), 2)
        self.assertEqual(merge.collect(self.plan, [self.a])["errors"], ["missing receipt: B"])

    def test_stale_attempt_and_missing_group_block(self):
        self.a["attempt"] = 1
        del self.b["handoffs"]
        result = merge.collect(self.plan, [self.a, self.b])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("stale" in e for e in result["errors"]))
        self.assertIn("B: missing handoffs", result["errors"])


if __name__ == "__main__":
    unittest.main()
