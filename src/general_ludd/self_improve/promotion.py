"""Crash-safe promotion of accepted managed self-improvement artifacts."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.promotion_repository import (
    CompletedManagedPromotion,
    ManagedPromotionIdentity,
    ManagedPromotionLease,
    ManagedSelfImprovePromotionRepository,
)
from general_ludd.self_improve.codex_comparison import compare_with_codex
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    apply_proposal,
)
from general_ludd.self_improve.result_artifact import (
    ManagedSelfImproveResultArtifact,
)
from general_ludd.self_improve.runtime import MakeResult, MakeRunner

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_LEASE_DURATION = timedelta(hours=2)
_MARKER_TARGET = "self-improve-promotion-marker"


class _RootRunner(Protocol):
    """Make-only interface for canonical repository operations."""

    def run(
        self,
        target: str,
        variables: dict[str, str] | None = None,
        *,
        timeout: int = 120,
        read_only: bool = False,
    ) -> MakeResult:
        """Run one bounded Make target."""


class _WorktreeRunner(_RootRunner, Protocol):
    """Make-only interface for checks inside a candidate worktree."""

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        """Run one validated Make command."""


class _PromotionRepository(Protocol):
    """Durable saga boundary used by the promotion coordinator."""

    async def acquire(
        self,
        identity: ManagedPromotionIdentity,
        *,
        owner: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> ManagedPromotionLease | CompletedManagedPromotion:
        """Acquire a fence or return a completed durable receipt."""

    async def bind_worktree(
        self,
        claim: ManagedPromotionLease,
        branch: str,
        *,
        now: datetime,
    ) -> None:
        """Persist the intended worktree branch under the current fence."""

    async def complete(
        self,
        claim: ManagedPromotionLease,
        *,
        development_commit: str,
        marker: str,
        now: datetime,
    ) -> CompletedManagedPromotion:
        """Persist a verified development receipt under the current fence."""

    async def abandon(
        self,
        claim: ManagedPromotionLease,
        *,
        error: str,
        now: datetime,
    ) -> None:
        """Release a current failed claim after cleanup."""


@dataclass(frozen=True, slots=True)
class ManagedPromotionInputs:
    """Fully validated, mutually bound plan/result/outer identities."""

    plan: ApprovedSelfImprovePlan
    result: ManagedSelfImproveResultArtifact
    identity: ManagedPromotionIdentity


@dataclass(frozen=True, slots=True)
class ManagedPromotionReceipt:
    """Git-verified proof required before a managed todo becomes COMPLETE."""

    artifact_digest: str
    plan_identity_digest: str
    attempt_identity_digest: str
    todo_id: str
    project_id: str
    repo_root: Path
    return_id: str
    development_commit: str
    marker: str
    fencing_token: int
    marker_verified: bool

    def __post_init__(self) -> None:
        """Normalize the repository path and reject synthetic receipts."""
        object.__setattr__(self, "repo_root", self.repo_root.resolve(strict=False))
        if not self.marker_verified:
            raise ValueError("managed promotion receipt lacks Git marker verification")
        if _COMMIT_RE.fullmatch(self.development_commit) is None:
            raise ValueError("development commit must be a 40-character commit SHA")
        if self.marker != _artifact_marker(self.artifact_digest):
            raise ValueError("managed promotion receipt marker does not match artifact")
        ManagedPromotionIdentity(
            artifact_digest=self.artifact_digest,
            plan_identity_digest=self.plan_identity_digest,
            attempt_identity_digest=self.attempt_identity_digest,
            todo_id=self.todo_id,
            project_id=self.project_id,
            return_id=self.return_id,
            repo_root=str(self.repo_root),
        )
        if isinstance(self.fencing_token, bool) or self.fencing_token <= 0:
            raise ValueError("fencing token must be a positive integer")

    def verify_for(
        self,
        *,
        todo_id: str,
        project_id: str,
        repo_root: str | Path | None,
        return_id: str,
    ) -> None:
        """Fail closed unless this receipt grants the exact todo authority."""
        if todo_id != self.todo_id:
            raise ValueError("managed promotion receipt todo identity mismatch")
        if project_id != self.project_id:
            raise ValueError("managed promotion receipt project identity mismatch")
        if return_id != self.return_id:
            raise ValueError("managed promotion receipt return identity mismatch")
        if repo_root is None:
            raise ValueError("managed promotion receipt requires a repository root")
        resolved = Path(repo_root).resolve(strict=False)
        if resolved != self.repo_root:
            raise ValueError("managed promotion receipt repository identity mismatch")


def _artifact_marker(artifact_digest: str) -> str:
    """Return the exact marker searched only on the development history."""
    return f"Gludd-Self-Improve-Artifact={artifact_digest}"


def validate_managed_promotion_inputs(
    *,
    plan_artifact: str,
    result_artifact: str,
    todo_id: str,
    project_id: str,
    repo_root: str | Path,
    repository_binding_digest: str = "",
    return_id: str,
) -> ManagedPromotionInputs:
    """Load and cross-check every authority, scope, and comparison identity."""
    plan = ApprovedSelfImprovePlan.from_json(plan_artifact)
    result = ManagedSelfImproveResultArtifact.from_json(result_artifact)
    plan.verify_approval()
    resolved_root = Path(repo_root).resolve(strict=True)
    if todo_id != plan.todo_id:
        raise ValueError("todo identity does not match the approved plan")
    if project_id != plan.project_id:
        raise ValueError("project identity does not match the approved plan")
    if plan.repository_binding_digest:
        try:
            plan = plan.bind_execution_repository(
                resolved_root,
                repository_binding_digest=repository_binding_digest,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(
                "repo binding does not match the approved plan"
            ) from exc
    else:
        plan_root = plan.repo_root
        if plan_root is None or resolved_root != plan_root.resolve(strict=True):
            raise ValueError("repo identity does not match the approved plan")
    if not hmac.compare_digest(result.plan_identity_digest, plan.identity_digest):
        raise ValueError("result plan identity does not match the approved plan")
    if not hmac.compare_digest(
        result.attempt_identity_digest,
        plan.attempt_identity_digest,
    ):
        raise ValueError("result attempt identity does not match the approved plan")
    if not result.accepted or not result.comparison.accepted:
        raise ValueError("only an accepted managed result can be promoted")
    if result.proposal.baseline_sha != plan.reference.baseline_sha:
        raise ValueError("proposal baseline identity does not match the approved plan")
    if result.proposal.task_id != plan.task.task_id:
        raise ValueError("proposal task identity does not match the approved plan")
    proposal_paths = frozenset(edit.path for edit in result.proposal.edits)
    if proposal_paths != plan.reference.changed_files:
        raise ValueError("proposal scope does not exactly match the approved reference")
    recomputed = compare_with_codex(result.proposal, result.evidence, plan.reference)
    if recomputed != result.comparison:
        raise ValueError("recomputed comparison does not match the result artifact")
    identity = ManagedPromotionIdentity(
        artifact_digest=result.artifact_digest,
        plan_identity_digest=plan.identity_digest,
        attempt_identity_digest=plan.attempt_identity_digest,
        todo_id=todo_id,
        project_id=project_id,
        return_id=return_id,
        repo_root=str(resolved_root),
    )
    return ManagedPromotionInputs(plan=plan, result=result, identity=identity)


class ManagedSelfImprovePromotionCoordinator:
    """Coordinate one fenced database/Git promotion saga."""

    def __init__(
        self,
        repository: _PromotionRepository,
        *,
        root_runner: _RootRunner,
        make_runner_factory: Callable[[Path], _WorktreeRunner] = MakeRunner,
        owner: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind durable state, Make-only execution, and an observable owner."""
        self._repository = repository
        self._root_runner = root_runner
        self._make_runner_factory = make_runner_factory
        self._owner = owner
        self._clock = clock or (lambda: datetime.now(UTC))

    async def promote(
        self,
        *,
        plan_artifact: str,
        result_artifact: str,
        todo_id: str,
        project_id: str,
        repo_root: str | Path,
        repository_binding_digest: str = "",
        return_id: str,
    ) -> ManagedPromotionReceipt:
        """Promote exactly once, recovering a post-merge crash from its marker."""
        inputs = validate_managed_promotion_inputs(
            plan_artifact=plan_artifact,
            result_artifact=result_artifact,
            todo_id=todo_id,
            project_id=project_id,
            repo_root=repo_root,
            repository_binding_digest=repository_binding_digest,
            return_id=return_id,
        )
        acquired = await self._repository.acquire(
            inputs.identity,
            owner=self._owner,
            now=self._clock(),
            lease_duration=_LEASE_DURATION,
        )
        existing_commit = await self._find_verified_marker(inputs)
        if isinstance(acquired, CompletedManagedPromotion):
            if existing_commit is None:
                raise RuntimeError(
                    "durable promotion receipt is not reachable from development"
                )
            if existing_commit != acquired.development_commit:
                raise RuntimeError("durable promotion receipt commit identity drifted")
            return _receipt(acquired, marker_verified=True)

        claim = acquired
        if existing_commit is not None:
            await self._cleanup_stale_branch(claim.stale_worktree_branch)
            completed = await self._repository.complete(
                claim,
                development_commit=existing_commit,
                marker=_artifact_marker(inputs.result.artifact_digest),
                now=self._clock(),
            )
            return _receipt(completed, marker_verified=True)

        branch = (
            f"self-improve-promote-{inputs.result.artifact_digest[:12]}-"
            f"{claim.fencing_token}"
        )
        await self._cleanup_stale_branch(claim.stale_worktree_branch)
        await self._repository.bind_worktree(claim, branch, now=self._clock())
        cleanup_needed = True
        try:
            worktree = await self._create_worktree(
                branch,
                inputs.plan.reference.baseline_sha,
            )
            await asyncio.to_thread(
                self._prepare_candidate,
                worktree,
                inputs,
            )
            merged = await asyncio.to_thread(
                self._root_runner.run,
                "agent-merge-dev",
                {"BRANCH": branch},
                timeout=300,
            )
            _require_green("development merge", merged)
            await self._cleanup_branch(branch)
            cleanup_needed = False
            development_commit = await self._find_verified_marker(inputs)
            if development_commit is None:
                raise RuntimeError(
                    "development merge completed without the immutable artifact marker"
                )
            completed = await self._repository.complete(
                claim,
                development_commit=development_commit,
                marker=_artifact_marker(inputs.result.artifact_digest),
                now=self._clock(),
            )
            return _receipt(completed, marker_verified=True)
        except BaseException as exc:
            if cleanup_needed:
                with contextlib.suppress(Exception):
                    await self._cleanup_branch(branch)
            with contextlib.suppress(Exception):
                await self._repository.abandon(
                    claim,
                    error=f"{type(exc).__name__}: {exc}",
                    now=self._clock(),
                )
            raise

    async def _find_verified_marker(
        self,
        inputs: ManagedPromotionInputs,
    ) -> str | None:
        """Find a marker only when Git verifies all bound digests on development."""
        result = await asyncio.to_thread(
            self._root_runner.run,
            _MARKER_TARGET,
            {
                "SELF_IMPROVE_PROMOTION_ARTIFACT_DIGEST": (
                    inputs.result.artifact_digest
                ),
                "SELF_IMPROVE_PROMOTION_PLAN_DIGEST": inputs.plan.identity_digest,
                "SELF_IMPROVE_PROMOTION_ATTEMPT_DIGEST": (
                    inputs.plan.attempt_identity_digest
                ),
                "SELF_IMPROVE_PROMOTION_VALIDATE_ONLY": "0",
            },
            timeout=60,
            read_only=True,
        )
        if result.returncode == 3:
            return None
        _require_green("development marker verification", result)
        prefix = "PROMOTION_COMMIT="
        commits = [
            line.removeprefix(prefix)
            for line in result.stdout.splitlines()
            if line.startswith(prefix)
        ]
        if len(commits) != 1 or _COMMIT_RE.fullmatch(commits[0]) is None:
            raise RuntimeError("marker target did not return one canonical commit SHA")
        return commits[0]

    async def _create_worktree(self, branch: str, baseline_sha: str) -> Path:
        """Create one namespaced worktree at the immutable approved baseline."""
        result = await asyncio.to_thread(
            self._root_runner.run,
            "agent-worktree-base",
            {"BRANCH": branch, "BASE": baseline_sha},
            timeout=180,
        )
        _require_green("promotion worktree creation", result)
        prefix = "WORKTREE_PATH="
        markers = [
            line.removeprefix(prefix)
            for line in result.stdout.splitlines()
            if line.startswith(prefix)
        ]
        if len(markers) != 1:
            raise RuntimeError("worktree creation did not return one path")
        return Path(markers[0]).resolve(strict=True)

    def _prepare_candidate(
        self,
        worktree: Path,
        inputs: ManagedPromotionInputs,
    ) -> None:
        """Reapply, check, and atomically commit the exact accepted proposal."""
        apply_proposal(worktree, inputs.result.proposal)
        runner = self._make_runner_factory(worktree)
        commands = tuple(
            dict.fromkeys(
                (
                    *inputs.plan.task.canonical_make_commands,
                    *inputs.result.proposal.make_commands,
                )
            )
        )
        for command in commands:
            _require_green(
                f"canonical check {command!r}",
                runner.run_command(command),
            )
        _require_green(
            "test collection",
            runner.run_command("make test-count", timeout=600),
        )
        paths = " ".join(edit.path for edit in inputs.result.proposal.edits)
        _require_green("promotion staging", runner.run("git-add", {"FILES": paths}))
        message = (
            f"self-improve: promote {inputs.result.artifact_digest[:12]} "
            f"{_artifact_marker(inputs.result.artifact_digest)} "
            f"Gludd-Self-Improve-Plan={inputs.plan.identity_digest} "
            "Gludd-Self-Improve-Attempt="
            f"{inputs.plan.attempt_identity_digest}"
        )
        _require_green(
            "promotion commit",
            runner.run("repo-commit", {"MSG": message}, timeout=300),
        )
        status = runner.run("repo-status", read_only=True)
        _require_green("promotion worktree status", status)
        if status.stdout.strip():
            raise RuntimeError("promotion worktree is dirty after its atomic commit")

    async def _cleanup_stale_branch(self, branch: str | None) -> None:
        """Clean a worktree left by an expired prior fence."""
        if branch is not None:
            await self._cleanup_branch(branch)

    async def _cleanup_branch(self, branch: str) -> None:
        """Require the owned worktree and branch cleanup target to succeed."""
        result = await asyncio.to_thread(
            self._root_runner.run,
            "agent-cleanup",
            {"BRANCH": branch},
            timeout=180,
        )
        _require_green("promotion worktree cleanup", result)


def _require_green(label: str, result: MakeResult) -> None:
    """Raise one bounded error for a failed Make operation."""
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        detail = detail.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        raise RuntimeError(f"{label} failed: {detail or 'no diagnostics'}")


def _receipt(
    completed: CompletedManagedPromotion,
    *,
    marker_verified: bool,
) -> ManagedPromotionReceipt:
    """Convert a DB record only after an independent development marker check."""
    identity = completed.identity
    return ManagedPromotionReceipt(
        artifact_digest=identity.artifact_digest,
        plan_identity_digest=identity.plan_identity_digest,
        attempt_identity_digest=identity.attempt_identity_digest,
        todo_id=identity.todo_id,
        project_id=identity.project_id,
        repo_root=Path(identity.repo_root),
        return_id=identity.return_id,
        development_commit=completed.development_commit,
        marker=completed.marker,
        fencing_token=completed.fencing_token,
        marker_verified=marker_verified,
    )


def build_managed_self_improve_promotion_coordinator(
    session: AsyncSession,
    repo_root: Path,
    owner: str,
) -> ManagedSelfImprovePromotionCoordinator:
    """Build the installed database/Make promotion composition root."""
    root = repo_root.resolve(strict=True)
    return ManagedSelfImprovePromotionCoordinator(
        ManagedSelfImprovePromotionRepository(session),
        root_runner=MakeRunner(root),
        make_runner_factory=MakeRunner,
        owner=owner,
    )
