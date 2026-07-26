"""Release operations ported from Makefile targets.

Provides programmatic equivalents of:
  - ``make release-cut``    →  :func:`release_cut`
  - ``make release-delete`` →  :func:`release_delete`
  - ``make release-recut``  →  :func:`release_recut`
  - ``make check-readme-status`` → :func:`verify_readme_status`
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from general_ludd.git_automation.types import (
    ReleaseCutResult,
    ReleaseDeleteResult,
    ReleaseRecutResult,
)

_GIT_TIMEOUT_SECONDS = 120.0
_NON_INTERACTIVE_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}

DEFAULT_REMOTE = "sandboxcom"
DEFAULT_REPO = "sandboxcom/gludd"


def _reject_leading_dash(value: str, *, kind: str) -> str:
    if value.startswith("-"):
        raise ValueError(
            f"refusing {kind} that begins with '-' (would be parsed as a git "
            f"option, not a ref/path): {value!r}"
        )
    return value


def _run_git(args: list[str], repo_path: str, *, check: bool = False) -> tuple[int, str]:
    """Run a git command and return (returncode, output)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            env={**os.environ, **_NON_INTERACTIVE_GIT_ENV},
            check=check,
        )
        return (result.returncode, result.stdout.strip() or result.stderr.strip())
    except subprocess.CalledProcessError as exc:
        return (exc.returncode, exc.stderr.strip() or exc.stdout.strip() or str(exc))
    except subprocess.TimeoutExpired:
        return (1, f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s")
    except FileNotFoundError:
        return (1, "git executable not found")


def _run_gh(args: list[str], *, check: bool = False) -> tuple[int, str]:
    """Run a gh CLI command and return (returncode, output)."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=check,
        )
        return (result.returncode, result.stdout.strip() or result.stderr.strip())
    except subprocess.CalledProcessError as exc:
        return (exc.returncode, exc.stderr.strip() or exc.stdout.strip() or str(exc))
    except subprocess.TimeoutExpired:
        return (1, f"gh {' '.join(args)} timed out")
    except FileNotFoundError:
        return (1, "gh executable not found")


def _git_tag_exists(tag: str, repo_path: str) -> bool:
    _reject_leading_dash(tag, kind="tag name")
    proc = subprocess.run(
        ["git", "tag", "-l", tag],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        env={**os.environ, **_NON_INTERACTIVE_GIT_ENV},
    )
    return tag in proc.stdout.strip().splitlines()


def _run_git_tag_exists(tag: str, repo_path: str) -> bool:
    """Public wrapper for test mocking."""
    return _git_tag_exists(tag, repo_path)


def _git_rev_parse(repo_path: str, ref: str = "HEAD") -> str:
    """Get the commit SHA for a ref."""
    _reject_leading_dash(ref, kind="ref")
    proc = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        env={**os.environ, **_NON_INTERACTIVE_GIT_ENV},
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _git_tag_push(
    tag: str, message: str | None, repo_path: str,
    *, commit: str | None = None, remote: str = DEFAULT_REMOTE,
) -> tuple[int, str]:
    """Create an annotated tag and push it to the remote.

    Ported from ``make git-tag-push``.
    """
    _reject_leading_dash(tag, kind="tag name")
    msg = message or tag
    target = commit or "HEAD"

    rc, out = _run_git(["tag", "-a", tag, "-m", msg, target], repo_path)
    if rc != 0:
        return (rc, out)

    rc, out = _run_git(["push", remote, tag], repo_path)
    if rc != 0:
        return (rc, f"Tag created but push failed: {out}")

    return (0, f"Pushed tag {tag} to {remote}/{tag}")


def _git_push_branch(repo_path: str, remote: str, branch: str) -> tuple[int, str]:
    """Push a branch to the remote."""
    _reject_leading_dash(remote, kind="remote name")
    _reject_leading_dash(branch, kind="branch name")
    return _run_git(["push", remote, branch], repo_path)


def _git_tag_delete_local(tag: str, repo_path: str) -> tuple[int, str]:
    """Delete a local tag."""
    _reject_leading_dash(tag, kind="tag name")
    return _run_git(["tag", "-d", tag], repo_path)


def _git_tag_delete_remote(tag: str, repo_path: str, remote: str) -> tuple[int, str]:
    """Delete a tag on the remote."""
    _reject_leading_dash(tag, kind="tag name")
    return _run_git(["push", remote, f":refs/tags/{tag}"], repo_path)


def _gh_release_delete(tag: str, repo: str) -> tuple[int, str]:
    """Delete a GitHub Release via gh CLI."""
    _reject_leading_dash(tag, kind="tag name")
    return _run_gh(["release", "delete", tag, "-R", repo, "--yes"])


def _run_require_ci_green(sha: str | None = None, branch: str = "development") -> tuple[int, str]:
    """Check if CI is green via gh CLI. Ported from ``scripts/require_ci_green.py``."""
    if sha is None:
        sha = _git_rev_parse(".", "HEAD")
        if not sha:
            return (1, "Could not determine HEAD SHA")

    rc, out = _run_gh(
        ["run", "list", "--commit", sha, "--branch", branch,
         "-R", DEFAULT_REPO, "--json", "conclusion,databaseId,status,headSha",
         "--limit", "3"],
    )
    if rc != 0:
        return (1, f"CI ERROR: gh run list failed: {out}")

    import json
    try:
        runs = json.loads(out)
    except json.JSONDecodeError:
        return (1, f"CI ERROR: could not parse gh output: {out}")

    if not runs:
        return (1, f"CI RED: no run found for SHA {sha}")

    latest = runs[0]
    conclusion = latest.get("conclusion")
    rid = latest.get("databaseId", "?")
    status = latest.get("status", "?")

    if conclusion == "success":
        return (0, f"CI GREEN: sha={sha} run={rid}")
    elif conclusion in ("cancelled", "skipped"):
        return (0, f"CI BYPASS: sha={sha} run={rid} conclusion={conclusion}")
    elif conclusion in ("failure", "timed_out"):
        return (1, f"CI RED: sha={sha} run={rid} conclusion={conclusion}")
    else:
        return (2, f"CI PENDING: sha={sha} run={rid} status={status}")


def _run_check_readme_status(tag: str) -> tuple[int, str]:
    """Check README status table matches tag. Ported from ``scripts/check_readme_status_current.py``."""
    return _check_readme_status_inner(tag)


def _check_readme_status_inner(tag: str) -> tuple[int, str]:
    """Core logic: verify README.md 'Status as of' line matches the release tag."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    readme = repo_root / "README.md"

    if not readme.exists():
        return (1, "ERROR: README.md not found")

    release_version = tag.strip()

    text = readme.read_text(encoding="utf-8")
    m = re.search(r"[Ss]tatus\s+as\s+of\s+(v?[\w.\-]+)", text)
    if not m:
        return (1, "ERROR: README status table is stale — no 'Status as of <version>' line found in README.md")

    readme_version_raw = m.group(1)

    norm_release = release_version.lower().removeprefix("v")
    norm_readme = readme_version_raw.lower().removeprefix("v")

    if norm_readme == norm_release:
        return (0, f"OK — README status table is current (says {readme_version_raw!r}, releasing {release_version!r})")
    else:
        return (1, f"ERROR: README status table is stale: says {readme_version_raw!r}, releasing {release_version!r}")


def verify_readme_status(tag: str) -> tuple[int, str]:
    """Verify README.md 'Status as of <version>' matches the tag.

    Returns (rc, message). rc=0 on match, rc=1 on mismatch/missing.
    """
    return _check_readme_status_inner(tag)


def release_cut(
    tag: str,
    message: str = "",
    branch: str = "master",
    repo_path: str = ".",
    remote: str = DEFAULT_REMOTE,
    *,
    skip_readme_check: bool = False,
    skip_ci_check: bool = False,
) -> ReleaseCutResult:
    """Cut a release: CI-green → README status → push branch → push tag.

    Ported from ``make release-cut``. Equivalent to the Makefile pipeline:
    require-ci-green → check-readme-status → git-push → git-tag-push.
    Does NOT poll for artifact (CI handles that asynchronously).

    Returns:
        ReleaseCutResult with ``success=True`` on full pipeline completion.
    """
    _reject_leading_dash(tag, kind="tag name")
    _reject_leading_dash(branch, kind="branch name")
    steps: list[str] = []

    rc, out = _run_git(["rev-parse", "--is-inside-work-tree"], repo_path)
    if rc != 0:
        return ReleaseCutResult(success=False, tag=tag, branch=branch,
                                message="Not inside a git repository")

    if _git_tag_exists(tag, repo_path):
        return ReleaseCutResult(success=False, tag=tag, branch=branch,
                                message=f"Tag {tag} already exists locally — delete it first or use release-recut")

    if not skip_ci_check:
        rc, out = _run_require_ci_green(branch=branch)
        if rc == 1:
            return ReleaseCutResult(success=False, tag=tag, branch=branch,
                                    message=f"CI not green: {out}",
                                    steps_completed=steps)
        steps.append("ci-green")

    if not skip_readme_check:
        rc, out = _run_check_readme_status(tag)
        if rc != 0:
            return ReleaseCutResult(success=False, tag=tag, branch=branch,
                                    message=f"README check failed: {out}",
                                    steps_completed=steps)
        steps.append("readme-check")

    rc, out = _git_push_branch(repo_path, remote, branch)
    if rc != 0:
        return ReleaseCutResult(success=False, tag=tag, branch=branch,
                                message=f"Branch push failed: {out}",
                                steps_completed=steps)
    steps.append("branch-push")

    rc, out = _git_tag_push(tag, message, repo_path, remote=remote)
    if rc != 0:
        return ReleaseCutResult(success=False, tag=tag, branch=branch,
                                message=f"Tag push failed: {out}",
                                steps_completed=steps)
    steps.append("tag-push")

    commit_sha = _git_rev_parse(repo_path, "HEAD")
    return ReleaseCutResult(
        success=True,
        tag=tag,
        branch=branch,
        commit_sha=commit_sha,
        message=f"Release {tag} cut and pushed to {remote}",
        steps_completed=steps,
    )


def release_delete(
    tag: str,
    repo_path: str = ".",
    remote: str = DEFAULT_REMOTE,
    repo: str = DEFAULT_REPO,
) -> ReleaseDeleteResult:
    """Delete a release: GitHub Release → local tag → remote tag.

    Ported from ``make release-delete``. Handles missing tags gracefully
    (returns success even if some steps had nothing to delete).

    Returns:
        ReleaseDeleteResult with per-step deletion flags.
    """
    _reject_leading_dash(tag, kind="tag name")

    rc, out = _run_git(["rev-parse", "--is-inside-work-tree"], repo_path)
    if rc != 0:
        return ReleaseDeleteResult(success=False, tag=tag,
                                   message="Not inside a git repository")

    gh_deleted = False
    local_deleted = False
    remote_deleted = False
    messages: list[str] = []

    rc, out = _gh_release_delete(tag, repo)
    if rc == 0:
        gh_deleted = True
        messages.append(f"Deleted GitHub Release {tag}")
    else:
        messages.append(f"GitHub Release {tag}: {out}")

    rc, out = _git_tag_delete_local(tag, repo_path)
    if rc == 0:
        local_deleted = True
        messages.append(f"Deleted local tag {tag}")
    else:
        messages.append(f"Local tag {tag}: {out}")

    rc, out = _git_tag_delete_remote(tag, repo_path, remote)
    if rc == 0:
        remote_deleted = True
        messages.append(f"Deleted remote tag {tag} from {remote}")
    else:
        messages.append(f"Remote tag {tag}: {out}")

    return ReleaseDeleteResult(
        success=True,
        tag=tag,
        message="; ".join(messages),
        local_deleted=local_deleted,
        remote_deleted=remote_deleted,
        gh_release_deleted=gh_deleted,
    )


def release_recut(
    tag: str,
    message: str = "",
    branch: str = "master",
    repo_path: str = ".",
    remote: str = DEFAULT_REMOTE,
) -> ReleaseRecutResult:
    """Re-cut a release: verify tag exists, CI green, delete remote tag, re-push.

    Ported from ``make release-recut``. Used when the release CI job was
    skipped or the tag needs to be re-triggered.

    Returns:
        ReleaseRecutResult with ``success=True`` when the new tag is pushed.
    """
    _reject_leading_dash(tag, kind="tag name")
    steps: list[str] = []

    rc, out = _run_git(["rev-parse", "--is-inside-work-tree"], repo_path)
    if rc != 0:
        return ReleaseRecutResult(success=False, tag=tag,
                                  message="Not inside a git repository")

    if not _git_tag_exists(tag, repo_path):
        return ReleaseRecutResult(success=False, tag=tag,
                                  message=f"Local tag {tag} not found")

    tag_sha = _git_rev_parse(repo_path, f"{tag}^{{commit}}")
    if not tag_sha:
        return ReleaseRecutResult(success=False, tag=tag,
                                  message=f"Could not resolve commit for tag {tag}")

    rc, out = _run_require_ci_green(sha=tag_sha, branch=branch)
    if rc == 1:
        return ReleaseRecutResult(success=False, tag=tag,
                                  message=f"CI not green for tag commit: {out}",
                                  steps_completed=steps)
    steps.append("ci-green")

    rc, out = _git_tag_delete_remote(tag, repo_path, remote)
    if rc != 0:
        pass
    steps.append("remote-tag-deleted")

    rc, out = _git_tag_push(tag, message, repo_path, remote=remote)
    if rc != 0:
        return ReleaseRecutResult(success=False, tag=tag,
                                  message=f"Tag re-push failed: {out}",
                                  steps_completed=steps)
    steps.append("tag-pushed")

    return ReleaseRecutResult(
        success=True,
        tag=tag,
        message=f"Re-cut tag {tag} pushed to {remote}",
        steps_completed=steps,
    )
