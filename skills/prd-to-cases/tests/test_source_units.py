import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/source_units.py"
spec = importlib.util.spec_from_file_location("source_units", SCRIPT)
units = importlib.util.module_from_spec(spec)
spec.loader.exec_module(units)

HEADER = "unitId\tsourceIds\tsourceExcerpt\tevidenceRef\tdisposition\tobligationIds\treason\treviewRef"


class SourceUnitsTest(unittest.TestCase):
    def test_converts_tsv_and_rejects_bad_rows(self):
        text = HEADER + "\nU1\tS1|S2\t原文\tprd:1-3\tcovered\tOB1\tbecause\tref\n"
        got = units.convert(text)
        self.assertEqual(got[0]["sourceIds"], ["S1", "S2"])
        self.assertEqual(got[0]["obligationIds"], ["OB1"])
        with self.assertRaises(ValueError):
            units.convert(text + "U1\tS1\t原文\tprd:1\tcovered\t\tbecause\tref\n")
        with self.assertRaises(ValueError):
            units.convert(HEADER + "\nU2\tS1\t原文\tprd:1\tmaybe\t\tbecause\tref\n")
        self.assertEqual(units.convert(HEADER + "\nU3\tS1\t原文\tprd:1-4\tcovered\t\tbecause\tref\n")[0]["evidenceRef"], "prd:1-4")

    def test_cli_writes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tsv = root / "units.tsv"
            tsv.write_text(HEADER + "\nU1\tS1\t原文\tprd:1\tunknown\t\tunknown\t\n", encoding="utf-8")
            out = root / "units.json"
            out.write_text("[]\n", encoding="utf-8")
            blocked = subprocess.run([sys.executable, str(SCRIPT), str(tsv), "-o", str(out)],
                                     capture_output=True, text=True)
            self.assertEqual(blocked.returncode, 2)
            self.assertEqual(out.read_text(encoding="utf-8"), "[]\n")
            fresh = root / "fresh.json"
            written = subprocess.run([sys.executable, str(SCRIPT), str(tsv), "-o", str(fresh)],
                                     capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(written.stdout)["count"], 1)
            self.assertEqual(json.loads(fresh.read_text(encoding="utf-8"))[0]["unitId"], "U1")


if __name__ == "__main__":
    unittest.main()
