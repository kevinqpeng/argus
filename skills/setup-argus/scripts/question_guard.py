#!/usr/bin/env python3
"""Validate and deduplicate a proposed setup question batch from stdin."""
import json
import sys


def prepare_questions(questions, answers):
    if not isinstance(questions, list) or not isinstance(answers, dict):
        raise ValueError("invalid question batch")
    result, seen_ids, seen_prompts = [], {}, {}
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("invalid question")
        key, prompt = question.get("id"), question.get("question")
        if not isinstance(key, str) or not key.strip() or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("question needs an ID and prompt")
        if key in seen_ids:
            if seen_ids[key] != question:
                raise ValueError("conflicting question ID")
            continue
        normalized = " ".join(prompt.split())
        if normalized in seen_prompts and seen_prompts[normalized] != key:
            raise ValueError("same question has different IDs")
        seen_ids[key], seen_prompts[normalized] = question, key
        if key not in answers or answers[key] is None or answers[key] == "":
            result.append(question)
    return result


def main():
    try:
        data = json.load(sys.stdin)
        questions = prepare_questions(data["questions"], data["answers"])
        print(json.dumps({"questions": questions}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError):
        print(json.dumps({"status": "BLOCKED", "reason": "invalid or conflicting question batch"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
