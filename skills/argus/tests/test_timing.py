import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/timing.py"


class TimingTest(unittest.TestCase):
    def test_run_records_real_command_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "manifest.json").write_text("{}")
            result = subprocess.run([sys.executable, str(SCRIPT), str(run), "run",
                                     "--stage", "generation", "--exec", sys.executable,
                                     "-c", "raise SystemExit(3)"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 3, result.stderr)
            slot = json.loads((run / "manifest.json").read_text())["timing"]["generation"]
            self.assertLess(slot["startedAt"], slot["endedAt"])
            metric = json.loads((run / "timing.metrics.jsonl").read_text())
            self.assertEqual(metric["status"], "FAILED")

    def test_stamp_keeps_the_first_start_and_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "timing": {"review": {"approvedAt": "2026-11-11T10:00:00+08:00"}}
            }), encoding="utf-8")
            start = subprocess.run([sys.executable, str(SCRIPT), str(root), "stamp",
                                    "--stage", "exploration", "--event", "start"],
                                   capture_output=True, text=True)
            self.assertEqual(start.returncode, 0, start.stdout)
            first = json.loads(start.stdout)["startedAt"]
            again = subprocess.run([sys.executable, str(SCRIPT), str(root), "stamp",
                                    "--stage", "exploration", "--event", "start"],
                                   capture_output=True, text=True)
            self.assertEqual(json.loads(again.stdout)["startedAt"], first)
            end = subprocess.run([sys.executable, str(SCRIPT), str(root), "stamp",
                                  "--stage", "exploration", "--event", "end"],
                                 capture_output=True, text=True)
            self.assertEqual(end.returncode, 0, end.stdout)
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(saved["timing"]["review"]["approvedAt"], "2026-11-11T10:00:00+08:00")
            self.assertEqual(saved["timing"]["exploration"]["startedAt"], first)
            self.assertIn("endedAt", saved["timing"]["exploration"])
            missing = subprocess.run([sys.executable, str(SCRIPT), str(root), "stamp",
                                      "--stage", "publish", "--event", "end"],
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
