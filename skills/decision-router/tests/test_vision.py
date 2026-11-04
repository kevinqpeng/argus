import json
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import jev
import decisions


def png(width=300, height=300):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = (b"\x00" + b"\xff\xff\xff" * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class VisionTest(unittest.TestCase):
    def test_payload_and_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.png"
            path.write_bytes(png())
            data = {"caseId": "CASE-1", "state": {"target": "submit button", "acceptance": "fully visible"}, "images": {"screenshot": str(path)}, "questions": {"visual_obscured": "ignored", "visual_truncated": "ignored"}}
            payload, hashes = jev.build_request(data, "jev-1.14.0")
            self.assertEqual(payload["model"], "jev-1.14.0")
            self.assertEqual(len(payload["assets"]), 1)
            self.assertIn(payload["state"]["screenshot"], payload["assets"])
            self.assertEqual(len(payload["questions"]), 2)
            self.assertEqual(len(hashes["screenshot"]), 64)
            with self.assertRaises(ValueError): jev.build_request(data, None)
            with self.assertRaises(ValueError): jev.build_request({**data, "caseId": ""}, "jev-1.14.0")

    def test_invalid_image_and_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.png"
            path.write_bytes(png(100, 300))
            data = {"caseId": "CASE-1", "state": {"target": "x", "acceptance": "x"}, "images": {"screenshot": str(path)}, "questions": {"visual_obscured": "x"}}
            with self.assertRaises(ValueError): jev.build_request(data, "jev-1.14.0")
            with path.open("wb") as image_file:
                image_file.truncate(16 * 1024 * 1024 + 1)
            with self.assertRaises(ValueError): jev.build_request(data, "jev-1.14.0")
            path.write_bytes(b"not an image")
            with self.assertRaises(ValueError): jev.build_request(data, "jev-1.14.0")
            data["images"] = {}
            with self.assertRaises(ValueError): jev.build_request(data, "jev-1.14.0")

    def test_http_visual_text_and_failures(self):
        class Response(io.BytesIO):
            def __init__(self, body, content_type="application/json"):
                super().__init__(body)
                self.headers = mock.Mock()
                self.headers.get_content_type.return_value = content_type
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "s.png"
            image.write_bytes(png())
            config = root / "config.json"
            config.write_text(json.dumps({"config": {"decision": {"visualModel": "jev-1.14.0"}}}))
            source = root / "input.json"
            raw, out = root / "raw.json", root / "out.json"
            visual = {"caseId": "CASE-1", "state": {"target": "button", "acceptance": "visible"}, "images": {"screenshot": str(image)}, "questions": {"visual_obscured": "button", "visual_truncated": "button"}}
            source.write_text(json.dumps(visual))
            response = {"model": "jev-1.14.0", "answers": {k: {"type": "noul", "noul": .9} for k in visual["questions"]}}
            argv = ["jev.py", str(source), "--run-config", str(config), "--raw", str(raw), "--output", str(out)]
            with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test"}), mock.patch.object(sys, "argv", argv), mock.patch("urllib.request.urlopen", return_value=Response(json.dumps(response).encode())) as http, mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(jev.main(), 0)
                request = http.call_args.args[0]
                self.assertEqual(request.get_header("Typesafe-danger-zone-images"), "true")
                sent = json.loads(request.data)
                self.assertEqual(sent["model"], "jev-1.14.0")
                self.assertEqual(len(sent["questions"]), 2)
                self.assertEqual(len(sent["assets"]), 1)
            raw.unlink(); out.unlink()
            source.write_text(json.dumps({"state": "text", "questions": {"ui": "Does this require UI?"}}))
            response = {"model": "jev-1.13.0", "answers": {"ui": {"type": "noul", "noul": .9}}}
            with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test"}), mock.patch.object(sys, "argv", argv), mock.patch("urllib.request.urlopen", return_value=Response(json.dumps(response).encode())) as http, mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(jev.main(), 0)
                self.assertEqual(json.loads(http.call_args.args[0].data)["model"], "jev-latest")
                self.assertIsNone(http.call_args.args[0].get_header("Typesafe-danger-zone-images"))
            with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}), mock.patch.object(sys, "argv", argv), mock.patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(jev.main(), 2)
            for failure in [urllib.error.HTTPError("url", 401, "unauthorized", {}, None), urllib.error.HTTPError("url", 429, "rate limit", {}, None), TimeoutError("timeout"), Response(b"<html>", "text/html"), Response(b"{}"), Response(b"{bad json"), Response(json.dumps({"model": "jev-1.13.0", "answers": {"ui": {"type": "noul", "noul": 1.5}}}).encode())]:
                raw.unlink(missing_ok=True); out.unlink(missing_ok=True)
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test"}), mock.patch.object(sys, "argv", argv), mock.patch("urllib.request.urlopen", **kwargs), mock.patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(jev.main(), 2)
                    self.assertFalse(out.exists())

    def test_choice_requires_applicability_and_is_reviewed(self):
        q = decisions.DEFINITIONS["questions"]["visual_state"]
        answer = {"choice": "other", "probabilities": {"first": .05, "second": .05, "other": .9}, "probabilityKind": "calibrated"}
        self.assertEqual(decisions.route(answer, q)["route"], "vision-review")
        with self.assertRaises(ValueError):
            decisions.route({**answer, "probabilities": {"first": .8, "second": .8, "other": .1}}, q)
        with self.assertRaises(ValueError):
            jev.build_request({"caseId": "CASE-1", "state": {"states": {"first": "ready", "second": "loading"}}, "images": {"screenshot": "x"}, "questions": {"visual_state": "x"}}, "jev-1.14.0")

    def test_visual_routes_never_pass(self):
        q = decisions.DEFINITIONS["questions"]["visual_obscured"]
        for value, label in [(0.9, "yes"), (0.1, "no"), (0.5, "uncertain")]:
            result = decisions.route({"noul": value, "probabilityKind": "calibrated"}, q)
            self.assertEqual(result["judgment"], label)
            self.assertEqual(result["route"], "vision-review")
        with self.assertRaises(ValueError): decisions.route({"noul": float("nan"), "probabilityKind": "calibrated"}, q)

if __name__ == "__main__": unittest.main()
