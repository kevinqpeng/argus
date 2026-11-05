import csv
import json
import tempfile
import unittest
from pathlib import Path

from local_table import publish


class DeliveryNamesTest(unittest.TestCase):
    def test_delivery_files_are_named_and_scoped_to_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            project = state / "projects" / "app-01"
            def run(run_id, date, feature):
                config = project / "runs" / run_id / "argus-model.json"
                config.parent.mkdir(parents=True)
                config.write_text(json.dumps({"schemaVersion": 1, "runId": run_id,
                    "sources": {"stateRoot": str(state), "projectId": "app-01", "projectDir": str(project)},
                    "config": {"delivery": {"mode": "local"}}}))
                data = {"schemaVersion": 1, "runId": run_id, "bugs": [], "summary": {
                    "status": "passed", "scope": "full", "coverage": {}, "p0": {}, "counts": {},
                    "cleanup": {}, "limitations": [], "featureOverview": feature, "caseCount": 0,
                    "executedCount": 0, "passedCount": 0, "incompleteCases": [], "executor": "",
                    "executedAt": "2026-11-05T12:00:00+08:00"}}
                return publish(config, data, delivery_date=date, feature_name=feature)
            run("run1", "20261104", "旧功能")
            result = run("run2", "20261105", "审批意见")
            self.assertEqual(Path(result["bugDelivery"]).name, "20261105-审批意见-Bug列表.csv")
            summary = Path(result["summaryDelivery"])
            self.assertEqual(summary.name, "20261105-审批意见-测试汇总表.csv")
            with summary.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["测试功能概述"], "审批意见")
            self.assertTrue((project / "tables" / "runs.csv").is_file())


if __name__ == "__main__":
    unittest.main()
