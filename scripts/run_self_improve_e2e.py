#!/usr/bin/env python3
"""Compare bounded local-model changes with an independent Codex reference."""

from __future__ import annotations

import argparse
import difflib
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Protocol, TextIO, cast

from general_ludd.hardware.model_fit import unified_probe
from general_ludd.local_model import LocalModelConfig
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
    build_retry_prompt,
    compare_with_codex,
)
from general_ludd.self_improve.model_candidate_planner import (
    PlannedModelCandidate,
    load_latest_failed_model_ids,
    plan_model_candidates,
    record_self_improve_outcome,
)
from general_ludd.self_improve.model_lifecycle import (
    AcquiredModel,
    ModelAcquisitionEvent,
    ModelLeaseManager,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.recommender import map_task_to_capabilities

_MAX_CAPTURE_BYTES: Final = 2_097_152
_MAX_TASK_BYTES: Final = 262_144
_MAX_PROPOSAL_BYTES: Final = 1_310_720
_MAX_REFERENCE_FILES: Final = 128
_HEARTBEAT_SECONDS: Final = 15.0
_FORBIDDEN_COMMAND_CHARS: Final = frozenset(";|&$()<>\n\r")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_TASK_RE: Final = re.compile(r"^S[0-9]+(?:\.[0-9]+)?$")


def _report_model_resolution_failure(
    model: LocalModelConfig,
    reason: str,
) -> None:
    print(
        "SELF_IMPROVE_MODEL_UNAVAILABLE "
        f"model={model.name} error={json.dumps(reason[:1000])}",
        flush=True,
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
class TaskSpec:
    """Deterministic benchmark task and canonical quality commands."""

    task_id: str
    objective: str
    canonical_make_commands: tuple[str, ...]
    reference_elapsed_seconds: float = 0.0

    @classmethod
    def from_path(cls, path: Path) -> TaskSpec:
        """Load one strict, bounded JSON benchmark task."""
        if not path.is_file():
            raise FileNotFoundError(f"self-improvement task is not readable: {path}")
        if path.stat().st_size > _MAX_TASK_BYTES:
            raise ValueError(f"task exceeds {_MAX_TASK_BYTES} bytes")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"task is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("task must be a JSON object")
        allowed = {
            "task_id",
            "objective",
            "canonical_make_commands",
            "reference_elapsed_seconds",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"task has unknown fields: {sorted(unknown)}")
        missing = {"task_id", "objective", "canonical_make_commands"} - set(value)
        if missing:
            raise ValueError(f"task is missing fields: {sorted(missing)}")

        task_id = value["task_id"]
        objective = value["objective"]
        commands = value["canonical_make_commands"]
        elapsed = value.get("reference_elapsed_seconds", 0.0)
        if not isinstance(task_id, str) or not _TASK_RE.fullmatch(task_id):
            raise ValueError("task_id must use the canonical S<number>[.<number>] form")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be non-empty text")
        if len(objective.encode("utf-8")) > 65_536:
            raise ValueError("objective exceeds 65536 bytes")
        if not isinstance(commands, list) or not commands or len(commands) > 32:
            raise ValueError("canonical_make_commands must contain 1..32 entries")
        parsed_commands: list[str] = []
        for command in commands:
            if not isinstance(command, str) or not _is_safe_make_command(command):
                raise ValueError("every canonical step must be one bounded make command")
            parsed_commands.append(command)
        if not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise ValueError("reference_elapsed_seconds must be non-negative")
        return cls(
            task_id=task_id,
            objective=objective.strip(),
            canonical_make_commands=tuple(parsed_commands),
            reference_elapsed_seconds=float(elapsed),
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
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        captured: list[str] = []
        captured_bytes = 0
        next_heartbeat = time.monotonic() + _HEARTBEAT_SECONDS
        timed_out = False
        try:
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
        finally:
            selector.close()
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


def generate_local_proposal(
    runner: _ObservableRunner,
    model_path: Path,
    prompt: str,
) -> ProposalManifest:
    """Generate one proposal through an isolated, parent-owned Make worker."""
    if not model_path.is_file():
        raise FileNotFoundError(f"local GGUF is not readable: {model_path}")
    if not prompt.strip() or len(prompt.encode("utf-8")) > _MAX_TASK_BYTES:
        raise ValueError(f"proposal prompt must contain 1..{_MAX_TASK_BYTES} bytes")

    with tempfile.TemporaryDirectory(prefix="gludd-self-improve-proposal-") as raw_exchange:
        exchange = Path(raw_exchange)
        prompt_path = exchange / "prompt.txt"
        proposal_path = exchange / "proposal.json"
        temporary = _write_atomic_temp(
            prompt_path,
            prompt,
            0o600,
            ".prompt-tmp",
        )
        os.replace(temporary, prompt_path)
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
            raise RuntimeError("local proposal worker did not publish one bounded regular file")
        try:
            proposal_text = proposal_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"local proposal output is not readable UTF-8: {exc}") from exc
        return ProposalManifest.from_json(proposal_text)


@dataclass(frozen=True)
class AttemptResult:
    """Final evidence, comparison, and patch identity for one local attempt."""

    comparison: ComparisonResult
    evidence: CandidateEvidence
    patch_equivalence: str
    proposal: ProposalManifest
    diagnostics: str


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


def apply_proposal(repo_root: Path, proposal: ProposalManifest) -> int:
    """Transactionally apply confined exact patches and return changed line count."""
    proposal.validate_paths(repo_root)
    originals: dict[Path, tuple[bool, str, int]] = {}
    planned: dict[Path, tuple[bool, str]] = {}
    for edit in proposal.edits:
        destination = repo_root / edit.path
        if destination.is_symlink():
            raise ValueError(f"proposal path must not be a symlink: {edit.path}")
        if destination not in originals:
            exists = destination.is_file()
            before = destination.read_text(encoding="utf-8") if exists else ""
            mode = destination.stat().st_mode if exists else 0o644
            originals[destination] = (exists, before, mode)
            planned[destination] = (exists, before)
        exists, current = planned[destination]
        if edit.operation == "replace":
            if not exists or current.count(edit.old_text) != 1:
                raise ValueError(
                    f"replace old_text must occur exactly once: {edit.path}"
                )
            planned[destination] = (
                True,
                current.replace(edit.old_text, edit.new_text, 1),
            )
        elif edit.operation == "create":
            if exists:
                raise ValueError(f"create target already exists: {edit.path}")
            planned[destination] = (True, edit.new_text)
        elif edit.operation == "delete":
            if not exists or current != edit.old_text:
                raise ValueError(
                    f"delete old_text must equal the complete file: {edit.path}"
                )
            planned[destination] = (False, "")
        else:
            raise ValueError(f"unsupported edit operation: {edit.operation}")

    changed_lines = sum(
        _line_delta(originals[path][1], final_text)
        for path, (_exists, final_text) in planned.items()
    )
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    try:
        for destination, (final_exists, final_text) in planned.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            original_exists, original_text, original_mode = originals[destination]
            if original_exists:
                backups[destination] = _write_atomic_temp(
                    destination,
                    original_text,
                    original_mode,
                    ".self-improve-backup",
                )
            if final_exists:
                staged[destination] = _write_atomic_temp(
                    destination,
                    final_text,
                    original_mode,
                    ".self-improve-tmp",
                )
        try:
            for destination, (final_exists, _final_text) in planned.items():
                if final_exists:
                    os.replace(staged.pop(destination), destination)
                else:
                    destination.unlink()
        except BaseException:
            for destination, (original_exists, _text, _mode) in originals.items():
                if original_exists:
                    backup = backups.get(destination)
                    if backup is not None and backup.exists():
                        os.replace(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            raise
        return changed_lines
    finally:
        for temporary in (*staged.values(), *backups.values()):
            temporary.unlink(missing_ok=True)


def _write_atomic_temp(
    destination: Path,
    content: str,
    mode: int,
    suffix: str,
) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=".gludd-self-improve-",
        suffix=suffix,
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    return temporary


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


def build_prompt(
    task: TaskSpec,
    reference: CodexReference,
    baseline_root: Path,
) -> str:
    """Build a bounded teacher-guided prompt without exposing the Codex solution."""
    excerpts: list[str] = []
    remaining = 48_000
    for relative in sorted(reference.changed_files):
        path = baseline_root / relative
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        clipped = content[:remaining]
        excerpts.append(f"FILE {relative}\n{clipped}")
        remaining -= len(clipped)
        if remaining <= 0:
            break
    required_tests = (
        tuple(sorted(reference.test_files))
        or canonical_test_paths(task.canonical_make_commands)
    )
    if not required_tests:
        raise ValueError("task has no canonical test path for the proposal contract")
    return (
        "Produce a Codex-quality repository patch proposal. You have no shell, Git, or "
        "tool authority. Return exactly one JSON object and no prose. The JSON grammar "
        "is supplied separately. Use minimal exact patch operations, never regenerate "
        "a whole existing file. For replace, copy the smallest unique old_text verbatim "
        "from the baseline excerpt and provide only its new_text. Preserve every other "
        "byte, including the final newline. Use create only for an absent path and delete "
        "only when old_text is the complete baseline file.\n"
        f"Task: {task.objective}\n"
        f"Baseline: {reference.baseline_sha}\n"
        f"Task ID: {task.task_id}\n"
        "Every and only these independent Codex reference paths must be edited:\n"
        + "\n".join(sorted(reference.changed_files))
        + "\nRequired test paths:\n"
        + "\n".join(required_tests)
        + "\nRequired canonical evidence commands (copy exactly):\n"
        + "\n".join(task.canonical_make_commands)
        + "\nBaseline file excerpts:\n"
        + "\n\n".join(excerpts)
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
    proposal: ProposalManifest,
    attempt: int,
    *,
    merge: bool,
) -> AttemptResult:
    """Apply, test, commit, compare, and clean one local proposal."""
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
        )
    worktree, branch = create_worktree(root_runner, reference.baseline_sha, attempt)
    runner = MakeRunner(worktree)
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
        )
    except BaseException:
        if not cleanup_passed:
            root_runner.run("agent-cleanup", {"BRANCH": branch}, timeout=180)
        raise


def run_benchmark(args: argparse.Namespace) -> AttemptResult:
    """Run bounded local attempts until Codex parity or the attempt limit."""
    root = Path(__file__).resolve().parents[1]
    root_runner = MakeRunner(root)
    task = TaskSpec.from_path(Path(args.task_file))
    if not args.local_model_path and not map_task_to_capabilities(task.objective):
        raise ValueError(
            "automatic local model task must match a mapped coding capability"
        )
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
    if required_output_tokens > 4096 and not args.validate_only:
        raise ValueError(
            "Codex reference exceeds the local decode budget: "
            f"estimated={required_output_tokens} available=4096; "
            "select a larger local model/context or a smaller atomic task"
        )
    if args.validate_only:
        print(
            "SELF_IMPROVE_CODEX_PLAN "
            f"task={task.task_id} baseline={reference.baseline_sha} "
            f"reference={reference.reference_sha} files={len(reference.changed_files)} "
            f"tests={len(reference.test_files)} "
            f"estimated_output_tokens={required_output_tokens} "
            f"model={Path(args.local_model_path) if args.local_model_path else 'auto'}"
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
            proposal=ProposalManifest.from_json(
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
                        "tests": sorted(reference.test_files) or ["tests/unit/test_placeholder.py"],
                        "make_commands": list(task.canonical_make_commands),
                        "commit_message": "test: validate self-improvement plan",
                    }
                )
            ),
            diagnostics="validate-only",
        )

    explicit_model_path = Path(args.local_model_path).expanduser() if args.local_model_path else None
    context_root, context_branch = create_worktree(
        root_runner,
        reference.baseline_sha,
        0,
    )
    mechanical_proposal: ProposalManifest | None = None
    try:
        base_prompt = build_prompt(task, reference, context_root)
        mechanical_proposal = generate_mechanical_proposal(
            MakeRunner(context_root),
            task,
            reference,
            context_root,
        )
    finally:
        context_cleanup = root_runner.run(
            "agent-cleanup",
            {"BRANCH": context_branch},
            timeout=180,
        )
        if context_cleanup.returncode != 0:
            raise RuntimeError(
                "baseline context worktree cleanup failed: "
                + (context_cleanup.stderr or context_cleanup.stdout)
            )
    prompt = base_prompt
    final: AttemptResult | None = None
    model_manager: ModelLeaseManager | None = None
    model_evidence_store: CapabilityEvidenceStore | None = None
    managed_candidates: tuple[PlannedModelCandidate, ...] | None = None
    candidate_index = 0
    for attempt in range(1, args.max_attempts + 1):
        print(f"SELF_IMPROVE_ATTEMPT_START attempt={attempt}", flush=True)
        use_mechanical = attempt == 1 and mechanical_proposal is not None
        candidate: PlannedModelCandidate | None = None
        if not use_mechanical:
            if model_manager is None:
                model_manager = ModelLeaseManager(
                    event_sink=_report_model_acquisition_event,
                )
            if explicit_model_path is None:
                if managed_candidates is None:
                    model_attempt_budget = args.max_attempts - (
                        1 if mechanical_proposal is not None else 0
                    )
                    evidence_path = (
                        model_manager.cache_root
                        / ".gludd"
                        / "capability-evidence.json"
                    )
                    model_evidence_store = CapabilityEvidenceStore(str(evidence_path))
                    prior_failed_model_ids = load_latest_failed_model_ids(
                        model_evidence_store,
                        task_text=task.objective,
                    )
                    managed_candidates = plan_model_candidates(
                        task.objective,
                        required_output_tokens,
                        prior_failed_model_ids,
                        unified_probe(),
                        model_evidence_store,
                        model_manager.resolve_revision,
                        input_tokens=max(1, (len(prompt.encode("utf-8")) + 3) // 4),
                        max_candidates=min(3, max(1, model_attempt_budget)),
                        on_resolution_failure=_report_model_resolution_failure,
                    )
                    print(
                        "SELF_IMPROVE_MODEL_PLAN "
                        f"candidates={json.dumps([item.config.name for item in managed_candidates])}",
                        flush=True,
                    )
                    if not managed_candidates:
                        raise RuntimeError(
                            "no fitting local coding model candidates for task, context, and hardware"
                        )
                if candidate_index >= len(managed_candidates):
                    raise RuntimeError(
                        "bounded local coding model candidate plan is exhausted"
                    )
                candidate = managed_candidates[candidate_index]
                candidate_index += 1
        try:
            if use_mechanical:
                if mechanical_proposal is None:
                    raise RuntimeError("mechanical proposal was not generated")
                proposal = mechanical_proposal
            else:
                if model_manager is None:
                    raise RuntimeError("local model manager was not initialized")
                if explicit_model_path is not None:
                    acquisition = model_manager.acquire(
                        task.objective,
                        explicit_path=explicit_model_path,
                    )
                elif candidate is not None:
                    acquisition = model_manager.acquire(
                        task.objective,
                        model_config=candidate.config,
                        resolved_revision=candidate.resolved_revision,
                    )
                else:
                    raise RuntimeError("local model candidate was not selected")
                acquired_model: AcquiredModel | None = None
                try:
                    with acquisition as acquired:
                        acquired_model = acquired
                        print(
                            "SELF_IMPROVE_MODEL_ACQUIRED "
                            f"model={acquired.model_id} source={acquired.source} "
                            f"revision={acquired.resolved_revision or 'explicit'} "
                            f"sha256={acquired.artifact_sha256}",
                            flush=True,
                        )
                        proposal = generate_local_proposal(
                            root_runner,
                            acquired.path,
                            prompt,
                        )
                finally:
                    if acquired_model is not None:
                        _report_model_release(acquired_model)
        except (RuntimeError, ValueError) as exc:
            if candidate is not None and model_evidence_store is not None:
                outcome_id = record_self_improve_outcome(
                    model_evidence_store,
                    task_text=task.objective,
                    candidate=candidate,
                    succeeded=False,
                )
                print(
                    "SELF_IMPROVE_MODEL_OUTCOME "
                    f"model={candidate.config.name} succeeded=false record={outcome_id}",
                    flush=True,
                )
            print(
                f"SELF_IMPROVE_PROPOSAL_REJECTED attempt={attempt} "
                f"error={json.dumps(str(exc)[:1000])}",
                flush=True,
            )
            if attempt == args.max_attempts:
                raise
            prompt = (
                base_prompt
                + "\\nPrevious output failed strict proposal validation: "
                + str(exc)[:1000]
                + "\\nReturn a complete object satisfying every required field."
            )
            continue
        final = evaluate_attempt(
            root_runner,
            task,
            reference,
            proposal,
            attempt,
            merge=args.merge,
        )
        if candidate is not None and model_evidence_store is not None:
            outcome_id = record_self_improve_outcome(
                model_evidence_store,
                task_text=task.objective,
                candidate=candidate,
                succeeded=final.comparison.accepted,
            )
            print(
                "SELF_IMPROVE_MODEL_OUTCOME "
                f"model={candidate.config.name} "
                f"succeeded={str(final.comparison.accepted).lower()} "
                f"record={outcome_id}",
                flush=True,
            )
        print(
            f"SELF_IMPROVE_ATTEMPT_END attempt={attempt} "
            f"score={final.comparison.score:.2f} accepted={final.comparison.accepted} "
            f"blockers={json.dumps(final.comparison.blockers)}",
            flush=True,
        )
        if final.comparison.accepted:
            return final
        prompt = build_retry_prompt(
            base_prompt,
            final.comparison,
            diagnostics=final.diagnostics,
        )
    if final is None:
        raise RuntimeError("no local-model attempt was executed")
    return final


def _clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    return environment


def _is_safe_make_command(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return (
        bool(tokens)
        and tokens[0] == "make"
        and len(command.encode("utf-8")) <= 4096
        and not any(character in command for character in _FORBIDDEN_COMMAND_CHARS)
    )


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


def _line_delta(before: str, after: str) -> int:
    delta = 0
    for line in difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        lineterm="",
    ):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            delta += 1
    return delta


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
        message = str(exc).replace("\n", " ").replace("\r", " ")[:2000]
        print(
            f"SELF_IMPROVE_ERROR type={type(exc).__name__} "
            f"message={json.dumps(message)}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    payload = {
        "target": args.target,
        "accepted": result.comparison.accepted,
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
    main()
