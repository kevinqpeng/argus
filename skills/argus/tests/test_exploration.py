import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/exploration.py"
spec = importlib.util.spec_from_file_location("exploration", SCRIPT)
exploration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exploration)


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.cases = [{"caseId": name, "suite": "ui", "steps": [{"action": "login"}],
                       "assertions": [{"expected": "welcome"}]} for name in ("C1", "C2")]
        self.request = {"runId": "r1", "requiredMatrix": ["web", "android", "ios"],
                        "matrixPlatforms": {p: p for p in ("web", "android", "ios")}, "requirements": [
            {"caseId": c["caseId"], "matrixId": platform, "platform": platform,
             "page": "login", "state": "signed-out", "scope": "role-tenant-context",
             "element": name, "refs": [{"kind": kind, "index": 0}]}
            for c in self.cases for platform in ("web", "android", "ios")
            for kind, name in (("steps", "submit"), ("assertions", "welcome"))]}
        self.plan = exploration.plan(self.cases, self.request)
        self.task = self.plan["tasks"][0]

    def entry(self, task=None, name="submit"):
        task = task or self.task
        entry = {k: task[k] for k in ("platform", "page", "state", "scope")}
        entry.update(runId="r1", taskId=task["id"], element=name,
                     targetHash="target", buildHash="build", environmentHash="environment",
                     stateFingerprint="fingerprint", locator={"value": name}, strategy="id",
                     evidenceRef="evidence/probe.json", confidence="unknown")
        entry["current"] = dict(entry)
        entry["current"]["observationId"] = "observation-1"
        entry["probe"] = {"runId": "r1", "observedAt": "2026-11-06T10:00:00Z", "matches": 1,
                          "usable": True, "stateFingerprint": "fingerprint",
                          "locatorHash": exploration.digest(entry["locator"]), "observationId": "observation-1"}
        return entry

    def test_all_platforms_deduplicate_pages_and_preserve_assertions(self):
        self.assertEqual(len(self.plan["tasks"]), 3)
        for task in self.plan["tasks"]:
            self.assertEqual(len(task["needs"]), 4)
            self.assertEqual({n["element"] for n in task["needs"]}, {"submit", "welcome"})

    def test_missing_assertions_or_matrix_block(self):
        for mutation in ("assertions", "matrix"):
            request = copy.deepcopy(self.request)
            if mutation == "assertions":
                request["requirements"] = [r for r in request["requirements"] if r["refs"][0]["kind"] == "steps"]
            else:
                request["requiredMatrix"].append("device2")
                request["matrixPlatforms"]["device2"] = "android"
            with self.assertRaisesRegex(ValueError, "unmapped"):
                exploration.plan(self.cases, request)

    def test_non_ui_disposition_keeps_api_assertion_accounted(self):
        request = copy.deepcopy(self.request)
        request["requirements"] = [r if r["refs"][0]["kind"] == "steps" else
                                   {**r, "nonUIReason": "API checks final state"} for r in request["requirements"]]
        result = exploration.plan(self.cases, request)
        self.assertTrue(result["nonUI"])
        self.assertTrue(all(len(t["needs"]) == 2 for t in result["tasks"]))

    def test_scopes_and_platforms_never_merge(self):
        row = {**self.request["requirements"][0], "scope": "other-tenant"}
        self.request["requirements"].append(row)
        self.assertEqual(len(exploration.plan(self.cases, self.request)["tasks"]), 4)

    def test_bad_dependencies_and_budget(self):
        self.request["requirements"][0]["dependsOn"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "dependencies"):
            exploration.plan(self.cases, self.request)
        for budget in ({"maxPages": 0}, {"cache": 1}, {"typo": 2}):
            with self.assertRaises(ValueError):
                exploration.limits(budget)
        self.request["requirements"][0].pop("dependsOn")
        self.request["budgets"] = {"maxPages": 2}
        self.assertEqual(exploration.plan(self.cases, self.request)["status"], "BLOCKED")

    def test_cache_requires_fresh_probe_and_full_scope(self):
        for task in self.plan["tasks"]:
            e = self.entry(task)
            budget = exploration.limits()
            self.assertTrue(exploration.reusable(e, e["current"], e["probe"], budget))
            for field in ("platform", "scope", "buildHash", "environmentHash", "stateFingerprint"):
                current = {**e["current"], field: "changed"}
                self.assertFalse(exploration.reusable(e, current, e["probe"], budget))
            for count in (0, 2):
                self.assertFalse(exploration.reusable(e, e["current"], {**e["probe"], "matches": count}, budget))
            self.assertFalse(exploration.reusable(e, e["current"], {}, budget))
            self.assertFalse(exploration.reusable(e, e["current"], e["probe"], {**budget, "cache": False}))

    def test_cross_run_opt_in_still_needs_current_probe(self):
        e = self.entry()
        e["runId"] = "old"
        self.assertFalse(exploration.reusable(e, e["current"], e["probe"], exploration.limits()))
        budget = exploration.limits({"crossRunCache": True})
        self.assertTrue(exploration.reusable(e, e["current"], e["probe"], budget))
        self.assertFalse(exploration.reusable(e, e["current"], {**e["probe"], "runId": "old"}, budget))

    def test_replayed_probe_is_not_a_fresh_observation(self):
        e = self.entry()
        current = {**e["current"], "observationId": "observation-2"}
        self.assertFalse(exploration.reusable(e, current, e["probe"], exploration.limits()))

    def test_wrong_platform_does_not_satisfy_mobile_matrix(self):
        self.request["requirements"][0]["platform"] = "ios"
        with self.assertRaisesRegex(ValueError, "matrix platform"):
            exploration.plan(self.cases, self.request)

    def test_invalid_counters_and_unobserved_platform(self):
        with self.assertRaisesRegex(ValueError, "recovery count"):
            exploration.recovery("Timeout", {}, -1, exploration.limits())
        with self.assertRaisesRegex(ValueError, "object"):
            exploration.limits([])
        with self.assertRaisesRegex(ValueError, "platform"):
            exploration.summarize([{"platform": "mobile"}])
        self.assertEqual(exploration.summarize([])["ios"]["pages"], "unknown")

    def test_map_gate_checks_every_needed_locator_and_input_hash(self):
        page_map = {"runId": "r1", "entries": [self.entry(t, name) for t in self.plan["tasks"] for name in ("submit", "welcome")]}
        self.assertEqual(exploration.check_map(self.plan, self.cases, page_map)["status"], "PASSED")
        page_map["entries"].pop()
        result = exploration.check_map(self.plan, self.cases, page_map)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["missing"]), 1)
        with self.assertRaisesRegex(ValueError, "cases changed"):
            exploration.check_map(self.plan, [], page_map)

    def test_scoped_map_releases_ready_cases_without_weakening_full_gate(self):
        for row in self.request["requirements"]:
            if row["caseId"] == "C2":
                row["page"] = "users"
        plan = exploration.plan(self.cases, self.request)
        page_map = {"runId": "r1", "entries": [self.entry(t, name)
                    for t in plan["tasks"] if t["page"] == "login"
                    for name in ("submit", "welcome")]}
        result = exploration.check_map(plan, self.cases, page_map, ["C1"])
        self.assertEqual((result["status"], result["scope"]), ("PASSED", "cases"))
        self.assertEqual(exploration.check_map(plan, self.cases, page_map)["status"], "BLOCKED")
        self.assertEqual(exploration.check_map(plan, self.cases, page_map, ["C2"])["status"], "BLOCKED")
        # One missing assertion in one matrix still blocks the selected spec.
        page_map["entries"].pop()
        self.assertEqual(exploration.check_map(plan, self.cases, page_map, ["C1"])["status"], "BLOCKED")
        for ids in ([], ["unknown"], ["C1", "C1"], "C1"):
            with self.assertRaisesRegex(ValueError, "caseIds"):
                exploration.check_map(plan, self.cases, page_map, ids)
        with self.assertRaisesRegex(ValueError, "cases changed"):
            exploration.check_map(plan, self.cases[:1], page_map, ["C1"])

    def test_shared_page_does_not_require_unrelated_elements(self):
        for row in self.request["requirements"]:
            if row["caseId"] == "C2":
                row["element"] += "-other"
        plan = exploration.plan(self.cases, self.request)
        page_map = {"runId": "r1", "entries": [self.entry(t, name)
                    for t in plan["tasks"] for name in ("submit", "welcome")]}
        self.assertEqual(exploration.check_map(plan, self.cases, page_map, ["C1"])["status"], "PASSED")
        plan["status"] = "BLOCKED"
        self.assertEqual(exploration.check_map(plan, self.cases, page_map, ["C1"])["status"], "BLOCKED")

    def test_failure_routes_and_bounded_recovery(self):
        budget = exploration.limits()
        evidence = {"correctState": True, "locatorChanged": True, "transientState": True, "safeToRepeat": True}
        for error in ("LocatorNotFound", "LocatorAmbiguous", "StaleElement"):
            self.assertEqual(exploration.recovery(error, evidence, 0, budget), "local-exploration")
            self.assertEqual(exploration.recovery(error, {}, 0, budget), "report")
            self.assertEqual(exploration.recovery(error, evidence, 1, budget), "stop")
        for error in ("NavigationFailure", "Timeout"):
            self.assertEqual(exploration.recovery(error, evidence, 0, budget), "restore-state")
        for error in ("AssertionFailure", "AppiumSessionFailure", "ApplicationError"):
            self.assertEqual(exploration.recovery(error, evidence, 0, budget), "report")

    def test_preflight_time_visits_screenshots_retries_recovery(self):
        op = dict(taskId=self.task["id"], category="navigation", elapsedMs=0, pageElapsedMs=0, visitStart=True)
        self.assertEqual(exploration.allowance(self.plan, [], op)["timeoutMs"], 120000)
        for events, operation in (([], {**op, "elapsedMs": 600000}),
                                  ([], {**op, "pageElapsedMs": 120000}),
                                  ([op, op], op),
                                  ([{**op, "category": "screenshot"}] * 20, {**op, "category": "screenshot"}),
                                  ([{**op, "locatorRetry": True, "element": "submit"}],
                                   {**op, "locatorRetry": True, "element": "submit"}),
                                  ([{**op, "category": "recovery", "caseId": "C1", "matrixId": self.task["platform"]}],
                                   {**op, "category": "recovery", "caseId": "C1", "matrixId": self.task["platform"]})):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "exhausted"):
                exploration.allowance(self.plan, events, operation)

    def test_telemetry_keeps_platforms_and_unknown_wall_time(self):
        events = [{"platform": "android", "taskId": "login", "category": "structure", "durationMs": 4,
                   "visitStart": True, "collected": ["submit", "welcome"]},
                  {"platform": "android", "taskId": "login", "category": "cacheLookup", "durationMs": 2,
                   "cacheResult": "hit", "used": ["submit"]}]
        report = exploration.summarize(events)
        self.assertEqual(report["android"]["locatorUtilizationRate"], 0.5)
        self.assertEqual(report["android"]["cacheHitRate"], 1)
        self.assertEqual(report["android"]["wallTimeMs"], "unknown")
        self.assertEqual(report["web"]["timing"]["structure"]["durationMs"], "unknown")
        with self.assertRaises(ValueError):
            exploration.summarize([{**events[0], "durationMs": float("nan")}])

    def test_cli_plan_and_failure_exit(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "input.json"
            source.write_text(json.dumps({"cases": self.cases, "request": self.request}))
            result = subprocess.run([sys.executable, str(SCRIPT), "plan", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(len(json.loads(result.stdout)["tasks"]), 3)
            page_map = {"runId": "r1", "entries": [self.entry(t, name)
                        for t in self.plan["tasks"] for name in ("submit", "welcome")]}
            payload = {"plan": self.plan, "cases": self.cases, "map": page_map, "caseIds": ["C1"]}
            source.write_text(json.dumps(payload))
            result = subprocess.run([sys.executable, str(SCRIPT), "check", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(result.stdout)["scope"], "cases")
            payload["caseIds"] = []
            source.write_text(json.dumps(payload))
            result = subprocess.run([sys.executable, str(SCRIPT), "check", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            source.write_text("{}")
            result = subprocess.run([sys.executable, str(SCRIPT), "plan", str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
