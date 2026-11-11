import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/coverage_check.py"
PRD = "1. 开关默认关闭，由配置人手动开启。\n2. 开启后驳回人必须选择一种重新审批方式。\n"


def write(root, name, data):
    path = Path(root) / "cases" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def build(root, extra_ac=None, matrix_cells=None, ac_text="开关默认关闭", assertions=None):
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
    cells = matrix_cells if matrix_cells is not None else [
        {"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
        {"values": {"方式": "继续审批"}, "status": "not_applicable", "reason": "本轮不涉及继续审批"},
    ]
    write(root, "coverage-matrix.json", {"dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                                        "cells": cells})


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

    def test_blocks_on_matrix_hole(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, matrix_cells=[{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]}])
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("组合矩阵缺单元", done.stdout)

    def test_blocks_on_uncovered_gap_cell_and_missing_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp,
                  extra_ac=[{"acId": "AC-1-02", "requirementId": "REQ-1", "sourceRef": "cases/sources/prd.md:2"}],
                  matrix_cells=[{"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                                {"values": {"方式": "继续审批"}, "status": "gap", "reason": "缺少继续审批用例"}])
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("验收标准没有场景：AC-1-02", done.stdout)
            self.assertIn("组合矩阵单元缺口", done.stdout)

    def test_blocks_on_person_quantifier_stronger_than_ac(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, ac_text="由其任意一人审批即可", assertions=[
                {"stepId": "s1", "expected": "甲、乙均可看到并处理该待办"}])
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("人员量词「均可」", done.stdout)

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
            build(tmp, matrix_cells=[])
            write(tmp, "coverage-matrix.json", {
                "dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                "coveredBy": [{"caseIds": ["CASE-01"], "values": {"方式": "重新发起"}}],
                "cells": [{"values": {"方式": "继续审批"}, "status": "not_applicable", "reason": "本轮不涉及"}],
            })
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_matrix_cell_cap_blocks_and_can_be_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, matrix_cells=[])
            write(tmp, "coverage-matrix.json", {
                "dimensions": [{"name": f"d{i}", "values": ["a", "b"]} for i in range(7)],
                "coveredBy": [{"caseIds": ["CASE-01"], "values": {}}],
            })
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("超过上限", done.stdout)
            allowed = run(tmp, "--allow-large-matrix")
            self.assertNotIn("超过上限", allowed.stdout)

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
                {"values": {"方式": "继续审批"}, "status": "out_of_scope",
                 "reason": "本轮不涉及从当前节点继续审批，沿用基线行为"}])
            write(tmp, "decision-table.json", {
                "schemaVersion": 1,
                "dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                "rows": [{"values": {"方式": "重新发起"}, "status": "covered",
                          "scenarios": ["SCN-01"], "expected": "弹窗出现两种方式"},
                         {"values": {"方式": "继续审批"}, "status": "not_applicable",
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
            build(tmp, matrix_cells=[
                {"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                {"values": {"方式": "继续审批"}, "status": "unknown",
                 "reason": "PRD 没有规定从当前节点继续审批时任务归谁，需产品确认"}])
            write(tmp, "decision-table.json", {
                "schemaVersion": 1,
                "dimensions": [{"name": "方式", "values": ["重新发起", "继续审批"]}],
                "rows": [{"values": {"方式": "重新发起"}, "status": "covered",
                          "scenarios": ["SCN-01"], "expected": "弹窗出现两种方式"},
                         {"values": {"方式": "继续审批"}, "status": "unknown", "risk": "high",
                          "reason": "PRD 没有规定从当前节点继续审批时任务归谁，需产品确认"}],
            })
            done = run(tmp)
            self.assertEqual(done.returncode, 0, done.stdout)

    def test_unregistered_unknown_cell_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, matrix_cells=[
                {"values": {"方式": "重新发起"}, "status": "covered", "caseIds": ["CASE-01"]},
                {"values": {"方式": "继续审批"}, "status": "unknown",
                 "reason": "PRD 没有规定从当前节点继续审批时任务归谁，需产品确认"}])
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("未定论", done.stdout)

    def test_requires_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp)
            (Path(tmp) / "cases/coverage-matrix.json").unlink()
            done = run(tmp)
            self.assertEqual(done.returncode, 2)
            self.assertIn("coverage-matrix.json", done.stdout)


if __name__ == "__main__":
    unittest.main()
