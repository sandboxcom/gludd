#!/usr/bin/env python3
"""Compose shadow receipt sessions and bounded runner-facing diagnostics."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

_MODULE_ALIASES = {
    "ci_shadow_receipt_runtime": "scripts.ci_shadow_receipt_runtime",
    "scripts.ci_shadow_receipt_runtime": "ci_shadow_receipt_runtime",
}
if __name__ in _MODULE_ALIASES:
    sys.modules.setdefault(_MODULE_ALIASES[__name__], sys.modules[__name__])

if TYPE_CHECKING:
    from scripts.ci_batch_receipts import (
        MAX_FAILURE_NODES,
        MAX_FAILURE_RECEIPTS_PER_GENERATION,
        FailureReceiptRequest,
        ShadowBatchReceiptWriter,
        ShadowFailureReceiptWriter,
    )
    from scripts.ci_batch_replay_audit import (
        ReplayAdmissionRequest,
        ReplayAdmissionResult,
        ReplayAuditRequest,
        ShadowReplayAuditor,
    )
    from scripts.ci_gate_progress import (
        MAX_PROGRESS_BATCHES,
        MAX_PROGRESS_OUTPUT_BYTES,
        MAX_TIMING_SAMPLES,
        ProgressBatch,
        ProgressExecution,
        ReceiptStatus,
        ShadowGateProgress,
    )
    from scripts.ci_receipt_auth import AUTH_ALGORITHM
else:
    from ci_batch_receipts import (
        MAX_FAILURE_NODES,
        MAX_FAILURE_RECEIPTS_PER_GENERATION,
        FailureReceiptRequest,
        ShadowBatchReceiptWriter,
        ShadowFailureReceiptWriter,
    )
    from ci_batch_replay_audit import (
        ReplayAdmissionRequest,
        ReplayAdmissionResult,
        ReplayAuditRequest,
        ShadowReplayAuditor,
    )
    from ci_gate_progress import (
        MAX_PROGRESS_BATCHES,
        MAX_PROGRESS_OUTPUT_BYTES,
        MAX_TIMING_SAMPLES,
        ProgressBatch,
        ProgressExecution,
        ReceiptStatus,
        ShadowGateProgress,
    )
    from ci_receipt_auth import AUTH_ALGORITHM

BatchIdentityFactory = Callable[[str, int, list[str]], dict[str, object]]
SessionT = TypeVar("SessionT")


class PlanShards(Protocol):
    """Callable boundary for the runner's canonical batch planner."""

    def __call__(
        self,
        shards: list[str],
        *,
        max_files_per_batch: int,
    ) -> list[tuple[str, list[list[str]]]]: ...


class RepositoryIdentity(Protocol):
    """Callable boundary for a fresh repository observation."""

    def __call__(self, *, expected_sha: str | None) -> dict[str, object]: ...


class DiskHeadroom(Protocol):
    """Callable boundary for the runner's fail-closed disk observation."""

    def __call__(self, path: Path, *, context: str) -> bool: ...


@dataclass(frozen=True)
class ShadowReceiptBindings:
    """Late-bound runner seams retained across the module split."""

    root: Path
    scripts: Path
    runner_path: Path
    coverage_config: Path
    plan_shards: PlanShards
    repository_state_id: Callable[..., str]
    repository_identity: RepositoryIdentity
    interpreter_identity: Callable[[], dict[str, str]]
    execution_policy: Callable[[list[str]], dict[str, object]]
    build_runtime_identity: Callable[..., dict[str, Any]]
    resource_root: Callable[[], Path]
    disk_headroom: DiskHeadroom
    canonical_json_sha256: Callable[[object], str]
    batch_writer: Callable[..., ShadowBatchReceiptWriter]
    failure_writer: Callable[..., ShadowFailureReceiptWriter]
    replay_auditor: Callable[..., ShadowReplayAuditor]
    gate_progress: Callable[..., ShadowGateProgress]
    load_auth_context: Callable[..., Any]
    parse_revocations: Callable[[str], frozenset[str]]


@dataclass(frozen=True)
class BatchReceiptSession:
    """Receipt dependencies for one bounded serial runner invocation."""

    writer: ShadowBatchReceiptWriter
    run_id: str
    expected_identity: BatchIdentityFactory
    observed_identity: BatchIdentityFactory
    failure_writer: ShadowFailureReceiptWriter | None = None
    auditor: ShadowReplayAuditor | None = None
    progress: ShadowGateProgress | None = None
    admission_enabled: bool = False


def admit_exact_batch(
    session: BatchReceiptSession,
    *,
    shard: str,
    batch_index: int,
    files: list[str],
    coverage_destination: Path,
) -> tuple[ReplayAdmissionResult | None, str]:
    """Attempt one fail-closed exact-action admission through the session auditor."""
    try:
        action_identity = session.expected_identity(shard, batch_index, files)
        observed_action_identity = session.observed_identity(shard, batch_index, files)
        admission = (
            session.auditor.admit(
                ReplayAdmissionRequest(
                    action_identity=action_identity,
                    observed_action_identity=observed_action_identity,
                    coverage_destination=coverage_destination,
                )
            )
            if session.auditor is not None
            else None
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return None, f"identity-{type(exc).__name__}"
    return admission, "auditor-unavailable" if admission is None else admission.reason


def print_batch_execution_summary(
    *,
    planned: int,
    executed: int,
    resumed: int,
    time_saved_seconds: float,
    max_files_per_batch: int,
    batch_workers: int,
) -> dict[str, object]:
    """Emit and return bounded executed-versus-resumed reconciliation evidence."""
    not_started = max(0, planned - executed - resumed)
    reconciled = executed >= 0 and resumed >= 0 and executed + resumed <= planned
    summary: dict[str, object] = {
        "planned": planned,
        "executed": executed,
        "resumed": resumed,
        "not_started": not_started,
        "time_saved_seconds": round(time_saved_seconds, 3),
        "max_files_per_batch": max_files_per_batch,
        "workers": batch_workers,
        "reconciled": reconciled,
    }
    print(
        "GATE-BATCH-EXECUTION "
        f"planned={planned} executed={executed} resumed={resumed} "
        f"not_started={not_started} "
        f"time_saved_seconds={time_saved_seconds:.3f} "
        f"max_files_per_batch={max_files_per_batch} workers={batch_workers} "
        f"reconciliation={'pass' if reconciled else 'fail'}",
        flush=True,
    )
    return summary


def gate_owner_is_alive(environment: Mapping[str, str] | None = None) -> bool:
    """Fail closed when the full-gate make owner has disappeared."""
    owner = (os.environ if environment is None else environment).get(
        "GLUDD_GATE_OWNER_PID"
    )
    if owner is None:
        return True
    try:
        owner_pid = int(owner)
    except ValueError:
        return False
    if owner_pid <= 1:
        return False
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def record_phase_result(
    phase_results: dict[str, int | str],
    phase: str,
    result: int | str,
) -> None:
    """Publish one durable, machine-readable phase result."""
    phase_results[phase] = result
    print(f"SERIAL-SHARD-PHASE phase={phase} result={result}", flush=True)


def build_serial_runner_parser(
    *,
    description: str | None,
    default_shards: tuple[str, ...] | list[str],
    max_files_per_batch: int,
    heartbeat_seconds: float,
    no_progress_seconds: float,
) -> argparse.ArgumentParser:
    """Build the bounded serial runner CLI without owning execution behavior."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--shards",
        default=" ".join(default_shards),
        help="space or comma separated shard names",
    )
    parser.add_argument("--pytest-args", default="", help="extra pytest arguments")
    parser.add_argument(
        "--max-files-per-batch",
        type=int,
        default=max_files_per_batch,
        help="maximum collected test files in one owned pytest process",
    )
    parser.add_argument(
        "--batch-workers",
        type=int,
        choices=(1, 2),
        default=1,
        help="bounded pytest-xdist loadfile workers per batch (maximum: 2)",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=heartbeat_seconds,
        help="visible owned-process heartbeat interval",
    )
    parser.add_argument(
        "--no-progress-seconds",
        type=float,
        default=no_progress_seconds,
        help="quiet-output deadline before owned TERM-to-KILL cleanup",
    )
    parser.add_argument(
        "--skip-isolated",
        action="store_true",
        help="skip the separately scheduled process-heavy test",
    )
    parser.add_argument(
        "--skip-aggregate",
        action="store_true",
        help="defer the 85/75 aggregate coverage gate to a downstream job",
    )
    parser.add_argument(
        "--collect-all-failures",
        action="store_true",
        help=(
            "diagnostic only: continue after ordinary pytest failures; "
            "safety, cleanup, cancellation, and coverage failures remain terminal"
        ),
    )
    parser.add_argument(
        "--coverage-output",
        type=Path,
        help="combine this invocation's batch coverage into one data file",
    )
    parser.add_argument(
        "--attestation-output",
        type=Path,
        help="also publish the terminal exact-SHA attestation at this path",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="print the bounded canonical plan without executing tests or writing evidence",
    )
    parser.add_argument(
        "--require-release-policy",
        action="store_true",
        help="reject noncanonical pytest policy before release-attestation work",
    )
    parser.add_argument(
        "--allow-dirty-worktree",
        action="store_true",
        help=(
            "permit a stable dirty worktree for commit-preflight testing; "
            "the resulting attestation remains ineligible for release"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "skip batches that already passed in a previous run for the same "
            "candidate SHA and reuse their coverage fragments"
        ),
    )
    parser.add_argument(
        "--resume-file",
        type=Path,
        default=None,
        help="path to the resume state file (default: <resource-root>/ci-shards/resume.json)",
    )
    receipt_admission = parser.add_mutually_exclusive_group()
    receipt_admission.add_argument(
        "--exact-sha-resume",
        dest="exact_sha_resume",
        action="store_true",
        default=False,
        help="admit authenticated pass receipts for the same clean exact SHA",
    )
    receipt_admission.add_argument(
        "--no-exact-sha-resume",
        dest="exact_sha_resume",
        action="store_false",
        help="force a cold run while continuing bounded shadow receipt writes",
    )
    shadow_toggles = (
        (
            "--no-shadow-batch-receipts",
            "execute every batch without publishing any shadow receipt",
        ),
        (
            "--no-shadow-failure-receipts",
            "keep pass receipts but disable sanitized non-reusable failure receipts",
        ),
        (
            "--no-shadow-receipt-authentication",
            "write legacy unsigned shadow receipts and disable future eligibility",
        ),
        (
            "--no-shadow-replay-audit",
            "keep shadow writes but disable prior-receipt eligibility reports",
        ),
        (
            "--no-shadow-gate-progress",
            "keep shadow writes and replay auditing but disable progress/ETA reports",
        ),
    )
    for flag, help_text in shadow_toggles:
        parser.add_argument(flag, action="store_true", help=help_text)
    parser.add_argument(
        "--watchdog-owned-gate",
        action="store_true",
        help="mark the gate runner and pytest children for legacy watchdog exclusion",
    )
    return parser


def receipt_base_identity(
    bindings: ShadowReceiptBindings,
    *,
    repository_identity: Mapping[str, object],
    repository_state_identifier: str,
    pytest_args: list[str],
    max_files_per_batch: int,
    heartbeat_seconds: float,
    no_progress_seconds: float,
    include_uv_probe: bool,
    batch_workers: int = 1,
) -> dict[str, object]:
    """Build exact run-scoped identity shared by all batch actions."""
    policy = bindings.execution_policy(pytest_args)
    runtime = bindings.build_runtime_identity(
        root=bindings.root,
        scripts=bindings.scripts,
        runner=bindings.runner_path,
        coverage_config=bindings.coverage_config,
        interpreter=bindings.interpreter_identity(),
        include_uv_probe=include_uv_probe,
        environment=os.environ,
    )
    runner = runtime["runner"]
    if not isinstance(runner, dict):
        raise TypeError("runtime runner identity must be an object")
    runner.update(
        {
            "pytest_args": list(pytest_args),
            "heartbeat_seconds": heartbeat_seconds,
            "no_progress_seconds": no_progress_seconds,
            "worker_count": batch_workers,
            "distribution": "loadfile" if batch_workers == 2 else "none",
            "cleanup_policy_version": 1,
        }
    )
    return {
        "schema_version": 1,
        "source": {
            "candidate_sha": repository_identity.get("head_sha"),
            "expected_sha": repository_identity.get("expected_sha"),
            "branch": repository_identity.get("branch"),
            "clean": repository_identity.get("clean") is True,
            "exact_sha": repository_identity.get("exact_sha") is True,
            "queries_ok": repository_identity.get("queries_ok") is True,
            "repository_state_id": repository_state_identifier,
        },
        "plan": {
            "max_files_per_batch": max_files_per_batch,
            "execution_policy_sha256": bindings.canonical_json_sha256(policy),
        },
        "runner": runner,
        "toolchain": runtime["toolchain"],
        "plugins": runtime["plugins"],
        "coverage": runtime["coverage"],
        "platform": runtime["platform"],
        "environment": runtime["environment"],
        "external_inputs": [],
    }


def batch_receipt_action_identity(
    bindings: ShadowReceiptBindings,
    base_identity: Mapping[str, object],
    shard_plan_sha256: Mapping[str, str],
    complete_plan_sha256: str,
    shard: str,
    batch_index: int,
    files: list[str],
) -> dict[str, object]:
    """Bind one batch to its full source, plan, and runtime action identity."""
    identity = copy.deepcopy(dict(base_identity))
    source = identity["source"]
    plan = identity["plan"]
    if not isinstance(source, dict) or not isinstance(plan, dict):
        raise TypeError("receipt identity source and plan must be objects")
    source.update(
        {
            "test_files": list(files),
            "test_files_sha256": bindings.canonical_json_sha256(files),
        }
    )
    plan.update(
        {
            "shard": shard,
            "batch_index": batch_index,
            "shard_plan_sha256": shard_plan_sha256[shard],
            "complete_plan_sha256": complete_plan_sha256,
            "collection_manifest_kind": "canonical-test-file-plan-v1",
            "collection_manifest_sha256": bindings.canonical_json_sha256(files),
        }
    )
    return identity


def create_shadow_receipt_session(
    bindings: ShadowReceiptBindings,
    *,
    repository_identity: Mapping[str, object],
    shards: list[str],
    pytest_args: list[str],
    max_files_per_batch: int,
    heartbeat_seconds: float,
    no_progress_seconds: float,
    replay_audit_enabled: bool = True,
    progress_enabled: bool = True,
    failure_receipts_enabled: bool = True,
    receipt_authentication_enabled: bool = True,
    receipt_admission_enabled: bool = False,
    batch_workers: int = 1,
) -> BatchReceiptSession:
    """Create writers, auditing, and optional exact-receipt admission."""
    plans = bindings.plan_shards(
        shards,
        max_files_per_batch=max_files_per_batch,
    )
    canonical_plan = [
        {"shard": shard, "batches": batches} for shard, batches in plans
    ]
    shard_plan_sha256 = {
        shard: bindings.canonical_json_sha256(batches) for shard, batches in plans
    }
    complete_plan_sha256 = bindings.canonical_json_sha256(canonical_plan)
    repository_state_identifier = bindings.repository_state_id(
        bindings.root,
        source="index",
    )
    expected_base = receipt_base_identity(
        bindings,
        repository_identity=repository_identity,
        repository_state_identifier=repository_state_identifier,
        pytest_args=pytest_args,
        max_files_per_batch=max_files_per_batch,
        heartbeat_seconds=heartbeat_seconds,
        no_progress_seconds=no_progress_seconds,
        include_uv_probe=True,
        batch_workers=batch_workers,
    )
    candidate_sha = str(repository_identity["expected_sha"])

    def expected(shard: str, batch_index: int, files: list[str]) -> dict[str, object]:
        return batch_receipt_action_identity(
            bindings,
            expected_base,
            shard_plan_sha256,
            complete_plan_sha256,
            shard,
            batch_index,
            files,
        )

    def observed(shard: str, batch_index: int, files: list[str]) -> dict[str, object]:
        current_repository = bindings.repository_identity(expected_sha=candidate_sha)
        observed_base = receipt_base_identity(
            bindings,
            repository_identity=current_repository,
            repository_state_identifier=repository_state_identifier,
            pytest_args=pytest_args,
            max_files_per_batch=max_files_per_batch,
            heartbeat_seconds=heartbeat_seconds,
            no_progress_seconds=no_progress_seconds,
            include_uv_probe=True,
            batch_workers=batch_workers,
        )
        return batch_receipt_action_identity(
            bindings,
            observed_base,
            shard_plan_sha256,
            complete_plan_sha256,
            shard,
            batch_index,
            files,
        )

    run_id = bindings.canonical_json_sha256(
        {
            "candidate_sha": candidate_sha,
            "pid": os.getpid(),
            "root": str(bindings.root),
            "started_ns": time.time_ns(),
        }
    )[:32]
    cache_root = bindings.resource_root() / "batch-receipts"
    auth_context = None
    if receipt_authentication_enabled:
        key_path = Path(
            os.environ.get(
                "GLUDD_GATE_KEY_PATH",
                str(Path.home() / ".config" / "gludd" / "gate-attestation.key"),
            )
        )
        auth_context = bindings.load_auth_context(
            key_path,
            issued_at=int(time.time()),
            revoked_signers=bindings.parse_revocations(
                os.environ.get("GLUDD_RECEIPT_REVOKED_SIGNERS", "")
            ),
        )
    signer = None if auth_context is None else auth_context.signer
    trust_policy = None if auth_context is None else auth_context.trust_policy
    auditor = (
        bindings.replay_auditor(
            cache_root,
            candidate_sha=candidate_sha,
            trust_policy=trust_policy,
        )
        if replay_audit_enabled or receipt_admission_enabled
        else None
    )
    progress_batches = [
        ProgressBatch(
            shard=shard,
            batch_index=batch_index,
            test_files_sha256=bindings.canonical_json_sha256(files),
            collection_manifest_sha256=bindings.canonical_json_sha256(files),
        )
        for shard, batches in plans
        for batch_index, files in enumerate(batches, start=1)
    ]
    return BatchReceiptSession(
        writer=bindings.batch_writer(cache_root, signer=signer),
        run_id=run_id,
        expected_identity=expected,
        observed_identity=observed,
        failure_writer=(
            bindings.failure_writer(cache_root, signer=signer)
            if failure_receipts_enabled
            else None
        ),
        auditor=auditor,
        progress=(
            bindings.gate_progress(
                trust_policy=trust_policy,
                candidate_sha=candidate_sha,
                batches=progress_batches,
                completed_receipts=(
                    auditor.snapshot_receipts() if auditor is not None else ()
                ),
            )
            if progress_enabled
            else None
        ),
        admission_enabled=receipt_admission_enabled,
    )


def report_shadow_replay_eligibility(
    session: BatchReceiptSession,
    *,
    shard: str,
    batch_index: int,
    action_identity: Mapping[str, object],
    observed_action_identity: Mapping[str, object],
    coverage_path: Path,
    outcome_manifest: Mapping[str, object] | None,
    returncode: int,
    cleanup_returncode: int,
) -> ReceiptStatus:
    """Emit bounded audit-only evidence without changing execution control flow."""
    if session.auditor is None:
        return "ineligible"
    try:
        result = session.auditor.audit(
            ReplayAuditRequest(
                action_identity=action_identity,
                observed_action_identity=observed_action_identity,
                coverage_path=coverage_path,
                outcome_manifest=outcome_manifest,
                returncode=returncode,
                cleanup_returncode=cleanup_returncode,
            )
        )
        if result.skip_authorized:
            status = "refused"
            reason = "skip-authorization-forbidden"
            receipt_status: ReceiptStatus = "ineligible"
        elif result.eligible:
            status = "candidate"
            reason = result.reason
            receipt_status = "eligible"
        elif result.reason == "no-prior-candidate":
            status = "miss"
            reason = result.reason
            receipt_status = "missing"
        else:
            status = "refused"
            reason = result.reason
            receipt_status = "ineligible"
        print(
            f"BATCH-REPLAY-SHADOW status={status} shard={shard} "
            f"batch={batch_index} reason={reason} "
            f"action={result.action_digest or 'none'} "
            f"authentication={result.authentication} skips=0",
            flush=True,
        )
        return receipt_status
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            f"BATCH-REPLAY-SHADOW status=refused shard={shard} "
            f"batch={batch_index} reason=auditor-{type(exc).__name__} skips=0",
            flush=True,
        )
        return "ineligible"


def publish_shadow_failure_receipt(
    bindings: ShadowReceiptBindings,
    session: BatchReceiptSession,
    *,
    shard: str,
    batch_index: int,
    files: list[str],
    failure_node_metadata: Mapping[str, object] | None,
    elapsed_seconds: float,
    returncode: int,
    cleanup_returncode: int,
) -> bool:
    """Publish sanitized diagnostics without making the failure reusable."""
    if session.failure_writer is None:
        return False
    if cleanup_returncode != 0:
        print(
            f"BATCH-FAILURE-RECEIPT-SHADOW status=refused shard={shard} "
            f"batch={batch_index} reason=cleanup-incomplete skips=0",
            flush=True,
        )
        return False
    try:
        action_identity = session.expected_identity(shard, batch_index, files)
        observed_action_identity = session.observed_identity(
            shard,
            batch_index,
            files,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            f"BATCH-FAILURE-RECEIPT-SHADOW status=refused shard={shard} "
            f"batch={batch_index} reason=identity-{type(exc).__name__} skips=0",
            flush=True,
        )
        return False
    if not bindings.disk_headroom(
        bindings.resource_root(),
        context=f"{shard}:batch-{batch_index:03d}:failure-receipt-write",
    ):
        print(
            f"BATCH-FAILURE-RECEIPT-SHADOW status=refused shard={shard} "
            f"batch={batch_index} reason=disk-headroom skips=0",
            flush=True,
        )
        return False
    try:
        publication = session.failure_writer.publish(
            FailureReceiptRequest(
                action_identity=action_identity,
                observed_action_identity=observed_action_identity,
                failure_node_metadata=failure_node_metadata,
                originating_run_id=session.run_id,
                elapsed_seconds=elapsed_seconds,
                returncode=returncode,
                cleanup_returncode=cleanup_returncode,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            f"BATCH-FAILURE-RECEIPT-SHADOW status=refused shard={shard} "
            f"batch={batch_index} reason=publisher-{type(exc).__name__} skips=0",
            flush=True,
        )
        return False
    recorded = publication.published or (
        publication.reason == "already-present" and publication.path is not None
    )
    print(
        "BATCH-FAILURE-RECEIPT-SHADOW "
        f"status={'published' if recorded else 'refused'} "
        f"shard={shard} batch={batch_index} reason={publication.reason} "
        f"action={publication.action_digest or 'none'} reusable=false skips=0",
        flush=True,
    )
    return recorded


def report_shadow_gate_progress(
    session: BatchReceiptSession,
    *,
    batch: ProgressBatch,
    passed: bool,
    receipt_status: ReceiptStatus,
    completed_receipt: Path | None,
) -> None:
    """Emit one bounded JSON estimate without claiming terminal gate status."""
    if session.progress is None:
        return
    try:
        summary = session.progress.record(
            ProgressExecution(
                batch=batch,
                passed=passed,
                receipt_status=receipt_status,
            ),
            completed_receipt=completed_receipt,
        )
        rendered = session.progress.render(summary)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        rendered = json.dumps(
            {
                "error": f"progress-{type(exc).__name__}",
                "gate_result": "unknown",
                "kind": "shadow-gate-progress-error",
                "overall_green": None,
                "schema_version": 1,
                "skips": 0,
                "terminal_phases_complete": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    print(f"GATE-PROGRESS-SHADOW {rendered}", flush=True)


def configure_shadow_receipts(
    *,
    source_release_eligible: bool,
    writer_enabled: bool,
    create_session: Callable[..., SessionT],
    repository_identity: Mapping[str, object],
    shards: list[str],
    pytest_args: list[str],
    max_files_per_batch: int,
    heartbeat_seconds: float,
    no_progress_seconds: float,
    replay_audit_enabled: bool,
    progress_enabled: bool,
    failure_receipts_enabled: bool,
    receipt_authentication_enabled: bool,
    receipt_admission_enabled: bool = False,
    batch_workers: int = 1,
) -> SessionT | None:
    """Configure receipt diagnostics and optional exact-SHA admission."""
    if not writer_enabled:
        print("BATCH-RECEIPT-SHADOW status=disabled reason=operator-off", flush=True)
        print(
            "BATCH-REPLAY-SHADOW status=disabled reason=writer-off skips=0",
            flush=True,
        )
        print(
            "BATCH-RECEIPT-AUTH-SHADOW status=disabled "
            "reason=writer-off future_eligible=0 skips=0",
            flush=True,
        )
        print(
            "BATCH-FAILURE-RECEIPT-SHADOW status=disabled "
            "reason=writer-off skips=0",
            flush=True,
        )
        print(
            "GATE-PROGRESS-SHADOW status=disabled reason=writer-off skips=0",
            flush=True,
        )
        print(
            "BATCH-RECEIPT-ADMISSION status=disabled reason=writer-off",
            flush=True,
        )
        return None
    if not source_release_eligible:
        print(
            "BATCH-RECEIPT-SHADOW status=disabled reason=source-ineligible",
            flush=True,
        )
        print(
            "BATCH-REPLAY-SHADOW status=disabled reason=source-ineligible skips=0",
            flush=True,
        )
        print(
            "BATCH-RECEIPT-AUTH-SHADOW status=disabled "
            "reason=source-ineligible future_eligible=0 skips=0",
            flush=True,
        )
        print(
            "BATCH-FAILURE-RECEIPT-SHADOW status=disabled "
            "reason=source-ineligible skips=0",
            flush=True,
        )
        print(
            "GATE-PROGRESS-SHADOW status=disabled reason=source-ineligible skips=0",
            flush=True,
        )
        print(
            "BATCH-RECEIPT-ADMISSION status=disabled reason=source-ineligible",
            flush=True,
        )
        return None
    try:
        session = create_session(
            repository_identity=repository_identity,
            shards=shards,
            pytest_args=pytest_args,
            max_files_per_batch=max_files_per_batch,
            heartbeat_seconds=heartbeat_seconds,
            no_progress_seconds=no_progress_seconds,
            replay_audit_enabled=replay_audit_enabled,
            progress_enabled=progress_enabled,
            failure_receipts_enabled=failure_receipts_enabled,
            receipt_authentication_enabled=receipt_authentication_enabled,
            receipt_admission_enabled=receipt_admission_enabled,
            batch_workers=batch_workers,
        )
        print(
            "BATCH-RECEIPT-SHADOW status=enabled "
            f"workers={batch_workers} max_generations=2 max_bytes=2147483648",
            flush=True,
        )
        if receipt_admission_enabled:
            print(
                "BATCH-RECEIPT-ADMISSION status=enabled "
                f"mode=authenticated-exact-sha workers={batch_workers}",
                flush=True,
            )
        else:
            print(
                "BATCH-RECEIPT-ADMISSION status=disabled reason=operator-off",
                flush=True,
            )
        if receipt_authentication_enabled:
            print(
                "BATCH-RECEIPT-AUTH-SHADOW status=enabled "
                f"algorithm={AUTH_ALGORITHM} mode=offline-shadow "
                "future_eligible=verified-only skips=0",
                flush=True,
            )
        else:
            print(
                "BATCH-RECEIPT-AUTH-SHADOW status=disabled "
                "reason=operator-off future_eligible=0 skips=0",
                flush=True,
            )
        if failure_receipts_enabled:
            print(
                "BATCH-FAILURE-RECEIPT-SHADOW status=enabled "
                "mode=non-reusable skips=0 "
                f"max_per_generation={MAX_FAILURE_RECEIPTS_PER_GENERATION} "
                f"max_nodes={MAX_FAILURE_NODES}",
                flush=True,
            )
        else:
            print(
                "BATCH-FAILURE-RECEIPT-SHADOW status=disabled "
                "reason=operator-off skips=0",
                flush=True,
            )
        if replay_audit_enabled:
            print(
                "BATCH-REPLAY-SHADOW status=enabled mode=audit-only "
                "skips=0 max_candidates=512 max_index_bytes=16777216",
                flush=True,
            )
        else:
            print(
                "BATCH-REPLAY-SHADOW status=disabled reason=operator-off skips=0",
                flush=True,
            )
        if progress_enabled:
            print(
                "GATE-PROGRESS-SHADOW status=enabled "
                "mode=estimate-only skips=0 gate_result=unknown "
                f"max_batches={MAX_PROGRESS_BATCHES} "
                f"max_samples={MAX_TIMING_SAMPLES} "
                f"max_output_bytes={MAX_PROGRESS_OUTPUT_BYTES}",
                flush=True,
            )
        else:
            print(
                "GATE-PROGRESS-SHADOW status=disabled reason=operator-off skips=0",
                flush=True,
            )
        return session
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        reason = f"identity-{type(exc).__name__}"
        print(
            f"BATCH-RECEIPT-SHADOW status=disabled reason={reason}",
            flush=True,
        )
        print(
            f"BATCH-REPLAY-SHADOW status=disabled reason={reason} skips=0",
            flush=True,
        )
        print(
            "BATCH-RECEIPT-AUTH-SHADOW status=disabled "
            f"reason={reason} future_eligible=0 skips=0",
            flush=True,
        )
        print(
            "BATCH-FAILURE-RECEIPT-SHADOW status=disabled "
            f"reason={reason} skips=0",
            flush=True,
        )
        print(
            f"GATE-PROGRESS-SHADOW status=disabled reason={reason} skips=0",
            flush=True,
        )
        print(
            f"BATCH-RECEIPT-ADMISSION status=disabled reason={reason}",
            flush=True,
        )
        return None


__all__ = [
    "BatchReceiptSession",
    "ShadowReceiptBindings",
    "batch_receipt_action_identity",
    "configure_shadow_receipts",
    "create_shadow_receipt_session",
    "publish_shadow_failure_receipt",
    "receipt_base_identity",
    "report_shadow_gate_progress",
    "report_shadow_replay_eligibility",
]
