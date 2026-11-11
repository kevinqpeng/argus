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


WAIVERS = [
    {"kind": "surface", "waiver": "本表没有来源点名的界面文案、随选项变化的说明、可选字段或字数上限"},
    {"kind": "subtype", "waiver": "本表角色已是最细取值，检索后没有再拆开的子类型"},
    {"kind": "both-arms", "waiver": "本表没有第二条会改变结果的原因，选择轴本身已经完整"},
    {"kind": "identity", "waiver": "本表的规则不按标识回找变更前的原对象，没有改名或替换后的查找"},
]


def table(rows, defaults=None):
    return {"schemaVersion": 1, "dimensions": AXES, "rows": rows, "defaults": defaults or [],
            "expansions": WAIVERS}


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
        self.assertTrue(any("多个同级口径" in item for item in errors), errors)

    def test_a_partial_covered_row_cannot_stand_in_for_every_value(self):
        build(self.root, [{"values": {"方式": "重新发起"}, "status": "covered",
                           "scenarios": ["SCN-01"], "expected": "弹窗出现两种重新审批方式"},
                          {"values": {"方式": "继续审批"}, "status": "out_of_scope",
                           "reason": "本轮不含从当前节点继续审批的改造，该方式沿用基线行为"}],
              cases=[{"caseId": "CASE-01", "scenarioId": "SCN-01"}])
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("互斥取值", result.stdout)
        self.assertIn("角色", result.stdout)

    def test_the_more_specific_row_wins_over_the_slice(self):
        build(self.root, [{"values": {"角色": "原审批人", "方式": "重新发起"}, "status": "covered",
                           "scenarios": ["SCN-01"], "expected": "弹窗出现两种重新审批方式"},
                          {"values": {"角色": "转办接手人", "方式": "重新发起"}, "status": "covered",
                           "scenarios": ["SCN-02"], "expected": "转办接手人重新发起后从起点审批，路径不含接手人"},
                          {"values": {"方式": "继续审批"}, "status": "out_of_scope",
                           "reason": "本轮不含从当前节点继续审批的改造，该方式沿用基线行为"},
                          {"values": {"角色": "转办接手人", "方式": "继续审批"},
                           "status": "unknown", "risk": "high",
                           "reason": "PRD 没有规定转办接手人从当前节点继续审批时任务归谁"}])
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"]["covered"], 2)
        self.assertEqual(report["counts"]["out_of_scope"], 1)
        self.assertEqual(report["counts"]["unknown"], 1)
        self.assertEqual(len(report["pendingReview"]), 1)

    def test_one_scenario_cannot_cover_fifteen_cells(self):
        write(self.root, "decision-table.json", {
            "schemaVersion": 1,
            "dimensions": [{"name": "角色", "values": ["普通审批人", "转办接手人", "加签处理人"]},
                           {"name": "路径", "values": ["经过原驳回节点", "不经过原驳回节点",
                                                       "分支变更后仍经过原驳回节点",
                                                       "新增节点后仍经过原驳回节点",
                                                       "新增节点后不经过原驳回节点"]},
                           {"name": "方式", "values": ["重新发起", "继续审批"]},
                           {"name": "开关", "values": ["开启", "关闭"]}],
            "rows": [{"values": {"方式": "重新发起", "开关": "开启"}, "status": "covered",
                      "scenarios": ["SCN-01"], "expected": "驳回后重新发起时重新走审批流"},
                     {"values": {"方式": "重新发起", "开关": "关闭"}, "status": "covered",
                      "scenarios": ["SCN-02"], "expected": "开关关闭时只走原有的驳回后重新提交"},
                     {"values": {"方式": "继续审批", "开关": "开启", "角色": "普通审批人"},
                      "status": "covered", "scenarios": ["SCN-03"], "expected": "弹窗出现两种方式"},
                     {"values": {"方式": "继续审批", "开关": "开启", "角色": "转办接手人",
                                 "路径": "经过原驳回节点"},
                      "status": "unknown", "risk": "high",
                      "reason": "PRD 只规定转办接手人回到该审核人，没有给出任务归属"}],
            "defaults": [{"values": {"方式": "继续审批", "开关": "开启"}, "status": "out_of_scope",
                          "reason": "PRD 未给出路径变化或新增节点后的归属规则，本轮无验收标的"},
                         {"values": {"方式": "继续审批", "开关": "关闭"}, "status": "out_of_scope",
                          "reason": "开关关闭的实例不出现两种重新审批方式，与基线一致"}],
            "expansions": WAIVERS})
        result = run(self.root)
        self.assertEqual(result.returncode, 2)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any("SCN-01" in item and "互斥取值" in item for item in errors), errors)
        self.assertTrue(any("SCN-03" in item and "路径" in item for item in errors), errors)

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

    def test_a_table_without_expansions_is_blocked(self):
        data = table([row("原审批人", "重新发起", scenarios=["SCN-01"], expected="出现两种方式"),
                      row("原审批人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为"),
                      row("转办接手人", "重新发起", "out_of_scope", reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖该角色"),
                      row("转办接手人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为")])
        data.pop("expansions")
        write(self.root, "decision-table.json", data)
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("展开项缺失" in item and "surface" in item for item in errors), errors)

    def test_a_short_expansion_waiver_is_blocked(self):
        data = table([row("原审批人", "重新发起", scenarios=["SCN-01"], expected="出现两种方式"),
                      row("原审批人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为"),
                      row("转办接手人", "重新发起", "out_of_scope", reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖该角色"),
                      row("转办接手人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为")])
        data["expansions"] = [dict(item) for item in WAIVERS]
        data["expansions"][0] = {"kind": "surface", "waiver": "没有界面"}
        write(self.root, "decision-table.json", data)
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("豁免不足" in item and "surface" in item for item in errors), errors)

    def test_subtype_values_must_sit_on_an_axis(self):
        data = _closed()
        data["expansions"] = [dict(item) for item in WAIVERS]
        data["expansions"][1] = {"kind": "subtype", "parent": "加签处理人",
                                 "values": ["向前加签", "向后加签"], "evidence": "SignType.java:12"}
        write(self.root, "decision-table.json", data)
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("向前加签" in item and "不在任何维度" in item for item in errors), errors)

    def test_both_arms_cause_needs_its_own_axis(self):
        data = _closed()
        data["expansions"] = [dict(item) for item in WAIVERS]
        data["expansions"][2] = {"kind": "both-arms", "choice": "方式", "cause": "分支已改",
                                 "arms": ["重新发起", "继续审批"]}
        write(self.root, "decision-table.json", data)
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("cause" in item and "不同的轴" in item for item in errors), errors)

    def test_surface_items_each_need_a_scenario_with_a_case(self):
        data = _closed()
        data["expansions"] = [dict(item) for item in WAIVERS]
        data["expansions"][0] = {"kind": "surface", "object": "驳回弹窗", "items": [
            {"name": "选中方式的说明", "scenario": "SCN-12"},
            {"name": "抄送人", "scenario": "SCN-12"},
            {"name": "驳回意见五百字", "scenario": "SCN-13"}]}
        write(self.root, "decision-table.json", data)
        write(self.root, "cases.json", [{"caseId": "CASE-01", "scenarioId": "SCN-01"}])
        errors = json.loads(run(self.root).stdout)["errors"]
        self.assertTrue(any("SCN-12" in item and "同时负责" in item for item in errors), errors)
        self.assertTrue(any("SCN-13" in item and "没有用例" in item for item in errors), errors)

    def test_lookup_miss_keeps_one_parent_value(self):
        data = _closed()
        data["expansions"] = [dict(item) for item in WAIVERS]
        data["expansions"][1] = {"kind": "subtype", "parent": "转办接手人",
                                 "evidence": "检索无命中：SignType 与按钮文案"}
        write(self.root, "decision-table.json", data)
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_four_expansions_pass_when_values_sit_on_axes(self):
        dimensions = [{"name": "角色", "values": ["原审批人", "向前加签", "向后加签"]},
                      {"name": "方式", "values": ["重新发起", "继续审批"]},
                      {"name": "原因", "values": ["分支未改", "分支已改"]},
                      {"name": "原节点", "values": ["仍能找到并沿用", "标识改名后找不到", "定义被替换后找不到"]}]
        reason = "这一片沿用基线行为，本轮没有单独的规则变化"
        write(self.root, "decision-table.json", {
            "schemaVersion": 1,
            "dimensions": dimensions,
            "rows": [{"values": {"角色": "向前加签", "方式": "重新发起", "原因": "分支已改",
                                 "原节点": "仍能找到并沿用"},
                      "status": "covered", "scenarios": ["SCN-02"],
                      "expected": "分支改完后重新发起，走新分支"}],
            "defaults": [{"values": {"角色": role}, "status": "not_applicable", "reason": reason}
                         for role in ("原审批人", "向前加签", "向后加签")],
            "expansions": [
                {"kind": "surface", "object": "驳回弹窗", "items": [
                    {"name": "选中方式的说明", "scenario": "SCN-12"}]},
                {"kind": "subtype", "parent": "加签处理人", "values": ["向前加签", "向后加签"],
                 "evidence": "SignType.java:12"},
                {"kind": "both-arms", "choice": "方式", "cause": "分支已改",
                 "arms": ["重新发起", "继续审批"]},
                {"kind": "identity", "target": "原驳回节点",
                 "values": ["仍能找到并沿用", "标识改名后找不到", "定义被替换后找不到"]},
            ]})
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout)


def _closed():
    return table([row("原审批人", "重新发起", scenarios=["SCN-01"], expected="出现两种方式"),
                  row("原审批人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为"),
                  row("转办接手人", "重新发起", "out_of_scope", reason="转办接手人不在本轮角色范围内，PRD 没有要求覆盖该角色"),
                  row("转办接手人", "继续审批", "out_of_scope", reason="本轮不含从当前节点继续审批的改造，该方式沿用基线行为")])


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
            "defaults": [],
            "expansions": WAIVERS})
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
