#!/usr/bin/env python3
"""Add one detached worktree at a frozen commit. The same path and SHA is reused."""
import argparse
import json
import re
import subprocess
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")


def git(args, cwd):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(detail or "git failed")
    return result.stdout.strip()


def add(repo, sha, dest):
    repo = Path(repo).resolve()
    dest = Path(dest).resolve()
    if not SHA.fullmatch(sha):
        raise ValueError("SHA must be the full 40-hex commit")
    if git(["git", "rev-parse", "--verify", f"{sha}^{{commit}}"], repo) != sha:
        raise ValueError("ref is not that commit")
    if dest.exists():
        if git(["git", "-C", str(dest), "rev-parse", "HEAD"], repo) != sha:
            raise ValueError("worktree exists at a different commit")
        return {"path": str(dest), "sha": sha, "created": False}
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(["git", "worktree", "add", "--detach", str(dest), sha], repo)
    return {"path": str(dest), "sha": sha, "created": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("sha")
    parser.add_argument("dest", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(add(args.repo, args.sha, args.dest)))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    main()
