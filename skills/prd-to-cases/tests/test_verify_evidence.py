import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_evidence.py"
spec = importlib.util.spec_from_file_location("verify_evidence", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerifyEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.baseline = self.root / "baseline"
        self.baseline.mkdir()
        (self.baseline / "src").mkdir()
        self.source = self.baseline / "src/Service.java"
        self.source.write_text("\n".join(f"line {index}" for index in range(1, 21)), encoding="utf-8")
        self.run = self.root / "run"

    def document(self, *refs):
        return {"checks": [{"checkId": f"C{index}", "evidenceRef": ref}
                           for index, ref in enumerate(refs, start=1)]}

    def test_real_window_passes_and_fabricated_path_fails(self):
        result = verify.verify(self.document("src/Service.java:10-12"), self.baseline)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["verified"], 1)
        result = verify.verify(self.document("src/Missing.java:10-12"), self.baseline)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("文件不存在", result["failures"][0]["reason"])

    def test_line_range_and_blank_window_are_rejected(self):
        result = verify.verify(self.document("src/Service.java:19-24"), self.baseline)
        self.assertIn("行号越界", result["failures"][0]["reason"])
        self.source.write_text("a\n\n\nb", encoding="utf-8")
        result = verify.verify(self.document("src/Service.java:2-3"), self.baseline)
        self.assertIn("窗口为空行", result["failures"][0]["reason"])

    def test_run_relative_reference_and_merged_candidates(self):
        (self.run / "cases/sources").mkdir(parents=True)
        (self.run / "cases/sources/prd.md").write_text("PRD 原文第 1 行\n第 2 行", encoding="utf-8")
        merged = {"candidates": {"checks": {"A-C1": [{"value": {
            "checkId": "A-C1", "evidenceRef": "run://cases/sources/prd.md:1-2"}, "tasks": ["A"]}]},
            "sourceUnits": {"A-U1": [{"value": {"unitId": "A-U1", "evidenceRef": "src/Service.java:3"},
                                      "tasks": ["A"]}]}}}
        result = verify.verify(merged, self.baseline, self.run)
        self.assertEqual(result["status"], "PASSED", result["failures"])
        self.assertEqual(result["verified"], 2)

    def test_cli_writes_evidence_check_under_run(self):
        merged = self.root / "merged.json"
        merged.write_text(json.dumps(self.document("src/Service.java:1")), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT), str(merged), "--baseline", str(self.baseline),
                                 "--run", str(self.run)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads((self.run / "cases/exploration/evidence-check.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "PASSED")
        merged.write_text(json.dumps(self.document("src/Nope.java:1")), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT), str(merged), "--baseline", str(self.baseline),
                                 "--run", str(self.run)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads((self.run / "cases/exploration/evidence-check.json")
                                    .read_text(encoding="utf-8"))["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
