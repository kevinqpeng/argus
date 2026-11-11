import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "coverage_check.py"
sys.path.insert(0, str(SCRIPTS))
from coverage_check import AXIS_FAMILIES  # noqa: E402

PRD = "1. 开关默认关闭，由配置人手动开启。\n2. 开启后驳回人必须选择一种重新审批方式。\n"
WAIVER = "本需求只改审批开关文案，这一族形态在需求文本里没有任何对应行为可测"
OUT_OF_SCOPE = {
    "status": "out_of_scope",
    "reason": "跨租户场景不属于本次需求授权范围，由平台权限专项覆盖",
    "reviewer": "产品经理",
    "approvalRef": "REQ-评审-0031",
}


def write(root, name, data):
    path = Path(root) / "cases" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def axes(**families):
    """families: 族 → 维度名；其余形态族写豁免理由。"""
    declared = {key: {"dimension": name} for key, name in families.items()}
    for key in AXIS_FAMILIES:
        declared.setdefault(key, {"waiver": WAIVER})
    return declared


def matrix(dimensions, cells, families=None):
    return {"dimensions": dimensions, "cells": cells,
            "axes": axes(**(families or {"subject": dimensions[0]["name"]}))}


def build(root, extra_ac=None, matrix_data=None, matrix_cells=None, ac_text="开关默认关闭", assertions=None):
    run = Path(root)
    (run / "cases/sources").mkdir(parents=True, exist_ok=True)
    (run / "cases/sources/prd.md").write_text(PRD, encoding="utf-8")
    acs = [{"acId": "AC-1-01", "requirementId": "REQ-1", "sourceRef": "cases/sources/prd.md:1", "text": ac_text}]
    write(root, "acs.json", acs + (extra_ac or []))
    write(root, "scenarios.json", [{"scenarioId": "SCN-01", "title": "开关默认关闭", "acIds": ["AC-1-01"]}])
    write(root, "cases.json", [{
        "caseId": "CASE-01", "title": "开关默认关闭", "scenarioIds": ["SCN-01"],
        "steps": [{"id": "s1", "action": "进入模型编辑页", "kind": "action"}],
        "assertions": assertions or [{"stepId": "s1", "expected": "开关为关闭"}],
    }])
    write(root, "impact-analysis.json", {
        "sourceUnits": [{"unitId": "SU-1", "sourceExcerpt": "开关默认关闭", "evidenceRef": "cases/sources/prd.md:1",
                         "disposition": "covered", "obligationIds": ["OBL-1"]},
                        {"unitId": "SU-2", "sourceExcerpt": "必须选择方式", "evidenceRef": "cases/sources/prd.md:2",
                         "disposition": "covered", "obligationIds": ["OBL-2"]}],
        "obligations": [{"obligationId": "OBL-1", "acIds": ["AC-1-01"]},
                        {"obligationId": "OBL-2", "acIds": ["AC-1-01"]}],
    })
    if matrix_data is None and matrix_cells is not None:
        matrix_data = matrix([{"name": "方式", "values": ["重新发起", "继续审批"]}], matrix_cells)
    if matrix_data is None:
        matrix_data = matrix(
            [{"name": "方式", "values": ["重新发起", "继续审批"]}],
            [{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
             {"values": {"方式": "继续审批"}, "status": "not_applicable",
              "reason": "本轮需求不涉及继续审批分支的任何行为变化"}])
    write(root, "coverage-matrix.json", matrix_data)


def run(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), str(root), *args],
                          capture_output=True, text=True)


class CoverageCheckTest(unittest.TestCase):
    def test_passes_when_chain_and_matrix_are_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("PASSED", done.stdout)
            self.assertIn("覆盖率：综合", done.stdout)

    def test_blocks_on_matrix_hole(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, matrix_data=matrix(
                [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                [{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]}]))
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("组合矩阵缺单元", done.stdout)

    def test_blocks_on_uncovered_gap_cell_and_missing_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp,
                  extra_ac=[{"acId": "AC-1-02", "requirementId": "REQ-1", "sourceRef": "cases/sources/prd.md:2"}],
                  matrix_data=matrix(
                      [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                      [{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                       {"values": {"方式": "继续审批"}, "status": "gap", "reason": "缺少继续审批用例"}]))
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("验收标准没有场景：AC-1-02", done.stdout)
            self.assertIn("组合矩阵单元缺口", done.stdout)

    def test_blocks_when_a_morphology_axis_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "方式", "values": ["重新发起", "继续审批"]}]
            data = matrix(dims, [{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                                 {"values": {"方式": "继续审批"}, "status": "not_applicable",
                                  "reason": "本轮需求不涉及继续审批分支的任何行为变化"}])
            data["axes"].pop("lifecycle")
            build(tmp, matrix_data=data)
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("缺少形态轴「lifecycle」", done.stdout)
            self.assertIn("生命周期时点", done.stdout)

    def test_axis_waiver_needs_a_real_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "方式", "values": ["重新发起", "继续审批"]}]
            data = matrix(dims, [{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                                 {"values": {"方式": "继续审批"}, "status": "not_applicable",
                                  "reason": "本轮需求不涉及继续审批分支的任何行为变化"}])
            data["axes"]["trigger"] = {"waiver": "不适用"}
            build(tmp, matrix_data=data)
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("axes.trigger 缺豁免理由", done.stdout)

    def test_axis_can_be_declared_on_the_dimension(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "加签方向", "family": "subject", "values": ["无", "向前", "向后"]}]
            cells = [{"values": {"加签方向": value}, "status": "covered", "caseIds": ["CASE-01"]}
                     for value in ("无", "向前", "向后")]
            data = {"dimensions": dims, "cells": cells,
                    "axes": {key: {"waiver": WAIVER} for key in AXIS_FAMILIES if key != "subject"}}
            build(tmp, matrix_data=data)
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_out_of_scope_needs_reason_reviewer_and_approval_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "角色", "values": ["发起人", "外部审计员"]},
                    {"name": "方式", "values": ["重新发起", "继续审批"]}]
            cells = [{"values": {"角色": "发起人", "方式": "重新发起"}, "status": "covered",
                      "caseIds": ["CASE-01"]},
                     {"values": {"角色": "发起人", "方式": "继续审批"}, "status": "covered",
                      "caseIds": ["CASE-01"]},
                     {"values": {"角色": "外部审计员", "方式": "重新发起"}, "status": "covered",
                      "caseIds": ["CASE-01"]},
                     {"values": {"角色": "外部审计员", "方式": "继续审批"}, "status": "out_of_scope",
                      "reason": "跨租户场景不属于本次需求授权范围，由平台权限专项覆盖"}]
            families = {"subject": "角色", "target": "方式"}
            build(tmp, matrix_data=matrix(dims, cells, families=families))
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("out_of_scope 未经签署", done.stdout)
            self.assertIn("reviewer", done.stdout)
            cells[3] = dict(cells[3], **{k: v for k, v in OUT_OF_SCOPE.items() if k != "status"})
            build(tmp, matrix_data=matrix(dims, cells, families=families))
            done = run(tmp)
            self.assertNotIn("out_of_scope 未经签署", done.stdout)

    def test_blocks_when_a_value_has_only_signed_scope_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "加签方向", "values": ["无", "向前"]},
                    {"name": "驳回后路径", "values": ["经过原节点", "不经过原节点"]}]
            cells = [{"values": {"加签方向": "无", "驳回后路径": "经过原节点"}, "status": "covered",
                      "caseIds": ["CASE-01"]},
                     {"values": {"加签方向": "无", "驳回后路径": "不经过原节点"}, "status": "covered",
                      "caseIds": ["CASE-01"]}]
            for path in ("经过原节点", "不经过原节点"):
                cells.append(dict(OUT_OF_SCOPE, values={"加签方向": "向前", "驳回后路径": path}))
            build(tmp, matrix_data=matrix(dims, cells, families={"subject": "加签方向"}))
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("取值没有任何用例或不适用说明：加签方向=向前", done.stdout)

    def test_pairwise_pairs_must_be_settled(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "角色", "values": ["发起人", "审批人"]},
                    {"name": "开关", "values": ["开", "关"]}]
            cells = [{"values": {"角色": "发起人", "开关": "开"}, "status": "covered", "caseIds": ["CASE-01"]},
                     {"values": {"角色": "发起人", "开关": "关"}, "status": "covered", "caseIds": ["CASE-01"]},
                     {"values": {"角色": "审批人", "开关": "开"}, "status": "covered", "caseIds": ["CASE-01"]}]
            build(tmp, matrix_data=matrix(dims, cells, families={"subject": "角色", "lifecycle": "开关"}))
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("二维组合未定论：角色=审批人 × 开关=关", done.stdout)

    def test_not_applicable_needs_a_real_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "方式", "values": ["重新发起", "继续审批"]}]
            cells = [{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                     {"values": {"方式": "继续审批"}, "status": "not_applicable", "reason": "不涉及"}]
            build(tmp, matrix_data=matrix(dims, cells))
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("not_applicable 缺理由", done.stdout)

    def test_blocks_on_person_quantifier_stronger_than_ac(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, ac_text="由其任意一人审批即可", assertions=[
                {"stepId": "s1", "expected": "甲、乙均可看到并处理该待办"}])
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("人员量词「均可」", done.stdout)

    def test_blocks_on_quantifier_weaker_than_ac(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, ac_text="节点内所有人都会收到驳回待办", assertions=[
                {"stepId": "s1", "expected": "待办只出现在其中一个审批人的列表里"}])
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("或然量词", done.stdout)
            self.assertIn("来源是全员口径", done.stdout)

    def test_vague_quantifier_exclusion_needs_pending_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, ac_text="节点内所有人都会收到驳回待办", assertions=[
                {"stepId": "s1", "expected": "待办只出现在其中一个审批人的列表里"}])
            write(tmp, "quantifier-exclusions.json",
                  {"items": [{"caseId": "CASE-01", "token": "其中一个", "reason": "产品尚未裁决待办接收范围"}]})
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("pendingRef", done.stdout)
            write(tmp, "quantifier-exclusions.json",
                  {"items": [{"caseId": "CASE-01", "token": "其中一个", "reason": "产品尚未裁决待办接收范围",
                              "pendingRef": "REQ-待确认-0007"}]})
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_person_quantifier_ignores_record_counts_and_non_person_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, assertions=[
                {"stepId": "s1", "expected": "流转记录新增一条驳回条目，两条条目各自独立"},
                {"stepId": "s1", "expected": "两个固定方式且二者均可被选中"}])
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_quantifier_exclusion_needs_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, ac_text="由其任意一人审批即可", assertions=[
                {"stepId": "s1", "expected": "甲、乙均可看到并处理该待办"}])
            write(tmp, "quantifier-exclusions.json", {"items": [{"caseId": "CASE-01", "token": "均可", "reason": ""}]})
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("quantifier-exclusions.json 缺理由", done.stdout)
            write(tmp, "quantifier-exclusions.json",
                  {"items": [{"caseId": "CASE-01", "token": "均可", "reason": "来源在待确认事项中未定"}]})
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_covered_by_expands_and_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, matrix_data={
                "dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                "axes": axes(subject="方式"),
                "coveredBy": [{"caseIds": ["CASE-01"], "values": {"方式": "重新发起"}}],
                "cells": [{"values": {"方式": "继续审批"}, "status": "not_applicable",
                           "reason": "本轮需求不涉及继续审批分支的任何行为变化"}],
            })
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_hard_cell_cap_blocks_and_can_be_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": f"d{i}", "values": ["a", "b"]} for i in range(9)]
            build(tmp, matrix_data={"dimensions": dims, "axes": axes(subject="d0"),
                                    "coveredBy": [{"caseIds": ["CASE-01"], "values": {}}]})
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("超过硬上限", done.stdout)
            allowed = run(tmp, "--allow-large-matrix")
            self.assertNotIn("超过硬上限", allowed.stdout)

    def test_matrix_over_60_cells_no_longer_forces_cutting_a_dimension(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "角色", "values": ["发起人", "审批人", "管理员"]},
                    {"name": "路径", "values": ["p1", "p2", "p3", "p4", "p5"]},
                    {"name": "方式", "values": ["重新发起", "继续"]},
                    {"name": "开关", "values": ["开", "关"]}]
            build(tmp, matrix_data={"dimensions": dims,
                                    "axes": axes(subject="角色", lifecycle="开关", trigger="路径", target="方式"),
                                    "coveredBy": [{"caseIds": ["CASE-01"], "values": {}}]})
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_blocks_when_obligation_has_no_assertion_backed_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            impact = json.loads((Path(tmp) / "cases/impact-analysis.json").read_text(encoding="utf-8"))
            impact["obligations"].append({"obligationId": "OBL-3", "acIds": ["AC-9-99"]})
            write(tmp, "impact-analysis.json", impact)
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("OBL-3 的验收标准没有落到带断言的用例", done.stdout)

    def test_coverage_below_threshold_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "角色", "values": ["发起人", "审批人"]}]
            cells = [{"values": {"角色": "发起人"}, "status": "covered", "caseIds": ["CASE-01"]},
                     dict(OUT_OF_SCOPE, values={"角色": "审批人"})]
            build(tmp, matrix_data=matrix(dims, cells, families={"subject": "角色"}))
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("覆盖率未达阈值", done.stdout)
            self.assertIn("组合矩阵 50%", done.stdout)

    def test_coverage_threshold_is_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = [{"name": "角色", "values": ["发起人", "外部审计员"]},
                    {"name": "方式", "values": ["重新发起", "继续审批"]}]
            cells = [{"values": {"角色": "发起人", "方式": "重新发起"}, "status": "covered",
                      "caseIds": ["CASE-01"]},
                     {"values": {"角色": "发起人", "方式": "继续审批"}, "status": "covered",
                      "caseIds": ["CASE-01"]},
                     {"values": {"角色": "外部审计员", "方式": "重新发起"}, "status": "covered",
                      "caseIds": ["CASE-01"]},
                     dict(OUT_OF_SCOPE, values={"角色": "外部审计员", "方式": "继续审批"})]
            families = {"subject": "角色", "target": "方式"}
            build(tmp, matrix_data=matrix(dims, cells, families=families))
            blocked = run(tmp)
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("组合矩阵 75%", blocked.stdout)
            (Path(tmp) / "argus-model.json").write_text(
                json.dumps({"coverage": {"matrixCoverage": 0.75, "score": 0.7}}), encoding="utf-8")
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_matrix_without_decision_table_only_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout)
            self.assertIn("未写决策表", done.stdout)

    def test_decision_table_drift_from_the_matrix_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, matrix_cells=[
                {"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                {"values": {"方式": "继续审批"}, "status": "not_applicable",
                 "reason": "本轮不涉及从当前节点继续审批，沿用基线行为"}])
            write(tmp, "decision-table.json", {
                "schemaVersion": 1,
                "dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                "rows": [{"values": {"方式": "重新发起"}, "status": "covered",
                          "scenarios": ["SCN-01"], "expected": "弹窗出现两种方式"},
                         {"values": {"方式": "继续审批"}, "status": "out_of_scope",
                          "reason": "本轮不涉及从当前节点继续审批，沿用基线行为"}],
            })
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("决策表", done.stdout)

    def test_decision_table_covered_row_without_its_case_blocks_here_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            write(tmp, "decision-table.json", {
                "schemaVersion": 1,
                "dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                "rows": [{"values": {"方式": "重新发起"}, "status": "covered",
                          "scenarios": ["SCN-MISSING"], "expected": "弹窗出现两种方式"},
                         {"values": {"方式": "继续审批"}, "status": "not_applicable",
                          "reason": "本轮不涉及从当前节点继续审批，沿用基线行为"}],
            })
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("没有用例", done.stdout)

    def test_unknown_cell_registered_by_the_decision_table_is_not_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            roles = ["发起人", "审批人", "转办接手人", "加签处理人", "管理员",
                     "部门主管", "财务", "法务", "外部顾问", "审计员"]
            cells = [{"values": {"角色": role}, "status": "covered", "caseIds": ["CASE-01"]}
                     for role in roles[:-1]]
            cells.append({"values": {"角色": roles[-1]}, "status": "unknown",
                          "reason": "PRD 没有规定外部审计员驳回后任务归谁，需产品确认"})
            build(tmp, matrix_data=matrix(
                [{"name": "角色", "values": roles}], cells, families={"subject": "角色"}))
            rows = [{"values": {"角色": role}, "status": "covered", "scenarios": ["SCN-01"],
                     "expected": "驳回后出现两种处理方式"} for role in roles[:-1]]
            rows.append({"values": {"角色": roles[-1]}, "status": "unknown", "risk": "high",
                         "reason": "PRD 没有规定外部审计员驳回后任务归谁，需产品确认"})
            write(tmp, "decision-table.json", {
                "schemaVersion": 1,
                "dimensions": [{"name": "角色", "values": roles}],
                "rows": rows,
            })
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout)
            self.assertIn("组合矩阵 90%", done.stdout)

    def test_unregistered_unknown_cell_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, matrix_cells=[
                {"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                {"values": {"方式": "继续审批"}, "status": "unknown",
                 "reason": "PRD 没有规定从当前节点继续审批时任务归谁，需产品确认"}])
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("未定论", done.stdout)

    def test_p0_cases_must_appear_in_the_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            data = json.loads((Path(tmp) / "cases/cases.json").read_text(encoding="utf-8"))
            data[0]["priority"] = "P0"
            write(tmp, "cases.json", data)
            matrix_data = json.loads((Path(tmp) / "cases/coverage-matrix.json").read_text(encoding="utf-8"))
            matrix_data["cells"][0]["caseIds"] = []
            write(tmp, "coverage-matrix.json", matrix_data)
            blocked = run(tmp)
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("P0 用例没有落在组合矩阵任何单元", blocked.stdout)
            matrix_data["cells"] = matrix_data["cells"][1:]
            matrix_data["coveredBy"] = [{"caseIds": ["CASE-01"], "values": {"方式": "重新发起"}}]
            write(tmp, "coverage-matrix.json", matrix_data)
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_requires_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            (Path(tmp) / "cases/coverage-matrix.json").unlink()
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("coverage-matrix.json", done.stdout)


class PhaseGateWiringTest(unittest.TestCase):
    """覆盖自检必须由 cases 门禁强制执行，不能只靠 agent 记得跑脚本。"""

    def gate_gaps(self, tmp):
        sys.path.insert(0, str(SCRIPTS.parents[1] / "argus/scripts"))
        import phase_gate

        gate = phase_gate.Gate(Path(tmp))
        gate.cases()
        return gate.gaps

    def test_cases_gate_reports_coverage_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            data = json.loads((Path(tmp) / "cases/coverage-matrix.json").read_text(encoding="utf-8"))
            data["axes"].pop("lifecycle")
            (Path(tmp) / "cases/coverage-matrix.json").write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue([gap for gap in self.gate_gaps(tmp) if gap.startswith("用例覆盖未闭合")])

    def test_gate_skips_coverage_for_runs_without_generated_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            for name in ("acs.json", "scenarios.json", "coverage-matrix.json"):
                (Path(tmp) / "cases" / name).unlink()
            self.assertFalse([gap for gap in self.gate_gaps(tmp) if gap.startswith("覆盖率自检")])
            self.assertFalse([gap for gap in self.gate_gaps(tmp) if gap.startswith("用例覆盖未闭合")])


if __name__ == "__main__":
    unittest.main()
