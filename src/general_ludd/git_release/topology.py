"""Read-only repository topology assessment (spec GRC-001 §4.1, §5.1, GRC-P1).

``assess_repo()`` is the canonical entry point for the ``repo_assess`` role. It
gathers HEAD, branch, upstream divergence, linked worktrees, in-flight git
operations, dirty paths, and policy sources into a typed ``RepoEvidence``
record.

The function performs NO mutations and is safe to invoke at any point — even
mid-rebase or mid-merge. It is the read-only foundation for GRC-SEC-001
(plan-before-mutation) and GRC-SEC-004 (fail-closed): a planner consumes the
returned evidence to decide whether a mutating plan can proceed.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import UTC, datetime

from .contracts import RepoEvidence, _DirtyPath, _Operation, _Policy, _Upstream, _Worktree

__all__ = ["assess_repo"]


def _run_git(repo: str, *args: str) -> str:
    """Run a git command, returning stripped stdout. Empty string on failure.

    Never raises: a missing remote, unborn branch, or detached HEAD must not
    abort evidence collection — they simply produce empty fields, and the
    planner fails closed on the missing data.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _git_dir(repo: str) -> str | None:
    """Resolve the ``.git`` directory for ``repo``.

    Handles standard repos (``.git`` is a directory) and linked worktrees
    (``.git`` is a file pointing at the common git dir).
    """
    dot_git = os.path.join(repo, ".git")
    if os.path.isdir(dot_git):
        return dot_git
    if os.path.isfile(dot_git):
        try:
            with open(dot_git, encoding="utf-8") as fh:
                line = fh.read().strip()
        except OSError:
            return None
        if line.startswith("gitdir:"):
            target = line.split(":", 1)[1].strip()
            if os.path.isdir(target):
                return target
    return None


def _repository_evidence_time(repo: str, git_dir: str, head_sha: str) -> str:
    """Return an RFC3339 timestamp derived from immutable repository state.

    Repository assessment is a deterministic snapshot operation: collecting an
    unchanged repository twice must produce equal evidence.  A wall-clock
    collection timestamp violates that contract, so committed repositories use
    the HEAD committer timestamp.  An unborn repository falls back to the stable
    ``HEAD`` metadata timestamp (and the Unix epoch if that metadata disappears
    during collection).
    """
    if head_sha != "0" * 40:
        commit_time = _run_git(repo, "show", "-s", "--format=%cI", head_sha)
        try:
            datetime.fromisoformat(commit_time)
        except ValueError:
            pass
        else:
            return commit_time

    try:
        timestamp = os.stat(os.path.join(git_dir, "HEAD")).st_mtime
    except OSError:
        timestamp = 0.0
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _detect_operations(git_dir: str) -> list[_Operation]:
    """Detect in-progress git operations via filesystem markers.

    Returns one ``_Operation`` per detected in-flight operation. ``state`` is
    always ``"in_progress"`` — we never infer completion from the filesystem
    alone, matching GRC-SEC-004 (ambiguous state → blocked). Each entry carries
    a stable ``recovery_command_id`` so a planner can cite the abort primitive
    without constructing a shell string (GRC-SEC-002).
    """
    ops: list[_Operation] = []
    if os.path.isdir(os.path.join(git_dir, "rebase-merge")) or os.path.isdir(os.path.join(git_dir, "rebase-apply")):
        ops.append(
            _Operation(
                kind="rebase",
                state="in_progress",
                recovery_command_id="git.rebase.abort",
            )
        )
    if os.path.exists(os.path.join(git_dir, "MERGE_HEAD")):
        ops.append(
            _Operation(
                kind="merge",
                state="in_progress",
                recovery_command_id="git.merge.abort",
            )
        )
    if os.path.exists(os.path.join(git_dir, "CHERRY_PICK_HEAD")):
        ops.append(
            _Operation(
                kind="cherry-pick",
                state="in_progress",
                recovery_command_id="git.cherry-pick.abort",
            )
        )
    if os.path.exists(os.path.join(git_dir, "BISECT_LOG")):
        ops.append(
            _Operation(
                kind="bisect",
                state="in_progress",
                recovery_command_id="git.bisect.reset",
            )
        )
    return ops


_XY_STATE_MAP: dict[str, str | None] = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "type-changed",
    "U": "unmerged",
    "N": None,
    ".": None,
}


def _xy_to_state(ch: str) -> str | None:
    return _XY_STATE_MAP.get(ch)


def _parse_porcelain(lines: list[str]) -> list[_DirtyPath]:
    """Parse ``git status --porcelain=v2`` output into ``_DirtyPath`` records.

    Handles ordinary changes (``1`` entries), renames/copies (``2`` entries),
    and untracked files (``?`` entries). Path-only best-effort: rename
    ``origPath`` is dropped because porcelain v2 without ``-z`` embeds a tab
    inside the path token, which we do not split here.
    """
    dirty: list[_DirtyPath] = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line.startswith("? "):
            dirty.append(_DirtyPath(path=line[2:].strip(), untracked=True))
            continue
        parts = line.split(" ")
        if len(parts) < 2:
            continue
        tag = parts[0]
        if tag in ("1", "2"):
            xy = parts[1]
            index_state = _xy_to_state(xy[0]) if len(xy) > 0 else None
            worktree_state = _xy_to_state(xy[1]) if len(xy) > 1 else None
            path = parts[-1]
            dirty.append(
                _DirtyPath(
                    path=path,
                    index_state=index_state,
                    worktree_state=worktree_state,
                    untracked=False,
                )
            )
    return dirty


def _collect_policies(repo: str) -> list[_Policy]:
    """Collect read-only policy records from ``.gitattributes``.

    Each non-comment line becomes a ``_Policy`` with the pattern as
    ``rule_id`` and a ``sha256:`` digest of the rule text. Branch-protection
    and remote policy discovery require forge API access (a provider adapter)
    and are intentionally out of scope for GRC-P1.
    """
    policies: list[_Policy] = []
    attrs = os.path.join(repo, ".gitattributes")
    if os.path.isfile(attrs):
        try:
            with open(attrs, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    pattern = stripped.split()[0]
                    digest = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
                    policies.append(
                        _Policy(
                            source=".gitattributes",
                            rule_id=pattern,
                            text_digest=f"sha256:{digest}",
                        )
                    )
        except OSError:
            pass
    return policies


def _parse_worktrees(porcelain_lines: list[str], dirty_paths: list[_DirtyPath]) -> list[_Worktree]:
    """Parse ``git worktree list --porcelain`` output.

    Marks the current worktree dirty when ``dirty_paths`` is non-empty. The
    per-worktree dirty bit for OTHER worktrees is best-effort-false: a true
    value requires running ``git status`` inside each worktree, which GRC-P1
    defers to a future topology refresh role.
    """
    worktrees: list[_Worktree] = []
    current: dict[str, str] = {}
    for line in porcelain_lines:
        if not line:
            if current:
                worktrees.append(_build_worktree(current, dirty_paths))
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head_sha"] = value
        elif key == "branch":
            current["branch"] = value.replace("refs/heads/", "")
    if current:
        worktrees.append(_build_worktree(current, dirty_paths))
    return worktrees


def _build_worktree(d: dict[str, str], dirty_paths: list[_DirtyPath]) -> _Worktree:
    return _Worktree(
        path=d.get("path", ""),
        branch=d.get("branch") or None,
        head_sha=d.get("head_sha", ""),
        dirty=bool(dirty_paths),
    )


def assess_repo(path: str) -> RepoEvidence:
    """Collect read-only :class:`RepoEvidence` for the repository at ``path``.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        NotADirectoryError: ``path`` exists but is not a directory.
        RuntimeError: ``path`` exists but is not a git repository.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"repository path does not exist: {path}")
    if not os.path.isdir(path):
        raise NotADirectoryError(f"repository path is not a directory: {path}")
    git_dir = _git_dir(path)
    if git_dir is None:
        raise RuntimeError(f"not a git repository: {path}")

    abs_path = os.path.abspath(path)

    # Unborn branch: sentinel zeros so the SHA pattern still validates and a
    # downstream planner can fail closed on the empty branch.
    head_sha = _run_git(path, "rev-parse", "HEAD") or "0" * 40

    branch: str | None = _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in ("", "HEAD"):
        branch = None

    upstreams = _collect_upstreams(path, branch)
    dirty_paths = _parse_porcelain(_run_git(path, "status", "--porcelain=v2").splitlines())
    worktrees = _parse_worktrees(
        _run_git(path, "worktree", "list", "--porcelain").splitlines(),
        dirty_paths,
    )
    operations = _detect_operations(git_dir)
    policies = _collect_policies(abs_path)
    evidence_time = _repository_evidence_time(path, git_dir, head_sha)

    return RepoEvidence(
        repo_root=abs_path,
        head_sha=head_sha,
        branch=branch,
        upstreams=upstreams,
        worktrees=worktrees,
        operations=operations,
        dirty_paths=dirty_paths,
        policies=policies,
        evidence_time=evidence_time,
    )


def _collect_upstreams(repo: str, branch: str | None) -> list[_Upstream]:
    """Resolve upstream ahead/behind counts for the current branch.

    Returns an empty list when there is no upstream (detached HEAD, unborn
    branch, or no remote tracking configured). The counts come from
    ``git rev-list --left-right --count <upstream>...HEAD``: left side is
    ``behind`` (commits on upstream not on HEAD), right side is ``ahead``.
    """
    if branch is None:
        return []
    upstream_ref = _run_git(
        repo,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
    )
    if not upstream_ref:
        return []
    counts = _run_git(repo, "rev-list", "--left-right", "--count", f"{upstream_ref}...HEAD")
    if not counts:
        return []
    try:
        behind_str, ahead_str = counts.split()
        return [
            _Upstream(
                local_ref=branch,
                remote_ref=upstream_ref,
                ahead=int(ahead_str),
                behind=int(behind_str),
            )
        ]
    except (ValueError, TypeError):
        return []
