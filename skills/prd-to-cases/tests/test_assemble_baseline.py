import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/assemble_baseline.py"
spec = importlib.util.spec_from_file_location("assemble_baseline", SCRIPT)
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)


class AssembleBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        (self.run / "cases/sources").mkdir(parents=True)
        self.prd = self.run / "cases/sources/prd.md"
        self.prd.write_text(
            "# 6.2 驳回\n\n| 规则 | 要求 |\n| --- | --- |\n| 驳回意见 | 必填 |\n| 附件 | 可选 |\n",
            encoding="utf-8")
        self.relative = "cases/sources/prd.md"

    def test_cover_blocks_a_must_row_until_it_has_an_ac(self):
        rows = assemble.load_rows(self.prd.read_text(encoding="utf-8"), self.relative)
        must = [row["ref"] for row in rows if row["must"]]
        self.assertEqual(must, [f"{self.relative}:5"])
        acs_path = self.run / "cases/acs.json"
        acs_path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "prd.md:5"):
            assemble.command_cover(self.run, self.relative, "cases/acs.json")
        acs_path.unlink()
        bindings = self.run / "bindings.tsv"
        with bindings.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(assemble.BINDINGS)
            writer.writerow([must[0], "AC-6.2-13", "REQ-6.2", "驳回意见必填", "P0",
                             "SCN-6.2-13", "驳回意见", "未填意见时不能提交"])
        assemble.command_ac(self.run, self.relative, "PRD", bindings, "draft")
        assemble.command_cover(self.run, self.relative, "cases/acs.json")
        acs = json.loads((self.run / "cases/acs.json").read_text(encoding="utf-8"))
        self.assertIn("run://cases/sources/prd.md:5", acs[0]["sourceRef"])

    def test_impact_uses_the_merged_receipt_shape(self):
        digest = assemble.file_hash(self.prd)
        merged = {"status": "READY_FOR_REVIEW", "candidates": {
            "sources": {"PRD": [{"value": {"sourceId": "PRD", "kind": "prd",
                                           "sourceRef": "run://cases/sources/prd.md", "sourceHash": digest},
                                 "tasks": ["A"]}]},
            "sourceUnits": {"A-U1": [{"value": {"unitId": "A-U1", "sourceIds": ["PRD"], "sourceExcerpt": "驳回意见必填",
                                                "evidenceRef": "cases/sources/prd.md:5", "disposition": "covered",
                                                "obligationIds": ["A-OB-1"], "reason": "校验项"}, "tasks": ["A"]}]},
            "objects": {}, "checks": {},
            "obligations": {"A-OB-1": [{"value": {"obligationId": "A-OB-1", "sourceIds": ["PRD"], "text": "驳回意见必填"},
                                        "tasks": ["A"]}]}}}
        path = self.run / "merged.json"
        path.write_text(json.dumps(merged), encoding="utf-8")
        assemble.command_impact(self.run, self.relative, "PRD", path)
        impact = json.loads((self.run / "cases/impact-analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(impact["sourceUnits"][0]["unitId"], "A-U1")
        self.assertEqual(impact["objects"], [])
        self.assertEqual(impact["obligations"][0]["reviewStatus"], "needs_clarification")
        blocked = subprocess.run([sys.executable, str(SCRIPT), "impact", "--run", str(self.run),
                                  "--prd", self.relative, "--source-id", "PRD", "--candidates", str(path)],
                                 capture_output=True, text=True)
        self.assertEqual(blocked.returncode, 0, blocked.stderr)

    def test_impact_keeps_the_cross_task_handoffs(self):
        digest = assemble.file_hash(self.prd)
        handoff = {"schemaVersion": 1, "handoffId": "A-H1", "producer": "A", "receiver": "",
                   "path": "模型设置 → 流程定义扩展表 → 发起时读取", "evidenceRef": "cases/sources/prd.md:5",
                   "question": "开关生效时点：跟发布快照还是发起时读模型？"}
        merged = {"status": "READY_FOR_REVIEW", "candidates": {
            "sources": {"PRD": [{"value": {"sourceId": "PRD", "kind": "prd",
                                           "sourceRef": "run://cases/sources/prd.md", "sourceHash": digest},
                                 "tasks": ["A"]}]},
            "sourceUnits": {}, "objects": {}, "checks": {}, "obligations": {},
            "handoffs": {"A-H1": [{"value": handoff, "tasks": ["A"]}]}}}
        path = self.run / "merged.json"
        path.write_text(json.dumps(merged), encoding="utf-8")
        assemble.command_impact(self.run, self.relative, "PRD", path)
        impact = json.loads((self.run / "cases/impact-analysis.json").read_text(encoding="utf-8"))
        self.assertEqual([row["handoffId"] for row in impact["handoffs"]], ["A-H1"])
        self.assertEqual(impact["handoffs"][0]["receiver"], "")
        self.assertEqual(impact["handoffs"][0]["question"], handoff["question"])

    def test_impact_refuses_a_handoff_without_its_evidence(self):
        digest = assemble.file_hash(self.prd)
        handoff = {"schemaVersion": 1, "handoffId": "A-H1", "producer": "A",
                   "path": "模型设置 → 流程定义扩展表", "question": "开关生效时点？"}
        merged = {"status": "READY_FOR_REVIEW", "candidates": {
            "sources": {"PRD": [{"value": {"sourceId": "PRD", "kind": "prd",
                                           "sourceRef": "run://cases/sources/prd.md", "sourceHash": digest},
                                 "tasks": ["A"]}]},
            "sourceUnits": {}, "objects": {}, "checks": {}, "obligations": {},
            "handoffs": {"A-H1": [{"value": handoff, "tasks": ["A"]}]}}}
        path = self.run / "merged.json"
        path.write_text(json.dumps(merged), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "交接项 A-H1 缺少 evidenceRef"):
            assemble.command_impact(self.run, self.relative, "PRD", path)
        self.assertFalse((self.run / "cases/impact-analysis.json").exists())

    def test_impact_blocks_a_hand_edited_object_without_six_dimensions(self):
        digest = assemble.file_hash(self.prd)
        merged = {"status": "READY_FOR_REVIEW", "candidates": {
            "sources": {"PRD": [{"value": {"sourceId": "PRD", "kind": "prd",
                                           "sourceRef": "run://cases/sources/prd.md", "sourceHash": digest},
                                 "tasks": ["A"]}]},
            "sourceUnits": {}, "obligations": {},
            "objects": {"OBJ-01": [{"value": {"objectId": "OBJ-01", "description": "驳回请求体"}, "tasks": ["A"]}]},
            "checks": {"A-C1": [{"value": {"checkId": "A-C1", "objectId": "OBJ-01", "dimension": "data",
                                           "sourceIds": ["PRD"], "evidenceRef": "cases/sources/prd.md:5",
                                           "disposition": "covered", "reason": "字段新增"}, "tasks": ["A"]}]}}}
        path = self.run / "merged.json"
        path.write_text(json.dumps(merged), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "六维留空 5/6 超过三分之一"):
            assemble.command_impact(self.run, self.relative, "PRD", path)
        self.assertFalse((self.run / "cases/impact-analysis.json").exists())

    def test_impact_registers_blank_cells_as_named_gaps(self):
        digest = assemble.file_hash(self.prd)
        row = {"sourceId": "PRD", "kind": "prd", "sourceRef": "run://cases/sources/prd.md", "sourceHash": digest}
        dimensions = [{"dimension": "data", "disposition": "covered", "reason": "字段新增"},
                      {"dimension": "permission", "disposition": "covered", "reason": "沿用列表权限"},
                      {"dimensions": ["inflight", "lifecycle", "side_effect", "ordering"][:3],
                       "disposition": "not_applicable",
                       "reason": "该开关仅随模型发布保存，无运行中实例、定时任务与并发写入，顺序与副作用不成立"}]
        merged = {"status": "READY_FOR_REVIEW", "candidates": {
            "sources": {"PRD": [{"value": row, "tasks": ["A"]}]},
            "sourceUnits": {}, "obligations": {},
            "objects": {"OBJ-01": [{"value": {"objectId": "OBJ-01", "description": "配置开关"}, "tasks": ["A"]}]},
            "checks": {f"A-C{index}": [{"value": {"checkId": f"A-C{index}", "objectId": "OBJ-01",
                                                 "evidenceRef": "cases/sources/prd.md:5", "sourceIds": ["PRD"], **item}, "tasks": ["A"]}]
                       for index, item in enumerate(dimensions, start=1)}}}
        path = self.run / "merged.json"
        path.write_text(json.dumps(merged), encoding="utf-8")
        assemble.command_impact(self.run, self.relative, "PRD", path)
        gaps = json.loads((self.run / "cases/exploration/impact-gaps.json").read_text(encoding="utf-8"))["gaps"]
        self.assertEqual([item["gapId"] for item in gaps], ["GAP-OBJ-01-ordering"])


if __name__ == "__main__":
    unittest.main()
