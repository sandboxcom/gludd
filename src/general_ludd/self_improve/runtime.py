"""Concrete installed-package adapters for managed self-improvement."""

from __future__ import annotations

import argparse
import ast
import contextlib
import difflib
import hashlib
import io
import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import tokenize
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Final, Protocol, TextIO, cast, runtime_checkable

from general_ludd.hardware.model_fit import unified_probe
from general_ludd.local_model import LocalModelConfig
from general_ludd.planning.repo_map import RepoMapBuilder
from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V3,
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_REPAIR_CANDIDATE_LIMIT,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    EVALUATION_DIAGNOSIS_PROTOCOL,
    LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
    CandidateEvidence,
    CodexReference,
    CompactSpanProposal,
    ComparisonResult,
    ProposalContract,
    ProposalManifest,
    bind_compact_focus_path,
    compact_v4_repair_shard_state_digest,
    compare_with_codex,
    decode_compact_span_batch,
    decode_proposal_batch,
    encode_prompt_batch,
    expand_compact_span_proposals,
    merge_proposal_manifests,
    safe_evaluation_retry_diagnosis,
)
from general_ludd.self_improve.evaluator import (
    _PARENT_SYNTAX_ERROR_MARKER,
    _compact_evaluation_diagnosis,
    _evaluation_target_identity,
    _EvaluationLifecycleEvent,
    _record_evaluation_event,
    _repair_candidate_syntax_diagnosis,
    _run_evaluation_operation,
    _syntax_diagnosis_fields,
    _syntax_failure_class,
)
from general_ludd.self_improve.evaluator import (
    _bounded_evaluation_duration_ms as _bounded_evaluation_duration_ms,
)
from general_ludd.self_improve.evaluator import (
    _bounded_evaluation_returncode as _bounded_evaluation_returncode,
)
from general_ludd.self_improve.evaluator import (
    _failure_diagnosis_trace_view as _failure_diagnosis_trace_view,
)
from general_ludd.self_improve.evaluator import (
    _last_diagnosis_fact as _last_diagnosis_fact,
)
from general_ludd.self_improve.evaluator import (
    compact_failure_diagnosis as compact_failure_diagnosis,
)
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    CapabilityEvidenceOutcomeAdapter,
    GeneratedProposal,
    ManagedOutcomeAdapter,
    ManagedRunResult,
    ManagedSelfImproveRunner,
    ModelPlanError,
    PromptPlan,
    PromptShard,
    _build_validation_retry_prompt_plan,
    _validate_attempt_identity_digest,
    _validation_retry_feedback,
    build_retry_prompt_plan,
    build_syntax_repair_prompt_plan,
)
from general_ludd.self_improve.managed_runner import (
    AttemptResult as AttemptResult,
)
from general_ludd.self_improve.managed_runner import (
    ModelPlanFailure as ModelPlanFailure,
)
from general_ludd.self_improve.managed_runner import (
    PlanBoundProposal as PlanBoundProposal,
)
from general_ludd.self_improve.managed_runner import (
    TaskSpec as TaskSpec,
)
from general_ludd.self_improve.managed_runner import (
    _attempt_identity_digest as _attempt_identity_digest,
)
from general_ludd.self_improve.managed_runner import (
    _is_safe_make_command as _is_safe_make_command,
)
from general_ludd.self_improve.managed_runner import (
    _OutcomeAdapterFactory as _OutcomeAdapterFactory,
)
from general_ludd.self_improve.managed_runner import (
    _SyntaxRepairBuilder as _SyntaxRepairBuilder,
)
from general_ludd.self_improve.managed_runner import (
    _validate_approved_result_identity as _validate_approved_result_identity,
)
from general_ludd.self_improve.managed_runner import (
    _write_atomic_temp as _write_atomic_temp,
)
from general_ludd.self_improve.managed_runner import (
    apply_proposal as apply_proposal,
)
from general_ludd.self_improve.model_candidate_planner import (
    PlannedModelCandidate,
    load_latest_failed_model_ids,
    plan_model_candidates,
    record_self_improve_outcome,
)
from general_ludd.self_improve.model_lifecycle import (
    AcquiredModel,
    ModelAcquisitionError,
    ModelAcquisitionEvent,
    ModelArtifactIdentity,
    ModelLeaseManager,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.recommender import map_task_to_capabilities

_MAX_CAPTURE_BYTES: Final = 2_097_152
_MAX_TASK_BYTES: Final = 262_144
_MAX_PROPOSAL_BYTES: Final = 1_310_720
_MAX_REFERENCE_FILES: Final = 128
_MAX_PROMPT_PATHS: Final = 32
_MAX_PROMPT_SHARD_BYTES: Final = 16_384
_MAX_BASE_PROMPT_SHARD_BYTES: Final = 12_000
_MAX_FILE_EXCERPT_BYTES: Final = 4_096
_MAX_CONTEXT_FILE_BYTES: Final = 2_097_152
_MAX_SYNTAX_DIAGNOSTIC_BYTES: Final = 192
_PROMPT_CONTEXT_LINES: Final = 5
_HEARTBEAT_SECONDS: Final = 15.0
_FORBIDDEN_COMMAND_CHARS: Final = frozenset(";|&$()<>\n\r")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_TASK_RE: Final = re.compile(r"^S[0-9]+(?:\.[0-9]+)?$")
_WORD_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_RELEVANCE_STOPWORDS: Final = frozenset(
    {
        "and", "cannot", "each", "every", "file", "five", "from", "full",
        "into", "local", "model", "proposal", "required", "self", "should",
        "that", "the", "this", "uses", "with", "without",
    }
)


def _report_model_resolution_failure(
    model: LocalModelConfig,
    reason: str,
) -> None:
    print(
        "SELF_IMPROVE_MODEL_UNAVAILABLE "
        f"model={model.name} error={json.dumps(reason[:1000])}",
        flush=True,
    )


def _planned_artifact_identity(
    candidate: PlannedModelCandidate,
) -> ModelArtifactIdentity:
    """Adapt a planner result to the lifecycle's immutable artifact boundary."""
    return ModelArtifactIdentity(
        model_id=candidate.config.name,
        repo_id=candidate.config.repo,
        filename=candidate.config.filename,
        revision=candidate.resolved_revision,
    )


def _report_model_acquisition_event(event: ModelAcquisitionEvent) -> None:
    """Publish one secret-safe, bounded acquisition phase marker."""
    print(
        "SELF_IMPROVE_MODEL_ACQUISITION "
        f"phase={event.phase.value} operation={event.operation_id} "
        f"repository={event.repository_key} model={event.model_key or 'none'} "
        f"revision={event.revision or 'none'} "
        f"elapsed_seconds={event.elapsed_seconds:.2f} "
        f"failure={event.failure.value if event.failure is not None else 'none'}",
        flush=True,
    )


def _report_model_release(model: AcquiredModel) -> None:
    try:
        released = not model.lease_path.exists()
    except OSError:
        released = False
    print(
        "SELF_IMPROVE_MODEL_RELEASED "
        f"model={model.model_id} lease_released={str(released).lower()}",
        flush=True,
    )



@dataclass(frozen=True)
class MakeResult:
    """One observable Make operation and its bounded output."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float


@runtime_checkable
class _ObservableRunner(Protocol):
    """Make-mediated observable process boundary used by local inference."""

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        """Run an owned Make target and return bounded evidence."""


@runtime_checkable
class _CommandRunner(Protocol):
    """Make-only tool execution boundary used by mechanical repair routing."""

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        """Run one observable Make command."""


@runtime_checkable
class _TargetRunner(Protocol):
    """Minimal Make-target interface shared by root runners and test doubles."""

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
class _RuntimeMakeRunner(_ObservableRunner, _CommandRunner, _TargetRunner, Protocol):
    """Complete Make-mediated boundary used by the production composition root."""


@runtime_checkable
class _MakeRunnerFactory(Protocol):
    """Construct a repository-bound Make operation adapter."""

    def __call__(self, repo_root: Path) -> _RuntimeMakeRunner:
        """Return a runner bound to one canonical repository root."""


@runtime_checkable
class _AttemptEvaluationAdapter(Protocol):
    """Historical evaluation callable retained as an injectable CLI seam."""

    def __call__(
        self,
        root_runner: _TargetRunner,
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        """Evaluate one bound proposal without widening its authority."""


class _OwnedProcessGroup(Protocol):
    """Owned process-group interface used for bounded termination."""

    pid: int

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the owned child process."""


class MakeRunner:
    """Execute all repository and system operations through explicit Make targets."""

    def __init__(self, repo_root: Path) -> None:
        """Bind Make operations to one canonical repository root."""
        self.repo_root = repo_root.resolve(strict=True)
        self.operations: list[MakeResult] = []
        self._read_cache: dict[tuple[str, tuple[tuple[str, str], ...]], MakeResult] = {}

    def run(
        self,
        target: str,
        variables: dict[str, str] | None = None,
        *,
        timeout: int = 120,
        read_only: bool = False,
    ) -> MakeResult:
        """Run one short Make target; optionally cache an exact read-only call."""
        values = variables or {}
        _validate_target_and_variables(target, values)
        cache_key = (target, tuple(sorted(values.items())))
        if read_only and cache_key in self._read_cache:
            return self._read_cache[cache_key]
        argv = ["make", target, *[f"{key}={value}" for key, value in values.items()]]
        started = time.monotonic()
        completed = subprocess.run(
            argv,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
            env=_clean_environment(),
        )
        result = MakeResult(
            argv=tuple(argv),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_seconds=time.monotonic() - started,
        )
        self.operations.append(result)
        if read_only and result.returncode == 0:
            self._read_cache[cache_key] = result
        return result

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        """Run a model/task-supplied Make command with live output and heartbeats."""
        if not _is_safe_make_command(command):
            raise ValueError("tool command must be one bounded make command")
        return self._run_observable_argv(shlex.split(command), timeout=timeout)

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        """Run one explicit Make target in an owned observable process group."""
        _validate_target_and_variables(target, variables)
        argv = ["make", target, *[f"{key}={value}" for key, value in variables.items()]]
        return self._run_observable_argv(argv, timeout=timeout)

    def _run_observable_argv(self, argv: list[str], *, timeout: int) -> MakeResult:
        command = shlex.join(argv)
        started = time.monotonic()
        print(f"SELF_IMPROVE_COMMAND_START command={json.dumps(command)}", flush=True)
        proc = subprocess.Popen(
            argv,
            cwd=str(self.repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            start_new_session=True,
            env=_clean_environment(),
        )
        if proc.stdout is None:
            _terminate_process_group(proc)
            raise RuntimeError("Make command did not expose an output stream")
        selector: selectors.BaseSelector | None = None
        captured: list[str] = []
        captured_bytes = 0
        timed_out = False
        try:
            next_heartbeat = time.monotonic() + _HEARTBEAT_SECONDS
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                now = time.monotonic()
                if now - started > timeout:
                    timed_out = True
                    _terminate_process_group(proc)
                    break
                events = selector.select(timeout=1.0)
                for key, _mask in events:
                    stream = cast("TextIO", key.fileobj)
                    line = stream.readline()
                    if line:
                        print(line, end="", flush=True)
                        encoded = line.encode("utf-8", errors="replace")
                        remaining = _MAX_CAPTURE_BYTES - captured_bytes
                        if remaining > 0:
                            clipped = encoded[:remaining].decode("utf-8", errors="replace")
                            captured.append(clipped)
                            captured_bytes += len(clipped.encode("utf-8"))
                if proc.poll() is not None:
                    remainder = proc.stdout.read()
                    if remainder:
                        print(remainder, end="", flush=True)
                        encoded = remainder.encode("utf-8", errors="replace")
                        remaining = _MAX_CAPTURE_BYTES - captured_bytes
                        if remaining > 0:
                            captured.append(encoded[:remaining].decode("utf-8", errors="replace"))
                    break
                if now >= next_heartbeat:
                    print(
                        f"SELF_IMPROVE_HEARTBEAT elapsed={now - started:.1f}s "
                        f"pid={proc.pid} command={json.dumps(command)}",
                        flush=True,
                    )
                    next_heartbeat = now + _HEARTBEAT_SECONDS
        except BaseException:
            _terminate_process_group(proc)
            raise
        finally:
            try:
                if selector is not None:
                    selector.close()
            finally:
                proc.stdout.close()
        returncode = 124 if timed_out else int(proc.returncode or 0)
        result = MakeResult(
            argv=tuple(argv),
            returncode=returncode,
            stdout="".join(captured),
            stderr="timed out" if timed_out else "",
            elapsed_seconds=time.monotonic() - started,
        )
        self.operations.append(result)
        print(
            f"SELF_IMPROVE_COMMAND_END rc={returncode} "
            f"elapsed={result.elapsed_seconds:.2f}s",
            flush=True,
        )
        return result


def _run_local_proposal_request(
    runner: _ObservableRunner,
    model_path: Path,
    request: str,
    *,
    contract: ProposalContract | None = None,
) -> str:
    """Run one bounded request through one isolated parent-owned Make worker."""
    if not model_path.is_file():
        raise FileNotFoundError(f"local GGUF is not readable: {model_path}")
    if not request.strip() or len(request.encode("utf-8")) > _MAX_TASK_BYTES:
        raise ValueError(f"proposal prompt must contain 1..{_MAX_TASK_BYTES} bytes")

    with tempfile.TemporaryDirectory(
        prefix="gludd-self-improve-proposal-"
    ) as raw_exchange:
        exchange = Path(raw_exchange)
        prompt_path = exchange / "prompt.txt"
        proposal_path = exchange / "proposal.json"
        contract_path = exchange / "contract.json"
        temporary = _write_atomic_temp(
            prompt_path,
            request,
            0o600,
            ".prompt-tmp",
        )
        os.replace(temporary, prompt_path)
        if contract is not None:
            contract_temporary = _write_atomic_temp(
                contract_path,
                contract.to_json(),
                0o600,
                ".contract-tmp",
            )
            os.replace(contract_temporary, contract_path)
        worker_variables = {
            "SELF_IMPROVE_MODEL_PATH": str(model_path),
            "SELF_IMPROVE_PROMPT_FILE": str(prompt_path),
            "SELF_IMPROVE_PROPOSAL_FILE": str(proposal_path),
        }
        if contract is not None:
            worker_variables["SELF_IMPROVE_CONTRACT_FILE"] = str(contract_path)
        result = runner.run_observable(
            "self-improve-local-proposal",
            worker_variables,
            timeout=300,
        )
        if result.returncode != 0:
            diagnostic = (result.stderr or result.stdout or "no worker diagnostic")[-2000:]
            raise RuntimeError(
                f"local proposal worker failed rc={result.returncode}: {diagnostic}"
            )
        if (
            proposal_path.is_symlink()
            or not proposal_path.is_file()
            or proposal_path.stat().st_size > _MAX_PROPOSAL_BYTES
        ):
            raise RuntimeError(
                "local proposal worker did not publish one bounded regular file"
            )
        try:
            return proposal_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"local proposal output is not readable UTF-8: {exc}"
            ) from exc


def generate_local_proposal(
    runner: _ObservableRunner,
    model_path: Path,
    prompt: str,
) -> ProposalManifest:
    """Generate one legacy proposal through one isolated owned Make worker."""
    return ProposalManifest.from_json(
        _run_local_proposal_request(runner, model_path, prompt)
    )


def _one_shard_prompt_plan(plan: PromptPlan, shard: PromptShard) -> PromptPlan:
    """Select one immutable shard without widening its baseline or edit scope."""
    if len(shard.focus_paths) != 1:
        raise ValueError("compact-v4 repair shard must bind exactly one focus path")
    path = shard.focus_paths[0]
    baseline_by_path = dict(plan.baseline_files)
    if path not in baseline_by_path:
        raise ValueError("compact-v4 repair shard is absent from the trusted baseline")
    baseline = baseline_by_path[path]
    return PromptPlan(
        shards=(shard,),
        source_bytes=len(baseline.encode("utf-8")) if baseline is not None else 0,
        protocol_digest=plan.protocol_digest,
        baseline_files=((path, baseline),),
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
    )


def _combine_shard_prompt_plans(
    plans: tuple[PromptPlan, ...],
    *,
    protocol_digest: str,
) -> PromptPlan:
    """Combine disjoint single-shard plans without changing their trusted snapshots."""
    if not plans:
        raise ValueError("compact-v4 repair requires at least one failing shard")
    return PromptPlan(
        shards=tuple(shard for item in plans for shard in item.shards),
        source_bytes=sum(item.source_bytes for item in plans),
        protocol_digest=protocol_digest,
        baseline_files=tuple(
            baseline for item in plans for baseline in item.baseline_files
        ),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )


def _report_repair_shard_state(
    relative_path: str,
    *,
    candidate: str,
    state: str,
    diagnostic: str | None = None,
    target_span: tuple[int, int] | None = None,
) -> None:
    """Emit one bounded source-free state after binding diagnosis to its shard."""
    if state not in {
        "frozen",
        "preflight_rejected",
        "proposal_rejected",
        "span_targeted",
        "syntax_rejected",
    }:
        raise RuntimeError("compact-v4 repair shard state is unsupported")
    fields = _syntax_diagnosis_fields(diagnostic)
    path_sha256 = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
    if diagnostic is not None and fields["path_sha256"] != path_sha256:
        raise RuntimeError("repair syntax diagnosis does not match its owning shard")
    if (state in {"preflight_rejected", "span_targeted", "syntax_rejected"}) != (
        diagnostic is not None
    ):
        raise RuntimeError("compact-v4 repair shard state and diagnosis disagree")
    if (state == "span_targeted") != (target_span is not None):
        raise RuntimeError("compact-v4 repair target telemetry is inconsistent")
    target_detail = ""
    if target_span is not None:
        start_line, old_line_count = target_span
        if (
            isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or isinstance(old_line_count, bool)
            or not isinstance(old_line_count, int)
            or start_line < 1
            or old_line_count < 0
        ):
            raise RuntimeError("compact-v4 repair target telemetry is invalid")
        target_detail = f" target_s={start_line} target_n={old_line_count}"
    category = fields["category"] if diagnostic is not None else "none"
    event = (
        "SELF_IMPROVE_REPAIR_SHARD_STATE "
        f"candidate={candidate} path_sha256={path_sha256} state={state} "
        f"category={category} line={fields['line']} column={fields['column']}"
        f"{target_detail}"
    )
    if len(event.encode("ascii")) > 256:
        raise RuntimeError("compact-v4 repair shard event exceeded 256 bytes")
    _runtime_progress(event)


def _repair_preflight_state(diagnostic: str | None) -> str:
    """Classify a trusted per-file parser result without exposing its source."""
    if diagnostic is None:
        return "frozen"
    return (
        "syntax_rejected"
        if _syntax_failure_class(diagnostic) == "python_syntax"
        else "preflight_rejected"
    )


def _compact_v4_syntax_owning_span_index(
    baseline: str | None,
    proposal: CompactSpanProposal,
    diagnostic: str,
) -> int | None:
    """Return the unique model-authored output span containing one parser line."""
    fields = _syntax_diagnosis_fields(diagnostic)
    expected_path_sha256 = hashlib.sha256(proposal.focus_path.encode("utf-8")).hexdigest()
    if fields["path_sha256"] != expected_path_sha256:
        raise RuntimeError("repair syntax diagnosis does not match its owning shard")
    if fields["category"] != "python_syntax" or not isinstance(fields["line"], int):
        return None
    parser_line = fields["line"]
    baseline_lines = [] if baseline is None else baseline.splitlines(keepends=True)
    baseline_cursor = 0
    output_lines = 0
    owners: list[int] = []
    for index, span in enumerate(proposal.edits):
        start = span.start_line - 1
        if start < baseline_cursor or start > len(baseline_lines):
            return None
        output_lines += start - baseline_cursor
        replacement_lines = len(span.new_text.splitlines())
        first_replacement_line = output_lines + 1
        last_replacement_line = output_lines + replacement_lines
        if replacement_lines and first_replacement_line <= parser_line <= last_replacement_line:
            owners.append(index)
        output_lines = last_replacement_line
        baseline_cursor = start + span.old_line_count
    return owners[0] if len(owners) == 1 else None


def _proposal_with_repaired_span(
    proposal: CompactSpanProposal,
    replacement: CompactSpanProposal,
    target_span: tuple[int, int],
) -> CompactSpanProposal:
    """Replace exactly one owned span while retaining every non-owning span."""
    if proposal.focus_path != replacement.focus_path or len(replacement.edits) != 1:
        raise ValueError("compact-v4 targeted repair must return exactly one owning span")
    edit = replacement.edits[0]
    if (edit.start_line, edit.old_line_count) != target_span:
        raise ValueError("compact-v4 targeted repair changed immutable span coordinates")
    matches = [
        index
        for index, prior in enumerate(proposal.edits)
        if (prior.start_line, prior.old_line_count) == target_span
    ]
    if len(matches) != 1:
        raise ValueError("compact-v4 targeted repair span is not uniquely owned")
    edits = list(proposal.edits)
    edits[matches[0]] = edit
    return CompactSpanProposal(proposal.focus_path, tuple(edits))


def _build_targeted_repair_prompt_plan(
    plan: PromptPlan,
    shard: PromptShard,
    task: TaskSpec,
    proposal: CompactSpanProposal,
    diagnostic: str,
    target_span: tuple[int, int],
) -> PromptPlan:
    """Render bounded local baseline context for one provenance-owned span."""
    path = proposal.focus_path
    baseline_by_path = dict(plan.baseline_files)
    if shard.focus_paths != (path,) or path not in baseline_by_path:
        raise ValueError("targeted syntax repair drifted from its immutable shard")
    baseline = baseline_by_path[path]
    if baseline is None:
        context = "ABSENT FILE"
    else:
        lines = baseline.splitlines(keepends=True)
        start = target_span[0] - 1
        consumed_end = start + max(1, target_span[1])
        if start > len(lines) or consumed_end > len(lines) + (target_span[1] == 0):
            raise ValueError("targeted syntax repair span is outside its baseline")
        context_start = max(0, start - _PROMPT_CONTEXT_LINES)
        context_end = min(len(lines), consumed_end + _PROMPT_CONTEXT_LINES)
        context = _render_selected_lines(lines, set(range(context_start, context_end)))
    body = (
        "EDIT_TASK_BEGIN\n"
        f"{task.objective}\n"
        "EDIT_TASK_END\n"
        "TARGET_REPAIR_REQUIREMENTS_BEGIN\n"
        f"Repair only {path}. Only the exact target s/n is editable; surrounding "
        "lines are context, and every frozen sibling path and span remains immutable. "
        "Return compact spans only.\n"
        "TARGET_REPAIR_REQUIREMENTS_END\n"
        "TARGET_REPAIR_CONTEXT_BEGIN\n"
        f"{context}\n"
        "TARGET_REPAIR_CONTEXT_END"
    )
    targeted_shard = PromptShard(
        focus_paths=(path,),
        prompt=bind_compact_focus_path(
            body,
            path,
            editable_ranges=shard.editable_ranges,
        ),
        editable_ranges=shard.editable_ranges,
    )
    targeted = PromptPlan(
        shards=(targeted_shard,),
        source_bytes=(len(baseline.encode("utf-8")) if baseline is not None else 0),
        protocol_digest=plan.protocol_digest,
        baseline_files=((path, baseline),),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    return build_syntax_repair_prompt_plan(
        targeted,
        (proposal,),
        _repair_candidate_syntax_diagnosis(diagnostic),
        target_span=target_span,
    )


@dataclass
class _CompactRepairState:
    """Mutable parent-owned state for bounded compact repair candidates."""

    active_plan: PromptPlan
    original_path_groups: tuple[tuple[str, ...], ...]
    original_paths: tuple[str, ...]
    original_ranges: tuple[tuple[tuple[int, int], ...], ...]
    original_baselines: dict[str, str | None]
    frozen: dict[str, CompactSpanProposal]
    latest: dict[str, CompactSpanProposal]
    targets: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class _RepairCandidateOutcome:
    """One candidate pass over every currently failing repair shard."""

    next_plans: tuple[PromptPlan, ...]
    first_diagnostic: str | None
    last_contract: ProposalContract | None


def _initial_repair_shard_plan(
    state: _CompactRepairState,
    plan: PromptPlan,
    shard: PromptShard,
    span_proposal: CompactSpanProposal,
    task: TaskSpec,
    contract: ProposalContract,
) -> PromptPlan | None:
    """Validate one inherited repair shard and return its next failing plan."""
    path = span_proposal.focus_path
    state.latest[path] = span_proposal
    shard_plan = _one_shard_prompt_plan(plan, shard)
    proposal = expand_compact_span_proposals(
        (span_proposal,),
        contract=contract,
        expected_path_groups=(shard.focus_paths,),
        expected_baseline_files=dict(shard_plan.baseline_files),
        expected_editable_ranges=(shard.editable_ranges,),
    )
    diagnostic = _proposal_python_syntax_diagnostics(proposal).get(path)
    _report_repair_shard_state(
        path,
        candidate="initial",
        state=_repair_preflight_state(diagnostic),
        diagnostic=diagnostic,
    )
    if diagnostic is None:
        state.frozen[path] = span_proposal
        return None
    baseline = dict(shard_plan.baseline_files)[path]
    owning_index = _compact_v4_syntax_owning_span_index(
        baseline,
        span_proposal,
        diagnostic,
    )
    if owning_index is not None:
        owning_edit = span_proposal.edits[owning_index]
        target = (owning_edit.start_line, owning_edit.old_line_count)
        state.targets[path] = target
        _report_repair_shard_state(
            path,
            candidate="initial",
            state="span_targeted",
            diagnostic=diagnostic,
            target_span=target,
        )
        return _build_targeted_repair_prompt_plan(
            plan,
            shard,
            task,
            span_proposal,
            diagnostic,
            target,
        )
    if (
        hashlib.sha256(path.encode("utf-8")).hexdigest()
        != plan.repair_diagnosis_path_sha256
        and _syntax_failure_class(diagnostic) == "python_syntax"
    ):
        return build_syntax_repair_prompt_plan(
            shard_plan,
            (span_proposal,),
            _repair_candidate_syntax_diagnosis(diagnostic),
        )
    return shard_plan


def _prepare_compact_repair_state(
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
) -> _CompactRepairState:
    """Validate immutable shard identity and prepare inherited repair state."""
    path_groups = tuple(shard.focus_paths for shard in plan.shards)
    if any(len(paths) != 1 for paths in path_groups):
        raise ValueError("compact-v4 prompt shards must bind exactly one focus path")
    state = _CompactRepairState(
        active_plan=plan,
        original_path_groups=path_groups,
        original_paths=tuple(paths[0] for paths in path_groups),
        original_ranges=tuple(shard.editable_ranges for shard in plan.shards),
        original_baselines=dict(plan.baseline_files),
        frozen={},
        latest={},
        targets={},
    )
    if not plan.repair_proposals:
        return state
    contract = ProposalContract(
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
    )
    failing = tuple(
        next_plan
        for shard, proposal in zip(plan.shards, plan.repair_proposals, strict=True)
        if (next_plan := _initial_repair_shard_plan(state, plan, shard, proposal, task, contract))
        is not None
    )
    if not failing:
        raise ValueError("compact-v4 repair state did not reproduce its parent syntax failure")
    state.active_plan = _combine_shard_prompt_plans(
        failing,
        protocol_digest=plan.protocol_digest,
    )
    return state


def _repair_shard_contract(
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
    state: _CompactRepairState,
    shard: PromptShard,
    candidate_index: int,
) -> tuple[str, ProposalContract, PromptPlan]:
    """Bind one repair request before entering the rejectable model boundary."""
    shard_plan = _one_shard_prompt_plan(state.active_plan, shard)
    request = encode_prompt_batch((shard.prompt,), protocol_digest=plan.protocol_digest)
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
        sampling_candidate_index=candidate_index,
        repair_state_sha256=compact_v4_repair_shard_state_digest(
            tuple(state.latest[path] for path in state.original_paths if path in state.latest)
        ),
    )
    return request, contract, shard_plan


def _decode_repair_shard(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    state: _CompactRepairState,
    shard: PromptShard,
    request: str,
    contract: ProposalContract,
    shard_plan: PromptPlan,
) -> tuple[CompactSpanProposal, ProposalManifest]:
    """Request and expand one independently bounded repair shard."""
    raw = _run_local_proposal_request(runner, model_path, request, contract=contract)
    span = decode_compact_span_batch(
        raw,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=1,
    )[0]
    target = state.targets.get(span.focus_path)
    if target is not None:
        span = _proposal_with_repaired_span(state.latest[span.focus_path], span, target)
    proposal = expand_compact_span_proposals(
        (span,),
        contract=contract,
        expected_path_groups=(shard.focus_paths,),
        expected_baseline_files=dict(shard_plan.baseline_files),
        expected_editable_ranges=(shard.editable_ranges,),
    )
    return span, proposal


def _advance_repair_shard(
    state: _CompactRepairState,
    shard_plan: PromptPlan,
    shard: PromptShard,
    span: CompactSpanProposal,
    proposal: ProposalManifest,
    task: TaskSpec,
    candidate: str,
) -> tuple[PromptPlan | None, str | None]:
    """Freeze one valid shard or build its next syntax-repair plan."""
    path = span.focus_path
    state.latest[path] = span
    diagnostic = _proposal_python_syntax_diagnostics(proposal).get(path)
    _report_repair_shard_state(
        path,
        candidate=candidate,
        state=_repair_preflight_state(diagnostic),
        diagnostic=diagnostic,
    )
    if diagnostic is None:
        state.frozen[path] = span
        state.targets.pop(path, None)
        return None, None
    next_plan = shard_plan
    if _syntax_failure_class(diagnostic) == "python_syntax":
        baseline = dict(shard_plan.baseline_files)[path]
        owning_index = _compact_v4_syntax_owning_span_index(baseline, span, diagnostic)
        target: tuple[int, int] | None = None
        if owning_index is not None:
            edit = span.edits[owning_index]
            target = (edit.start_line, edit.old_line_count)
            state.targets[path] = target
            _report_repair_shard_state(
                path,
                candidate=candidate,
                state="span_targeted",
                diagnostic=diagnostic,
                target_span=target,
            )
        else:
            state.targets.pop(path, None)
        next_plan = (
            _build_targeted_repair_prompt_plan(
                shard_plan,
                shard,
                task,
                span,
                diagnostic,
                target,
            )
            if target is not None
            else build_syntax_repair_prompt_plan(
                shard_plan,
                (span,),
                _repair_candidate_syntax_diagnosis(diagnostic),
            )
        )
    return next_plan, diagnostic


def _run_repair_candidate(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
    state: _CompactRepairState,
    candidate_index: int,
) -> _RepairCandidateOutcome:
    """Evaluate one candidate number across all currently failing shards."""
    next_plans: list[PromptPlan] = []
    first_diagnostic: str | None = None
    last_contract: ProposalContract | None = None
    candidate = f"{candidate_index + 1}/{COMPACT_V4_REPAIR_CANDIDATE_LIMIT}"
    for shard in state.active_plan.shards:
        request, contract, shard_plan = _repair_shard_contract(
            plan,
            task,
            reference,
            required_tests,
            state,
            shard,
            candidate_index,
        )
        last_contract = contract
        try:
            span, proposal = _decode_repair_shard(
                runner,
                model_path,
                plan,
                state,
                shard,
                request,
                contract,
                shard_plan,
            )
        except (RuntimeError, ValueError):
            _report_repair_shard_state(
                shard.focus_paths[0],
                candidate=candidate,
                state="proposal_rejected",
            )
            next_plans.append(shard_plan)
            continue
        next_plan, diagnostic = _advance_repair_shard(
            state,
            shard_plan,
            shard,
            span,
            proposal,
            task,
            candidate,
        )
        if diagnostic is not None and first_diagnostic is None:
            first_diagnostic = diagnostic
        if next_plan is not None:
            next_plans.append(next_plan)
    return _RepairCandidateOutcome(tuple(next_plans), first_diagnostic, last_contract)


def _finalize_repair_candidate(
    state: _CompactRepairState,
    outcome: _RepairCandidateOutcome,
    candidate_index: int,
) -> GeneratedProposal | None:
    """Return a completely frozen aggregate or report the bounded rejection."""
    candidate = f"{candidate_index + 1}/{COMPACT_V4_REPAIR_CANDIDATE_LIMIT}"
    if outcome.next_plans:
        result = "syntax_rejected" if outcome.first_diagnostic else "proposal_rejected"
        diagnostic = f" {outcome.first_diagnostic}" if outcome.first_diagnostic else ""
        _runtime_progress(
            "SELF_IMPROVE_REPAIR_CANDIDATE "
            f"candidate={candidate} result={result} "
            f"failing_shards={len(outcome.next_plans)} "
            f"frozen_shards={len(state.frozen)}{diagnostic}"
        )
        return None
    if outcome.last_contract is None or set(state.frozen) != set(state.original_paths):
        raise ValueError("compact-v4 repair did not cover the immutable shard set")
    spans = tuple(state.frozen[path] for path in state.original_paths)
    proposal = expand_compact_span_proposals(
        spans,
        contract=outcome.last_contract,
        expected_path_groups=state.original_path_groups,
        expected_baseline_files=state.original_baselines,
        expected_editable_ranges=state.original_ranges,
    )
    if _proposal_python_syntax_preflight(proposal) is not None:
        raise ValueError("compact-v4 frozen repair aggregate failed immutable syntax revalidation")
    _runtime_progress(
        "SELF_IMPROVE_REPAIR_CANDIDATE "
        f"candidate={candidate} result=selected "
        f"failing_shards=0 frozen_shards={len(state.frozen)}"
    )
    return GeneratedProposal(proposal, spans)


def _generate_compact_v4_repair_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
) -> GeneratedProposal:
    """Generate bounded repair shards independently and freeze each valid result."""
    required_tests = _required_prompt_tests(task, reference)
    state = _prepare_compact_repair_state(plan, task, reference, required_tests)
    for candidate_index in range(COMPACT_V4_REPAIR_CANDIDATE_LIMIT):
        outcome = _run_repair_candidate(
            runner,
            model_path,
            plan,
            task,
            reference,
            required_tests,
            state,
            candidate_index,
        )
        result = _finalize_repair_candidate(state, outcome, candidate_index)
        if result is not None:
            return result
        if candidate_index + 1 < COMPACT_V4_REPAIR_CANDIDATE_LIMIT:
            state.active_plan = _combine_shard_prompt_plans(
                outcome.next_plans,
                protocol_digest=plan.protocol_digest,
            )
    raise ValueError(
        "compact-v4 syntax repair exhausted "
        f"{COMPACT_V4_REPAIR_CANDIDATE_LIMIT} bounded candidates"
    )


def _generate_compact_v4_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
) -> GeneratedProposal:
    """Decode one initial compact-v4 candidate against trusted snapshots."""
    if not plan.baseline_files:
        raise ValueError("compact-v4 prompt plan requires trusted baseline snapshots")
    if plan.sampling_profile == COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID:
        return _generate_compact_v4_repair_plan_result(
            runner,
            model_path,
            plan,
            task,
            reference,
        )
    path_groups = tuple(shard.focus_paths for shard in plan.shards)
    if any(len(paths) != 1 for paths in path_groups):
        raise ValueError("compact-v4 prompt shards must bind exactly one focus path")
    paths = tuple(group[0] for group in path_groups)
    request = encode_prompt_batch(
        tuple(shard.prompt for shard in plan.shards),
        protocol_digest=plan.protocol_digest,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
        sampling_candidate_index=0,
    )
    raw = _run_local_proposal_request(runner, model_path, request, contract=contract)
    spans = decode_compact_span_batch(
        raw,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=len(plan.shards),
    )
    by_path: dict[str, CompactSpanProposal] = {}
    for span in spans:
        if span.focus_path in by_path:
            raise ValueError("compact-v4 repair tried to replace a frozen shard")
        by_path[span.focus_path] = span
    if set(by_path) != set(paths):
        raise ValueError("compact-v4 repair did not cover the immutable shard set")
    ordered = tuple(by_path[path] for path in paths)
    proposal = expand_compact_span_proposals(
        ordered,
        contract=contract,
        expected_path_groups=path_groups,
        expected_baseline_files=dict(plan.baseline_files),
        expected_editable_ranges=tuple(shard.editable_ranges for shard in plan.shards),
    )
    return GeneratedProposal(proposal, ordered)


def _generate_legacy_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
    required_tests: tuple[str, ...],
) -> GeneratedProposal:
    """Decode and merge legacy proposal shards."""
    request = encode_prompt_batch(
        tuple(shard.prompt for shard in plan.shards),
        protocol_digest=plan.protocol_digest,
    )
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
        proposal_protocol=plan.proposal_protocol,
        sampling_profile=plan.sampling_profile,
    )
    raw = _run_local_proposal_request(runner, model_path, request, contract=contract)
    proposals = decode_proposal_batch(
        raw,
        expected_protocol_digest=plan.protocol_digest,
        expected_count=len(plan.shards),
    )
    return GeneratedProposal(
        merge_proposal_manifests(
            proposals,
            expected_path_groups=tuple(shard.focus_paths for shard in plan.shards),
            expected_baseline_sha=reference.baseline_sha,
            expected_task_id=task.task_id,
            expected_tests=required_tests,
            expected_make_commands=task.canonical_make_commands,
            expected_baseline_files=(
                dict(plan.baseline_files) if plan.baseline_files else None
            ),
        )
    )


def _generate_local_proposal_plan_result(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
) -> GeneratedProposal:
    """Decode all shards and retain only validated compact-v4 repair material."""
    required_tests = _required_prompt_tests(task, reference)
    if plan.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4:
        return _generate_compact_v4_plan_result(
            runner,
            model_path,
            plan,
            task,
            reference,
            required_tests,
        )
    return _generate_legacy_plan_result(
        runner,
        model_path,
        plan,
        task,
        reference,
        required_tests,
    )


def generate_local_proposal_plan(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
) -> ProposalManifest:
    """Decode and merge a plan while preserving the legacy manifest-only API."""
    return _generate_local_proposal_plan_result(
        runner,
        model_path,
        plan,
        task,
        reference,
    ).proposal


@dataclass(frozen=True)
class _ProtocolFailureBinding:
    """Trusted structural metadata attached to an otherwise unchanged exception."""

    proposal_protocol: str

    def __post_init__(self) -> None:
        """Reject bindings outside the two supported compact protocols."""
        if self.proposal_protocol not in {
            COMPACT_PROPOSAL_PROTOCOL_V3,
            COMPACT_PROPOSAL_PROTOCOL_V4,
        }:
            raise ValueError("proposal failure protocol is unsupported")


_PROTOCOL_FAILURE_BINDING_ATTR = "_gludd_self_improve_protocol_binding"


def _bind_failure_protocol(
    exc: BaseException,
    proposal_protocol: str,
) -> BaseException:
    """Attach trusted protocol metadata without changing exception type or text."""
    if not isinstance(exc, BaseException):
        raise TypeError("proposal failure cause must be an exception")
    binding = _ProtocolFailureBinding(proposal_protocol)
    with contextlib.suppress(AttributeError, TypeError):
        setattr(exc, _PROTOCOL_FAILURE_BINDING_ATTR, binding)
    return exc


def _protocol_bound_failure(
    exc: BaseException,
) -> tuple[str | None, BaseException]:
    """Find one structural protocol binding through a bounded exception chain."""
    current: BaseException | None = exc
    seen: set[int] = set()
    for _depth in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        binding = getattr(current, _PROTOCOL_FAILURE_BINDING_ATTR, None)
        if isinstance(binding, _ProtocolFailureBinding):
            return binding.proposal_protocol, current
        current = current.__cause__ or current.__context__
    return None, exc


def _public_failure_feedback(exc: BaseException) -> str:
    """Return only a typed, bounded, model-text-free public failure marker."""
    proposal_protocol, failure = _protocol_bound_failure(exc)
    typed_failure: str | None = None
    source = "runner"
    if isinstance(failure, ModelPlanError):
        typed_failure = failure.failure.value
    elif isinstance(failure, ModelAcquisitionError):
        typed_failure = failure.failure.value
        source = "model_lifecycle"
    if typed_failure is None:
        return _validation_retry_feedback(
            failure,
            proposal_protocol=proposal_protocol,
        )

    protocol = LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
    feedback = (
        f"protocol={protocol.version} type={typed_failure} "
        f"source={source} detail={protocol.redacted_detail}"
    )
    if len(feedback.encode("utf-8")) > protocol.max_feedback_bytes:
        raise RuntimeError("typed public failure feedback exceeds its protocol bound")
    return feedback


def _validation_retry_suffix(error: str) -> str:
    """Frame one typed diagnostic with the identity-bearing retry protocol."""
    protocol = LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
    return (
        protocol.prompt_prefix
        + _validation_retry_feedback(error)
        + protocol.prompt_suffix
    )





def parse_reference_files(output: str) -> frozenset[str]:
    """Extract the exact bounded repository file set from git-show-name-only."""
    files: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("git show ", "commit ", "Author:", "Date:")):
            continue
        if raw_line.startswith("    "):
            continue
        candidate = Path(line)
        if candidate.is_absolute() or ".." in candidate.parts or line.startswith(".git/"):
            raise ValueError(f"unsafe reference path: {line}")
        files.append(line)
    unique = frozenset(files)
    if not unique:
        raise ValueError("reference contains no repository files")
    if len(unique) > _MAX_REFERENCE_FILES:
        raise ValueError(f"reference exceeds {_MAX_REFERENCE_FILES} files")
    return unique


def parse_coverage_evidence(output: str) -> tuple[float, float]:
    """Parse aggregate and minimum-file percentages from canonical coverage output."""
    token = re.search(
        r"COVERAGE_FILES_PASS\s+aggregate=(\d+(?:\.\d+)?)\s+min_file=(\d+(?:\.\d+)?)",
        output,
    )
    if token:
        return float(token.group(1)), float(token.group(2))
    total = re.search(r"(?m)^TOTAL\s+.*?\s(\d+(?:\.\d+)?)%\s*$", output)
    minimum = re.search(
        r"minimum file coverage:\s*(\d+(?:\.\d+)?)%",
        output,
        flags=re.IGNORECASE,
    )
    if total and minimum:
        return float(total.group(1)), float(minimum.group(1))
    raise ValueError("canonical coverage evidence is missing aggregate or per-file coverage")


def canonical_test_paths(commands: tuple[str, ...]) -> tuple[str, ...]:
    """Extract exact Python test files from canonical Make command arguments."""
    found: set[str] = set()
    for command in commands:
        found.update(
            re.findall(r"tests/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py", command)
        )
    return tuple(sorted(found))


def proposal_scope_matches(
    proposal: ProposalManifest,
    reference_files: frozenset[str],
) -> bool:
    """Return whether every and only Codex-touched file is proposed."""
    return frozenset(edit.path for edit in proposal.edits) == reference_files


def mechanical_make_route(
    task: TaskSpec,
    reference: CodexReference,
) -> str | None:
    """Select a mature Make repair only for an exact, proven mechanical class."""
    objective = task.objective.casefold()
    if (
        "trailing whitespace" in objective
        and reference.changed_files
        and all(path.endswith((".md", ".mdx")) for path in reference.changed_files)
    ):
        return "make fix-docs-drift"
    return None


def proposal_from_mechanical_changes(
    task: TaskSpec,
    reference: CodexReference,
    before: dict[str, str],
    after: dict[str, str],
) -> ProposalManifest:
    """Convert mature-tool output into minimal exact edits under Codex scope."""
    if set(before) != set(reference.changed_files) or set(after) != set(before):
        raise ValueError("mechanical tool evidence does not cover the exact Codex scope")
    edits: list[dict[str, str]] = []
    for path in sorted(before):
        original = before[path]
        updated = after[path]
        matcher = difflib.SequenceMatcher(
            None,
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            autojunk=False,
        )
        file_edits = 0
        for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
            if tag == "equal":
                continue
            old_text = "".join(original.splitlines(keepends=True)[first_start:first_end])
            new_text = "".join(updated.splitlines(keepends=True)[second_start:second_end])
            if tag != "replace" or not old_text or not new_text:
                raise ValueError(
                    f"mechanical route requires bounded replacements, got {tag}: {path}"
                )
            if original.count(old_text) != 1:
                raise ValueError(f"mechanical replacement is not unique: {path}")
            edits.append(
                {
                    "operation": "replace",
                    "path": path,
                    "old_text": old_text,
                    "new_text": new_text,
                }
            )
            file_edits += 1
        if file_edits == 0:
            raise ValueError(f"mechanical tool did not change Codex-scoped file: {path}")
    test_paths = tuple(sorted(reference.test_files)) or canonical_test_paths(
        task.canonical_make_commands
    )
    if not test_paths:
        raise ValueError("mechanical route has no canonical test path")
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": reference.baseline_sha,
                "task_id": task.task_id,
                "edits": edits,
                "tests": list(test_paths),
                "make_commands": list(task.canonical_make_commands),
                "commit_message": "fix: apply Codex-scoped mechanical repair",
            }
        )
    )


def generate_mechanical_proposal(
    runner: _CommandRunner,
    task: TaskSpec,
    reference: CodexReference,
    baseline_root: Path,
) -> ProposalManifest | None:
    """Run an allowlisted mature Make repair and capture only Codex-scoped edits."""
    command = mechanical_make_route(task, reference)
    if command is None:
        return None
    before: dict[str, str] = {}
    for relative in sorted(reference.changed_files):
        path = baseline_root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_PROPOSAL_BYTES:
            raise ValueError(f"mechanical route input is not a bounded regular file: {relative}")
        before[relative] = path.read_text(encoding="utf-8")
    result = runner.run_command(command, timeout=120)
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout or "no tool diagnostic")[-2000:]
        raise RuntimeError(
            f"mechanical Make repair failed rc={result.returncode}: {diagnostic}"
        )
    after = {
        relative: (baseline_root / relative).read_text(encoding="utf-8")
        for relative in sorted(reference.changed_files)
    }
    print(
        f"SELF_IMPROVE_MECHANICAL_ROUTE command={json.dumps(command)} "
        f"files={len(after)}",
        flush=True,
    )
    return proposal_from_mechanical_changes(task, reference, before, after)


def quality_defaults_for_paths(
    paths: list[str],
    *,
    aggregate: float,
    minimum: float,
    targets: set[str],
) -> tuple[float, float, bool, bool, bool]:
    """Mark Python-only gates not applicable for a documentation/config patch."""
    python_changed = any(
        path.endswith(".py") and path.startswith(("src/", "scripts/"))
        for path in paths
    )
    if not python_changed:
        return 100.0, 100.0, True, True, True
    return (
        aggregate,
        minimum,
        bool(targets & {"lint", "lint-files"}),
        bool(targets & {"typecheck", "typecheck-scope"}),
        "lint-docstrings" in targets,
    )


def estimate_required_output_tokens(changed_lines: int, changed_files: int) -> int:
    """Estimate the decode budget needed for a complete multi-file proposal."""
    if changed_lines < 0 or changed_files < 0:
        raise ValueError("reference metrics must be non-negative")
    return 512 + changed_lines * 5 + changed_files * 96


def build_reference(
    runner: _TargetRunner,
    baseline_ref: str,
    reference_ref: str,
    elapsed_seconds: float,
) -> CodexReference:
    """Load the independent Codex patch boundary through bounded Make targets."""
    _validate_sha("baseline_ref", baseline_ref)
    _validate_sha("reference_ref", reference_ref)
    names = runner.run(
        "git-show-name-only", {"SHA": reference_ref}, read_only=True
    )
    if names.returncode != 0:
        raise RuntimeError(f"cannot inspect Codex reference: {names.stderr or names.stdout}")
    patch = runner.run("git-show-full", {"SHA": reference_ref}, read_only=True)
    if patch.returncode != 0:
        raise RuntimeError(f"cannot inspect Codex patch: {patch.stderr or patch.stdout}")
    changed_files = parse_reference_files(names.stdout)
    return CodexReference(
        baseline_sha=baseline_ref,
        reference_sha=reference_ref,
        changed_files=changed_files,
        test_files=frozenset(path for path in changed_files if path.startswith("tests/")),
        changed_lines=_line_count_from_patch(patch.stdout),
        elapsed_seconds=elapsed_seconds,
    )


def _relevance_terms(task: TaskSpec, relative: str) -> frozenset[str]:
    raw = " ".join((task.objective, Path(relative).stem.replace("_", " ")))
    terms: set[str] = set()
    for match in _WORD_RE.finditer(raw.casefold()):
        term = match.group(0)
        if len(term) < 3 or term in _RELEVANCE_STOPWORDS:
            continue
        terms.add(term)
        for suffix in ("ing", "ed", "es", "s"):
            if term.endswith(suffix) and len(term) - len(suffix) >= 4:
                terms.add(term[: -len(suffix)])
                break
    return frozenset(terms)


def _relevance_score(text: str, terms: frozenset[str]) -> int:
    lowered = text.casefold()
    return sum(1 for term in terms if term in lowered)


def _merge_selected_lines(selected: set[int]) -> tuple[tuple[int, int], ...]:
    if not selected:
        return ()
    ordered = sorted(selected)
    ranges: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append((start, previous + 1))
        start = previous = number
    ranges.append((start, previous + 1))
    return tuple(ranges)


def _render_selected_lines(lines: list[str], selected: set[int]) -> str:
    sections: list[str] = []
    for start, end in _merge_selected_lines(selected):
        numbered = "".join(
            f"L{number + 1}|{lines[number]}" for number in range(start, end)
        )
        sections.append(f"LINES {start + 1}-{end}\n{numbered}")
    return "\n".join(sections)


def _editable_line_ranges(selected: set[int]) -> tuple[tuple[int, int], ...]:
    """Return selected lines as immutable 1-based half-open ranges."""
    return tuple((start + 1, end + 1) for start, end in _merge_selected_lines(selected))


def _select_python_excerpt(
    relative: str,
    content: str,
    terms: frozenset[str],
    *,
    budget: int,
) -> tuple[str, int, tuple[tuple[int, int], ...]]:
    lines = content.splitlines(keepends=True)
    if not lines:
        return "", 0, ()
    candidates: list[tuple[int, int, int]] = []
    header_end = min(len(lines), 24)
    candidates.append((2, 0, header_end))
    symbols = RepoMapBuilder().parse_file(relative, content)
    for symbol in symbols:
        start = max(0, symbol.line_start)
        end = min(len(lines), symbol.line_end + 1)
        body = "".join(lines[start:end])
        score = _relevance_score(symbol.name, terms) * 8
        score += _relevance_score(body, terms)
        if score <= 0:
            continue
        if len(body.encode("utf-8")) <= budget * 3 // 4:
            candidates.append((score + 4, start, end))
        candidates.append((score + 3, start, min(end, start + 3)))
        for number in range(start, end):
            line_score = _relevance_score(lines[number], terms)
            if line_score:
                candidates.append(
                    (
                        score + line_score,
                        max(start, number - _PROMPT_CONTEXT_LINES),
                        min(end, number + _PROMPT_CONTEXT_LINES + 1),
                    )
                )
    for number, line in enumerate(lines):
        score = _relevance_score(line, terms)
        if score:
            candidates.append(
                (
                    score,
                    max(0, number - _PROMPT_CONTEXT_LINES),
                    min(len(lines), number + _PROMPT_CONTEXT_LINES + 1),
                )
            )
    candidates.append((1, max(0, len(lines) - 8), len(lines)))

    selected: set[int] = set()
    for _score, start, end in sorted(
        candidates,
        key=lambda item: (-item[0], item[1], item[2]),
    ):
        candidate = selected.union(range(start, end))
        rendered = _render_selected_lines(lines, candidate)
        if len(rendered.encode("utf-8")) <= budget:
            selected = candidate
    if not selected:
        for number, _line in enumerate(lines):
            rendered_candidate = _render_selected_lines(lines, {number})
            if len(rendered_candidate.encode("utf-8")) <= budget:
                selected.add(number)
                break
    return (
        _render_selected_lines(lines, selected),
        len(selected),
        _editable_line_ranges(selected),
    )


def _build_file_context(
    baseline_root: Path,
    relative: str,
    task: TaskSpec,
) -> tuple[str, int, str | None, tuple[tuple[int, int], ...]]:
    path = baseline_root / relative
    if path.is_symlink():
        raise ValueError(f"baseline context path must not be a symlink: {relative}")
    if not path.exists():
        return f"FILE {relative} state=absent bytes=0 sha256=none", 0, None, ()
    if not path.is_file():
        raise ValueError(f"baseline context path is not a regular file: {relative}")
    raw = path.read_bytes()
    if len(raw) > _MAX_CONTEXT_FILE_BYTES:
        raise ValueError(
            f"baseline context file exceeds {_MAX_CONTEXT_FILE_BYTES} bytes: {relative}"
        )
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"baseline context is not UTF-8: {relative}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    lines = content.splitlines(keepends=True)
    if len(raw) <= _MAX_FILE_EXCERPT_BYTES:
        selected = set(range(len(lines)))
        excerpt = _render_selected_lines(lines, selected)
        selected_lines = len(lines)
        ranges = _editable_line_ranges(selected)
        complete = True
    elif relative.endswith(".py"):
        excerpt, selected_lines, ranges = _select_python_excerpt(
            relative,
            content,
            _relevance_terms(task, relative),
            budget=_MAX_FILE_EXCERPT_BYTES,
        )
        complete = False
    else:
        selected = set(range(min(len(lines), 40)))
        excerpt = _render_selected_lines(lines, selected)
        if len(excerpt.encode("utf-8")) > _MAX_FILE_EXCERPT_BYTES:
            excerpt = ""
            selected = set()
        selected_lines = min(len(lines), 40) if excerpt else 0
        ranges = _editable_line_ranges(selected)
        complete = selected_lines == len(lines)
    if not excerpt and raw:
        raise ValueError(f"no bounded exact context could be selected: {relative}")
    marker = (
        f"FILE {relative} state=present bytes={len(raw)} sha256={digest} "
        f"complete={str(complete).lower()} selected_lines={selected_lines}/{len(lines)}"
    )
    if not complete:
        marker += (
            "\nOMITTED content remains bound by the published sha256; "
            "do not select lines outside the exact numbered excerpts."
        )
    return f"{marker}\n{excerpt}", len(raw), content, ranges


def _required_prompt_tests(
    task: TaskSpec,
    reference: CodexReference,
) -> tuple[str, ...]:
    tests = tuple(sorted(reference.test_files)) or canonical_test_paths(
        task.canonical_make_commands
    )
    if not tests:
        raise ValueError("task has no canonical test path for the proposal contract")
    return tests


def _render_prompt_shard(
    task: TaskSpec,
    focus_paths: tuple[str, ...],
    contexts: dict[str, str],
    editable_ranges: tuple[tuple[int, int], ...],
    *,
    shard_index: int,
    shard_total: int,
) -> str:
    if len(focus_paths) != 1:
        raise ValueError("compact prompt shards must have exactly one focus path")
    body = (
        "EDIT_TASK_BEGIN\n"
        f"{task.objective}\n"
        "EDIT_TASK_END\n"
        "EDIT_REQUIREMENTS_BEGIN\n"
        "Edit only this shard's focus file. Make the smallest complete change needed "
        "for the task and preserve unrelated content. Return only the grammar-bound "
        "e array; the parent owns paths, tests, commands, and commit metadata. Use s "
        "and n only against shown L<number>| lines. z is literal replacement content "
        "without labels or prompt metadata. Python replacement text must be valid in "
        "the surrounding indentation.\n"
        "EDIT_REQUIREMENTS_END\n"
        f"SHARD {shard_index}/{shard_total}\n"
        "FOCUS_BASELINE_BEGIN\n"
        + contexts[focus_paths[0]]
        + "\nFOCUS_BASELINE_END"
    )
    return bind_compact_focus_path(
        body,
        focus_paths[0],
        editable_ranges=editable_ranges,
    )


def build_prompt(
    task: TaskSpec,
    reference: CodexReference,
    baseline_root: Path,
) -> PromptPlan:
    """Build complete identity as bounded syntax-aware proposal shards."""
    paths = tuple(sorted(reference.changed_files))
    if not paths:
        raise ValueError("Codex reference contains no prompt paths")
    if len(paths) > _MAX_PROMPT_PATHS:
        raise ValueError(
            f"Codex reference exceeds the {_MAX_PROMPT_PATHS}-path prompt boundary"
        )
    _required_prompt_tests(task, reference)
    contexts: dict[str, str] = {}
    editable_ranges: dict[str, tuple[tuple[int, int], ...]] = {}
    baseline_files: list[tuple[str, str | None]] = []
    source_bytes = 0
    for relative in paths:
        context, size, baseline_text, ranges = _build_file_context(
            baseline_root,
            relative,
            task,
        )
        contexts[relative] = context
        editable_ranges[relative] = ranges
        baseline_files.append((relative, baseline_text))
        source_bytes += size

    groups = [(relative,) for relative in paths]
    for relative, group in zip(paths, groups, strict=True):
        single = _render_prompt_shard(
            task,
            group,
            contexts,
            editable_ranges[group[0]],
            shard_index=1,
            shard_total=1,
        )
        if len(single.encode("utf-8")) > _MAX_BASE_PROMPT_SHARD_BYTES:
            raise ValueError(
                "one exact prompt shard cannot fit the bounded CPU context: "
                f"{relative}"
            )

    shards = tuple(
        PromptShard(
            focus_paths=group,
            prompt=_render_prompt_shard(
                task,
                group,
                contexts,
                editable_ranges[group[0]],
                shard_index=index,
                shard_total=len(groups),
            ),
            editable_ranges=editable_ranges[group[0]],
        )
        for index, group in enumerate(groups, start=1)
    )
    return PromptPlan(
        shards=shards,
        source_bytes=source_bytes,
        baseline_files=tuple(baseline_files),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
    )


def create_worktree(
    root_runner: _TargetRunner,
    baseline_ref: str,
    attempt: int,
) -> tuple[Path, str]:
    """Create one namespaced isolated worktree at the exact baseline."""
    branch = f"self-improve-codex-{os.getpid()}-{int(time.time())}-{attempt}"
    result = root_runner.run(
        "agent-worktree-base",
        {"BRANCH": branch, "BASE": baseline_ref},
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"worktree creation failed: {result.stderr or result.stdout}")
    marker = next(
        (line for line in result.stdout.splitlines() if line.startswith("WORKTREE_PATH=")),
        "",
    )
    if not marker:
        raise RuntimeError("worktree creation did not publish WORKTREE_PATH")
    path = Path(marker.split("=", 1)[1]).resolve(strict=True)
    return path, branch


def _syntax_diagnostic(
    relative: str,
    *,
    failure_type: str,
    line: int = 0,
    column: int = 0,
) -> str:
    """Render one bounded source-free syntax preflight result."""
    bounded_line = max(0, min(line, EVALUATION_DIAGNOSIS_PROTOCOL.max_coordinate))
    bounded_column = max(0, min(column, EVALUATION_DIAGNOSIS_PROTOCOL.max_coordinate))
    path_digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()
    diagnostic = (
        f"{_PARENT_SYNTAX_ERROR_MARKER} type={failure_type} "
        f"path_sha256={path_digest} line={bounded_line} column={bounded_column}"
    )
    if len(diagnostic.encode("ascii")) > _MAX_SYNTAX_DIAGNOSTIC_BYTES:
        raise RuntimeError("parent syntax diagnostic exceeded its fixed byte bound")
    return diagnostic


def _python_syntax_preflight(
    root: Path,
    relative_paths: tuple[str, ...],
) -> str | None:
    """Parse bounded changed Python files without exposing their authored source."""
    resolved_root = root.resolve(strict=True)
    for relative in sorted(set(relative_paths)):
        path = Path(relative)
        if (
            not relative
            or "\\" in relative
            or "\x00" in relative
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != relative
        ):
            raise ValueError("syntax preflight path must be repository-relative")
        if path.suffix != ".py":
            continue
        candidate = root / path
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            return _syntax_diagnostic(relative, failure_type="python_path")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            return _syntax_diagnostic(relative, failure_type="python_path")
        try:
            raw = candidate.read_bytes()
        except OSError:
            return _syntax_diagnostic(relative, failure_type="python_read")
        if len(raw) > _MAX_CONTEXT_FILE_BYTES:
            return _syntax_diagnostic(relative, failure_type="python_size")
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding)
        except (LookupError, SyntaxError, UnicodeError):
            return _syntax_diagnostic(relative, failure_type="python_encoding")
        try:
            ast.parse(source, filename="<parent-syntax-preflight>", mode="exec")
        except SyntaxError as exc:
            return _syntax_diagnostic(
                relative,
                failure_type="python_syntax",
                line=exc.lineno or 0,
                column=exc.offset or 0,
            )
    return None


def _proposal_python_syntax_preflight(proposal: ProposalManifest) -> str | None:
    """Parse bounded full-snapshot Python edits before selecting a repair decode."""
    diagnostics = _proposal_python_syntax_diagnostics(proposal)
    return next(iter(diagnostics.values()), None)


def _proposal_python_syntax_diagnostics(
    proposal: ProposalManifest,
) -> dict[str, str]:
    """Return one source-free parser diagnosis for every failing Python snapshot."""
    if proposal.schema_version != 2:
        raise ValueError("repair syntax preflight requires snapshot proposal schema-v2")
    diagnostics: dict[str, str] = {}
    for edit in proposal.edits:
        if Path(edit.path).suffix != ".py" or edit.operation == "delete":
            continue
        raw = edit.new_text.encode("utf-8")
        if len(raw) > _MAX_CONTEXT_FILE_BYTES:
            diagnostics[edit.path] = _syntax_diagnostic(
                edit.path,
                failure_type="python_size",
            )
            continue
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding)
        except (LookupError, SyntaxError, UnicodeError):
            diagnostics[edit.path] = _syntax_diagnostic(
                edit.path,
                failure_type="python_encoding",
            )
            continue
        try:
            ast.parse(source, filename="<parent-syntax-preflight>", mode="exec")
        except SyntaxError as exc:
            diagnostics[edit.path] = _syntax_diagnostic(
                edit.path,
                failure_type="python_syntax",
                line=exc.lineno or 0,
                column=exc.offset or 0,
            )
    return diagnostics


@dataclass
class _AttemptState:
    """Mutable evidence accumulated by one isolated evaluation transaction."""

    started: float
    results: list[MakeResult]
    events: list[_EvaluationLifecycleEvent]
    patch_identity: str = ""
    cleanup_passed: bool = False
    cleanup_attempted: bool = False
    commit_count: int = 0
    worktree_clean: bool = False
    changed_lines: int = 0
    syntax_diagnostic: str | None = None


@dataclass(frozen=True)
class _AttemptQuality:
    """Derived quality fields retained while the worktree is released."""

    aggregate: float
    minimum: float
    ruff_passed: bool
    mypy_passed: bool
    docstrings_passed: bool
    warnings: int
    targets: frozenset[str]


def _scope_rejected_attempt(
    proposal: ProposalManifest,
    reference: CodexReference,
    expected_identity: str,
) -> AttemptResult:
    """Return fail-closed evidence before creating an out-of-scope worktree."""
    edited_paths = [edit.path for edit in proposal.edits]
    aggregate, minimum, ruff_passed, mypy_passed, docstrings_passed = (
        quality_defaults_for_paths(
            edited_paths,
            aggregate=0.0,
            minimum=0.0,
            targets=set(),
        )
    )
    evidence = CandidateEvidence(
        changed_files=frozenset(edited_paths),
        tests_passed=False,
        warnings=0,
        coverage_aggregate=aggregate,
        coverage_min_file=minimum,
        ruff_passed=ruff_passed,
        mypy_passed=mypy_passed,
        docstrings_passed=docstrings_passed,
        markdown_passed=False,
        cleanup_passed=True,
        commit_count=0,
        worktree_clean=True,
        elapsed_seconds=0.0,
        changed_lines=0,
    )
    return AttemptResult(
        comparison=compare_with_codex(proposal, evidence, reference),
        evidence=evidence,
        patch_equivalence="scope-preflight-rejected",
        proposal=proposal,
        diagnostics=(
            "proposal changed paths outside the exact Codex reference: "
            + ", ".join(sorted(edit.path for edit in proposal.edits))
        ),
        attempt_identity_digest=expected_identity,
    )


def _apply_and_preflight_candidate(
    worktree: Path,
    proposal: ProposalManifest,
    state: _AttemptState,
    progress_sink: Callable[[str], None] | None,
) -> None:
    """Apply one proposal and record its parent-side syntax preflight."""
    apply_started = time.monotonic()
    try:
        state.changed_lines = apply_proposal(worktree, proposal)
    except BaseException:
        _record_evaluation_event(
            state.events,
            progress_sink,
            phase="apply",
            command_kind="filesystem_apply",
            command_identity="parent-atomic-proposal-apply-v1",
            returncode=1,
            elapsed_seconds=time.monotonic() - apply_started,
            failure_class="apply_failed",
        )
        raise
    _record_evaluation_event(
        state.events,
        progress_sink,
        phase="apply",
        command_kind="filesystem_apply",
        command_identity="parent-atomic-proposal-apply-v1",
        returncode=0,
        elapsed_seconds=time.monotonic() - apply_started,
        failure_class="apply_failed",
    )
    syntax_started = time.monotonic()
    try:
        state.syntax_diagnostic = _python_syntax_preflight(
            worktree,
            tuple(edit.path for edit in proposal.edits),
        )
    except BaseException:
        _record_evaluation_event(
            state.events,
            progress_sink,
            phase="syntax_preflight",
            command_kind="syntax_preflight",
            command_identity="parent-python-syntax-preflight-v2",
            returncode=1,
            elapsed_seconds=time.monotonic() - syntax_started,
            failure_class="python_syntax",
        )
        raise
    _record_evaluation_event(
        state.events,
        progress_sink,
        phase="syntax_preflight",
        command_kind="syntax_preflight",
        command_identity="parent-python-syntax-preflight-v2",
        returncode=0 if state.syntax_diagnostic is None else 2,
        elapsed_seconds=time.monotonic() - syntax_started,
        failure_class=_syntax_failure_class(state.syntax_diagnostic),
    )
    if state.syntax_diagnostic:
        state.results.append(
            MakeResult(
                ("parent-syntax-preflight",),
                2,
                "",
                state.syntax_diagnostic,
                0.0,
            )
        )


def _run_approved_candidate_commands(
    runner: _RuntimeMakeRunner,
    task: TaskSpec,
    proposal: ProposalManifest,
    state: _AttemptState,
    progress_sink: Callable[[str], None] | None,
) -> bool:
    """Run approved Make commands and the collection guard in order."""
    if state.syntax_diagnostic is None:
        commands = tuple(
            dict.fromkeys((*task.canonical_make_commands, *proposal.make_commands))
        )
        for command in commands:
            result = _run_evaluation_operation(
                partial(runner.run_command, command),
                state.events,
                progress_sink,
                phase="approved_make",
                command_kind="approved_make",
                command_identity=command,
                failure_class="make_failed",
            )
            state.results.append(result)
            if result.returncode != 0:
                break
    commands_green = bool(state.results) and all(
        item.returncode == 0 for item in state.results
    )
    if not commands_green:
        return False
    count = _run_evaluation_operation(
        lambda: runner.run_command("make test-count", timeout=600),
        state.events,
        progress_sink,
        phase="test_count",
        command_kind="approved_test_count",
        command_identity="make test-count",
        failure_class="test_count_failed",
    )
    state.results.append(count)
    return count.returncode == 0


def _inspect_committed_candidate(
    runner: _RuntimeMakeRunner,
    reference: CodexReference,
    branch: str,
    state: _AttemptState,
    progress_sink: Callable[[str], None] | None,
) -> None:
    """Record clean-tree and patch-equivalence evidence after one commit."""
    status_started = time.monotonic()
    try:
        status = runner.run("repo-status", read_only=True)
    except BaseException:
        _record_evaluation_event(
            state.events,
            progress_sink,
            phase="clean",
            command_kind="repository_clean",
            command_identity=_evaluation_target_identity("repo-status"),
            returncode=1,
            elapsed_seconds=time.monotonic() - status_started,
            failure_class="clean_failed",
        )
        raise
    state.results.append(status)
    state.worktree_clean = status.returncode == 0 and not status.stdout.strip()
    _record_evaluation_event(
        state.events,
        progress_sink,
        phase="clean",
        command_kind="repository_clean",
        command_identity=_evaluation_target_identity("repo-status"),
        returncode=status.returncode if state.worktree_clean else 1,
        elapsed_seconds=status.elapsed_seconds,
        failure_class="clean_failed",
    )
    variables = {
        "PATCH_UPSTREAM": reference.reference_sha,
        "PATCH_HEAD": branch,
        "PATCH_LIMIT": "1",
    }
    patch = _run_evaluation_operation(
        lambda: runner.run("git-patch-equivalence", variables, read_only=True),
        state.events,
        progress_sink,
        phase="patch_equivalence",
        command_kind="patch_equivalence",
        command_identity=_evaluation_target_identity("git-patch-equivalence", variables),
        failure_class="patch_equivalence_failed",
    )
    state.results.append(patch)
    state.patch_identity = patch.stdout.strip()


def _commit_candidate(
    runner: _RuntimeMakeRunner,
    proposal: ProposalManifest,
    reference: CodexReference,
    branch: str,
    state: _AttemptState,
    progress_sink: Callable[[str], None] | None,
) -> None:
    """Stage, commit, and inspect one command-green proposal."""
    stage_variables = {"FILES": " ".join(edit.path for edit in proposal.edits)}
    staged = _run_evaluation_operation(
        lambda: runner.run("git-add", stage_variables),
        state.events,
        progress_sink,
        phase="stage",
        command_kind="repository_stage",
        command_identity=_evaluation_target_identity("git-add", stage_variables),
        failure_class="stage_failed",
    )
    state.results.append(staged)
    commit_variables = {"MSG": proposal.commit_message}
    committed = _run_evaluation_operation(
        lambda: runner.run("repo-commit", commit_variables, timeout=300),
        state.events,
        progress_sink,
        phase="commit",
        command_kind="repository_commit",
        command_identity=_evaluation_target_identity("repo-commit", commit_variables),
        failure_class="commit_failed",
    )
    state.results.append(committed)
    if staged.returncode == 0 and committed.returncode == 0:
        state.commit_count = 1
        _inspect_committed_candidate(runner, reference, branch, state, progress_sink)


def _derive_attempt_quality(
    proposal: ProposalManifest,
    state: _AttemptState,
) -> _AttemptQuality:
    """Derive bounded coverage and static-analysis evidence from command output."""
    output = "\n".join(item.stdout + "\n" + item.stderr for item in state.results)
    try:
        aggregate, minimum = parse_coverage_evidence(output)
    except ValueError:
        aggregate, minimum = 0.0, 0.0
    targets = frozenset(
        item.argv[1]
        for item in state.results
        if item.returncode == 0 and len(item.argv) > 1
    )
    aggregate, minimum, ruff_passed, mypy_passed, docstrings_passed = (
        quality_defaults_for_paths(
            [edit.path for edit in proposal.edits],
            aggregate=aggregate,
            minimum=minimum,
            targets=set(targets),
        )
    )
    return _AttemptQuality(
        aggregate,
        minimum,
        ruff_passed,
        mypy_passed,
        docstrings_passed,
        _warning_count(output),
        targets,
    )


def _cleanup_candidate(
    root_runner: _TargetRunner,
    branch: str,
    state: _AttemptState,
    progress_sink: Callable[[str], None] | None,
) -> MakeResult:
    """Release one candidate worktree and record its terminal event."""
    state.cleanup_attempted = True
    variables = {"BRANCH": branch}
    cleanup = _run_evaluation_operation(
        lambda: root_runner.run("agent-cleanup", variables, timeout=180),
        state.events,
        progress_sink,
        phase="cleanup",
        command_kind="worktree_cleanup",
        command_identity=_evaluation_target_identity("agent-cleanup", variables),
        failure_class="cleanup_failed",
    )
    state.cleanup_passed = cleanup.returncode == 0
    return cleanup


def _finish_candidate_worktree(
    root_runner: _TargetRunner,
    branch: str,
    state: _AttemptState,
    progress_sink: Callable[[str], None] | None,
    *,
    merge: bool,
    commands_green: bool,
) -> None:
    """Optionally merge an eligible candidate, then always release its worktree."""
    if not (merge and commands_green and state.commit_count == 1 and state.worktree_clean):
        _cleanup_candidate(root_runner, branch, state, progress_sink)
        return
    variables = {"BRANCH": branch}
    merged = _run_evaluation_operation(
        lambda: root_runner.run("agent-merge-dev", variables, timeout=300),
        state.events,
        progress_sink,
        phase="merge",
        command_kind="repository_merge",
        command_identity=_evaluation_target_identity("agent-merge-dev", variables),
        failure_class="merge_failed",
    )
    cleanup = _cleanup_candidate(root_runner, branch, state, progress_sink)
    state.cleanup_passed = merged.returncode == 0 and cleanup.returncode == 0


def _build_attempt_result(
    proposal: ProposalManifest,
    reference: CodexReference,
    expected_identity: str,
    state: _AttemptState,
    quality: _AttemptQuality,
    commands_green: bool,
    progress_sink: Callable[[str], None] | None,
) -> AttemptResult:
    """Compare final evidence and bind one safe retry diagnosis if rejected."""
    evidence = CandidateEvidence(
        changed_files=frozenset(edit.path for edit in proposal.edits),
        tests_passed=commands_green,
        warnings=quality.warnings,
        coverage_aggregate=quality.aggregate,
        coverage_min_file=quality.minimum,
        ruff_passed=quality.ruff_passed,
        mypy_passed=quality.mypy_passed,
        docstrings_passed=quality.docstrings_passed,
        markdown_passed=(
            not any(edit.path.endswith((".md", ".mdx")) for edit in proposal.edits)
            or "lint-markdown" in quality.targets
        ),
        cleanup_passed=state.cleanup_passed,
        commit_count=state.commit_count,
        worktree_clean=state.worktree_clean,
        elapsed_seconds=time.monotonic() - state.started,
        changed_lines=state.changed_lines,
    )
    comparison = compare_with_codex(proposal, evidence, reference)
    failed_event = next(
        (event for event in state.events if event.failure_class != "none"),
        None,
    )
    if not comparison.accepted and failed_event is None:
        failed_event = _record_evaluation_event(
            state.events,
            progress_sink,
            phase="comparison",
            command_kind="quality_comparison",
            command_identity="codex-quality-comparison-v1",
            returncode=1,
            elapsed_seconds=time.monotonic() - state.started,
            failure_class="quality_rejected",
        )
    diagnostics = ""
    if failed_event is not None:
        diagnostics = _compact_evaluation_diagnosis(
            failed_event,
            syntax_diagnostic=(
                state.syntax_diagnostic
                if failed_event.phase == "syntax_preflight"
                else None
            ),
        )
    return AttemptResult(
        comparison=comparison,
        evidence=evidence,
        patch_equivalence=state.patch_identity,
        proposal=proposal,
        diagnostics=diagnostics,
        attempt_identity_digest=expected_identity,
    )


def evaluate_attempt(
    root_runner: _TargetRunner,
    task: TaskSpec,
    reference: CodexReference,
    bound_proposal: PlanBoundProposal,
    attempt: int,
    *,
    expected_attempt_identity_digest: str,
    merge: bool,
    make_runner_factory: _MakeRunnerFactory | None = None,
    progress_sink: Callable[[str], None] | None = None,
) -> AttemptResult:
    """Apply, test, commit, compare, and clean one local proposal."""
    expected_identity = _validate_attempt_identity_digest(
        expected_attempt_identity_digest
    )
    if bound_proposal.attempt_identity_digest != expected_identity:
        raise ValueError("proposal plan identity drifted before execution")
    proposal = bound_proposal.proposal
    if proposal.baseline_sha != reference.baseline_sha:
        raise ValueError("proposal baseline does not match the exact benchmark baseline")
    if proposal.task_id != task.task_id:
        raise ValueError("proposal task_id does not match the benchmark task")
    if not proposal_scope_matches(proposal, reference.changed_files):
        return _scope_rejected_attempt(proposal, reference, expected_identity)
    worktree, branch = create_worktree(root_runner, reference.baseline_sha, attempt)
    state = _AttemptState(time.monotonic(), [], [])
    runner_factory = make_runner_factory or MakeRunner
    try:
        runner = runner_factory(worktree)
        _apply_and_preflight_candidate(worktree, proposal, state, progress_sink)
        commands_green = _run_approved_candidate_commands(
            runner,
            task,
            proposal,
            state,
            progress_sink,
        )
        if commands_green:
            _commit_candidate(
                runner,
                proposal,
                reference,
                branch,
                state,
                progress_sink,
            )
        quality = _derive_attempt_quality(proposal, state)
        _finish_candidate_worktree(
            root_runner,
            branch,
            state,
            progress_sink,
            merge=merge,
            commands_green=commands_green,
        )
        return _build_attempt_result(
            proposal,
            reference,
            expected_identity,
            state,
            quality,
            commands_green,
            progress_sink,
        )
    except BaseException:
        if not state.cleanup_attempted:
            _cleanup_candidate(root_runner, branch, state, progress_sink)
        raise


def _default_runtime_outcome_adapter(cache_root: Path) -> ManagedOutcomeAdapter:
    """Build the canonical durable evidence adapter for one model cache."""
    store = CapabilityEvidenceStore(
        str(cache_root / ".gludd" / "capability-evidence.json")
    )
    return CapabilityEvidenceOutcomeAdapter(
        store,
        failure_loader=load_latest_failed_model_ids,
        outcome_recorder=record_self_improve_outcome,
    )


def _runtime_progress(message: str) -> None:
    """Publish one bounded managed-runner progress marker."""
    print(message, flush=True)


def _render_retry_diagnosis_event(diagnostics: str) -> str:
    """Render only allowlisted fields from the diagnosis consumed by a retry."""
    validated = safe_evaluation_retry_diagnosis(diagnostics)
    payload = cast(dict[str, object], json.loads(validated))
    path_sha256 = payload["path_sha256"] or "none"
    rendered = (
        "SELF_IMPROVE_RETRY_DIAGNOSIS "
        f"protocol={payload['protocol']} phase={payload['phase']} "
        f"failure={payload['failure_class']} rc={payload['exit_code']} "
        f"duration_ms={payload['duration_ms']} "
        f"command_sha256={payload['command_sha256']} "
        f"category={payload['category']} path_sha256={path_sha256} "
        f"line={payload['line']} column={payload['column']}"
    )
    if len(rendered.encode("ascii")) > EVALUATION_DIAGNOSIS_PROTOCOL.max_event_bytes:
        raise RuntimeError("retry diagnosis event exceeded its fixed byte bound")
    return rendered


class _RepositoryBoundManagedSelfImproveRunner(ManagedSelfImproveRunner):
    """Managed runner that rejects plans approved for a different repository."""

    _runtime_repo_root: Path

    def bind_repository(self, repo_root: Path) -> None:
        """Bind the service to its canonical construction-time repository."""
        self._runtime_repo_root = repo_root

    def run(self, plan: ApprovedSelfImprovePlan) -> ManagedRunResult:
        """Verify repository identity before entering the managed state machine."""
        if not isinstance(plan, ApprovedSelfImprovePlan):
            raise ValueError("plan must be an ApprovedSelfImprovePlan")
        if plan.repo_root != self._runtime_repo_root:
            raise ValueError("approved plan belongs to a different repository")
        return super().run(plan)


def _managed_comparison_retry_builder(
    progress_sink: Callable[[str], None],
) -> Callable[[PromptPlan, ComparisonResult, str], PromptPlan]:
    """Bind comparison retry construction to one progress sink."""

    def build(
        plan: PromptPlan,
        comparison: ComparisonResult,
        diagnostics: str,
    ) -> PromptPlan:
        retry = build_retry_prompt_plan(plan, comparison, diagnostics=diagnostics)
        if plan.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4:
            progress_sink(_render_retry_diagnosis_event(diagnostics))
        return retry

    return build


def _managed_syntax_retry_builder(
    progress_sink: Callable[[str], None],
) -> _SyntaxRepairBuilder:
    """Bind syntax repair construction to one progress sink."""

    def build(
        plan: PromptPlan,
        compact_proposals: tuple[CompactSpanProposal, ...],
        diagnostics: str,
    ) -> PromptPlan:
        repair = build_syntax_repair_prompt_plan(plan, compact_proposals, diagnostics)
        progress_sink(_render_retry_diagnosis_event(diagnostics))
        return repair

    return build


def build_managed_self_improve_runner(
    repo_root: Path,
    *,
    root_runner: _RuntimeMakeRunner | None = None,
    make_runner_factory: _MakeRunnerFactory | None = None,
    attempt_evaluator: _AttemptEvaluationAdapter | None = None,
    progress_sink: Callable[[str], None] | None = None,
    outcome_adapter_factory: _OutcomeAdapterFactory | None = None,
) -> ManagedSelfImproveRunner:
    """Compose the production managed service from installed package adapters.

    The returned service is repository-bound, uses Make-only execution adapters,
    never merges an evaluated attempt, and retains injectable progress and durable
    outcome seams for daemon integrations.
    """
    if not isinstance(repo_root, Path):
        raise ValueError("repo_root must be a pathlib.Path")
    canonical_root = repo_root.resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("repo_root must be an existing directory")
    runner_factory = make_runner_factory or MakeRunner
    operation_runner = root_runner or runner_factory(canonical_root)
    runtime_progress_sink = progress_sink or _runtime_progress

    def generate_managed_proposal(
        model_path: Path,
        prompt: PromptPlan | str,
        task: TaskSpec,
        reference: CodexReference,
    ) -> ProposalManifest | GeneratedProposal:
        if isinstance(prompt, PromptPlan):
            return _generate_local_proposal_plan_result(
                operation_runner,
                model_path,
                prompt,
                task,
                reference,
            )
        return generate_local_proposal(operation_runner, model_path, prompt)

    def evaluate_managed_proposal(
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        if merge:
            raise ValueError("managed self-improvement cannot merge a live branch")
        if attempt_evaluator is not None:
            return attempt_evaluator(
                operation_runner,
                task,
                reference,
                bound_proposal,
                attempt,
                expected_attempt_identity_digest=expected_attempt_identity_digest,
                merge=False,
            )
        return evaluate_attempt(
            operation_runner,
            task,
            reference,
            bound_proposal,
            attempt,
            expected_attempt_identity_digest=expected_attempt_identity_digest,
            merge=False,
            make_runner_factory=runner_factory,
            progress_sink=runtime_progress_sink,
        )

    service = _RepositoryBoundManagedSelfImproveRunner(
        proposal_generator=generate_managed_proposal,
        attempt_evaluator=evaluate_managed_proposal,
        model_manager_factory=ModelLeaseManager,
        outcome_adapter_factory=(
            outcome_adapter_factory or _default_runtime_outcome_adapter
        ),
        candidate_planner=plan_model_candidates,
        hardware_probe=unified_probe,
        artifact_identity=_planned_artifact_identity,
        acquisition_event_sink=_report_model_acquisition_event,
        resolution_failure_sink=_report_model_resolution_failure,
        release_sink=_report_model_release,
        progress_sink=runtime_progress_sink,
        model_acquisition_error=ModelAcquisitionError,
        comparison_retry_builder=_managed_comparison_retry_builder(
            runtime_progress_sink
        ),
        validation_retry_builder=_build_validation_retry_prompt_plan,
        syntax_repair_builder=_managed_syntax_retry_builder(runtime_progress_sink),
    )
    service.bind_repository(canonical_root)
    return service


def prepare_managed_self_improve_plan(
    repo_root: Path,
    *,
    approval_id: str,
    todo_id: str,
    project_id: str,
    repository_binding_digest: str = "",
    baseline_ref: str,
    reference_ref: str,
    task: TaskSpec,
    max_attempts: int,
    explicit_model_path: Path | None = None,
    output_token_limit: int | None = None,
    root_runner: _RuntimeMakeRunner | None = None,
    make_runner_factory: _MakeRunnerFactory | None = None,
) -> ApprovedSelfImprovePlan:
    """Prepare one immutable, repository-bound plan without inference or merge.

    Reference inspection, baseline context acquisition, prompt construction, and
    an optional mature mechanical repair all remain Make-mediated. The temporary
    context worktree is released before the approval artifact crosses this API.
    """
    if not isinstance(repo_root, Path):
        raise ValueError("repo_root must be a pathlib.Path")
    if not isinstance(task, TaskSpec):
        raise ValueError("task must be a TaskSpec")
    canonical_root = repo_root.resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("repo_root must be an existing directory")
    runner_factory = make_runner_factory or MakeRunner
    operation_runner = root_runner or runner_factory(canonical_root)
    reference = build_reference(
        operation_runner,
        baseline_ref,
        reference_ref,
        task.reference_elapsed_seconds,
    )
    required_output_tokens = estimate_required_output_tokens(
        reference.changed_lines,
        len(reference.changed_files),
    )
    if output_token_limit is not None:
        if (
            isinstance(output_token_limit, bool)
            or not isinstance(output_token_limit, int)
            or output_token_limit <= 0
        ):
            raise ValueError("output_token_limit must be a positive integer")
        if required_output_tokens > output_token_limit:
            raise ValueError(
                "Codex reference exceeds the local decode budget: "
                f"estimated={required_output_tokens} available={output_token_limit}; "
                "select a larger local model/context or a smaller atomic task"
            )
    context_root, context_branch = create_worktree(
        operation_runner,
        reference.baseline_sha,
        0,
    )
    try:
        prompt = build_prompt(task, reference, context_root)
        mechanical_proposal = generate_mechanical_proposal(
            runner_factory(context_root),
            task,
            reference,
            context_root,
        )
    finally:
        context_cleanup = operation_runner.run(
            "agent-cleanup",
            {"BRANCH": context_branch},
            timeout=180,
        )
        if context_cleanup.returncode != 0:
            raise RuntimeError(
                "baseline context worktree cleanup failed: "
                + (context_cleanup.stderr or context_cleanup.stdout)
            )

    plan = ApprovedSelfImprovePlan.approve(
        approval_id=approval_id,
        todo_id=todo_id,
        project_id=project_id,
        repo_root=canonical_root,
        repository_binding_digest=repository_binding_digest,
        task=task,
        reference=reference,
        prompt=prompt,
        required_output_tokens=required_output_tokens,
        max_attempts=max_attempts,
        explicit_model_path=explicit_model_path,
        mechanical_proposal=mechanical_proposal,
    )
    return ApprovedSelfImprovePlan.from_json(plan.to_json())


def _validate_only_benchmark(
    args: argparse.Namespace,
    root_runner: _TargetRunner,
    task: TaskSpec,
) -> AttemptResult:
    """Render and return the historical side-effect-free benchmark plan."""
    reference = build_reference(
        root_runner,
        args.baseline_ref,
        args.reference_ref,
        task.reference_elapsed_seconds,
    )
    required_output_tokens = estimate_required_output_tokens(
        reference.changed_lines,
        len(reference.changed_files),
    )
    print(
        "SELF_IMPROVE_CODEX_PLAN "
        f"task={task.task_id} baseline={reference.baseline_sha} "
        f"reference={reference.reference_sha} files={len(reference.changed_files)} "
        f"tests={len(reference.test_files)} "
        f"estimated_output_tokens={required_output_tokens} "
        f"model={Path(args.local_model_path) if args.local_model_path else 'auto'}"
    )
    proposal = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": reference.baseline_sha,
                "task_id": task.task_id,
                "edits": [
                    {
                        "operation": "create",
                        "path": sorted(reference.changed_files)[0],
                        "old_text": "",
                        "new_text": "validate-only",
                    }
                ],
                "tests": sorted(reference.test_files)
                or ["tests/unit/test_placeholder.py"],
                "make_commands": list(task.canonical_make_commands),
                "commit_message": "test: validate self-improvement plan",
            }
        )
    )
    evidence = CandidateEvidence(
        changed_files=frozenset(),
        tests_passed=False,
        warnings=0,
        coverage_aggregate=0.0,
        coverage_min_file=0.0,
        ruff_passed=False,
        mypy_passed=False,
        docstrings_passed=False,
        markdown_passed=False,
        cleanup_passed=True,
        commit_count=0,
        worktree_clean=True,
        elapsed_seconds=0.0,
    )
    return AttemptResult(
        comparison=ComparisonResult(
            accepted=False,
            score=0.0,
            blockers=("validate-only",),
            changed_file_precision=0.0,
            changed_file_recall=0.0,
        ),
        evidence=evidence,
        patch_equivalence="validate-only",
        proposal=proposal,
        diagnostics="validate-only",
        attempt_identity_digest=_attempt_identity_digest(proposal.to_json()),
    )


def run_benchmark(args: argparse.Namespace) -> AttemptResult:
    """Run bounded local attempts until Codex parity or the attempt limit."""
    root = Path(__file__).resolve().parents[3]
    root_runner = MakeRunner(root)
    task = TaskSpec.from_path(Path(args.task_file))
    if not args.local_model_path and not map_task_to_capabilities(task.objective):
        raise ValueError(
            "automatic local model task must match a mapped coding capability"
        )
    if args.validate_only:
        return _validate_only_benchmark(args, root_runner, task)

    explicit_model_path = (
        Path(args.local_model_path).expanduser() if args.local_model_path else None
    )
    approved_plan = prepare_managed_self_improve_plan(
        root,
        approval_id=f"cli:{args.target}:{args.reference_ref}",
        todo_id=task.task_id,
        project_id="cli-self-improve",
        baseline_ref=args.baseline_ref,
        reference_ref=args.reference_ref,
        task=task,
        max_attempts=args.max_attempts,
        explicit_model_path=explicit_model_path,
        output_token_limit=4096,
        root_runner=root_runner,
        make_runner_factory=MakeRunner,
    )
    reference = approved_plan.reference
    if isinstance(approved_plan.prompt, PromptPlan):
        print(
            "SELF_IMPROVE_PROMPT_PLAN "
            f"shards={len(approved_plan.prompt.shards)} "
            f"source_bytes={approved_plan.prompt.source_bytes} "
            f"protocol_digest={approved_plan.prompt.protocol_digest} "
            f"attempt_identity_digest={approved_plan.attempt_identity_digest} "
            f"max_prompt_bytes={approved_plan.prompt.max_prompt_bytes} "
            f"paths={json.dumps(sorted(reference.changed_files))}",
            flush=True,
        )
    service = build_managed_self_improve_runner(
        root,
        root_runner=root_runner,
        make_runner_factory=MakeRunner,
    )
    try:
        return service.run(approved_plan).final_result
    except (OSError, RuntimeError, ValueError) as exc:
        proposal_protocol = (
            approved_plan.prompt.proposal_protocol
            if isinstance(approved_plan.prompt, PromptPlan)
            else COMPACT_PROPOSAL_PROTOCOL_V3
        )
        _bind_failure_protocol(exc, proposal_protocol)
        raise


def _clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    return environment





def _validate_target_and_variables(target: str, variables: dict[str, str]) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target):
        raise ValueError(f"unsafe Make target: {target}")
    for key, value in variables.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"unsafe Make variable: {key}")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"unsafe value for Make variable: {key}")


def _validate_sha(label: str, value: str) -> None:
    if not _SHA_RE.fullmatch(value):
        raise ValueError(f"{label} must be exactly 40 lowercase hex characters")


def _line_count_from_patch(patch: str) -> int:
    return sum(
        1
        for line in patch.splitlines()
        if line.startswith(("+", "-"))
        and not line.startswith(("+++", "---"))
    )


def _warning_count(output: str) -> int:
    return sum(
        1
        for line in output.splitlines()
        if re.search(r"\bwarning(?:s)?\b", line, flags=re.IGNORECASE)
        and not re.search(r"\b0\s+warnings?\b", line, flags=re.IGNORECASE)
    )


def _terminate_process_group(proc: _OwnedProcessGroup) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    proc.wait(timeout=5)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark local self-improvement against a Codex reference patch"
    )
    parser.add_argument("--target", required=True, help="Stable benchmark target label")
    parser.add_argument(
        "--local-model-path",
        default="",
        help="Optional operator GGUF override; managed acquisition is the default",
    )
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--reference-ref", required=True)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--max-attempts", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main() -> int:
    """Run the local-versus-Codex benchmark and publish bounded JSON evidence."""
    args = _parser().parse_args()
    try:
        result = run_benchmark(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            f"SELF_IMPROVE_ERROR {_public_failure_feedback(exc)}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    payload = {
        "target": args.target,
        "accepted": result.comparison.accepted,
        "attempt_identity_digest": result.attempt_identity_digest,
        "comparison": asdict(result.comparison),
        "evidence": {
            **asdict(result.evidence),
            "changed_files": sorted(result.evidence.changed_files),
        },
        "patch_equivalence": result.patch_equivalence,
        "proposal": {
            "task_id": result.proposal.task_id,
            "baseline_sha": result.proposal.baseline_sha,
            "changed_files": [edit.path for edit in result.proposal.edits],
            "tests": list(result.proposal.tests),
            "make_commands": list(result.proposal.make_commands),
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return int(not args.validate_only and not result.comparison.accepted)


if __name__ == "__main__":
    raise SystemExit(main())
