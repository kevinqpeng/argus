#!/usr/bin/env python3
"""Deterministic exploration planning, budgets, locator validation and telemetry.

The host supplies semantic requirements and real probe receipts; this module never
infers selectors or treats exploration as a passing business test.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


DEFAULTS = dict(maxPages=30, visitsPerPage=2, maxScreenshots=20,
                pageTimeoutMs=120000, totalTimeoutMs=600000,
                locatorRetries=1, recoveriesPerCase=1, cache=True, crossRunCache=False)
CATEGORIES = ("caseAnalysis", "planning", "navigation", "structure", "locator",
              "screenshot", "aiDecision", "cacheLookup", "recovery", "other")
PLATFORMS = ("web", "android", "ios")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def limits(overrides=None):
    require(overrides is None or isinstance(overrides, dict), "exploration budget must be an object")
    overrides = overrides or {}
    require(not set(overrides) - set(DEFAULTS), "unknown exploration budget")
    result = {**DEFAULTS, **overrides}
    for key, value in result.items():
        require(type(value) is bool if key in ("cache", "crossRunCache") else
                type(value) is int and value >= (0 if key in (
                    "maxScreenshots", "locatorRetries", "recoveriesPerCase") else 1),
                "invalid budget: " + key)
    return result


def plan(cases, request):
    """Merge page/state needs after accounting for every UI step and assertion."""
    require(request.get("runId") and request.get("requiredMatrix"), "runId/matrix required")
    matrix = request["requiredMatrix"]
    require(len(matrix) == len(set(matrix)), "duplicate matrix")
    require(set(request["matrixPlatforms"]) == set(matrix) and
            all(p in PLATFORMS for p in request["matrixPlatforms"].values()), "matrix platform mapping required")
    ids = [c["caseId"] for c in cases]
    require(len(ids) == len(set(ids)), "duplicate caseId")
    expected = {(c["caseId"], m, kind, i)
                for c in cases if c["suite"] == "ui" for m in matrix
                for kind in ("steps", "assertions") for i in range(len(c[kind]))}
    covered, tasks, excluded = set(), {}, []
    for row in request["requirements"]:
        refs = {(row["caseId"], row["matrixId"], r["kind"], r["index"]) for r in row["refs"]}
        require(refs and refs <= expected, "unknown case/matrix/step/assertion reference")
        covered |= refs
        if row.get("nonUIReason"):
            require(isinstance(row["nonUIReason"], str), "nonUIReason must explain the disposition")
            excluded.append({k: row[k] for k in ("caseId", "matrixId", "refs", "nonUIReason")})
            continue
        require(row.get("platform") in PLATFORMS, "explicit web/android/ios required")
        require(row["platform"] == request["matrixPlatforms"][row["matrixId"]], "wrong matrix platform")
        require(all(row.get(k) for k in ("page", "state", "scope", "element")), "missing UI identity")
        require(type(row.get("screenshot", False)) is bool, "screenshot must be boolean")
        require(type(row.get("dynamic", False)) is bool, "dynamic must be boolean")
        identity = {k: row[k] for k in ("platform", "page", "state", "scope")}
        key = digest(identity)
        task = tasks.setdefault(key, {"id": key, **identity, "needs": [], "dependsOn": [], "screenshot": False})
        need = {"element": row["element"], "caseId": row["caseId"], "matrixId": row["matrixId"],
                "refs": row["refs"], "dynamic": bool(row.get("dynamic", False))}
        if need not in task["needs"]:
            task["needs"].append(need)
        task["screenshot"] |= row.get("screenshot", False)
        task["dependsOn"] = sorted(set(task["dependsOn"]) | set(row.get("dependsOn", [])))
    require(covered == expected, "unmapped UI steps/assertions: " + str(sorted(expected - covered)))
    # Dependencies are task IDs, computed from the same identity; reject cycles.
    remaining, done = dict(tasks), set()
    ordered = []
    while remaining:
        ready = sorted(k for k, t in remaining.items() if set(t["dependsOn"]) <= done)
        require(ready, "unknown or cyclic page dependencies")
        for key in ready:
            ordered.append(remaining.pop(key))
            done.add(key)
    budget = limits(request.get("budgets"))
    return {"schemaVersion": 1, "runId": request["runId"], "casesHash": digest(cases),
            "requiredMatrix": matrix, "matrixPlatforms": request["matrixPlatforms"], "budgets": budget, "tasks": ordered,
            "nonUI": excluded, "status": "BLOCKED" if len(tasks) > budget["maxPages"] else "PLANNED"}


def reusable(entry, current, probe, budget):
    """A cache candidate is usable only after a fresh, scoped real locator probe."""
    if not budget["cache"]:
        return False
    if not entry.get("runId") or not current.get("runId") or not entry.get("locator"):
        return False
    fields = ("platform", "page", "state", "scope", "targetHash", "buildHash",
              "environmentHash", "stateFingerprint")
    if any(not current.get(k) or current[k] == "unknown" or entry.get(k) != current[k] for k in fields):
        return False
    if not budget["crossRunCache"] and entry.get("runId") != current.get("runId"):
        return False
    return (probe.get("runId") == current.get("runId") and bool(probe.get("observedAt"))
            and bool(current.get("observationId")) and probe.get("observationId") == current["observationId"]
            and probe.get("stateFingerprint") == current["stateFingerprint"]
            and probe.get("locatorHash") == digest(entry.get("locator"))
            and type(probe.get("matches")) is int and probe["matches"] == 1 and probe.get("usable") is True)


def recovery(error, evidence, used, budget):
    """Error names alone do not establish cause. Unknown causes stay unresolved."""
    require(type(used) is int and used >= 0, "invalid recovery count")
    if used >= budget["recoveriesPerCase"]:
        return "stop"
    if error in ("LocatorNotFound", "LocatorAmbiguous", "StaleElement"):
        if evidence.get("correctState") is True and evidence.get("locatorChanged") is True:
            return "local-exploration"
    if error in ("NavigationFailure", "Timeout"):
        if evidence.get("transientState") is True and evidence.get("safeToRepeat") is True:
            return "restore-state"
    # AssertionFailure, ApplicationError and AppiumSessionFailure never repair selectors.
    return "report"


def allowance(plan_data, events, operation):
    """Preflight each command; return a timeout bounded by remaining wall budget.

    elapsedMs/pageElapsedMs are active exploration wall times from the host clock,
    excluding human approval waits. Events include failed attempts.
    """
    budget = plan_data["budgets"]
    require(plan_data.get("status") != "BLOCKED", "exploration plan blocked")
    task = next((t for t in plan_data["tasks"] if t["id"] == operation["taskId"]), None)
    require(task is not None, "operation outside exploration plan")
    require(operation["category"] in CATEGORIES, "unknown timing category")
    for key in ("elapsedMs", "pageElapsedMs"):
        require(type(operation.get(key)) in (int, float) and math.isfinite(operation[key])
                and operation[key] >= 0, "invalid wall time")
    remaining = min(budget["totalTimeoutMs"] - operation["elapsedMs"],
                    budget["pageTimeoutMs"] - operation["pageElapsedMs"])
    require(remaining > 0, "exploration time budget exhausted")
    visits = [e for e in events if e.get("visitStart")]
    if operation.get("visitStart"):
        require(sum(e["taskId"] == task["id"] for e in visits) < budget["visitsPerPage"], "page visits exhausted")
        require(len({e["taskId"] for e in visits} | {task["id"]}) <= budget["maxPages"], "page budget exhausted")
    if operation["category"] == "screenshot":
        require(sum(e["category"] == "screenshot" for e in events) < budget["maxScreenshots"], "screenshot budget exhausted")
    if operation.get("locatorRetry"):
        require(operation.get("element") in {n["element"] for n in task["needs"]}, "unknown retry element")
        require(sum(e.get("locatorRetry", False) and e["taskId"] == task["id"] and
                    e.get("element") == operation["element"] for e in events) < budget["locatorRetries"], "locator retries exhausted")
    if operation["category"] == "recovery":
        require((operation.get("caseId"), operation.get("matrixId")) in
                {(n["caseId"], n["matrixId"]) for n in task["needs"]}, "unknown recovery case/matrix")
        require(sum(e["category"] == "recovery" and e.get("caseId") == operation["caseId"] and
                    e.get("matrixId") == operation["matrixId"] for e in events) < budget["recoveriesPerCase"], "recovery budget exhausted")
    return {"status": "READY", "timeoutMs": remaining}


def check_map(plan_data, cases, page_map, case_ids=None):
    require(plan_data["casesHash"] == digest(cases), "approved cases changed")
    require(page_map.get("runId") == plan_data["runId"], "wrong map run")
    if case_ids is not None:
        require(isinstance(case_ids, list) and bool(case_ids)
                and all(isinstance(c, str) for c in case_ids)
                and len(case_ids) == len(set(case_ids))
                and set(case_ids) <= {c["caseId"] for c in cases}, "invalid caseIds")
    selected = {c["caseId"] for c in cases} if case_ids is None else set(case_ids)
    missing = []
    for task in plan_data["tasks"]:
        for name in sorted({n["element"] for n in task["needs"] if n["caseId"] in selected}):
            entries = [e for e in page_map.get("entries", []) if e.get("taskId") == task["id"] and e.get("element") == name]
            valid = [e for e in entries if e.get("locator") and e.get("strategy") and e.get("evidenceRef")
                     and reusable(e, e.get("current", {}), e.get("probe", {}), {**plan_data["budgets"], "cache": True})
                     and e.get("current", {}).get("runId") == plan_data["runId"]
                     and all(e.get(k) == task[k] for k in ("platform", "page", "state", "scope"))]
            if not valid:
                missing.append({"taskId": task["id"], "element": name})
    return {"status": "BLOCKED" if missing or plan_data["status"] == "BLOCKED" else "PASSED",
            "scope": "all" if case_ids is None else "cases", "caseIds": sorted(selected), "missing": missing}


def summarize(events):
    result = {}
    require(all(e.get("platform") in PLATFORMS for e in events), "unknown event platform")
    for platform in PLATFORMS:
        rows = [e for e in events if e["platform"] == platform]
        for event in rows:
            require(event["category"] in CATEGORIES and type(event["durationMs"]) in (int, float)
                    and math.isfinite(event["durationMs"]) and event["durationMs"] >= 0, "invalid timing event")
        counts = {category: [e for e in rows if e["category"] == category] for category in CATEGORIES}
        visits = [e for e in rows if e.get("visitStart")]
        lookups = [e for e in rows if e["category"] == "cacheLookup"]
        collected = {(e["taskId"], name) for e in rows for name in e.get("collected", [])}
        used = {(e["taskId"], name) for e in rows for name in e.get("used", [])}
        result[platform] = {
            "status": "observed" if rows else "unmeasured",
            "timing": {k: {"count": len(v), "durationMs": sum(e["durationMs"] for e in v) if v else "unknown"} for k, v in counts.items()},
            "pages": len({e["taskId"] for e in visits}), "screenshots": len(counts["screenshot"]),
            "structureReads": len(counts["structure"]), "repeatVisits": len(visits) - len({e["taskId"] for e in visits}),
            "collectedLocators": len(collected), "usedLocators": len(used & collected),
            "locatorUtilizationRate": len(used & collected) / len(collected) if collected else "unknown",
            "cacheHitRate": sum(e.get("cacheResult") == "hit" for e in lookups) / len(lookups) if lookups else "unknown",
            "cacheInvalidationRate": sum(e.get("cacheResult") == "invalid" for e in lookups) / len(lookups) if lookups else "unknown",
            "wallTimeMs": "unknown", "e2eTimeMs": "unknown", "passRate": "unknown"}
        result[platform]["commands"] = {command: {
            "count": sum(e.get("command") == command for e in rows),
            "durationMs": sum(e["durationMs"] for e in rows if e.get("command") == command)}
            for command in sorted({e["command"] for e in rows if e.get("command")})}
        ai = counts["aiDecision"]
        result[platform]["aiCost"] = sum(e["cost"] for e in ai) if ai and all(
            type(e.get("cost")) in (int, float) and math.isfinite(e["cost"]) and e["cost"] >= 0 for e in ai) else "unknown"
        if not rows:
            for key in ("pages", "screenshots", "structureReads", "repeatVisits", "collectedLocators", "usedLocators"):
                result[platform][key] = "unknown"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "check", "allow", "cache", "recover", "report"))
    parser.add_argument("input", type=Path, help="private JSON request; result goes to stdout")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text())
        if args.action == "plan":
            result = plan(data["cases"], data["request"])
        elif args.action == "check":
            result = check_map(data["plan"], data["cases"], data["map"], data.get("caseIds"))
        elif args.action == "allow":
            result = allowance(data["plan"], data["events"], data["operation"])
        elif args.action == "cache":
            result = {"reusable": reusable(data["entry"], data["current"], data["probe"], limits(data.get("budgets")))}
        elif args.action == "recover":
            result = {"route": recovery(data["error"], data["evidence"], data["used"], limits(data.get("budgets")))}
        else:
            result = summarize(data["events"])
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if result.get("status") == "BLOCKED" else 0
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
