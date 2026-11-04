#!/usr/bin/env python3
"""Return the next blocking Bug-table questions; the host must call its real input tool."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

DEFINITIONS = json.loads((Path(__file__).resolve().parents[1] / "references/bug-questions.json").read_text())["questions"]


def is_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def next_questions(state):
    if state.get("schemaVersion") != 1 or not isinstance(state.get("answers"), dict):
        raise ValueError("invalid wizard state")
    answers = dict(state["answers"])
    for key, definition in DEFINITIONS.items():
        value = answers.get(key)
        if isinstance(value, str):
            answers[key] = {o["label"]: o["value"] for o in definition["options"]}.get(value, value)
    pending = []

    def choice(key):
        value = answers.get(key)
        if not value:
            pending.append(key)
            return None
        if value not in {o["value"] for o in DEFINITIONS[key]["options"]}:
            raise ValueError("invalid choice: " + key)
        return value

    destination = choice("bug_destination")
    overrides = {"schemaVersion": 1, "delivery": {"mode": destination, "requiresReadback": True}}
    publisher = {}
    if destination in {"online", "both"}:
        platform, action, fallback = choice("online_platform"), choice("table_action"), choice("failure_policy")
        if platform:
            publisher.update(provider="feishu" if platform == "feishu_cli" else "wecom", method="cli", enabled=True)
        overrides["delivery"]["onFailure"] = fallback
        if action == "existing" and answers.get("target_url") == "create":
            action = "create"
        publisher["tableAction"] = action
        if action == "existing":
            if is_url(answers.get("target_url")):
                publisher["targetUrl"] = answers["target_url"]
            else:
                pending.append("target_url")
        if action in {"create", "import"}:
            name = answers.get("table_name")
            if isinstance(name, str) and name.strip() and name not in {"custom", "paste"}:
                publisher["tableName"] = state.get("suggestedTableName", "Argus Bug 记录") if name == "suggested" else name.strip()
            else:
                pending.append("table_name")
        # Ask dependent target details only after platform/action/fallback have been answered.
        if not any(k in pending for k in ["online_platform", "table_action", "failure_policy"]):
            summary = choice("summary_location")
            if summary == "separate" and answers.get("summary_url") == "same_document":
                summary = "same_document"
            publisher["summaryLocation"] = summary
            if summary == "separate":
                if is_url(answers.get("summary_url")):
                    publisher["summaryUrl"] = answers["summary_url"]
                else:
                    pending.append("summary_url")
            publisher["evidencePolicy"] = choice("evidence_policy")
    if destination in {"local", "both"} or publisher.get("tableAction") == "import":
        publisher["localFormat"] = choice("local_format")
        if destination == "local":
            publisher.update(provider="local", method="local", tableAction="create", enabled=True)
    if pending:
        ids = list(dict.fromkeys(pending))[:3]
        questions, values = [], {}
        for key in ids:
            definition = DEFINITIONS[key]
            prompt = definition["question"]
            if key == "table_name":
                prompt += " 建议名称：" + str(state.get("suggestedTableName", "Argus Bug 记录"))
            questions.append({"id": key, "header": definition["header"], "question": prompt,
                              "options": [{"label": o["label"], "description": o["description"]} for o in definition["options"]]})
            values[key] = {o["label"]: o["value"] for o in definition["options"]}
        return {"schemaVersion": 1, "status": "AWAITING_USER", "questions": questions, "choiceValues": values}
    overrides["bugPublisher"] = publisher
    return {"schemaVersion": 1, "status": "CHOICES_COMPLETE", "projectOverrides": overrides,
            "capabilityStatus": "NOT_VERIFIED"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("state", type=Path, help="Private JSON with schemaVersion=1 and answers keyed by question ID")
    args = p.parse_args()
    try:
        result = next_questions(json.loads(args.state.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "BLOCKED", "errorType": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
