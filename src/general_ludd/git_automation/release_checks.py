"""Pure release-readiness checks shared by Git release operations."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

CommandRunner = Callable[[list[str]], tuple[int, str]]
RevisionResolver = Callable[[str, str], str]


def require_ci_green(
    sha: str | None,
    branch: str,
    *,
    git_rev_parse: RevisionResolver,
    run_gh: CommandRunner,
    repo: str,
) -> tuple[int, str]:
    """Return a stable release verdict for the newest matching CI run."""
    if sha is None:
        sha = git_rev_parse(".", "HEAD")
        if not sha:
            return (1, "Could not determine HEAD SHA")

    rc, output = run_gh(
        [
            "run",
            "list",
            "--commit",
            sha,
            "--branch",
            branch,
            "-R",
            repo,
            "--json",
            "conclusion,databaseId,status,headSha",
            "--limit",
            "3",
        ]
    )
    if rc != 0:
        return (1, f"CI ERROR: gh run list failed: {output}")
    try:
        runs = json.loads(output)
    except json.JSONDecodeError:
        return (1, f"CI ERROR: could not parse gh output: {output}")
    if not runs:
        return (1, f"CI RED: no run found for SHA {sha}")

    latest = runs[0]
    conclusion = latest.get("conclusion")
    run_id = latest.get("databaseId", "?")
    status = latest.get("status", "?")
    if conclusion == "success":
        return (0, f"CI GREEN: sha={sha} run={run_id}")
    if conclusion in ("cancelled", "skipped"):
        return (0, f"CI BYPASS: sha={sha} run={run_id} conclusion={conclusion}")
    if conclusion in ("failure", "timed_out"):
        return (1, f"CI RED: sha={sha} run={run_id} conclusion={conclusion}")
    return (2, f"CI PENDING: sha={sha} run={run_id} status={status}")


def check_readme_status_inner(tag: str, *, module_file: str) -> tuple[int, str]:
    """Verify README's status marker matches the proposed release tag."""
    repo_root = Path(module_file).resolve().parent.parent.parent.parent
    readme = repo_root / "README.md"
    if not readme.exists():
        return (1, "ERROR: README.md not found")

    release_version = tag.strip()
    text = readme.read_text(encoding="utf-8")
    match = re.search(r"[Ss]tatus\s+as\s+of\s+(v?[\w.\-]+)", text)
    if not match:
        return (
            1,
            "ERROR: README status table is stale — no 'Status as of <version>' line "
            "found in README.md",
        )

    readme_version_raw = match.group(1)
    normalized_release = release_version.lower().removeprefix("v")
    normalized_readme = readme_version_raw.lower().removeprefix("v")
    if normalized_readme == normalized_release:
        return (
            0,
            "OK — README status table is current "
            f"(says {readme_version_raw!r}, releasing {release_version!r})",
        )
    return (
        1,
        "ERROR: README status table is stale: "
        f"says {readme_version_raw!r}, releasing {release_version!r}",
    )


__all__ = ["check_readme_status_inner", "require_ci_green"]
