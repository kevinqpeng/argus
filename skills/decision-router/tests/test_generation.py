import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import decisions
import jev


class GenerationRoutingTest(unittest.TestCase):
    def request(self):
        return {"state": {"tasks": ["A", "B", "C"]}, "questions": {
            "A_needs_B": {"questionId": "generation_dependency", "subject": "A and B; evidence tasks/A.json and tasks/B.json"},
            "C_needs_B": {"questionId": "generation_dependency", "subject": "C and B; evidence tasks/C.json and tasks/B.json"},
            "A_conflicts_C": {"questionId": "generation_conflict", "subject": "A and C; evidence outputs.json"}}}

    def test_batch_preserves_template_and_subjects(self):
        data = self.request()
        payload, hashes = jev.build_request(data)
        self.assertFalse(hashes)
        self.assertEqual(set(payload["questions"]), set(data["questions"]))
        for name, binding in data["questions"].items():
            definition = jev.DEFINITIONS["questions"][binding["questionId"]]
            sent = payload["questions"][name]
            self.assertEqual(sent["instructions"], definition["instructions"] + "\nSubject: " + binding["subject"])
            self.assertEqual(sent["criteria"], definition["criteria"])
            self.assertEqual(sent["type"], "noul")

    def test_invalid_bindings_are_rejected(self):
        for binding in [{"questionId": "unknown", "subject": "A B"},
                        {"questionId": "generation_dependency", "subject": " "},
                        {"questionId": "generation_dependency", "subject": "A B", "instructions": "override"},
                        {"questionId": "visual_obscured", "subject": "button"},
                        {"questionId": [], "subject": "A B"}]:
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                jev.build_request({"state": {}, "questions": {"instance": binding}})

    def test_legacy_text_stays_compatible(self):
        payload, _ = jev.build_request({"state": "S1", "questions": {"ui": "Does S1 require observing UI?"}})
        self.assertEqual(payload["questions"]["ui"]["instructions"], "Does S1 require observing UI?")

    def test_batch_round_trip_and_uncertainty(self):
        data = self.request()
        response = {"model": "jev-test", "answers": {
            name: {"type": "noul", "noul": value}
            for name, value in zip(data["questions"], [.9, .1, .5])}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, raw, output = (root / name for name in ("input.json", "raw.json", "output.json"))
            source.write_text(json.dumps(data))
            http = mock.MagicMock()
            http.__enter__.return_value = io.StringIO(json.dumps(response))
            http.__enter__.return_value.headers = mock.Mock()
            http.__enter__.return_value.headers.get_content_type.return_value = "application/json"
            argv = ["jev.py", str(source), "--raw", str(raw), "--output", str(output)]
            with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test"}), mock.patch.object(sys, "argv", argv), mock.patch("urllib.request.urlopen", return_value=http), mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(jev.main(), 0)
            normalized = json.loads(output.read_text())
            self.assertEqual(normalized["questionBindings"], {name: binding["questionId"] for name, binding in data["questions"].items()})
            self.assertEqual(normalized["questionSetVersion"], "1.5.0")
            with mock.patch.object(sys, "argv", ["decisions.py", str(output)]), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(decisions.main(), 2)
                outcomes = json.loads(stdout.getvalue())["outcomes"]
            self.assertEqual([outcomes[name]["route"] for name in data["questions"]], ["yes", "no", "host-analysis"])

    def test_checker_rejects_invalid_bindings_and_old_version(self):
        current = {"schemaVersion": 1, "questionSetVersion": "1.5.0", "answers": {"ui": {"noul": .9, "probabilityKind": "calibrated"}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            for data in [current, {**current, "questionSetVersion": "1.4.0"},
                         {**current, "questionBindings": {}},
                         {**current, "questionBindings": {"ui": "unknown"}}]:
                path.write_text(json.dumps(data))
                with mock.patch.object(sys, "argv", ["decisions.py", str(path)]), mock.patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(decisions.main(), 0 if data == current else 2)


if __name__ == "__main__":
    unittest.main()
