import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/decision_table.py"
AXES = [{"name": "角色", "values": ["原审批人", "转办接手人"]},
        {"name": "方式", "values": ["重新发起", "继续审批"]}]


def write(root, name, data):
    path = Path(root) / "cases" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def row(role, way, status="covered", **extra):
    return {"values": {"角色": role, "方式": way}, "status": status, **extra}


def table(rows, defaults=None):
    return {"schemaVersion": 1, "dimensions": AXES, "rows": rows, "defaults": defaults or []}


def build(root, rows, cases=None, defaults=None):
    write(root, "decision-table.json", table(rows, defaults))
    if cases is not None:
        write(root, "cases.json", {"cases": cases})


def run(root, command="check"):
    return subprocess.run([sys.executable, str(SCRIPT), command, str(root)],
                          capture_output=True, text=True)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_product_with_judgements_passes(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="弹窗出现两种方式"),
                          row("原审批人", "继续审批", "out_of_scope", reason="本轮范围只覆盖驳回后重新发起这一条链路，继续审批不在需求内"),
                          row("转办接手人", "重新发起", "not_applicable", reason="转办接手人拿不到原驳回节点，无法触发重新发起入口"),
                          row("转办接手人", "继续审批", "unknown", risk="high",
                              reason="PRD 没有规定转办接手人从当前节点继续审批时的任务归属")])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["cells"], 4)
        self.assertEqual(len(report["pendingReview"]), 1)

    def test_unregistered_combo_blocks_without_defaults(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="出现两种方式")])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(any("未裁定" in item for item in json.loads(result.stdout)["errors"]))

    def test_defaults_fill_the_rest_and_stay_counted(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="出现两种方式"),
                          row("原审批人", "继续审批", "out_of_scope",
                              reason="从当前节点继续审批沿用基线行为，本轮没有改动")],
              defaults=[{"values": {"角色": "转办接手人"}, "status": "not_applicable",
                         "reason": "转办接手人看不到原驳回节点，两种方式都无从触发"}])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"]["not_applicable"], 2)
        self.assertEqual(report["unregistered"], [])

    def test_ambiguous_defaults_are_not_picked_silently(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="出现两种方式")],
              defaults=[{"values": {"角色": "转办接手人"}, "status": "not_applicable",
                         "reason": "转办接手人看不到原驳回节点，两种方式都无从触发"},
                        {"values": {"方式": "继续审批"}, "status": "out_of_scope",
                         "reason": "从当前节点继续审批沿用基线行为，本轮没有改动"}])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any("未裁定" in item for item in errors), errors)

    def test_covered_row_needs_scenario_and_expected(self):
        build(self.root, [row("原审批人", "重新发起", expected="出现两种方式")])
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("covered 但没有场景" in item for item in errors), errors)

    def test_undecided_row_needs_a_real_reason(self):
        build(self.root, [row("原审批人", "重新发起", "not_applicable", reason="不适用")])
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("理由不足" in item for item in errors), errors)

    def test_unknown_without_risk_does_not_become_a_review_item(self):
        build(self.root, [row("原审批人", "重新发起", "unknown", reason="PRD 没有说清这次驳回后任务归谁")])
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("必须写 risk" in item for item in errors), errors)

    def test_low_risk_unknown_folds_into_an_assumption(self):
        build(self.root, [row("原审批人", "重新发起", "unknown", risk="low",
                              reason="PRD 没有写按钮文案，但文案不改变验收结论")])
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("必须写 assumption" in item for item in errors), errors)

    def test_duplicate_and_off_axis_values_block(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="e"),
                          row("原审批人", "重新发起", scenarios=["SCN-02"], expected="e")])
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("行重复" in item for item in errors), errors)

    def test_covered_scenario_without_case_blocks(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="e")], cases=[])
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("没有用例" in item for item in errors), errors)


class ExpandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_expand_writes_the_full_cartesian_matrix(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="e"),
                          row("原审批人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为"),
                          row("转办接手人", "重新发起", "out_of_scope", reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖该角色"),
                          row("转办接手人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为")],
              cases=[{"caseId": "CASE-01", "scenarioId": "SCN-01"}])
        result = run(self.root, "expand")
        self.assertEqual(result.returncode, 0, result.stdout)
        matrix = json.loads((Path(self.root) / "cases/coverage-matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(len(matrix["cells"]), 4)
        covered = [cell for cell in matrix["cells"] if cell["status"] == "covered"]
        self.assertEqual(covered[0]["caseIds"], ["CASE-01"])
        self.assertTrue(all(cell.get("reason") for cell in matrix["cells"] if cell["status"] != "covered"))

    def test_expand_refuses_covered_cell_without_a_case(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="e"),
                          row("原审批人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为"),
                          row("转办接手人", "重新发起", "out_of_scope", reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖该角色"),
                          row("转办接手人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为")],
              cases=[{"caseId": "CASE-01", "scenarioId": "SCN-99"}])
        result = run(self.root, "expand")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((Path(self.root) / "cases/coverage-matrix.json").exists())

    def test_folded_unknown_persists_its_assumption(self):
        write(self.root, "decision-table.json", {
            "schemaVersion": 1,
            "dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
            "rows": [{"values": {"方式": "重新发起"}, "status": "covered",
                      "scenarios": ["SCN-01"], "expected": "弹窗出现两种方式"},
                     {"values": {"方式": "继续审批"}, "status": "unknown", "risk": "low",
                      "reason": "PRD 没有写继续审批的入口位置，但入口位置不改变验收结论",
                      "assumption": "按基线行为放在同一驳回弹窗内"}],
            "defaults": []})
        write(self.root, "cases.json", [{"caseId": "CASE-01", "scenarioId": "SCN-01"}])
        result = run(self.root, "expand")
        self.assertEqual(result.returncode, 0, result.stdout)
        note = (Path(self.root) / "cases/assumptions.md").read_text(encoding="utf-8")
        self.assertIn("按基线行为放在同一驳回弹窗内", note)
        self.assertIn("继续审批 | 按基线行为放在同一驳回弹窗内", note)
        self.assertTrue(json.loads(result.stdout)["assumptions"].endswith("cases/assumptions.md"))

    def test_no_folded_unknown_still_writes_the_section(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="出现两种方式"),
                          row("原审批人", "继续审批", "out_of_scope",
                              reason="从当前节点继续审批沿用基线行为，本轮没有改动"),
                          row("转办接手人", "重新发起", "out_of_scope",
                              reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖"),
                          row("转办接手人", "继续审批", "out_of_scope",
                              reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖")],
              cases=[{"caseId": "CASE-01", "scenarioId": "SCN-01"}])
        self.assertEqual(run(self.root, "expand").returncode, 0)
        note = (Path(self.root) / "cases/assumptions.md").read_text(encoding="utf-8")
        self.assertIn("本轮没有需要合并口径的未规定组合", note)

    def test_expand_needs_cases_before_it_can_claim_coverage(self):
        build(self.root, [row("原审批人", "重新发起", scenarios=["SCN-01"], expected="e"),
                          row("原审批人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为"),
                          row("转办接手人", "重新发起", "out_of_scope", reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖该角色"),
                          row("转办接手人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为")])
        result = run(self.root, "expand")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(any("先按决策表生成用例" in item for item in json.loads(result.stdout)["errors"]))


if __name__ == "__main__":
    unittest.main()
