import hashlib
import runpy
import json
import tempfile
import uuid
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path

from host_config import configure, load_override, project_settings, runtime_overrides, workspace_paths


class RunIdTests(unittest.TestCase):
    def test_generated_ids_are_unique_with_concurrent_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "项目 name"
            project.mkdir()
            options = dict(runtime_dir=root / "runtime", state_dir=root / "state", initialize=True)
            with ThreadPoolExecutor(max_workers=4) as executor:
                runs = list(executor.map(lambda _: configure(project, "codex", **options), range(4)))
            self.assertEqual(len({run[4] for run in runs}), 4)
            self.assertTrue(all(uuid.UUID(run[4]).version == 4 for run in runs))
            self.assertEqual({run[1]["projectId"] for run in runs}, {"项目-name"})
            self.assertTrue(all(run[3].is_file() for run in runs))
            self.assertEqual(configure(project, "codex", run_id=runs[0][4], **options)[4], runs[0][4])

    def test_same_name_projects_and_legacy_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "a" / "app", root / "b" / "app"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            options = dict(runtime_dir=root / "runtime", state_dir=root / "state", initialize=True)
            one = configure(first, "codex", **options)
            two = configure(second, "codex", **options)
            self.assertEqual(one[1]["projectId"], "app")
            self.assertEqual(two[1]["projectId"], "app-01")
            self.assertEqual(configure(first, "codex", **options)[1]["projectId"], "app")
            third = root / "c" / "app"
            third.mkdir(parents=True)
            self.assertEqual(configure(third, "codex", **options)[1]["projectId"], "app-02")
            legacy = root / "legacy"
            legacy.mkdir()
            old_id = hashlib.sha256(str(legacy.resolve()).encode()).hexdigest()[:24]
            (root / "state" / "projects" / old_id).mkdir()
            self.assertEqual(configure(legacy, "codex", **options)[1]["projectId"], old_id)
            self.assertEqual(configure(first, "codex", project_id=old_id, **options)[1]["projectId"], old_id)

    def test_existing_numbered_project_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "app"
            project.mkdir()
            existing = root / "state" / "projects" / "app-01"
            existing.mkdir(parents=True)
            (existing / ".project-id").write_text(hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:24])
            options = dict(runtime_dir=root / "runtime", state_dir=root / "state")
            self.assertEqual(configure(project, "codex", **options)[1]["projectId"], "app-01")


class InlineOverridesTests(unittest.TestCase):
    def test_cli_accepts_confirmed_inline_overrides_and_freezes_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            command = [sys.executable, str(Path(__file__).with_name("host_config.py")),
                       "--host", "codex", "--project", str(project), "--runtime-dir", str(root / "runtime"),
                       "--state-dir", str(root / "state"), "--init",
                       "--overrides", json.dumps({"schemaVersion": 1, "execution": {"requireHumanCaseReview": True}}),
                       "--project-overrides", json.dumps({"schemaVersion": 1, "delivery": {"mode": "local"},
                           "bugPublisher": {"method": "local", "localFormat": "csv"}})]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            report = json.loads(result.stdout)
            snapshot = json.loads(Path(report["runConfigPath"]).read_text())
            self.assertEqual(snapshot["config"]["delivery"]["mode"], "local")
            self.assertTrue(snapshot["config"]["execution"]["requireHumanCaseReview"])
            self.assertEqual(list((root / "state").rglob("*.csv")), [])
            self.assertEqual(list((root / "state").rglob("*.xlsx")), [])

    def test_file_override_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "override.json"
            data = {"schemaVersion": 1, "execution": {"webAssistant": "none"}}
            path.write_text(json.dumps(data))
            self.assertEqual(load_override(str(path)), data)

    def test_rejects_mistyped_delivery_keys(self):
        with self.assertRaises(ValueError):
            project_settings({"schemaVersion": 1, "delivery": {"channel": "local", "format": "csv"}})


class SetupArtifactTests(unittest.TestCase):
    def test_initialization_does_not_create_or_change_tables(self):
        for mode, publisher in (("local", {"method": "local", "localFormat": "csv"}),
                                ("local", {"method": "local", "localFormat": "xlsx"}),
                                ("online", {"method": "auto"})):
            with self.subTest(mode=mode, publisher=publisher), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / "project"
                project.mkdir()
                state = root / "state"
                existing = state / "projects" / "existing" / "tables" / "bugs.csv"
                existing.parent.mkdir(parents=True)
                existing.write_text("historical data\n")
                before = {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()}
                configure(project, "codex", runtime_dir=root / "runtime", state_dir=state,
                          initialize=True, project_overrides={"schemaVersion": 1,
                          "delivery": {"mode": mode}, "bugPublisher": publisher})
                tables = {p.relative_to(state): p.read_bytes() for p in state.rglob("*")
                          if p.is_file() and p.suffix in {".csv", ".xlsx"}}
                self.assertEqual(tables, before)


class HumanCaseReviewConfigTests(unittest.TestCase):
    def test_default_legacy_config_and_frozen_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            runtime = root / "runtime"
            runtime.mkdir()
            config_path = runtime / "argus-model.json"
            config_path.write_text(json.dumps({
                "schemaVersion": 1, "execution": {"workers": 3}, "projects": {}
            }))

            def run(run_id, **options):
                return configure(project, "codex", runtime_dir=runtime,
                                 state_dir=root / "state", run_id=run_id, **options)

            config, _, _, snapshot, _ = run("default")
            self.assertIs(config["execution"]["requireHumanCaseReview"], True)
            original_snapshot = snapshot.read_bytes()
            config, _, _, _, _ = run("disabled", initialize=True, overrides={
                "schemaVersion": 1, "execution": {"requireHumanCaseReview": False}
            })
            self.assertIs(config["execution"]["requireHumanCaseReview"], False)
            self.assertEqual(config["execution"]["workers"], 3)
            self.assertIs(run("persisted")[0]["execution"]["requireHumanCaseReview"], False)
            self.assertEqual(snapshot.read_bytes(), original_snapshot)
            config, _, _, _, _ = run("enabled", overrides={
                "schemaVersion": 1, "execution": {"requireHumanCaseReview": True}
            })
            self.assertIs(config["execution"]["requireHumanCaseReview"], True)
            self.assertIs(run("still-disabled")[0]["execution"]["requireHumanCaseReview"], False)

    def test_project_switch_and_runtime_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            options = dict(runtime_dir=root / "runtime", state_dir=root / "state",
                           project_overrides={"schemaVersion": 1, "execution": {
                               "requireHumanCaseReview": False}})
            self.assertIs(configure(project, "codex", **options)[0]["execution"]["requireHumanCaseReview"], False)
            config = configure(project, "codex", **options, overrides={
                "schemaVersion": 1, "execution": {"requireHumanCaseReview": True}})[0]
            self.assertIs(config["execution"]["requireHumanCaseReview"], True)

    def test_switch_rejects_non_booleans(self):
        for validate in (project_settings, runtime_overrides):
            for value in ("false", "true", 0, 1, None, [], {}):
                with self.subTest(validator=validate.__name__, value=value):
                    with self.assertRaisesRegex(ValueError, "must be a boolean"):
                        validate({"schemaVersion": 1, "execution": {
                            "requireHumanCaseReview": value}})





class WebAssistantConfigTests(unittest.TestCase):
    def test_values_and_invalid_inputs(self):
        for validate in (project_settings, runtime_overrides):
            for value in ("none", "ego"):
                data = {"schemaVersion": 1, "execution": {"webAssistant": value}}
                self.assertEqual(validate(data)["execution"]["webAssistant"], value)
            for value in (True, None, [], {}, "chrome", ""):
                with self.assertRaises(ValueError):
                    validate({"schemaVersion": 1, "execution": {"webAssistant": value}})

    def test_persist_and_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            options = dict(runtime_dir=root / "runtime", state_dir=root / "state")
            config, _, _, snapshot, _ = configure(project, "codex", **options,
                initialize=True, run_id="ego", project_overrides={
                    "schemaVersion": 1, "execution": {"webAssistant": "ego"}})
            self.assertEqual(config["execution"]["webAssistant"], "ego")
            frozen = snapshot.read_bytes()
            self.assertEqual(configure(project, "codex", **options)[0]["execution"]["webAssistant"], "ego")
            changed = configure(project, "codex", **options, run_id="none", overrides={
                "schemaVersion": 1, "execution": {"webAssistant": "none"}})[0]
            self.assertEqual(changed["execution"]["webAssistant"], "none")
            self.assertEqual(snapshot.read_bytes(), frozen)


class ProjectNamingTests(unittest.TestCase):
    def test_names_collisions_and_concurrent_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a" / "todo-app"
            options = dict(host="unknown", state_dir=root / "state", allocate=True)
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(lambda _: workspace_paths(first, **options), range(4)))
            self.assertEqual({p["projectId"] for p in paths}, {"todo-app"})
            for index, expected in enumerate(("todo-app-01", "todo-app-02")):
                self.assertEqual(workspace_paths(root / str(index) / "todo-app", **options)["projectId"], expected)

    def test_existing_numbered_and_legacy_projects_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "app"
            project_id = hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:24]
            existing = root / "state" / "projects" / "app-01"
            existing.mkdir(parents=True)
            (existing / ".project-id").write_text(project_id)
            options = dict(host="unknown", state_dir=root / "state", allocate=True)
            self.assertEqual(workspace_paths(project, **options)["projectId"], "app-01")
            legacy_project = root / "legacy"
            legacy_id = hashlib.sha256(str(legacy_project.resolve()).encode()).hexdigest()[:24]
            (root / "state" / "projects" / legacy_id).mkdir()
            self.assertEqual(workspace_paths(legacy_project, **options)["projectId"], legacy_id)

    def test_configure_persists_named_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = dict(runtime_dir=root / "runtime", state_dir=root / "state", initialize=True)
            first = configure(root / "todo-app", "codex", **options)
            self.assertEqual(first[1]["projectId"], "todo-app")
            self.assertEqual(configure(root / "todo-app", "codex", **options)[1]["projectId"], "todo-app")

    def test_local_publisher_accepts_named_project(self):
        publish = runpy.run_path(str(Path(__file__).resolve().parents[2] / "bug-reporter/scripts/local_table.py"))["publish"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, sources, _, snapshot, run_id = configure(root / "todo-app", "codex", runtime_dir=root / "runtime",
                state_dir=root / "state", initialize=True, project_overrides={"schemaVersion": 1,
                    "delivery": {"mode": "local"}, "bugPublisher": {"method": "local"}})
            summary = dict.fromkeys(("status", "scope", "coverage", "p0", "counts", "cleanup", "limitations"), "test")
            summary.update(featureOverview="test", caseCount=0, executedCount=0, passedCount=0,
                           incompleteCases=[], executor="", executedAt="2026-11-05T00:00:00+00:00")
            result = publish(snapshot, {"schemaVersion": 1, "runId": run_id, "summary": summary, "bugs": []}, delivery_date="20261105", feature_name="test")
            self.assertEqual(Path(result["runTable"]).parent.parent, Path(sources["projectDir"]))


if __name__ == "__main__":
    unittest.main()
