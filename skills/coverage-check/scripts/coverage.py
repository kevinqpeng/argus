#!/usr/bin/env python3
"""Deterministic gates over a frozen baseline and runner discovery evidence."""
import argparse
import hashlib
import json
from pathlib import Path
from fractions import Fraction
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "argus/scripts"))
from case_contract import validate_steps


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


def check_impact(data, acs):
    analysis = data.get("impactAnalysis")
    if not isinstance(analysis, dict) or analysis.get("schemaVersion") != 1:
        return ["missing impact analysis or unsupported version"], {}
    errors = []
    if digest(analysis) != data.get("impactHash"):
        errors.append("impact hash mismatch")
    sources = index(analysis["sources"], "sourceId")
    units = index(analysis["sourceUnits"], "unitId")
    objects = index(analysis["objects"], "objectId")
    checks = index(analysis["checks"], "checkId")
    obligations = index(analysis["obligations"], "obligationId")
    if not sources or not units or not objects or not obligations:
        errors.append("empty source inventory, objects or obligations")
    current = data.get("currentSourceHashes", {})
    if set(current) != set(sources):
        errors.append("source inventory fingerprint set mismatch")
    for sid, source in sources.items():
        fingerprint = source.get("sourceHash", "")
        if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(c not in "0123456789abcdef" for c in fingerprint)
                or fingerprint != current.get(sid) or not source.get("sourceRef")
                or source.get("kind") not in {"prd", "business_rule", "contract", "history", "code", "observation"}):
            errors.append(f"invalid or stale source: {sid}")
    basis = data.get("inputBasis", {"kind": "prd"})
    if not isinstance(basis, dict) or basis.get("kind") not in {"prd", "cases"}:
        errors.append("invalid input basis")
    elif basis["kind"] == "cases":
        ids = basis.get("sourceIds")
        if (not isinstance(ids, list) or not ids or not all(isinstance(sid, str) for sid in ids)
                or len(ids) != len(set(ids)) or not basis.get("approvalRef")
                or any(sources.get(sid, {}).get("kind") != "history" for sid in ids)):
            errors.append("supplied cases require source inventory and approval evidence")
    elif not any(s.get("kind") == "prd" for s in sources.values()):
        errors.append("source inventory missing PRD")

    def refs(row, key, targets):
        ids = row.get(key)
        return (isinstance(ids, list) and bool(ids) and all(isinstance(i, str) for i in ids)
                and len(set(ids)) == len(ids) and all(i in targets for i in ids))

    resolved = {"sources": set(), "impacts": set(), "obligations": set()}
    for group, rows in (("sources", units), ("impacts", checks)):
        for ident, row in rows.items():
            disposition = row.get("disposition")
            valid = (refs(row, "sourceIds", sources) and bool(row.get("evidenceRef"))
                     and bool(row.get("reason")) and bool(row.get("reviewRef")))
            if group == "sources":
                excerpt = row.get("sourceExcerpt")
                if not isinstance(excerpt, str) or not excerpt.strip():
                    valid = False
                    errors.append(f"missing original source excerpt: {ident}")
            if disposition == "covered":
                valid = valid and refs(row, "obligationIds", obligations)
            elif disposition == "not_applicable":
                valid = valid and row.get("obligationIds") == []
            else:
                valid = False
            if disposition == "out_of_scope":
                errors.append(f"out of scope obligation retained: {ident}")
            if valid:
                resolved[group].add(ident)
            else:
                errors.append(f"unresolved {group}: {ident}")
    inventoried = {sid for row in units.values() for sid in row.get("sourceIds", [])}
    if set(sources) - inventoried:
        errors.append("sources without source inventory: " + ",".join(sorted(set(sources) - inventoried)))
    dimensions = {"data", "inflight", "lifecycle", "permission", "side_effect", "ordering"}
    for oid, obj in objects.items():
        found = {c.get("dimension") for c in checks.values() if c.get("objectId") == oid}
        if not obj.get("description") or dimensions - found:
            errors.append(f"incomplete impact dimensions: {oid}: " + ",".join(sorted(dimensions - found)))
    for cid, check in checks.items():
        if check.get("objectId") not in objects or check.get("dimension") not in dimensions:
            errors.append(f"invalid impact check: {cid}")
    linked = {o for row in list(units.values()) + list(checks.values()) for o in row.get("obligationIds", [])}
    for oid, obligation in obligations.items():
        if (oid not in linked or not refs(obligation, "sourceIds", sources)
                or not refs(obligation, "acIds", acs) or not obligation.get("text")
                or not obligation.get("expected") or not obligation.get("approvalRef")
                or obligation.get("reviewStatus") != "approved"):
            errors.append(f"unresolved obligation: {oid}")
        else:
            resolved["obligations"].add(oid)
    mapped = {aid for o in obligations.values() for aid in o.get("acIds", [])}
    if set(acs) - mapped:
        errors.append("ACs without obligations: " + ",".join(sorted(set(acs) - mapped)))
    for aid, ac in acs.items():
        source = sources.get(ac.get("sourceId"), {})
        if not source or ac.get("sourceHash") != source.get("sourceHash"):
            errors.append(f"invalid AC source: {aid}")
    summary = {}
    for group, rows in (("sources", units), ("impacts", checks), ("obligations", obligations)):
        summary[group] = {"numerator": len(resolved[group]), "denominator": len(rows),
                          "missingIds": sorted(set(rows) - resolved[group])}
    return errors, summary


def evaluate(data, phase="execution"):
    if phase not in {"design", "plan", "execution"}:
        raise ValueError("unknown coverage phase")
    if data.get("schemaVersion") != 1:
        raise ValueError("schemaVersion must be 1")
    threshold = Fraction(str(data.get("threshold", 0.95)))
    if not Fraction(95, 100) <= threshold <= 1:
        raise ValueError("threshold must be between 0.95 and 1")
    acs = index(data["acs"], "acId")
    scenarios = index(data["scenarios"], "scenarioId")
    cases = index(data["cases"], "caseId")
    baseline_hash = digest(data["scenarios"])
    errors, analysis_summary = check_impact(data, acs)
    analysis_status = "BLOCKED" if errors else "PASSED"
    if not data.get("runId"):
        errors.append("missing runId")
    if not acs or not scenarios:
        errors.append("empty baseline or AC set")
    if baseline_hash != data["baselineHash"]:
        errors.append("baseline hash mismatch")
    if not data["sourceHash"] or data["sourceHash"] != data["currentSourceHash"]:
        errors.append("PRD hash mismatch")
    for a in acs.values():
        if not a.get("sourceRef") or a.get("reviewStatus") != "approved":
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
        assertions = c.get("assertions", [])
        steps_valid = True
        try:
            validate_steps(steps, assertions)
        except ValueError as exc:
            steps_valid = False
            errors.append(f"invalid case steps: {cid}: {exc}")
        ref = c.get("automationRef", "")
        if not isinstance(ref, str):
            ref = ""
        if phase == "design":
            path, separator, title = ref.removeprefix("planned:").partition("::")
            entry_ready = (ref.startswith("planned:") and bool(separator) and bool(title.strip())
                           and bool(Path(path).parts) and bool(path.strip()) and not Path(path).is_absolute()
                           and ".." not in Path(path).parts and "\\" not in path and ":" not in path)
        else:
            entry_ready = (bool(ref) and not ref.startswith("planned:")
                           and all(found.get((cid, m)) == ref for m in required))
        valid[cid] = (c.get("reviewStatus") == "approved" and c.get("semanticReview") == "approved"
                      and bool(c.get("automationRef")) and steps_valid
                      and entry_ready)
    associations = {sid: [cid for cid, c in cases.items() if sid in c.get("scenarioIds", [])]
                    for sid in scenarios}
    planned = {sid for sid, ids in associations.items() if ids and all(valid[c] for c in ids)}
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
    gate("required_planned", planned, all_ids)
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
        gate("required_executed", complete, all_ids)
        gate("passed", passed, all_ids, Fraction(0))
        gate("p0_passed", passed, p0)
        if any(s != "passed" for s in effective.values()):
            errors.append("non-passing functional result")
    return {"schemaVersion": 1, "phase": phase, "status": "BLOCKED" if errors or any(
        g["status"] == "BLOCKED" for g in gates) else "PASSED", "gates": gates,
        "reasons": sorted(set(errors)), "baselineHash": baseline_hash,
        "inputBasis": data.get("inputBasis", {"kind": "prd"}),
        "impactAnalysis": {"status": analysis_status, "impactHash": data.get("impactHash"),
                           "coverage": analysis_summary},
        "statusCounts": {s: list(effective.values()).count(s) for s in sorted(statuses)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--phase", choices=["design", "plan", "execution"], default="execution")
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.input.read_text()), args.phase)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        result = {"schemaVersion": 1, "status": "BLOCKED", "reasons": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
