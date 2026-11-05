#!/usr/bin/env python3
"""Call the built-in Jev decision provider with a key from the environment."""
import argparse
import base64
import hashlib
import subprocess
import time
import uuid
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from decisions import route

DEFINITIONS = json.loads((Path(__file__).resolve().parents[1] / "references/questions.json").read_text())
URL = "https://api.typesafe.ai/v1/systemone"


def normalize(response, questions):
    if not response.get("model") or set(response["answers"]) != set(questions):
        raise ValueError("missing model or answer mismatch")
    answers = {}
    for name, question in questions.items():
        answer = response["answers"][name]
        if answer.get("type") != question["type"]:
            raise ValueError(f"answer type mismatch: {name}")
        answers[name] = {key: value for key, value in answer.items() if key in ("noul", "choice", "score", "probabilities", "confidence")}
        answers[name]["probabilityKind"] = "calibrated"
    return {"schemaVersion": 1, "questionSetVersion": DEFINITIONS["questionSetVersion"], "answers": answers}



def build_request(data, visual_model=None):
    requested = data["questions"]
    if not isinstance(requested, dict) or not requested:
        raise ValueError("unknown or empty question set")
    bindings = {}
    for name, value in requested.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("question instance ID must be nonempty")
        if isinstance(value, dict):
            if set(value) != {"questionId", "subject"}:
                raise ValueError("question binding requires questionId and subject")
            question_id, subject = value["questionId"], value["subject"]
        else:
            question_id, subject = name, value
        if not isinstance(question_id, str) or question_id not in DEFINITIONS["questions"]:
            raise ValueError("unknown question template")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("question instructions must bind a subject")
        if isinstance(value, dict) and DEFINITIONS["questions"][question_id].get("visual"):
            raise ValueError("structured bindings support text questions only")
        bindings[name] = question_id
    visual = any(DEFINITIONS["questions"][qid].get("visual") for qid in bindings.values())
    if visual and any(not DEFINITIONS["questions"][qid].get("visual") for qid in bindings.values()):
        raise ValueError("visual and text questions must use separate requests")
    questions = {}
    for name, instruction in requested.items():
        definition = DEFINITIONS["questions"][bindings[name]]
        questions[name] = {k: v for k, v in definition.items() if k in ("type", "instructions", "criteria")}
        if not visual:
            questions[name]["instructions"] = (definition["instructions"] + "\nSubject: " + instruction["subject"]
                                                 if isinstance(instruction, dict) else instruction)
    payload = {"model": "jev-latest", "state": data["state"], "questions": questions}
    hashes = {}
    if visual:
        if visual_model != "jev-1.14.0":
            raise ValueError("frozen visual model must be jev-1.14.0")
        state = data["state"]
        if not isinstance(data.get("caseId"), str) or not data["caseId"].strip():
            raise ValueError("visual caseId is required")
        images = data.get("images")
        if not isinstance(state, dict) or not isinstance(images, dict) or set(images) != {"screenshot"} or "screenshot" in state:
            raise ValueError("visual request requires one screenshot and named state")
        if any(name in requested for name in ("visual_obscured", "visual_error_visible", "visual_truncated")):
            if any(not isinstance(state.get(k), str) or not state[k].strip() for k in ("target", "acceptance")):
                raise ValueError("visual target and acceptance are required")
        if "visual_state" in requested or "visual_state_applicable" in requested:
            states = state.get("states")
            if not isinstance(states, dict) or set(states) != {"first", "second"} or any(not isinstance(v, str) or not v.strip() for v in states.values()):
                raise ValueError("two described page states are required")
            if not {"visual_state", "visual_state_applicable"} <= set(requested):
                raise ValueError("page state requires its applicability check")
        path = Path(images["screenshot"])
        with path.open("rb") as image_file:
            blob = image_file.read(16 * 1024 * 1024 + 1)
        if len(blob) > 16 * 1024 * 1024:
            raise ValueError("image exceeds 16 MiB")
        if blob.startswith(b"\x89PNG\r\n\x1a\n"):
            fmt = "png"
            if b"acTL" in blob:
                raise ValueError("animated PNG is unsupported")
        elif blob.startswith(b"\xff\xd8\xff"):
            fmt = "jpeg"
        else:
            raise ValueError("image must be PNG or JPEG")
        try:
            info = subprocess.run(["sips", "-g", "format", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], capture_output=True, text=True, check=True, timeout=10)
            properties = dict(line.strip().split(": ", 1) for line in info.stdout.splitlines() if ": " in line)
            width, height = int(properties["pixelWidth"]), int(properties["pixelHeight"])
            if properties["format"] != ("jpeg" if fmt == "jpeg" else "png") or not all(280 <= n <= 1568 for n in (width, height)):
                raise ValueError("image format or dimensions outside supported limits")
            with path.open("rb") as image_file:
                if image_file.read(16 * 1024 * 1024 + 1) != blob:
                    raise ValueError("image changed during validation")
        except (OSError, subprocess.SubprocessError, KeyError) as exc:
            raise ValueError("cannot validate image using sips") from exc
        ref = "image-" + uuid.uuid4().hex
        payload["model"] = visual_model
        payload["state"] = {**state, "screenshot": ref}
        payload["assets"] = {ref: {"type": "image", "format": fmt, "data_base64": base64.b64encode(blob).decode("ascii")}}
        hashes["screenshot"] = hashlib.sha256(blob).hexdigest()
    elif data.get("images"):
        raise ValueError("images require visual questions")
    return payload, hashes

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON with state and questions keyed by ID")
    parser.add_argument("--raw", type=Path, required=True, help="Private path for the original response")
    parser.add_argument("--run-config", type=Path, help="Frozen Argus run configuration (required for images)")
    parser.add_argument("--output", type=Path, required=True, help="Private path for decisions.py input")
    args = parser.parse_args()
    try:
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise ValueError("set TYPESAFE_API_KEY in the host's secret environment")
        data = json.loads(args.input.read_text())
        visual_model = None
        if args.run_config:
            visual_model = json.loads(args.run_config.read_text())["config"]["decision"].get("visualModel")
        payload, image_hashes = build_request(data, visual_model)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > 32 * 1024 * 1024:
            raise ValueError("request body exceeds 32 MiB")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if image_hashes:
            headers["TypeSafe-Danger-Zone-Images"] = "true"
        request = urllib.request.Request(URL, data=body, headers=headers)
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=10) as result:
            if result.headers.get_content_type() != "application/json":
                raise ValueError("unexpected response content type")
            response = json.load(result)
        if image_hashes and response.get("model") != payload["model"]:
            raise ValueError("visual response model mismatch")
        normalized = normalize(response, payload["questions"])
        normalized["questionBindings"] = {name: value["questionId"] if isinstance(value, dict) else name
                                          for name, value in data["questions"].items()}
        for name, answer in normalized["answers"].items():
            route(answer, DEFINITIONS["questions"][normalized["questionBindings"][name]])
        normalized["request"] = {"requestedModel": payload["model"], "actualModel": response["model"],
                                 "inputHash": hashlib.sha256(body).hexdigest(), "imageHashes": image_hashes,
                                 "durationMs": round((time.monotonic() - started) * 1000),
                                 "usage": response.get("usage", "unknown"), "rawEvidenceRef": str(args.raw)}
        if image_hashes:
            normalized["request"]["caseId"] = data["caseId"]
        args.raw.write_text(json.dumps(response, ensure_ascii=False))
        args.output.write_text(json.dumps(normalized, ensure_ascii=False))
        print(json.dumps({"model": response["model"], "usage": response.get("usage", "unknown"), "raw": str(args.raw), "normalized": str(args.output)}))
        return 0
    except (ValueError, KeyError, TypeError, AttributeError, OSError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
