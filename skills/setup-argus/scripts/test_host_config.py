import json
import hashlib
import runpy
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

from host_config import configure, project_settings, runtime_overrides, workspace_paths


class ProjectDirectoryTests(unittest.TestCase):
    def test_concurrent_names_do_not_overwrite_existing_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            occupied = root / "state" / "projects" / "example"
            occupied.mkdir(parents=True)
            def paths(index):
                return workspace_paths(root / str(index) / "example", "unknown",
                                       state_dir=root / "state")["projectDir"]
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(paths, range(4)))
            self.assertEqual({p.name for p in results}, {f"example-{i}" for i in range(2, 6)})
            self.assertEqual(list(occupied.iterdir()), [])

    def test_named_directory_publishes_tables_and_rejects_wrong_identity(self):
        publish = runpy.run_path(str(Path(__file__).resolve().parents[2] /
                                    "bug-reporter/scripts/local_table.py"))["publish"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, sources, _, snapshot, _ = configure(root / "example", "unknown",
                state_dir=root / "state", run_id="run", project_overrides={
                    "schemaVersion": 1, "delivery": {"mode": "local"}})
            summary = dict.fromkeys(("status", "scope", "coverage", "p0", "counts", "cleanup", "limitations"), "test")
            summary.update(featureOverview="test", caseCount=0, executedCount=0, passedCount=0,
                           incompleteCases=[], executor="", executedAt="2026-11-05T00:00:00+00:00")
            data = {"schemaVersion": 1, "runId": "run", "summary": summary, "bugs": []}
            receipt = publish(snapshot, data)
            self.assertEqual(Path(receipt["runTable"]).parent.parent, Path(sources["projectDir"]))
            (Path(sources["projectDir"]) / ".project-id").write_text("0" * 24)
            with self.assertRaisesRegex(ValueError, "invalid project scope"):
                publish(snapshot, data)

    def test_names_collisions_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = dict(host="unknown", state_dir=root / "state")
            first = workspace_paths(root / "one" / "项目", **options)
            second = workspace_paths(root / "two" / "项目", **options)
            self.assertEqual(first["projectDir"].name, "项目")
            self.assertEqual(second["projectDir"].name, "项目-2")
            self.assertNotEqual(first["projectId"], second["projectId"])
            self.assertEqual(workspace_paths(root / "one" / "项目", **options), first)
            self.assertEqual(workspace_paths(root / "elsewhere", project_id=first["projectId"],
                                             **options), first)

    def test_legacy_migration_preserves_files_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = (root / "example").resolve()
            project_id = hashlib.sha256(str(project).encode()).hexdigest()[:24]
            legacy = root / "state" / "projects" / project_id
            legacy.mkdir(parents=True)
            (legacy / "web-private.json").write_text("{}")
            _, _, _, snapshot, _ = configure(project, "codex", runtime_dir=root / "runtime",
                state_dir=root / "state", project_id=project_id, initialize=True, run_id="old",
                project_overrides={"schemaVersion": 1, "execution": {"requireHumanCaseReview": False}})
            frozen = snapshot.read_bytes()
            result = workspace_paths(project, "unknown", state_dir=root / "state")
            self.assertEqual(result["projectId"], project_id)
            self.assertEqual(result["projectDir"].name, "example")
            self.assertEqual((result["projectDir"] / "web-private.json").read_text(), "{}")
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(legacy.resolve(), result["projectDir"])
            self.assertEqual(snapshot.read_bytes(), frozen)
            config = configure(project, "codex", runtime_dir=root / "runtime", state_dir=root / "state")[0]
            self.assertIs(config["execution"]["requireHumanCaseReview"], False)


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


if __name__ == "__main__":
    unittest.main()
