import json
import tempfile
import unittest
from pathlib import Path

from host_config import configure, project_settings, runtime_overrides


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
