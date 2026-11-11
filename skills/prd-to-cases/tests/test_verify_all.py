import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_all.py"
RENDER = Path(__file__).resolve().parents[1] / "scripts/render_review_table.py"


def results():
    import importlib.util
    spec = importlib.util.spec_from_file_location("render_review_table", RENDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HEAD_MAIN, module.RESULTS


class VerifyAllTest(unittest.TestCase):
    def test_delivery_reports_workbook_and_gates_together(self):
        headers, options = results()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases"
            cases.mkdir()
            (cases / "cases.json").write_text(json.dumps([
                {"caseId": "C1", "steps": [{"id": "S1", "action": "甲驳回"}],
                 "assertions": [{"expected": "从 B0 开始"}]}
            ]), encoding="utf-8")
            book = Workbook()
            sheet = book.active
            sheet.title = "用例主表"
            sheet.append(["场景"])
            sheet.append(["组", "标题", "前置", "步骤", "预期", "已测试"])
            (cases / "cases.xlsx").write_bytes(b"")
            book.save(cases / "cases.xlsx")
            wrong = subprocess.run([sys.executable, str(SCRIPT), str(root)],
                                   capture_output=True, text=True)
            self.assertEqual(wrong.returncode, 2)
            report = json.loads(wrong.stdout)
            self.assertEqual(report["sections"]["xlsx"]["status"], "BLOCKED")
            self.assertTrue(any("表头" in gap for gap in report["sections"]["xlsx"]["gaps"]))
            self.assertIn("coverage", report["sections"])

            sheet.delete_rows(1, sheet.max_row)
            sheet.append(headers)
            sheet.append(["组", "标题", "前置", "步骤", "预期", "未测试"])
            check = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=False)
            sheet.add_data_validation(check)
            check.add("F2:F2")
            book.save(cases / "cases.xlsx")
            presentation = cases / "presentation/1"
            presentation.mkdir(parents=True)
            book.save(presentation / "cases.xlsx")
            (presentation / "content.json").write_text(json.dumps({"cases": [{"caseId": "C1"}]}), encoding="utf-8")
            (presentation / "mapping.json").write_text(json.dumps({"cases": [{
                "caseId": "C1",
                "stepMapping": [{"sourceStepIds": ["S1"]}],
                "assertionMapping": [{"sourceIndexes": [0]}],
            }]}), encoding="utf-8")
            for name in ("scenarios.json", "acs.json", "impact-analysis.json",
                         "presentation/1/cases.md"):
                (cases / name).write_text("{}\n" if name.endswith(".json") else "正文\n", encoding="utf-8")
            (presentation / "content.json").write_text(
                (presentation / "content.json").read_text(encoding="utf-8"), encoding="utf-8")
            fixed = subprocess.run([sys.executable, str(SCRIPT), str(root), "--round", "1"],
                                   capture_output=True, text=True)
            body = json.loads(fixed.stdout)
            self.assertEqual(body["sections"]["xlsx"]["status"], "PASSED", body["sections"]["xlsx"])
            self.assertEqual(body["sections"]["mapping"]["status"], "PASSED", body["sections"]["mapping"])
            self.assertTrue(body["sections"]["hashes"]["hashes"])

    def test_draft_rejects_a_long_evidence_span_and_a_missing_ac_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases"
            cases.mkdir()
            (cases / "sources").mkdir()
            (cases / "acs.json").write_text(json.dumps([{"acId": "AC-1", "text": "从 B0 开始"}]), encoding="utf-8")
            (cases / "sources/units.json").write_text(json.dumps([
                {"unitId": "U1", "evidenceRef": "prd.md:1-5"}
            ]), encoding="utf-8")
            (cases / "impact-analysis.json").write_text(json.dumps({
                "sources": [],
                "obligations": [{"obligationId": "OB-1", "text": "驳回后路径变更，从起点重新审批",
                                 "expected": "新一轮开始", "acIds": [], "reviewStatus": "draft"}],
            }), encoding="utf-8")
            (cases / "decision-table.json").write_text("{}", encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(root), "--draft"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stdout)
            gaps = json.loads(result.stdout)["gaps"]
            self.assertTrue(any("evidence span > 3 lines" in gap for gap in gaps))
            self.assertTrue(any("当场写 acIds" in gap for gap in gaps))


if __name__ == "__main__":
    unittest.main()
