import copy
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/coverage.py"
coverage = runpy.run_path(str(SCRIPT))


def fixture():
    source = dict(schemaVersion=1, sourceId="prd", kind="prd", sourceRef="prd.md",
                  sourceHash="a" * 64)
    obligation = dict(schemaVersion=1, obligationId="O1", sourceIds=["prd"],
                      text="Change owner", expected="Owner becomes B", acIds=["A1"],
                      reviewStatus="approved", approvalRef="review:1")
    record = dict(schemaVersion=1, sourceIds=["prd"], evidenceRef="prd.md:1",
                  disposition="covered", obligationIds=["O1"], reason="Owner changes",
                  reviewRef="review:1")
    impact = dict(schemaVersion=1, sources=[source],
                  sourceUnits=[dict(record, unitId="U1")],
                  objects=[dict(schemaVersion=1, objectId="owner", description="Owner change")],
                  checks=[dict(record, checkId=d, objectId="owner", dimension=d) for d in
                          ("data", "inflight", "lifecycle", "permission", "side_effect", "ordering")],
                  obligations=[obligation])
    scenario = dict(schemaVersion=1, scenarioId="S1", priority="P0",
                    title="Change owner", expected="Owner becomes B", baselineVersion="1",
                    preconditions=["Current owner is A"], reviewStatus="approved", acIds=["A1"])
    case = dict(schemaVersion=1, caseId="C1", suite="api", scenarioIds=["S1"],
                priority="P0", fixtures=[],
                reviewStatus="approved", semanticReview="approved", automationRef="test:owner",
                steps=[dict(id="step1", action="Change owner")],
                assertions=[dict(stepId="step1", expected="Owner becomes B")])
    return dict(schemaVersion=1, runId="run1", sourceHash="a" * 64,
                currentSourceHash="a" * 64, impactAnalysis=impact,
                impactHash=coverage["digest"](impact), currentSourceHashes={"prd": "a" * 64},
                baselineHash=coverage["digest"]([scenario]),
                acs=[dict(schemaVersion=1, acId="A1", sourceId="prd", sourceHash="a" * 64,
                          requirementId="R1", text="Owner becomes B", priority="P0",
                          sourceRef="prd.md:1", reviewStatus="approved")],
                scenarios=[scenario], cases=[case], requiredMatrix=["api"],
                discovered=[dict(caseId="C1", matrixId="api", automationRef="test:owner")])


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def evaluate(self, freeze=True):
        if freeze and "impactAnalysis" in self.data:
            self.data["impactHash"] = coverage["digest"](self.data["impactAnalysis"])
        return coverage["evaluate"](self.data, "plan")

    def blocked(self, reason):
        result = self.evaluate()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertTrue(any(reason in r for r in result["reasons"]), result)

    def test_complete_plan(self):
        self.assertEqual(self.evaluate()["status"], "PASSED")

    def test_every_mandatory_case_needs_assertions_and_discovery(self):
        self.data["cases"].append(dict(self.data["cases"][0], caseId="C2"))
        self.assertEqual(self.evaluate()["status"], "BLOCKED")
        self.data["discovered"].append(dict(self.data["discovered"][0], caseId="C2"))
        self.assertEqual(self.evaluate()["status"], "PASSED")
        self.data["cases"][1]["assertions"] = []
        self.assertEqual(self.evaluate()["status"], "BLOCKED")

    def test_missing_analysis_cannot_pass_legacy_input(self):
        del self.data["impactAnalysis"]
        self.blocked("missing impact analysis")

    def test_omitted_inflight_obligation(self):
        self.data["impactAnalysis"]["obligations"].append(dict(
            self.data["impactAnalysis"]["obligations"][0], obligationId="O2",
            text="Other active work orders", acIds=[]))
        self.blocked("O2")

    def test_missing_dimension_or_unresolved_rule(self):
        self.data["impactAnalysis"]["checks"].pop()
        self.blocked("ordering")
        self.data = fixture()
        self.data["impactAnalysis"]["checks"][1]["disposition"] = "unknown"
        self.blocked("inflight")

    def test_exclusions_require_evidence_and_out_of_scope_blocks(self):
        check = self.data["impactAnalysis"]["checks"][0]
        check.update(disposition="not_applicable", obligationIds=[], reason="No shared data")
        self.assertEqual(self.evaluate()["status"], "PASSED")
        check["reviewRef"] = ""
        self.blocked("data")
        check["reviewRef"] = "review:1"
        check["disposition"] = "out_of_scope"
        self.blocked("out of scope")

    def test_stale_impact_and_source(self):
        self.data["impactAnalysis"]["objects"][0]["description"] = "changed"
        self.assertEqual(self.evaluate(False)["status"], "BLOCKED")
        self.data["currentSourceHashes"]["prd"] = "b" * 64
        self.blocked("stale source")

    def test_non_prd_rule_can_supply_ac(self):
        source = dict(self.data["impactAnalysis"]["sources"][0], sourceId="rule",
                      kind="business_rule", sourceHash="b" * 64)
        self.data["impactAnalysis"]["sources"].append(source)
        unit = dict(self.data["impactAnalysis"]["sourceUnits"][0], unitId="U2", sourceIds=["rule"])
        self.data["impactAnalysis"]["sourceUnits"].append(unit)
        self.data["currentSourceHashes"]["rule"] = "b" * 64
        self.data["acs"][0].update(sourceId="rule", sourceHash="b" * 64)
        self.data["impactAnalysis"]["obligations"][0]["sourceIds"].append("rule")
        self.assertEqual(self.evaluate()["status"], "PASSED")

    def test_observation_does_not_authorize_expected_result(self):
        self.data["impactAnalysis"]["obligations"][0]["approvalRef"] = ""
        self.blocked("O1")

    def test_sources_and_acs_cannot_disappear_from_inventory(self):
        self.data["impactAnalysis"]["sourceUnits"] = []
        self.blocked("source inventory")
        self.data = fixture()
        self.data["acs"].append(dict(self.data["acs"][0], acId="A2"))
        self.blocked("obligations")

    def test_threshold_does_not_waive_missing_required_scenario(self):
        self.data["threshold"] = .95
        self.data["scenarios"] += [dict(self.data["scenarios"][0], scenarioId=f"S{i}", priority="P1")
                                   for i in range(2, 21)]
        self.data["cases"][0]["scenarioIds"] = [f"S{i}" for i in range(1, 20)]
        self.data["baselineHash"] = coverage["digest"](self.data["scenarios"])
        self.assertEqual(self.evaluate()["status"], "BLOCKED")

    def test_cli_rejects_malformed_analysis(self):
        self.data["impactAnalysis"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(self.data))
            result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(path), "--phase", "plan"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")

    def execution(self, statuses=("passed",), matrices=("api",)):
        self.data["requiredMatrix"] = list(matrices)
        self.data["discovered"] = [dict(caseId="C1", matrixId=m, automationRef="test:owner")
                                   for m in matrices]
        self.data.update(scope="full", inputHash="frozen", expectedShards=["shard1"])
        rows = [dict(schemaVersion=1, runId="run1", caseId="C1", matrixId=m,
                     shardId="shard1", attempt=i, status=status, startedAt="2026-11-05",
                     durationMs=1, environment="fixture", evidence=["synthetic"], error=None)
                for m in matrices for i, status in enumerate(statuses, 1)]
        self.data["results"] = rows
        self.freeze_shard()

    def freeze_shard(self):
        self.data["shards"] = [dict(shardId="shard1", inputHash="frozen", state="completed",
                                   resultHash=coverage["digest"](self.data["results"]))]

    def test_execution_all_matrices_and_business_failures(self):
        self.execution(matrices=("web", "mobile"))
        self.assertEqual(coverage["evaluate"](self.data)["status"], "PASSED")
        original = copy.deepcopy(self.data)
        for status in ("failed", "error", "blocked", "skipped", "cancelled", "flaky"):
            with self.subTest(status=status):
                self.data = copy.deepcopy(original)
                self.data["results"][0]["status"] = status
                self.freeze_shard()
                self.assertEqual(coverage["evaluate"](self.data)["status"], "BLOCKED")

    def test_retries_partial_missing_matrix_and_shards(self):
        self.execution(statuses=("failed", "passed"))
        result = coverage["evaluate"](self.data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["statusCounts"]["flaky"], 1)
        self.execution(matrices=("web", "mobile"))
        self.data["results"].pop()
        self.freeze_shard()
        self.assertEqual(coverage["evaluate"](self.data)["status"], "BLOCKED")
        self.execution()
        self.data["scope"] = "partial"
        self.assertEqual(coverage["evaluate"](self.data)["status"], "BLOCKED")
        self.data["scope"] = "full"
        self.data["expectedShards"].append("missing")
        self.assertEqual(coverage["evaluate"](self.data)["status"], "BLOCKED")

    def test_execution_rechecks_impact(self):
        self.execution()
        self.data["impactAnalysis"]["checks"][0]["disposition"] = "unknown"
        self.data["impactHash"] = coverage["digest"](self.data["impactAnalysis"])
        self.assertEqual(coverage["evaluate"](self.data)["status"], "BLOCKED")

    def test_unknown_and_dangling_records(self):
        original = copy.deepcopy(self.data)
        mutations = [
            lambda a: a["checks"][0].update(objectId="missing"),
            lambda a: a["checks"][0].update(obligationIds=["missing"]),
            lambda a: a["obligations"][0].update(sourceIds=["missing"]),
            lambda a: a["obligations"][0].update(reviewStatus="needs_clarification"),
            lambda a: a["obligations"][0].update(expected=""),
            lambda a: a["sourceUnits"][0].update(disposition="unknown"),
        ]
        for mutate in mutations:
            self.data = copy.deepcopy(original)
            mutate(self.data["impactAnalysis"])
            self.assertEqual(self.evaluate()["status"], "BLOCKED")

    def test_cli_valid_plan_and_execution(self):
        self.execution()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(self.data))
            for phase in ("plan", "execution"):
                result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(path), "--phase", phase],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(json.loads(result.stdout)["impactAnalysis"]["status"], "PASSED")


if __name__ == "__main__":
    unittest.main()
