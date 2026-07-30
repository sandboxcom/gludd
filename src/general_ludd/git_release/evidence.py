"""Machine-verifiable repository evidence collection (spec GRC-001 §4).

The :class:`RepoEvidence` shape is intentionally read-only and minimal. It
captures the smallest set of facts a planner needs to distinguish observations
from inferences, and to gate mutation plans on real state.

The collector avoids shell invocation when possible (it reads ``.git/HEAD`` and
``.git`` shape directly). ``git`` subprocess is only used to resolve the current
HEAD SHA — every other field is filesystem-derived so the collector is robust
to lock contention and dirty state.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

__all__ = ["RepoEvidence", "collect_repo_evidence"]


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class RepoEvidence:
    """Read-only snapshot of the smallest set of repository facts.

    Fields:
        path: Absolute filesystem path of the inspected repository.
        head_sha: 40-character lowercase hex SHA of HEAD, or empty string if
            the SHA could not be resolved (e.g. unborn branch).
        branch: Current branch name (``"main"``, ``"feature/x"``). Empty when
            detached.
        is_dirty: ``True`` when the working tree has tracked modifications or
            untracked entries. ``False`` otherwise.
        is_detached: ``True`` when HEAD is a bare commit, not a branch ref.
    """

    path: str
    head_sha: str
    branch: str
    is_dirty: bool
    is_detached: bool

    @classmethod
    def empty(cls, path: str) -> RepoEvidence:
        """Construct an evidence record for an empty / unborn repository."""
        return cls(
            path=path,
            head_sha="",
            branch="",
            is_dirty=False,
            is_detached=False,
        )


def _git_dir(repo_path: str) -> str | None:
    """Return the ``.git`` directory path for ``repo_path`` or ``None``.

    Handles both standard repos (``.git`` is a directory) and worktrees
    (``.git`` is a file pointing at the common git dir).
    """
    dot_git = os.path.join(repo_path, ".git")
    if os.path.isdir(dot_git):
        return dot_git
    if os.path.isfile(dot_git):
        try:
            with open(dot_git, encoding="utf-8") as fh:
                line = fh.read().strip()
        except OSError:
            return None
        # gitdir: /path/to/common/.git/worktrees/<name>
        if line.startswith("gitdir:"):
            target = line.split(":", 1)[1].strip()
            if os.path.isdir(target):
                return target
    return None


def _read_head(git_dir: str) -> tuple[str, str, bool]:
    """Parse ``HEAD`` into ``(branch, ref_path, is_detached)``.

    Returns ``("", "", False)`` for an unborn branch.
    """
    head_path = os.path.join(git_dir, "HEAD")
    try:
        with open(head_path, encoding="utf-8") as fh:
            content = fh.read().strip()
    except OSError:
        return "", "", False
    if content.startswith("ref:"):
        # ref: refs/heads/<branch>
        ref_path = content[4:].strip()
        branch = ref_path.split("refs/heads/", 1)[-1] if "refs/heads/" in ref_path else ""
        return branch, ref_path, False
    # detached: content is a SHA
    return "", "", True


def _resolve_head_sha(repo_path: str) -> str:
    """Resolve the 40-char SHA of HEAD via ``git rev-parse``.

    Returns an empty string when ``git`` is unavailable or the branch is unborn.
    Never raises.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = result.stdout.strip()
    return sha if _SHA_RE.match(sha) else ""


def _has_untracked(git_dir: str) -> bool:
    """Best-effort untracked-file check via the filesystem.

    We avoid ``git status`` to remain robust to concurrent locks. A non-empty
    ``MERGE_HEAD``, ``CHERRY_PICK_HEAD``, or ``REBASE_HEAD`` marker counts as
    dirty. This is intentionally conservative — it may underreport.
    """
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REBASE_HEAD", "BISECT_LOG"):
        if os.path.exists(os.path.join(git_dir, marker)):
            return True
    return False


def _is_dirty(repo_path: str, git_dir: str) -> bool:
    """Detect dirty state without invoking ``git status``.

    Uses ``git diff --quiet`` (fast, lock-light) and falls back to filesystem
    markers. Returns ``True`` on any sign of uncommitted work.
    """
    if _has_untracked(git_dir):
        return True
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "diff", "--quiet"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Lock contention or missing git — fall back to filesystem markers only.
        return False
    # exit 0 = clean, exit 1 = dirty, anything else = unknown (treat as clean).
    return result.returncode == 1


def collect_repo_evidence(path: str) -> RepoEvidence:
    """Collect read-only :class:`RepoEvidence` for the repository at ``path``.

    Raises:
        FileNotFoundError: when ``path`` does not exist or is not a directory.
        NotADirectoryError: when ``path`` exists but is not a directory.
        RuntimeError: when ``path`` exists but is not a git repository.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"repository path does not exist: {path}")
    if not os.path.isdir(path):
        raise NotADirectoryError(f"repository path is not a directory: {path}")
    git_dir = _git_dir(path)
    if git_dir is None:
        raise RuntimeError(f"not a git repository: {path}")
    abs_path = os.path.abspath(path)
    branch, _ref_path, is_detached = _read_head(git_dir)
    head_sha = _resolve_head_sha(path)
    is_dirty = _is_dirty(path, git_dir)
    return RepoEvidence(
        path=abs_path,
        head_sha=head_sha,
        branch=branch,
        is_dirty=is_dirty,
        is_detached=is_detached,
    )
