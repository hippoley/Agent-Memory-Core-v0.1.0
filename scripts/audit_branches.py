#!/usr/bin/env python3
"""Branch consolidation audit: which files exist on a branch but not on the base.

The question this answers is narrow and checkable: *by content path*, does the
integration base actually carry everything a branch added? It deliberately does
not merge anything, and it does not treat "N commits ahead" as evidence — a
branch can be hundreds of commits ahead and still contain nothing the base
lacks, because the base moved on and reimplemented the same capability.

    python scripts/audit_branches.py                 # table for every branch
    python scripts/audit_branches.py --json          # machine-readable
    python scripts/audit_branches.py --fail-on-drift # exit 1 when paths are missing

"DRIFT" means a path was added on the branch relative to the merge base and does
not exist at that path on the base. DRIFT is a question, not a verdict: the
capability may live on the base under another name. Resolve it by reading code,
then record the disposition in docs/BRANCH-INTEGRATION-AUDIT.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def _git(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _try_git(*args: str, cwd: str | None = None) -> str | None:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=cwd)
    return result.stdout if result.returncode == 0 else None


def list_remote_branches(remote: str, cwd: str | None = None) -> list[str]:
    # Use full refnames plus %(symref): refs/remotes/<remote>/HEAD is a symbolic
    # ref whose short name collapses to the remote name, which would otherwise
    # produce an empty branch called "".
    raw = _git("for-each-ref",
               "--format=%(refname)\t%(symref)",
               f"refs/remotes/{remote}", cwd=cwd)
    prefix = f"refs/remotes/{remote}/"
    branches = []
    for line in raw.splitlines():
        refname, _, symref = line.partition("\t")
        if symref.strip():
            continue
        if not refname.startswith(prefix):
            continue
        name = refname[len(prefix):]
        if name and name != "HEAD":
            branches.append(name)
    return sorted(branches)


def path_exists(ref: str, path: str, cwd: str | None = None) -> bool:
    return _try_git("cat-file", "-e", f"{ref}:{path}", cwd=cwd) is not None


def branch_drift(base_ref: str, branch_ref: str,
                 cwd: str | None = None) -> list[str]:
    """Paths added on the branch (vs merge base) that are absent on the base."""
    merge_base = _try_git("merge-base", base_ref, branch_ref, cwd=cwd)
    if not merge_base or not merge_base.strip():
        return []
    added = _git("diff", "--diff-filter=A", "--name-only",
                 merge_base.strip(), branch_ref, cwd=cwd)
    return [path for path in (p.strip() for p in added.splitlines())
            if path and not path_exists(base_ref, path, cwd=cwd)]


def collect(remote: str, base_branch: str, cwd: str | None = None) -> dict:
    base_ref = f"{remote}/{base_branch}"
    if _try_git("rev-parse", "--verify", base_ref, cwd=cwd) is None:
        raise SystemExit(f"base ref not found: {base_ref}")
    base_commit = _git("rev-parse", base_ref, cwd=cwd).strip()
    branches = list_remote_branches(remote, cwd=cwd)
    report = {"base_ref": base_ref, "base_commit": base_commit, "branches": []}
    for branch in branches:
        if branch == base_branch:
            continue
        branch_ref = f"{remote}/{branch}"
        merge_base = _try_git("merge-base", base_ref, branch_ref, cwd=cwd)
        added = 0
        if merge_base and merge_base.strip():
            added = len([p for p in _git(
                "diff", "--diff-filter=A", "--name-only",
                merge_base.strip(), branch_ref, cwd=cwd).splitlines() if p.strip()])
        missing = branch_drift(base_ref, branch_ref, cwd=cwd)
        report["branches"].append({
            "branch": branch,
            "ahead_of_base": int(_git(
                "rev-list", "--count", f"{base_ref}..{branch_ref}",
                cwd=cwd).strip() or 0),
            "files_added": added,
            "paths_missing_on_base": missing,
            "status": "DRIFT" if missing else "COVERED",
        })
    return report


def render(report: dict) -> str:
    lines = [
        f"base: {report['base_ref']} @ {report['base_commit'][:12]}",
        "",
        f"{'branch':<52}{'ahead':>7}{'added':>7}  status",
        "-" * 78,
    ]
    for entry in report["branches"]:
        lines.append(f"{entry['branch']:<52}{entry['ahead_of_base']:>7}"
                     f"{entry['files_added']:>7}  {entry['status']}")
    drift = [e for e in report["branches"] if e["paths_missing_on_base"]]
    if drift:
        lines += ["", "paths present on branch but absent on base:"]
        for entry in drift:
            lines.append(f"  {entry['branch']}:")
            lines += [f"    {path}" for path in entry["paths_missing_on_base"]]
    else:
        lines += ["", "no path-level drift: every added path exists on the base."]
    lines += ["", f"branches audited: {len(report['branches'])}  "
                  f"drift: {len(drift)}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--json", action="store_true", help="emit the raw report")
    parser.add_argument("--fail-on-drift", action="store_true",
                        help="exit 1 if any branch has paths missing on the base")
    args = parser.parse_args(argv)

    try:
        report = collect(args.remote, args.base_branch)
    except RuntimeError as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json
          else render(report))
    if args.fail_on_drift and any(e["paths_missing_on_base"] for e in report["branches"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
