import hashlib
import runpy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from host_config import configure, workspace_paths


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
            result = publish(snapshot, {"schemaVersion": 1, "runId": run_id, "summary": summary, "bugs": []})
            self.assertEqual(Path(result["runTable"]).parent.parent, Path(sources["projectDir"]))


if __name__ == "__main__":
    unittest.main()
