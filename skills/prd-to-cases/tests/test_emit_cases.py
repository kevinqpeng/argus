import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/emit_cases.py"
spec = importlib.util.spec_from_file_location("emit_cases", SCRIPT)
emit_cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(emit_cases)

generation_spec = importlib.util.spec_from_file_location("generation", ROOT / "scripts/generation.py")
generation = importlib.util.module_from_spec(generation_spec)
generation_spec.loader.exec_module(generation)

HEADER = "\t".join(emit_cases.HEADERS)


def row(**fields):
    values = {key: "" for key in emit_cases.HEADERS}
    values.update(fields)
    return "\t".join(values[key] for key in emit_cases.HEADERS)


class EmitCasesTest(unittest.TestCase):
    def sample(self):
        return "\n".join([
            HEADER,
            row(caseId="C1", scenarioId="S1", title="甲驳回", priority="P0", suite="ui",
                spec="reject.spec.ts", fixtures="甲已登录", stepId="s1", kind="setup",
                action="甲打开待办", exemption="只准备待办"),
            row(caseId="C1", stepId="s2", action="甲驳回", expected="待办消失"),
        ]) + "\n"

    def test_check_blocks_a_step_without_assertion_before_writing(self):
        text = "\n".join([
            HEADER,
            row(caseId="C1", scenarioId="S1", title="甲驳回", priority="P0", suite="ui",
                spec="reject.spec.ts", stepId="s1", action="甲打开待办", expected="看到待办"),
            row(caseId="C1", stepId="s2", action="甲驳回"),
        ]) + "\n"
        with self.assertRaisesRegex(ValueError, "s2: missing assertion"):
            emit_cases.convert(text)

    def test_rows_become_a_sealed_case(self):
        cases = emit_cases.convert(self.sample(), ["AC-1"])
        self.assertEqual(cases[0]["automationRef"], "planned:reject.spec.ts::甲驳回")
        task = {"scenarioIds": ["S1"], "acIds": ["AC-1"]}
        generation.validate_case(cases[0], task, "draft")
        generation.validate_automation_ref(cases[0], task, 1, {"cases.json": "x"}, "cases")

    def test_constant_fixture_and_foreign_ac_fail_early(self):
        text = "\n".join([
            HEADER,
            row(caseId="C1", scenarioId="S1", title="甲驳回", priority="P0", suite="ui",
                spec="reject.spec.ts", fixtures="status=REJECTED", stepId="s1",
                action="查看 AC-2", expected="仍是已驳回"),
        ]) + "\n"
        with self.assertRaisesRegex(ValueError, "constant"):
            emit_cases.convert(text)
        with self.assertRaisesRegex(ValueError, "AC outside this slice"):
            emit_cases.convert(text.replace("status=REJECTED", "甲已登录"), ["AC-1"])

    def test_cli_check_writes_nothing_and_out_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "G1.rows.tsv"
            source.write_text(self.sample(), encoding="utf-8")
            checked = subprocess.run([sys.executable, str(SCRIPT), "--tsv", str(source), "--check"],
                                     capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout)
            self.assertEqual(json.loads(checked.stdout)["cases"], 1)
            self.assertEqual(list(root.iterdir()), [source])
            target = root / "G1" / "1" / "cases.json"
            written = subprocess.run([sys.executable, str(SCRIPT), "--tsv", str(source), "--out", str(target)],
                                     capture_output=True, text=True)
            self.assertEqual(written.returncode, 0, written.stdout)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))[0]["caseId"], "C1")

    def test_cheatsheet_quotes_the_script_headers_and_errors(self):
        text = (ROOT / "references/script-cheatsheet.md").read_text(encoding="utf-8")
        units = importlib.util.spec_from_file_location("source_units", ROOT / "scripts/source_units.py")
        source_units = importlib.util.module_from_spec(units)
        units.loader.exec_module(source_units)
        baseline = importlib.util.spec_from_file_location("assemble_baseline", ROOT / "scripts/assemble_baseline.py")
        assemble = importlib.util.module_from_spec(baseline)
        baseline.loader.exec_module(assemble)
        self.assertIn("\t".join(source_units.HEADERS), text)
        self.assertIn("\t".join(assemble.BINDINGS), text)
        self.assertIn("\t".join(emit_cases.HEADERS), text)
        for phrase in ("invalid baselineHash", "evidence span > 3 lines",
                       "同一 AC 的场景或优先级不一致", "同一需求的正文不一致",
                       "绑定表表头必须是", "TSV header must be unitId,sourceIds",
                       "TSV header must be caseId", "input overlaps worker directory",
                       "planned automationRef requires file::title",
                       "missing assertion or explained setup/cleanup exemption"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
