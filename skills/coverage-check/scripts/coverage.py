#!/usr/bin/env python3
"""Deterministic gates over a frozen baseline and runner discovery evidence."""
import argparse
import hashlib
import json
from pathlib import Path
from fractions import Fraction


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def index(rows, key):
    result = {}
    for row in rows:
        ident = row[key]
        if not isinstance(ident, str) or not ident or ident in result:
            raise ValueError(f"invalid or duplicate {key}")
        if row.get("schemaVersion") != 1:
            raise ValueError(f"unsupported schemaVersion: {ident}")
        result[ident] = row
    return result


def evaluate(data, phase="execution"):
    if data.get("schemaVersion") != 1:
        raise ValueError("schemaVersion must be 1")
    threshold = Fraction(str(data.get("threshold", 0.95)))
    if not Fraction(95, 100) <= threshold <= 1:
        raise ValueError("threshold must be between 0.95 and 1")
    acs = index(data["acs"], "acId")
    scenarios = index(data["scenarios"], "scenarioId")
    cases = index(data["cases"], "caseId")
    baseline_hash = digest(data["scenarios"])
    errors = []
    if not data.get("runId"):
        errors.append("missing runId")
    if not acs or not scenarios:
        errors.append("empty baseline or AC set")
    if baseline_hash != data["baselineHash"]:
        errors.append("baseline hash mismatch")
    if not data["sourceHash"] or data["sourceHash"] != data["currentSourceHash"]:
        errors.append("PRD hash mismatch")
    for a in acs.values():
        if a.get("sourceHash") != data["sourceHash"] or not a.get("sourceRef") or a.get("reviewStatus") != "approved":
            errors.append(f"unreviewed or stale AC: {a['acId']}")
    for s in scenarios.values():
        if s.get("priority") not in {"P0", "P1", "P2", "P3"}:
            errors.append(f"invalid scenario priority: {s['scenarioId']}")
        if s.get("reviewStatus") != "approved" or not s.get("acIds") or any(a not in acs for a in s["acIds"]):
            errors.append(f"invalid scenario mapping: {s['scenarioId']}")
    mapped_acs = {a for s in scenarios.values() for a in s.get("acIds", [])}
    if set(acs) - mapped_acs:
        errors.append("ACs without scenarios: " + ",".join(sorted(set(acs) - mapped_acs)))
    discovered = data["discovered"]
    found = {}
    for d in discovered:
        key = (d["caseId"], d["matrixId"])
        if key in found or d["caseId"] not in cases or not d.get("automationRef"):
            errors.append("invalid or duplicate runner discovery")
        found[key] = d["automationRef"]
    required = data["requiredMatrix"]
    if not required or len(required) != len(set(required)) or any(not isinstance(m, str) or not m for m in required):
        errors.append("invalid required matrix")
    valid = {}
    for cid, c in cases.items():
        if c.get("suite") not in {"ui", "api", "performance", "security"}:
            errors.append(f"invalid case suite: {cid}")
        if not c.get("scenarioIds") or any(s not in scenarios for s in c["scenarioIds"]):
            errors.append(f"dangling case mapping: {cid}")
        steps = c.get("steps", [])
        step_ids = {s.get("id") for s in steps}
        assertions = c.get("assertions", [])
        valid[cid] = (c.get("reviewStatus") == "approved" and c.get("semanticReview") == "approved"
                      and bool(c.get("automationRef")) and bool(steps) and bool(assertions)
                      and all(a.get("stepId") in step_ids and a.get("expected") for a in assertions)
                      and all(found.get((cid, m)) == c["automationRef"] for m in required))
    associations = {sid: [cid for cid, c in cases.items() if sid in c.get("scenarioIds", [])]
                    for sid in scenarios}
    planned = {sid for sid, ids in associations.items() if any(valid[c] for c in ids)}
    p0 = {sid for sid, s in scenarios.items() if s["priority"] == "P0"}
    attempts = {}
    statuses = {"passed", "failed", "error", "blocked", "skipped", "flaky", "cancelled"}
    for r in data.get("results", []):
        required_result = {"schemaVersion", "runId", "caseId", "attempt", "status", "startedAt", "durationMs", "environment", "evidence", "error", "matrixId", "shardId"}
        if not required_result.issubset(r):
            errors.append("missing result fields")
        duration = r.get("durationMs")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= duration < float("inf"):
            errors.append("invalid result duration")
        if r.get("schemaVersion") != 1 or r.get("runId") != data["runId"]:
            errors.append("result version or run mismatch")
        key = (r["caseId"], r["matrixId"])
        if key not in found or r["matrixId"] not in required:
            errors.append("unexpected result case or matrix")
        if r.get("status") not in statuses or type(r.get("attempt")) is not int or r["attempt"] < 1:
            errors.append("invalid result status or attempt")
        if r.get("status") == "passed" and r.get("error") is not None:
            errors.append("passed result contains error")
        items = attempts.setdefault(key, [])
        if any(x["attempt"] == r["attempt"] for x in items):
            errors.append("duplicate result attempt")
        items.append(r)
    effective = {}
    for key, rows in attempts.items():
        rows.sort(key=lambda r: r["attempt"])
        if [r["attempt"] for r in rows] != list(range(1, len(rows) + 1)):
            errors.append("missing result attempt")
        status = rows[-1]["status"]
        if status == "passed" and any(r["status"] != "passed" for r in rows[:-1]):
            status = "flaky"
        effective[key] = status
    complete, passed = set(), set()
    for sid, ids in associations.items():
        # Every mapped case is mandatory. Optional cases belong to a separate approved scope.
        states = [effective.get((cid, m)) for cid in ids for m in required]
        if ids and all(valid[c] for c in ids) and states and all(s in {"passed", "failed"} for s in states):
            complete.add(sid)
        if sid in complete and all(s == "passed" for s in states):
            passed.add(sid)
    gates = []

    def gate(name, eligible, total, minimum=Fraction(1)):
        missing = sorted(total - eligible)
        status = "N/A" if not total and name.startswith("p0") else (
            "PASSED" if total and Fraction(len(total & eligible), len(total)) >= minimum else "BLOCKED")
        gates.append({"schemaVersion": 1, "gate": name, "status": status,
                      "numerator": len(total & eligible) if total else None,
                      "denominator": len(total) if total else None, "missingIds": missing,
                      "reasons": [] if status == "PASSED" else ["missing coverage" if total else "empty set"],
                      "baselineHash": baseline_hash})
    all_ids = set(scenarios)
    gate("planned", planned, all_ids, threshold)
    gate("p0_planned", planned, p0)
    if phase == "execution":
        if not data.get("inputHash"):
            errors.append("missing input fingerprint")
        if data.get("scope") != "full":
            errors.append("partial execution cannot pass full gates")
        expected_shards = data.get("expectedShards", [])
        shards = data.get("shards", [])
        if not expected_shards or len(expected_shards) != len(set(expected_shards)):
            errors.append("invalid expected shard list")
        actual = [s["shardId"] for s in shards]
        if len(actual) != len(set(actual)) or set(actual) != set(expected_shards):
            errors.append("missing or duplicate shards")
        for shard in shards:
            rows = [r for r in data.get("results", []) if r.get("shardId") == shard["shardId"]]
            if (shard.get("inputHash") != data["inputHash"] or shard.get("state") != "completed"
                    or shard.get("resultHash") != digest(rows)):
                errors.append("incomplete or mismatched shard")
        if any(r.get("shardId") not in expected_shards for r in data.get("results", [])):
            errors.append("result from unexpected shard")
        gate("executed", complete, all_ids, threshold)
        gate("passed", passed, all_ids, Fraction(0))
        gate("p0_passed", passed, p0)
        if any(s != "passed" for s in effective.values()):
            errors.append("non-passing functional result")
    return {"schemaVersion": 1, "phase": phase, "status": "BLOCKED" if errors or any(
        g["status"] == "BLOCKED" for g in gates) else "PASSED", "gates": gates,
        "reasons": sorted(set(errors)), "baselineHash": baseline_hash,
        "statusCounts": {s: list(effective.values()).count(s) for s in sorted(statuses)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--phase", choices=["plan", "execution"], default="execution")
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.input.read_text()), args.phase)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        result = {"schemaVersion": 1, "status": "BLOCKED", "reasons": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
