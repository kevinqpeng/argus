#!/usr/bin/env python3
"""Validate normalized decisions; this is not a provider HTTP adapter."""
import argparse
import json
import math
from pathlib import Path

DEFINITIONS = json.loads((Path(__file__).resolve().parents[1] / "references/questions.json").read_text())


def probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("invalid probability")
    return value


def route(answer, question):
    t = DEFINITIONS["thresholds"]
    if answer.get("probabilityKind") != "calibrated":
        return {"route": "host-analysis", "reason": "uncalibrated or unavailable probabilities"}
    kind = question["type"]
    if "confidence" in answer:
        probability(answer["confidence"])
    if kind == "noul":
        p = probability(answer["noul"])
        label = "yes" if p >= t["noulYes"] else "no" if p <= t["noulNo"] else "uncertain"
    else:
        probs = answer["probabilities"]
        options = list(question["criteria"]) if kind == "choice" else [str(i) for i in range(len(question["criteria"]))]
        if set(probs) != set(options):
            raise ValueError("option set mismatch")
        values = [probability(probs[o]) for o in options]
        if abs(sum(values) - 1) > t["sumTolerance"]:
            raise ValueError("probabilities do not sum to 1")
        ranked = sorted(probs, key=lambda k: probs[k], reverse=True)
        if kind == "choice":
            if answer["choice"] != ranked[0]:
                raise ValueError("choice disagrees with distribution")
            certain = probs[ranked[0]] >= t["choiceTop"] and probs[ranked[0]] - probs[ranked[1]] >= t["choiceMargin"] - 1e-12
            label = ranked[0] if certain else "uncertain"
        else:
            score = answer["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= len(options) - 1:
                raise ValueError("invalid score")
            # Two lower / two upper levels straddle the impact escalation boundary.
            upper = sum(values[2:])
            label = "uncertain" if t["scoreBoundaryMass"] < upper < 1 - t["scoreBoundaryMass"] else "high" if upper >= 1 - t["scoreBoundaryMass"] else "low"
    if question.get("visual"):
        return {"route": "vision-review", "judgment": label, "reason": "uncalibrated visual comparison; " + label}
    return {"route": "host-analysis" if label == "uncertain" else label, "reason": label}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="Normalized object: questionSetVersion, answers keyed by question ID")
    args = p.parse_args()
    try:
        data = json.loads(args.input.read_text())
        if data.get("schemaVersion") != 1 or data["questionSetVersion"] != DEFINITIONS["questionSetVersion"] or not data["answers"]:
            raise ValueError("missing answers or stale question version")
        bindings = data.get("questionBindings", {name: name for name in data["answers"]})
        if not isinstance(bindings, dict) or set(bindings) != set(data["answers"]):
            raise ValueError("question binding set mismatch")
        if any(not isinstance(qid, str) or qid not in DEFINITIONS["questions"] for qid in bindings.values()):
            raise ValueError("unknown question template")
        result = {k: route(v, DEFINITIONS["questions"][bindings[k]]) for k, v in data["answers"].items()}
        print(json.dumps({"schemaVersion": 1, "outcomes": result}, ensure_ascii=False))
        return 2 if any(r["route"] in ("host-analysis", "vision-review") for r in result.values()) else 0
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "BLOCKED", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
