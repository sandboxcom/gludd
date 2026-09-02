"""Concrete installed-package adapters for managed self-improvement."""

from __future__ import annotations

import argparse
import difflib
import hashlib
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
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Protocol, TextIO, cast

from general_ludd.hardware.model_fit import unified_probe
from general_ludd.local_model import LocalModelConfig
from general_ludd.planning.repo_map import RepoMapBuilder
from general_ludd.self_improve.codex_comparison import (
    LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalContract,
    ProposalManifest,
    bind_compact_focus_path,
    compare_with_codex,
    decode_proposal_batch,
    encode_prompt_batch,
    merge_proposal_manifests,
)
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    CapabilityEvidenceOutcomeAdapter,
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
_MAX_FAILURE_DIAGNOSIS_TRACE_BYTES: Final = 131_072
_MAX_FAILURE_DIAGNOSIS_HYPOTHESIS_BYTES: Final = 160
_PROMPT_CONTEXT_LINES: Final = 5
_HEARTBEAT_SECONDS: Final = 15.0
_FORBIDDEN_COMMAND_CHARS: Final = frozenset(";|&$()<>\n\r")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPT_IDENTITY_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_TASK_RE: Final = re.compile(r"^S[0-9]+(?:\.[0-9]+)?$")
_WORD_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_DIAGNOSIS_PHASE_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_[A-Z_]+\b[^\r\n]*\bphase=([a-z][a-z0-9_-]{0,63})\b"
)
_DIAGNOSIS_FAILURE_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_[A-Z_]+\b[^\r\n]*\bfailure=([a-z][a-z0-9_-]{0,63})\b"
)
_DIAGNOSIS_FINISH_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_LOCAL_DECODE\b[^\r\n]*\b"
    r"finish=(stop|length|tool_calls|function_call|content_filter|unknown)\b"
)
_DIAGNOSIS_EXIT_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_COMMAND_END\b[^\r\n]*\brc=(-?[0-9]{1,3})\b"
)
_DIAGNOSIS_SECRET_RE: Final = re.compile(
    r"(?i)(?:api[_-]?key|authorization|password|secret|token)\s*[:=]|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----"
)
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


class _CommandRunner(Protocol):
    """Make-only tool execution boundary used by mechanical repair routing."""

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        """Run one observable Make command."""


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


class _RuntimeMakeRunner(_ObservableRunner, _CommandRunner, _TargetRunner, Protocol):
    """Complete Make-mediated boundary used by the production composition root."""


class _MakeRunnerFactory(Protocol):
    """Construct a repository-bound Make operation adapter."""

    def __call__(self, repo_root: Path) -> _RuntimeMakeRunner:
        """Return a runner bound to one canonical repository root."""


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
        result = runner.run_observable(
            "self-improve-local-proposal",
            {
                "SELF_IMPROVE_MODEL_PATH": str(model_path),
                "SELF_IMPROVE_PROMPT_FILE": str(prompt_path),
                "SELF_IMPROVE_PROPOSAL_FILE": str(proposal_path),
            },
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


def generate_local_proposal_plan(
    runner: _ObservableRunner,
    model_path: Path,
    plan: PromptPlan,
    task: TaskSpec,
    reference: CodexReference,
) -> ProposalManifest:
    """Decode all shards in one retained worker, then strictly merge them."""
    request = encode_prompt_batch(
        tuple(shard.prompt for shard in plan.shards),
        protocol_digest=plan.protocol_digest,
    )
    required_tests = _required_prompt_tests(task, reference)
    contract = ProposalContract(
        baseline_sha=reference.baseline_sha,
        task_id=task.task_id,
        tests=required_tests,
        make_commands=task.canonical_make_commands,
    )
    proposals = decode_proposal_batch(
        _run_local_proposal_request(
            runner,
            model_path,
            request,
            contract=contract,
        ),
        expected_protocol_digest=plan.protocol_digest,
        expected_count=len(plan.shards),
    )
    return merge_proposal_manifests(
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





def _public_failure_feedback(exc: BaseException) -> str:
    """Return only a typed, bounded, model-text-free public failure marker."""
    typed_failure: str | None = None
    source = "runner"
    if isinstance(exc, ModelPlanError):
        typed_failure = exc.failure.value
    elif isinstance(exc, ModelAcquisitionError):
        typed_failure = exc.failure.value
        source = "model_lifecycle"
    if typed_failure is None:
        return _validation_retry_feedback(str(exc))

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


def build_failure_diagnostic(results: list[MakeResult]) -> str:
    """Return the first failed Make command and its bounded output tail."""
    for result in results:
        if result.returncode == 0:
            continue
        output = (result.stdout + "\n" + result.stderr).replace("\x00", "")
        tail = output.encode("utf-8")[-4096:].decode("utf-8", errors="replace")
        return (
            f"command={shlex.join(result.argv)} rc={result.returncode}\n"
            f"{tail}"
        )
    return ""


def compact_failure_diagnosis(
    trace: str,
    *,
    hypothesis: str,
    max_bytes: int = 512,
    max_tokens: int = 512,
) -> str:
    """Convert a marker-bearing failure trace to bounded canonical JSON.

    Only allowlisted execution facts are copied from the trace.  The output is
    ASCII JSON, so its byte length is also a conservative upper bound for a
    byte-fallback tokenizer's token count.
    """
    if not isinstance(trace, str) or not trace.strip():
        raise ValueError("failure trace must be a non-empty string")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("failure hypothesis must be a non-empty string")
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
        or isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens <= 0
    ):
        raise ValueError("diagnosis byte and token limits must be positive integers")

    normalized_hypothesis = " ".join(hypothesis.split())
    if (
        len(normalized_hypothesis.encode("utf-8"))
        > _MAX_FAILURE_DIAGNOSIS_HYPOTHESIS_BYTES
    ):
        raise ValueError("failure hypothesis exceeds its byte bound")
    if _DIAGNOSIS_SECRET_RE.search(normalized_hypothesis):
        raise ValueError("failure hypothesis contains secret-like material")

    bounded_trace = _failure_diagnosis_trace_view(trace)
    phase = _last_diagnosis_fact(_DIAGNOSIS_PHASE_RE, bounded_trace, "phase")
    failure = _last_diagnosis_fact(
        _DIAGNOSIS_FAILURE_RE, bounded_trace, "failure class"
    )
    finish = _last_diagnosis_fact(
        _DIAGNOSIS_FINISH_RE, bounded_trace, "finish reason"
    )
    exit_text = _last_diagnosis_fact(_DIAGNOSIS_EXIT_RE, bounded_trace, "exit code")
    exit_code = int(exit_text)
    if not -255 <= exit_code <= 255:
        raise ValueError("failure diagnosis exit code is outside the bounded range")

    artifact = json.dumps(
        {
            "exit_code": exit_code,
            "failure_class": failure,
            "finish_reason": finish,
            "finished": True,
            "hypothesis": normalized_hypothesis,
            "phase": phase,
            "schema_version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    artifact_bytes = len(artifact.encode("ascii"))
    if artifact_bytes > max_bytes:
        raise ValueError("failure diagnosis exceeds its byte budget")
    if artifact_bytes > max_tokens:
        raise ValueError("failure diagnosis exceeds its conservative token budget")
    return artifact


def _failure_diagnosis_trace_view(trace: str) -> str:
    """Retain only bounded head and tail windows for marker extraction."""
    encoded = trace.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_FAILURE_DIAGNOSIS_TRACE_BYTES:
        return encoded.decode("utf-8", errors="replace")
    half = _MAX_FAILURE_DIAGNOSIS_TRACE_BYTES // 2
    head = encoded[:half].decode("utf-8", errors="replace")
    tail = encoded[-half:].decode("utf-8", errors="replace")
    return f"{head}\n{tail}"


def _last_diagnosis_fact(pattern: re.Pattern[str], trace: str, label: str) -> str:
    """Return the final allowlisted fact for one required trace field."""
    matches = tuple(pattern.finditer(trace))
    if not matches:
        raise ValueError(f"failure trace is missing {label}")
    return matches[-1].group(1)


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
        sections.append(
            f"LINES {start + 1}-{end}\n" + "".join(lines[start:end])
        )
    return "\n".join(sections)


def _select_python_excerpt(
    relative: str,
    content: str,
    terms: frozenset[str],
    *,
    budget: int,
) -> tuple[str, int]:
    lines = content.splitlines(keepends=True)
    if not lines:
        return "", 0
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
    return _render_selected_lines(lines, selected), len(selected)


def _build_file_context(
    baseline_root: Path,
    relative: str,
    task: TaskSpec,
) -> tuple[str, int, str | None]:
    path = baseline_root / relative
    if path.is_symlink():
        raise ValueError(f"baseline context path must not be a symlink: {relative}")
    if not path.exists():
        return f"FILE {relative} state=absent bytes=0 sha256=none", 0, None
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
        excerpt = content
        selected_lines = len(lines)
        complete = True
    elif relative.endswith(".py"):
        excerpt, selected_lines = _select_python_excerpt(
            relative,
            content,
            _relevance_terms(task, relative),
            budget=_MAX_FILE_EXCERPT_BYTES,
        )
        complete = False
    else:
        excerpt = "".join(lines[: min(len(lines), 40)])
        if len(excerpt.encode("utf-8")) > _MAX_FILE_EXCERPT_BYTES:
            excerpt = ""
        selected_lines = min(len(lines), 40) if excerpt else 0
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
            "do not invent old_text outside the exact numbered excerpts."
        )
    return f"{marker}\n{excerpt}", len(raw), content


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
    reference: CodexReference,
    required_tests: tuple[str, ...],
    focus_paths: tuple[str, ...],
    contexts: dict[str, str],
    *,
    shard_index: int,
    shard_total: int,
) -> str:
    if len(focus_paths) != 1:
        raise ValueError("compact prompt shards must have exactly one focus path")
    body = (
        "Produce one shard of a Codex-quality repository patch proposal. You have "
        "no shell, Git, or tool authority. Return one compact JSON object and no "
        "prose: its only root key is e, whose array items have only string keys a and "
        "z. The separately supplied JSON grammar "
        "is authoritative. The parent supplies path, operation, commit message, tests, "
        "and commands; never emit those fields. For replace, copy the shortest unique "
        "old text verbatim and make old/new distinct. Never regenerate a whole existing "
        "file. Keep all edit text within 3,072 UTF-8 bytes total. Do not invent old_text "
        "from explicitly omitted content. Use empty old text only for an absent focus "
        "path and empty new text only when deleting a complete shown file.\n"
        f"Task: {task.objective}\n"
        f"Baseline: {reference.baseline_sha}\n"
        f"Task ID: {task.task_id}\n"
        "Global immutable Codex reference paths:\n"
        + "\n".join(sorted(reference.changed_files))
        + "\nRequired test paths (copy all exactly):\n"
        + "\n".join(required_tests)
        + "\nRequired canonical evidence commands (copy exactly and in order):\n"
        + "\n".join(task.canonical_make_commands)
        + "\nShard-specific contract:\n"
        f"Shard: {shard_index}/{shard_total}\n"
        "Exact focus paths for this shard:\nEdit every and only these paths:\n"
        + "\n".join(focus_paths)
        + "\nExact baseline contexts for the focus paths:\n"
        + "\n\n".join(contexts[path] for path in focus_paths)
    )
    return bind_compact_focus_path(body, focus_paths[0])


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
    required_tests = _required_prompt_tests(task, reference)
    contexts: dict[str, str] = {}
    baseline_files: list[tuple[str, str | None]] = []
    source_bytes = 0
    for relative in paths:
        context, size, baseline_text = _build_file_context(
            baseline_root,
            relative,
            task,
        )
        contexts[relative] = context
        baseline_files.append((relative, baseline_text))
        source_bytes += size

    groups = [(relative,) for relative in paths]
    for relative, group in zip(paths, groups, strict=True):
        single = _render_prompt_shard(
            task,
            reference,
            required_tests,
            group,
            contexts,
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
                reference,
                required_tests,
                group,
                contexts,
                shard_index=index,
                shard_total=len(groups),
            ),
        )
        for index, group in enumerate(groups, start=1)
    )
    return PromptPlan(
        shards=shards,
        source_bytes=source_bytes,
        baseline_files=tuple(baseline_files),
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
    worktree, branch = create_worktree(root_runner, reference.baseline_sha, attempt)
    runner_factory = make_runner_factory or MakeRunner
    runner = runner_factory(worktree)
    started = time.monotonic()
    results: list[MakeResult] = []
    patch_identity = ""
    cleanup_passed = False
    commit_count = 0
    worktree_clean = False
    changed_lines = 0
    try:
        changed_lines = apply_proposal(worktree, proposal)
        commands = tuple(
            dict.fromkeys((*task.canonical_make_commands, *proposal.make_commands))
        )
        for command in commands:
            result = runner.run_command(command)
            results.append(result)
            if result.returncode != 0:
                break

        commands_green = bool(results) and all(item.returncode == 0 for item in results)
        if commands_green:
            count = runner.run_command("make test-count", timeout=600)
            results.append(count)
            commands_green = count.returncode == 0
        if commands_green:
            changed = " ".join(edit.path for edit in proposal.edits)
            staged = runner.run("git-add", {"FILES": changed})
            results.append(staged)
            committed = runner.run("repo-commit", {"MSG": proposal.commit_message}, timeout=300)
            results.append(committed)
            if staged.returncode == 0 and committed.returncode == 0:
                commit_count = 1
                status = runner.run("repo-status", read_only=True)
                results.append(status)
                worktree_clean = status.returncode == 0 and not status.stdout.strip()
                patch = runner.run(
                    "git-patch-equivalence",
                    {
                        "PATCH_UPSTREAM": reference.reference_sha,
                        "PATCH_HEAD": branch,
                        "PATCH_LIMIT": "1",
                    },
                    read_only=True,
                )
                results.append(patch)
                patch_identity = patch.stdout.strip()

        output = "\n".join(item.stdout + "\n" + item.stderr for item in results)
        try:
            aggregate, minimum = parse_coverage_evidence(output)
        except ValueError:
            aggregate, minimum = 0.0, 0.0
        targets = {
            item.argv[1]
            for item in results
            if item.returncode == 0 and len(item.argv) > 1
        }
        warning_count = _warning_count(output)
        edited_paths = [edit.path for edit in proposal.edits]
        aggregate, minimum, ruff_passed, mypy_passed, docstrings_passed = (
            quality_defaults_for_paths(
                edited_paths,
                aggregate=aggregate,
                minimum=minimum,
                targets=targets,
            )
        )
        if merge and commands_green and commit_count == 1 and worktree_clean:
            merged = root_runner.run("agent-merge-dev", {"BRANCH": branch}, timeout=300)
            cleanup = root_runner.run("agent-cleanup", {"BRANCH": branch}, timeout=180)
            cleanup_passed = merged.returncode == 0 and cleanup.returncode == 0
        else:
            cleanup = root_runner.run("agent-cleanup", {"BRANCH": branch}, timeout=180)
            cleanup_passed = cleanup.returncode == 0

        evidence = CandidateEvidence(
            changed_files=frozenset(edit.path for edit in proposal.edits),
            tests_passed=commands_green,
            warnings=warning_count,
            coverage_aggregate=aggregate,
            coverage_min_file=minimum,
            ruff_passed=ruff_passed,
            mypy_passed=mypy_passed,
            docstrings_passed=docstrings_passed,
            markdown_passed=(
                not any(edit.path.endswith((".md", ".mdx")) for edit in proposal.edits)
                or "lint-markdown" in targets
            ),
            cleanup_passed=cleanup_passed,
            commit_count=commit_count,
            worktree_clean=worktree_clean,
            elapsed_seconds=time.monotonic() - started,
            changed_lines=changed_lines,
        )
        return AttemptResult(
            comparison=compare_with_codex(proposal, evidence, reference),
            evidence=evidence,
            patch_equivalence=patch_identity,
            proposal=proposal,
            diagnostics=build_failure_diagnostic(results),
            attempt_identity_digest=expected_identity,
        )
    except BaseException:
        if not cleanup_passed:
            root_runner.run("agent-cleanup", {"BRANCH": branch}, timeout=180)
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
    runner_factory = make_runner_factory or MakeRunner
    operation_runner = root_runner or runner_factory(canonical_root)

    def generate_managed_proposal(
        model_path: Path,
        prompt: PromptPlan | str,
        task: TaskSpec,
        reference: CodexReference,
    ) -> ProposalManifest:
        if isinstance(prompt, PromptPlan):
            return generate_local_proposal_plan(
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
        progress_sink=progress_sink or _runtime_progress,
        model_acquisition_error=ModelAcquisitionError,
        comparison_retry_builder=lambda plan, comparison, diagnostics: (
            build_retry_prompt_plan(
                plan,
                comparison,
                diagnostics=diagnostics,
            )
        ),
        validation_retry_builder=_build_validation_retry_prompt_plan,
    )
    service.bind_repository(canonical_root)
    return service


def prepare_managed_self_improve_plan(
    repo_root: Path,
    *,
    approval_id: str,
    todo_id: str,
    project_id: str,
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
        task=task,
        reference=reference,
        prompt=prompt,
        required_output_tokens=required_output_tokens,
        max_attempts=max_attempts,
        explicit_model_path=explicit_model_path,
        mechanical_proposal=mechanical_proposal,
    )
    return ApprovedSelfImprovePlan.from_json(plan.to_json())


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
        validation_proposal = ProposalManifest.from_json(
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
        return AttemptResult(
            comparison=ComparisonResult(
                accepted=False,
                score=0.0,
                blockers=("validate-only",),
                changed_file_precision=0.0,
                changed_file_recall=0.0,
            ),
            evidence=CandidateEvidence(
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
            ),
            patch_equivalence="validate-only",
            proposal=validation_proposal,
            diagnostics="validate-only",
            attempt_identity_digest=_attempt_identity_digest(
                validation_proposal.to_json()
            ),
        )

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
        attempt_evaluator=evaluate_attempt,
    )
    return service.run(approved_plan).final_result


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
