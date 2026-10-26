"""Crash-safe promotion of accepted managed self-improvement artifacts."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import re
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

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


@runtime_checkable
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


@runtime_checkable
class _WorktreeRunner(_RootRunner, Protocol):
    """Make-only interface for checks inside a candidate worktree."""

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        """Run one validated Make command."""



@dataclass(frozen=True, slots=True)
class SelfImproveNativeOutcome:
    """Keep the native result, operation verdict and every actual child distinct."""

    result: MakeResult
    confirmed: bool
    steps: tuple[MakeResult, ...]

    @property
    def returncode(self) -> int:
        """Preserve the final native child status without inventing application."""
        return self.result.returncode

    @property
    def elapsed_seconds(self) -> float:
        """Return sequential measured child wall time, not a performance claim."""
        return sum(step.elapsed_seconds for step in self.steps)


def _native_revision(runner: _RootRunner, reference: str) -> tuple[MakeResult, str | None]:
    """Read the existing native owner and its single exact summary boundary."""
    if reference != "HEAD" and _COMMIT_RE.fullmatch(reference) is None and (
        ".." in reference or re.fullmatch(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*", reference) is None
    ):
        raise RuntimeError("revision requires HEAD, a full SHA or an exact owned branch ref")
    result = runner.run("git-show-commit", {"C": reference}, timeout=30, read_only=True)
    lines = result.stdout.splitlines()
    headers = [index for index, line in enumerate(lines) if line == f"--- {reference} summary ---"]
    revision = None
    if result.returncode == 0 and len(headers) == 1 and headers[0] + 1 < len(lines):
        candidate = lines[headers[0] + 1]
        if _COMMIT_RE.fullmatch(candidate) and (
            _COMMIT_RE.fullmatch(reference) is None or candidate == reference
        ):
            revision = candidate
    return result, revision


def self_improve_revision(runner: _RootRunner, reference: str) -> str:
    """Require an actual canonical revision from the existing Make read owner."""
    result, revision = _native_revision(runner, reference)
    _require_green("immutable worktree revision", result)
    if revision is None:
        detail = (result.stdout + "\n" + result.stderr)[-4096:]
        raise RuntimeError(f"immutable worktree revision lacks one exact native summary/full SHA: {detail}")
    return revision


def _native_receipt(result: MakeResult, *, terminal_json: bool = False) -> dict[str, object] | None:
    """Parse native JSON without discarding legitimate preceding archive progress."""
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines or (len(lines) != 1 and not terminal_json):
        return None
    try:
        value = json.loads(lines[-1])
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _owned_context(branch: str, worktree: Path, head: str, purpose: str) -> None:
    """Reject missing or guessed owner context before any native invocation."""
    if (
        not branch or branch != branch.strip() or ".." in branch
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) is None
        or not worktree.is_absolute() or ".." in worktree.parts
        or worktree.resolve(strict=True) != worktree
        or _COMMIT_RE.fullmatch(head) is None
        or purpose not in {"merged", "self-improve-evaluation"}
    ):
        raise RuntimeError("owned canonical WORKTREE, branch, immutable HEAD and cleanup purpose are required")


def _receipt_matches(
    result: MakeResult, branch: str, worktree: Path, head: str, status: str,
    validate_only: bool | None,
) -> bool:
    """Require exact native identity, ownership and the actual requested outcome."""
    receipt = _native_receipt(result, terminal_json=status == "removed")
    return (
        receipt is not None and receipt.get("status") == status
        and receipt.get("branch") == branch and receipt.get("worktree") == str(worktree)
        and receipt.get("head") == head
        and type(receipt.get("native_ownership_verified")) is int
        and receipt["native_ownership_verified"] == 1
        and (
            validate_only is None
            or (
                type(receipt.get("validate_only")) is bool
                and receipt["validate_only"] is validate_only
            )
        )
    )


def _native_outcome(
    steps: list[MakeResult], *, confirmed: bool, target: str, reason: str,
) -> SelfImproveNativeOutcome:
    """Retain chronological native statuses and teach the measured next action."""
    final = steps[-1]
    history = "\n".join(
        f"SELF_IMPROVE_NATIVE_CHILD command={shlex.join(step.argv)} native_status={step.returncode}\n"
        f"{step.stdout[-4096:]}\n{step.stderr[-4096:]}"
        for step in steps[:-1]
    )
    guidance = (
        f"AGENT_NEXT target={target} outcome={'confirmed' if confirmed else 'deferred'} "
        f"native_status={final.returncode} gate_evidence=false release_ready=false; {reason}; "
        "retain native steps, candidate refs and evidence; required exact-SHA release proofs remain pending"
    )
    result = replace(final, stderr="\n".join(filter(None, (final.stderr, guidance, history))))
    return SelfImproveNativeOutcome(result, confirmed, tuple(steps))


def retire_owned_self_improve_worktree(
    runner: _RootRunner, *, branch: str, worktree: Path, head: str,
    purpose: str, apply: bool,
) -> SelfImproveNativeOutcome:
    """Plan, prove matching release, re-plan and retire only this known owner."""
    _owned_context(branch, worktree, head, purpose)
    variables = {
        "BRANCH": branch, "WORKTREE": str(worktree),
        "AGENT_CLEANUP_PURPOSE": purpose, "AGENT_CLEANUP_EXPECTED_HEAD": head,
        "AGENT_CLEANUP_VALIDATE_ONLY": "1",
    }
    steps = [runner.run("agent-cleanup", variables, timeout=180)]
    plan = steps[-1]
    release_marker = (
        f"wt-prune-safe: retirement refused: {purpose} owner is active; exact release required: "
        f"branch={branch!r} worktree={str(worktree)!r} head={head}"
    )
    release_required = plan.returncode == 2 and plan.stdout.splitlines() == [release_marker]
    valid_plan = _receipt_matches(plan, branch, worktree, head, "planned", True)
    if not apply or not (valid_plan or release_required):
        return _native_outcome(
            steps, confirmed=False, target="agent-cleanup",
            reason="plan is not removal; repair the actual native refusal or obtain explicit owned cleanup eligibility",
        )
    steps.append(runner.run("workstream-unregister", {
        "BRANCH": branch, "WORKTREE": str(worktree), "WORKSTREAM_EXPECTED_HEAD": head,
    }, timeout=180))
    if not _receipt_matches(steps[-1], branch, worktree, head, "released", None):
        return _native_outcome(
            steps, confirmed=False, target="agent-cleanup",
            reason="matching native ownership release is unverified; preserve this candidate and reconcile its owner",
        )
    steps.append(runner.run("agent-cleanup", variables, timeout=180))
    if not _receipt_matches(steps[-1], branch, worktree, head, "planned", True):
        return _native_outcome(
            steps, confirmed=False, target="agent-cleanup",
            reason="fresh native eligibility after release failed; repair that exact input before apply",
        )
    steps.append(runner.run(
        "agent-cleanup", {**variables, "AGENT_CLEANUP_VALIDATE_ONLY": "0"}, timeout=180,
    ))
    removed = _receipt_matches(steps[-1], branch, worktree, head, "removed", False)
    removed = removed and not worktree.exists() and not worktree.is_symlink()
    if removed:
        native, preserved_head = _native_revision(runner, f"refs/heads/{branch}")
        steps.append(native)
        removed = preserved_head == head
    return _native_outcome(
        steps, confirmed=removed, target="agent-cleanup",
        reason=(
            "actual native removal and preserved immutable branch verified; this is cleanup only"
            if removed else "actual native removal or preserved branch is unverified; reconcile before retrying"
        ),
    )


@contextlib.contextmanager
def owned_self_improve_evaluation(
    runner: _RootRunner, *, branch: str, worktree: Path, head: str,
) -> Iterator[None]:
    """Honor this invocation's cleanup intent without masking its original error."""
    try:
        yield
    except BaseException:
        with contextlib.suppress(Exception):
            retire_owned_self_improve_worktree(
                runner, branch=branch, worktree=worktree, head=head,
                purpose="self-improve-evaluation", apply=True,
            )
        raise
    else:
        outcome = retire_owned_self_improve_worktree(
            runner, branch=branch, worktree=worktree, head=head,
            purpose="self-improve-evaluation", apply=True,
        )
        if not outcome.confirmed:
            raise RuntimeError(
                "baseline context worktree cleanup is unverified: "
                + (outcome.result.stderr or outcome.result.stdout)
            )


def _matches_worktree(
    result: MakeResult, *, branch: str, head: str | None = None,
    worktree: Path | None = None,
) -> bool:
    """Bind clean native worktree state to the expected branch, path and commit."""
    state = _native_receipt(result)
    return (
        state is not None and state.get("branch") == branch and state.get("is_clean") is True
        and isinstance(state.get("head"), str)
        and _COMMIT_RE.fullmatch(str(state["head"])) is not None
        and (head is None or state["head"] == head)
        and (worktree is None or state.get("path") == str(worktree))
    )


def integrate_owned_self_improve_worktree(
    runner: _RootRunner, candidate_runner: _RootRunner, *,
    branch: str, worktree: Path, head: str,
) -> SelfImproveNativeOutcome:
    """Apply only an accepted exact candidate through the existing pinned owner."""
    _owned_context(branch, worktree, head, "merged")
    steps = [candidate_runner.run("worktree-state", timeout=30, read_only=True)]
    if not _matches_worktree(steps[-1], branch=branch, head=head, worktree=worktree):
        return _native_outcome(
            steps, confirmed=False, target="development-fast-forward",
            reason="the exact clean candidate branch, path or HEAD changed; preserve it and reconcile its owner",
        )
    steps.append(candidate_runner.run("_gate-fresh-check", timeout=120, read_only=True))
    if steps[-1].returncode != 0:
        return _native_outcome(
            steps, confirmed=False, target="development-fast-forward",
            reason="candidate native acceptance is missing; obtain its actual canonical gate before integration",
        )
    steps.append(runner.run("worktree-state", timeout=30, read_only=True))
    receiver = _native_receipt(steps[-1])
    if not _matches_worktree(steps[-1], branch="development") or receiver is None:
        return _native_outcome(
            steps, confirmed=False, target="development-fast-forward",
            reason="a real clean development receiver with a pinned HEAD is unavailable; never checkout another worker",
        )
    before = str(receiver["head"])
    variables = {
        "SOURCE": head, "EXPECTED_BEFORE_SHA": before,
        "EXPECTED_AFTER_SHA": head, "APPLY": "0",
    }
    steps.append(runner.run("development-fast-forward", variables, timeout=180))
    expected_plan = (
        f"DEVELOPMENT-FAST-FORWARD-VALIDATED source={head} before={before} after={head} apply=0"
    )
    if steps[-1].returncode != 0 or expected_plan not in steps[-1].stdout.splitlines():
        return _native_outcome(
            steps, confirmed=False, target="development-fast-forward",
            reason=(f"pinned fast-forward is unproved; preserve SOURCE={head} "
                    "and review development-merge-forward MODE=content APPLY=0"),
        )
    steps.append(runner.run(
        "development-fast-forward", {**variables, "APPLY": "1"}, timeout=180,
    ))
    applied = steps[-1]
    expected_applied = (
        f"DEVELOPMENT-FAST-FORWARDED source={head} before={before} after={head} mode=ff-only"
    )
    confirmed = applied.returncode == 0 and expected_applied in applied.stdout.splitlines()
    if confirmed:
        native, development_head = _native_revision(runner, "refs/heads/development")
        steps.append(native)
        confirmed = development_head == head
    return _native_outcome(
        steps, confirmed=confirmed, target="development-fast-forward",
        reason=(
            "exact candidate endpoint verified; unchanged ancestry is not a new application; "
            "marker/proofs still precede cleanup"
            if confirmed else "applied endpoint is unverified; preserve candidate and native failure before any cleanup"
        ),
    )


def verify_self_improve_recovery(
    runner: _RootRunner, head: str,
) -> SelfImproveNativeOutcome:
    """Verify a real accepted unchanged receiver; a plan alone grants no proof."""
    steps = [runner.run("worktree-state", timeout=30, read_only=True)]
    if not _matches_worktree(steps[-1], branch="development", head=head):
        return _native_outcome(
            steps, confirmed=False, target="self-improve-promotion-marker",
            reason="the actual clean development receiver does not match the native marker; preserve the claim",
        )
    variables = {
        "SOURCE": head, "EXPECTED_BEFORE_SHA": head, "EXPECTED_AFTER_SHA": head, "APPLY": "0",
    }
    steps.append(runner.run("development-fast-forward", variables, timeout=180))
    expected = f"DEVELOPMENT-FAST-FORWARD-VALIDATED source={head} before={head} after={head} apply=0"
    valid = steps[-1].returncode == 0 and expected in steps[-1].stdout.splitlines()
    if valid:
        steps.append(runner.run("_gate-fresh-check", timeout=120, read_only=True))
        valid = steps[-1].returncode == 0
    if valid:
        native, development_head = _native_revision(runner, "refs/heads/development")
        steps.append(native)
        valid = development_head == head
    return _native_outcome(
        steps, confirmed=valid, target="self-improve-promotion-marker",
        reason=(
            "native marker endpoint and actual receiver acceptance verified; no new integration was applied"
            if valid else "canonical receiver or native acceptance is missing; preserve the claim and obtain that proof"
        ),
    )


@runtime_checkable
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
            _require_confirmed(
                "durable development acceptance",
                await asyncio.to_thread(verify_self_improve_recovery, self._root_runner, existing_commit),
            )
            return _receipt(acquired, marker_verified=True)

        claim = acquired
        if existing_commit is not None:
            await self._cleanup_stale_branch(claim.stale_worktree_branch)
            _require_confirmed(
                "recovered development acceptance",
                await asyncio.to_thread(verify_self_improve_recovery, self._root_runner, existing_commit),
            )
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
        cleanup_verified = False
        try:
            worktree = await self._create_worktree(branch, inputs.plan.reference.baseline_sha)
            head = await asyncio.to_thread(self._prepare_candidate, worktree, inputs)
            integrated = await asyncio.to_thread(
                integrate_owned_self_improve_worktree,
                self._root_runner, self._make_runner_factory(worktree),
                branch=branch, worktree=worktree, head=head,
            )
            _require_confirmed("pinned development integration", integrated)
            development_commit = await self._find_verified_marker(inputs)
            if development_commit != head:
                raise RuntimeError(
                    "development integration lacks the exact candidate's immutable artifact marker; "
                    f"preserve BRANCH={branch} WORKTREE={worktree} EXPECTED_HEAD={head}"
                )
            await self._cleanup_branch(branch, worktree=worktree, head=head)
            cleanup_verified = True
            completed = await self._repository.complete(
                claim,
                development_commit=development_commit,
                marker=_artifact_marker(inputs.result.artifact_digest),
                now=self._clock(),
            )
            return _receipt(completed, marker_verified=True)
        except BaseException as exc:
            # A preserved candidate also retains its durable branch binding and lease.
            if cleanup_verified:
                with contextlib.suppress(Exception):
                    await self._repository.abandon(
                        claim, error=f"{type(exc).__name__}: {exc}", now=self._clock(),
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
        if result.returncode in {2, 3} and result.stdout.splitlines() == ["PROMOTION_ABSENT"]:
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
    ) -> str:
        """Reapply, check, commit and pin the exact accepted proposal."""
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
        return self_improve_revision(runner, "HEAD")

    async def _cleanup_stale_branch(self, branch: str | None) -> None:
        """Preserve a stale branch until its exact persisted owner context is known."""
        if branch is not None:
            raise RuntimeError(
                f"stale promotion cleanup deferred for BRANCH={branch}: "
                "canonical WORKTREE, immutable EXPECTED_HEAD and matching native ownership release "
                "are unavailable; preserve candidate refs and evidence, reconcile the original owner"
            )

    async def _cleanup_branch(self, branch: str, *, worktree: Path, head: str) -> None:
        """Require actual native removal of this invocation's exact accepted owner."""
        outcome = await asyncio.to_thread(
            retire_owned_self_improve_worktree, self._root_runner,
            branch=branch, worktree=worktree, head=head, purpose="merged", apply=True,
        )
        _require_confirmed("promotion worktree cleanup", outcome)


def _require_confirmed(label: str, outcome: SelfImproveNativeOutcome) -> None:
    """Reject a zero plan or unverified endpoint without hiding native failure."""
    _require_green(label, outcome.result)
    if not outcome.confirmed:
        detail = (outcome.result.stderr or outcome.result.stdout)[-4096:]
        raise RuntimeError(f"{label} is unverified: {detail}")


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
