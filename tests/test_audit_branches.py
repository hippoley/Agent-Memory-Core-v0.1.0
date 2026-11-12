"""The branch audit must be checkable, so its core logic gets tests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_branches import branch_drift, collect, render  # noqa: E402

GIT = ["git", "-c", "user.email=audit@example.com", "-c", "user.name=audit",
       "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(GIT + list(args), cwd=repo, capture_output=True,
                            text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _commit(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content, encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-m", message)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _run(root, "init")
    _commit(root, "core.py", "v1\n", "init")
    return root


def test_branch_only_file_is_reported_as_drift(repo):
    _run(repo, "checkout", "-b", "feature")
    _commit(repo, "extra.py", "new\n", "add extra")
    _run(repo, "checkout", "main")

    assert branch_drift("main", "feature", cwd=str(repo)) == ["extra.py"]


def test_modified_file_is_not_drift(repo):
    _run(repo, "checkout", "-b", "edit")
    _commit(repo, "core.py", "v2\n", "edit core")
    _run(repo, "checkout", "main")

    assert branch_drift("main", "edit", cwd=str(repo)) == []


def test_same_path_on_main_is_not_drift(repo):
    """Main reimplementing the same path means the capability landed, not drifted."""
    _run(repo, "checkout", "-b", "feature")
    _commit(repo, "shared.py", "branch version\n", "add shared")
    _run(repo, "checkout", "main")
    _commit(repo, "shared.py", "main version\n", "own shared")

    assert branch_drift("main", "feature", cwd=str(repo)) == []


def test_collect_counts_ahead_and_drift(repo):
    _run(repo, "checkout", "-b", "feature")
    _commit(repo, "extra.py", "new\n", "add extra")
    _run(repo, "checkout", "main")
    _run(repo, "update-ref", "refs/remotes/origin/main", "main")
    _run(repo, "update-ref", "refs/remotes/origin/feature", "feature")

    report = collect("origin", "main", cwd=str(repo))
    entry = next(b for b in report["branches"] if b["branch"] == "feature")

    assert entry["ahead_of_base"] == 1
    assert entry["files_added"] == 1
    assert entry["paths_missing_on_base"] == ["extra.py"]
    assert entry["status"] == "DRIFT"
    assert "drift: 1" in render(report)


def test_symbolic_head_ref_is_not_a_branch(repo):
    """refs/remotes/origin/HEAD must not show up as a branch called ''. """
    _run(repo, "update-ref", "refs/remotes/origin/main", "main")
    _run(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    report = collect("origin", "main", cwd=str(repo))
    names = [b["branch"] for b in report["branches"]]
    assert "" not in names
    assert "main" not in names
