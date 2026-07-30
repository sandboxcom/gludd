"""RepoEvidence collection — read-only repository state snapshot.

Implements spec §5.1 ``RepoEvidence`` by wrapping
``general_ludd.git_automation.GitAutomation``. Every operation is read-only:
``is_repo``, ``current_branch``, ``get_current_commit``, ``workflow_state``,
``list_worktrees``, ``list_branches``. No fetches, no index writes, no ref
mutations, no config changes — safe to invoke at any point without altering
repository state.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import GitStateResult, WorktreeInfo

SCHEMA_VERSION = "1.0"

# Branch names returned by GitAutomation.current_branch() that signal a
# detached HEAD (or a failure to resolve the branch). The evidence layer
# normalizes all of these to ``branch=None`` per spec §5.1.
_DETACHED_SENTINELS = frozenset({"unknown", "DETACHED", ""})

# Well-known repository instruction files (spec §4.3 helper_discover priority 1).
# Scanned for the ``policies`` field; each present file yields a Policy entry
# with a sha256 digest of its contents.
_POLICY_FILES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    "DEVELOPMENT.md",
    "SECURITY.md",
    "RELEASING.md",
)

# Git state markers that indicate an in-progress operation. Keys are the
# operation kind; values are paths under ``.git`` whose presence means the
# operation is mid-flight. Read-only ``os.path.exists`` probes — no mutation.
_OPERATION_MARKERS: dict[str, tuple[str, ...]] = {
    "rebase": ("rebase-merge", "rebase-apply"),
    "merge": ("MERGE_HEAD",),
    "cherry_pick": ("CHERRY_PICK_HEAD",),
    "revert": ("REVERT_HEAD",),
    "bisect": ("BISECT_LOG",),
    "am": ("am",),
}


class NotARepoError(RuntimeError):
    """Raised when ``collect_repo_evidence`` is pointed at a non-repo path."""


@dataclass(frozen=True)
class DirtyPath:
    """One entry from ``git status --porcelain`` (spec §5.1 ``dirty_paths``)."""

    path: str
    index_state: str
    worktree_state: str
    untracked: bool = False


@dataclass(frozen=True)
class Upstream:
    """Upstream tracking summary for a branch (spec §5.1 ``upstreams``).

    ``ahead``/``behind`` are exact commit counts when the remote HEAD equals
    the local HEAD (0/0, in sync) or ``-1`` when the two have diverged. Exact
    diverged counts require a fetch, which this read-only collector never
    performs; downstream mutating roles refresh evidence first.
    """

    local_ref: str
    remote_ref: str
    ahead: int
    behind: int


@dataclass(frozen=True)
class WorktreeEvidence:
    """One git worktree row (spec §5.1 ``worktrees``)."""

    path: str
    branch: str
    head_sha: str
    dirty: bool


@dataclass(frozen=True)
class Operation:
    """An in-progress git operation detected from ``.git`` markers."""

    kind: str
    state: str
    recovery_command_id: str


@dataclass(frozen=True)
class Policy:
    """A repository instruction file referenced by the evidence (spec §5.1)."""

    source: str
    rule_id: str
    text_digest: str


@dataclass
class RepoEvidence:
    """Normalized read-only repository evidence record (spec §5.1).

    JSON-serializable via ``dataclasses.asdict``. Unknown required fields fail
    at construction (TypeError); additive optional fields preserve backward
    compatibility because every collection-typed field has a default factory.
    """

    schema_version: str
    repo_root: str
    head_sha: str
    branch: str | None
    upstreams: list[Upstream] = field(default_factory=list)
    worktrees: list[WorktreeEvidence] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    dirty_paths: list[DirtyPath] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)
    evidence_time: str = ""


def collect_repo_evidence(
    repo_path: str = ".",
    *,
    git: GitAutomation | None = None,
) -> RepoEvidence:
    """Collect a read-only ``RepoEvidence`` snapshot for ``repo_path``.

    Wraps ``GitAutomation`` without mutating the repository. The optional
    ``git`` parameter supports dependency injection for tests; production
    callers omit it and a fresh ``GitAutomation(repo_path)`` is constructed.

    Raises ``NotARepoError`` if ``repo_path`` is not a git repository.
    """
    git = git if git is not None else GitAutomation(repo_path)
    if not git.is_repo():
        raise NotARepoError(f"not a git repository: {repo_path!r}")

    head_sha = git.get_current_commit()
    raw_branch = git.current_branch()
    branch: str | None = None if raw_branch in _DETACHED_SENTINELS else raw_branch

    state = git.workflow_state()

    return RepoEvidence(
        schema_version=SCHEMA_VERSION,
        repo_root=os.path.abspath(repo_path),
        head_sha=head_sha,
        branch=branch,
        upstreams=_collect_upstreams(state, head_sha),
        worktrees=_collect_worktrees(git, repo_path, state.status),
        operations=_detect_operations(repo_path),
        dirty_paths=_parse_dirty_paths(state.status),
        policies=_scan_policies(repo_path),
        evidence_time=datetime.now(UTC).isoformat(),
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_dirty_paths(status_lines: list[str]) -> list[DirtyPath]:
    """Parse ``git status --porcelain`` lines into ``DirtyPath`` records.

    Porcelain v1 lines are ``XY <path>``: X is the index state, Y is the
    worktree state, and a leading ``??`` marks an untracked path. Renames
    report ``old -> new``; the destination path is kept.
    """
    out: list[DirtyPath] = []
    for line in status_lines:
        if len(line) < 4:
            continue
        index_state = line[0]
        worktree_state = line[1]
        entry = line[3:].strip()
        if not entry:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1].strip()
        if len(entry) >= 2 and entry[0] == '"' and entry[-1] == '"':
            entry = entry[1:-1]
        out.append(
            DirtyPath(
                path=entry,
                index_state=index_state,
                worktree_state=worktree_state,
                untracked=line.startswith("??"),
            )
        )
    return out


def _collect_upstreams(state: GitStateResult, head_sha: str) -> list[Upstream]:
    """Build the upstreams list from ``workflow_state``'s remote probe.

    ``workflow_state`` already ran ``ls-remote`` (no fetch), so
    ``state.remote_head`` is the current remote tip without any object
    download. When the remote HEAD is empty there is no upstream to report.
    """
    if not state.remote_head:
        return []
    in_sync = head_sha == state.remote_head
    return [
        Upstream(
            local_ref=state.branch,
            remote_ref=state.remote_ref,
            ahead=0 if in_sync else -1,
            behind=0 if in_sync else -1,
        )
    ]


def _collect_worktrees(
    git: GitAutomation,
    repo_path: str,
    status_lines: list[str],
) -> list[WorktreeEvidence]:
    """Map ``GitAutomation.list_worktrees`` to evidence rows.

    ``dirty`` is True for the main worktree when any porcelain status line is
    present; sibling worktrees are probed individually in a later phase.
    """
    main_dirty = bool(status_lines)
    rows: list[WorktreeEvidence] = []
    for wt in git.list_worktrees(repo_path):
        rows.append(
            WorktreeEvidence(
                path=wt.path,
                branch=wt.branch.removeprefix("refs/heads/"),
                head_sha=wt.commit,
                dirty=main_dirty if wt.is_main else _worktree_is_dirty(git, wt),
            )
        )
    return rows


def _worktree_is_dirty(git: GitAutomation, wt: WorktreeInfo) -> bool:
    """Probe a sibling worktree for dirty state. Best-effort: any error → False."""
    try:
        proc = git._run_git("status", "--porcelain", _cwd=wt.path, check=False)
    except Exception:
        return False
    return bool(proc.stdout.strip())


def _detect_operations(repo_path: str) -> list[Operation]:
    """Probe ``.git`` for in-progress-operation markers. Read-only (no mutation).

    Handles both a ``.git`` directory (main checkout) and a ``.git`` file
    (worktree) by resolving the common git dir when possible.
    """
    git_dir = _resolve_git_dir(repo_path)
    if git_dir is None:
        return []
    found: list[Operation] = []
    for kind, markers in _OPERATION_MARKERS.items():
        for marker in markers:
            if (git_dir / marker).exists():
                found.append(
                    Operation(
                        kind=kind,
                        state="in_progress",
                        recovery_command_id=f"git.{kind}.abort",
                    )
                )
    return found


def _resolve_git_dir(repo_path: str) -> Path | None:
    """Resolve the absolute path to the repository's ``.git`` directory.

    Returns ``None`` when the path is not a git dir or file (caller skips
    operation detection). Worktrees write a ``.git`` *file* pointing at the
    common dir; we follow ``git rev-parse --git-common-dir`` via the GitAutomation
    interface only when available, otherwise we fall back to ``<repo>/.git``.
    """
    candidate = Path(repo_path) / ".git"
    if candidate.is_dir():
        return candidate
    return None


def _scan_policies(repo_path: str) -> list[Policy]:
    """Scan for well-known repository instruction files (spec §4.3 priority 1).

    For each present file, record its name and a sha256 digest of its bytes.
    Absent files contribute no entry.
    """
    policies: list[Policy] = []
    for name in _POLICY_FILES:
        path = Path(repo_path) / name
        if not path.is_file():
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        policies.append(Policy(source=name, rule_id=name, text_digest=digest))
    return policies
