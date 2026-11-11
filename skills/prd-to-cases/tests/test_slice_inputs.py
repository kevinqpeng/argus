import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/slice_inputs.py"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class SliceInputsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cases = self.root / "cases"
        write(cases / "cases.json", [
            {"caseId": "C1", "title": "甲驳回", "scenarioIds": ["S1"],
             "steps": [{"id": "S1", "action": "甲驳回"}],
             "assertions": [{"expected": "从 B0 开始"}]},
            {"caseId": "C2", "title": "乙通过", "scenarioIds": ["S2"],
             "steps": [{"id": "S2", "action": "乙通过"}],
             "assertions": [{"expected": "待办消失"}]},
        ])
        write(cases / "scenarios.json", [
            {"scenarioId": "S1", "acIds": ["AC-1"], "title": "驳回"},
            {"scenarioId": "S2", "acIds": ["AC-2"], "title": "通过"},
        ])
        write(cases / "acs.json", [
            {"acId": "AC-1", "text": "驳回后从 B0 重新审批"},
            {"acId": "AC-2", "text": "任一人通过即可"},
        ])
        write(cases / "impact-analysis.json", {
            "obligations": [
                {"obligationId": "OB-1", "acIds": ["AC-1"], "text": "路径变更",
                 "expected": "从 B0 开始", "anchors": {"start": "B0"}, "evidenceRef": "prd.md:1-2"},
                {"obligationId": "OB-2", "acIds": ["AC-2"], "text": "通过", "expected": "节点完成"},
            ],
            "sourceUnits": [
                {"unitId": "U1", "obligationIds": ["OB-1"], "sourceExcerpt": "从 B0 开始",
                 "evidenceRef": "prd.md:1-2"},
                {"unitId": "U2", "obligationIds": ["OB-2"], "sourceExcerpt": "通过即可",
                 "evidenceRef": "prd.md:3-3"},
            ],
            "checks": [{"checkId": "C", "reason": "整份影响分析不该进任务包"}],
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_packet_keeps_only_the_assigned_case(self):
        plan = self.root / "cases/presentation/1/plan.json"
        write(plan, {"slices": [
            {"name": "01", "kind": "cases", "caseIds": ["C1"],
             "output": "cases/presentation/1/slices/01/content.json", "checkCommand": "echo ok"},
            {"name": "notes", "kind": "notes", "caseIds": [],
             "output": "cases/presentation/1/slices/notes/content.json"},
        ]})
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.root), "--plan", str(plan)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        text = (self.root / "cases/presentation/1/slices/01/input.md").read_text(encoding="utf-8")
        self.assertIn("甲驳回", text)
        self.assertNotIn("乙通过", text)
        self.assertNotIn("整份影响分析不该进任务包", text)
        self.assertIn("B0", text)
        self.assertIn("prd.md:1-2", text)
        notes = (self.root / "cases/presentation/1/slices/notes/input.md").read_text(encoding="utf-8")
        self.assertNotIn("整份影响分析不该进任务包", notes)

    def test_generation_task_over_seven_cases_is_refused(self):
        plan = self.root / "cases/generation/plan.json"
        write(plan, {"tasks": [{
            "taskId": "G1", "caseIds": [f"C{i}" for i in range(8)],
            "scenarioIds": ["S1"], "dependsOn": [], "outputFiles": ["cases.json"],
        }]})
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.root), "--plan", str(plan)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("超过 7 条", result.stdout)
        self.assertFalse((self.root / "cases/generation/inputs/G1.md").exists())


if __name__ == "__main__":
    unittest.main()
