import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "baseline_worktree", Path(__file__).resolve().parents[1] / "scripts/baseline_worktree.py")
worktree = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worktree)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class BaselineWorktreeTest(unittest.TestCase):
    def test_adds_once_and_rejects_a_different_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.email", "t@example.com")
            git(repo, "config", "user.name", "t")
            (repo / "A.java").write_text("class A {}\n", encoding="utf-8")
            git(repo, "add", "A.java")
            git(repo, "commit", "-m", "base")
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            dest = root / "baseline"
            first = worktree.add(repo, sha, dest)
            second = worktree.add(repo, sha, dest)
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual((dest / "A.java").read_text(encoding="utf-8"), "class A {}\n")
            (repo / "A.java").write_text("class A { int x; }\n", encoding="utf-8")
            git(repo, "commit", "-am", "next")
            other = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            with self.assertRaises(ValueError):
                worktree.add(repo, other, dest)


if __name__ == "__main__":
    unittest.main()
