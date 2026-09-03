"""Approval-bound orchestration for managed local self-improvement attempts.

This module owns the reusable state machine between an immutable human-approved
plan and bounded local-model attempts.  Repository mutation and evaluation stay
behind injected callables so daemon and worker integrations can supply their own
execution boundary without importing the command-line script.
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import shlex
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, cast

from general_ludd.hardware.model_fit import unified_probe
from general_ludd.hardware.survey import HardwareInventory
from general_ludd.local_model import LocalModelConfig
from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V3,
    COMPACT_PROPOSAL_PROTOCOL_V4,
    LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
    LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
    _safe_compact_policy_telemetry,
    _safe_compact_scope_telemetry,
    build_retry_prompt,
    local_proposal_attempt_identity_digest,
    safe_evaluation_retry_diagnosis,
)
from general_ludd.self_improve.model_candidate_planner import (
    CODE_TASK_CAPABILITY_POLICY_ID,
    CodeTaskShape,
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

_MAX_TASK_BYTES: Final = 262_144
_MAX_PLAN_BYTES: Final = 4_194_304
_MAX_PROMPT_SHARD_BYTES: Final = 16_384
_FORBIDDEN_COMMAND_CHARS: Final = frozenset(";|&$()<>\n\r")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_TASK_RE: Final = re.compile(r"^S[0-9]+(?:\.[0-9]+)?$")
_LEGACY_PLAN_SCHEMA_VERSION: Final = 1
_LEGACY_BOUND_PLAN_SCHEMA_VERSION: Final = 2
_PLAN_SCHEMA_VERSION: Final = 3


class ModelPlanFailure(StrEnum):
    """Secret-safe terminal states for bounded model selection."""

    EXHAUSTED = "model_plan_exhausted"


class ModelPlanError(RuntimeError):
    """Typed failure raised before a candidate can begin an attempt."""

    def __init__(self, failure: ModelPlanFailure) -> None:
        """Retain only a stable category and operator-safe message."""
        if failure is not ModelPlanFailure.EXHAUSTED:
            raise ValueError("unsupported typed model plan failure")
        super().__init__("managed model candidate plan failed: model_plan_exhausted")
        self.failure = failure


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
            if proposal.schema_version == 2:
                if not exists or current != edit.old_text:
                    raise ValueError(
                        f"replace old_text must equal the complete trusted snapshot: {edit.path}"
                    )
                planned[destination] = (True, edit.new_text)
            else:
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
                    os.replace(staged[destination], destination)
                    staged.pop(destination)
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


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Deterministic benchmark task and canonical quality commands."""

    task_id: str
    objective: str
    canonical_make_commands: tuple[str, ...]
    reference_elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        """Reject mutable, unbounded, or non-canonical task state."""
        if not isinstance(self.task_id, str) or _TASK_RE.fullmatch(self.task_id) is None:
            raise ValueError("task_id must use the canonical S<number>[.<number>] form")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be non-empty text")
        if len(self.objective.encode("utf-8")) > 65_536:
            raise ValueError("objective exceeds 65536 bytes")
        if (
            not isinstance(self.canonical_make_commands, tuple)
            or not self.canonical_make_commands
            or len(self.canonical_make_commands) > 32
        ):
            raise ValueError("canonical_make_commands must be an immutable 1..32 tuple")
        if not all(
            isinstance(command, str) and _is_safe_make_command(command)
            for command in self.canonical_make_commands
        ):
            raise ValueError("every canonical step must be one bounded make command")
        if (
            isinstance(self.reference_elapsed_seconds, bool)
            or not isinstance(self.reference_elapsed_seconds, (int, float))
            or self.reference_elapsed_seconds < 0
        ):
            raise ValueError("reference_elapsed_seconds must be non-negative")

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
        return cls._from_json_value(value)

    @classmethod
    def _from_json_value(cls, value: object) -> TaskSpec:
        mapping = _exact_mapping(
            value,
            required={"task_id", "objective", "canonical_make_commands"},
            optional={"reference_elapsed_seconds"},
            label="task",
        )
        raw_commands = mapping["canonical_make_commands"]
        if not isinstance(raw_commands, list) or not all(
            isinstance(command, str) for command in raw_commands
        ):
            raise ValueError("canonical_make_commands must be a JSON array of strings")
        return cls(
            task_id=_required_string(mapping, "task_id"),
            objective=_required_string(mapping, "objective").strip(),
            canonical_make_commands=tuple(raw_commands),
            reference_elapsed_seconds=_non_negative_number(
                mapping.get("reference_elapsed_seconds", 0.0),
                "reference_elapsed_seconds",
            ),
        )

    def _json_value(self) -> dict[str, object]:
        return {
            "canonical_make_commands": list(self.canonical_make_commands),
            "objective": self.objective,
            "reference_elapsed_seconds": float(self.reference_elapsed_seconds),
            "task_id": self.task_id,
        }


@dataclass(frozen=True, slots=True)
class PromptShard:
    """One bounded proposal prompt with an exact, disjoint edit focus."""

    focus_paths: tuple[str, ...]
    prompt: str
    editable_ranges: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        """Reject empty, mutable, duplicate, or oversized shard state."""
        if (
            not isinstance(self.focus_paths, tuple)
            or not self.focus_paths
            or not all(isinstance(path, str) and path for path in self.focus_paths)
            or len(set(self.focus_paths)) != len(self.focus_paths)
        ):
            raise ValueError("prompt shard focus paths must be a non-empty unique tuple")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt shard must not be empty")
        if len(self.prompt.encode("utf-8")) > _MAX_PROMPT_SHARD_BYTES:
            raise ValueError(f"prompt shard exceeds {_MAX_PROMPT_SHARD_BYTES} bytes")
        if not isinstance(self.editable_ranges, tuple):
            raise ValueError("prompt shard editable ranges must be an immutable tuple")
        previous_end = 1
        for item in self.editable_ranges:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in item
                )
            ):
                raise ValueError("prompt shard editable ranges must contain integer pairs")
            start, end = item
            if start < 1 or end <= start or start < previous_end:
                raise ValueError("prompt shard editable ranges must be ordered half-open ranges")
            previous_end = end


@dataclass(frozen=True, slots=True)
class PromptPlan:
    """Complete reference identity split into bounded local-model prompts."""

    shards: tuple[PromptShard, ...]
    source_bytes: int
    protocol_digest: str = ""
    baseline_files: tuple[tuple[str, str | None], ...] = field(
        default=(),
        repr=False,
    )
    proposal_protocol: str = COMPACT_PROPOSAL_PROTOCOL_V3

    def __post_init__(self) -> None:
        """Require immutable non-overlapping shards and stable protocol identity."""
        if not isinstance(self.shards, tuple) or not self.shards:
            raise ValueError("prompt plan must contain at least one shard in an immutable tuple")
        if isinstance(self.source_bytes, bool) or not isinstance(self.source_bytes, int) or self.source_bytes < 0:
            raise ValueError("prompt plan source_bytes must be non-negative")
        paths = [path for shard in self.shards for path in shard.focus_paths]
        if len(paths) != len(set(paths)):
            raise ValueError("prompt plan focus paths must be disjoint")
        if not isinstance(self.baseline_files, tuple):
            raise ValueError("prompt plan baseline files must be an immutable tuple")
        if not isinstance(self.proposal_protocol, str) or self.proposal_protocol not in {
            COMPACT_PROPOSAL_PROTOCOL_V3,
            COMPACT_PROPOSAL_PROTOCOL_V4,
        }:
            raise ValueError("prompt plan compact proposal protocol is unsupported")
        if self.baseline_files:
            baseline_paths: list[str] = []
            baseline_bytes = 0
            for item in self.baseline_files:
                if (
                    not isinstance(item, tuple)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or (item[1] is not None and not isinstance(item[1], str))
                ):
                    raise ValueError("prompt plan baseline files must contain path/text pairs")
                path, content = item
                baseline_paths.append(path)
                if content is not None:
                    baseline_bytes += len(content.encode("utf-8"))
            if baseline_paths != paths:
                raise ValueError("prompt plan baseline files must match focus paths in order")
            if baseline_bytes != self.source_bytes:
                raise ValueError("prompt plan baseline bytes must match the source byte count")
        if self.protocol_digest:
            _validate_digest("prompt plan protocol_digest", self.protocol_digest)
            return
        object.__setattr__(self, "protocol_digest", _stable_digest(self._identity_value()))

    def _identity_value(self) -> dict[str, object]:
        if self.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V3:
            return {
                "protocol": "self-improve-prompt-plan-v1",
                "shards": [
                    {"focus_paths": list(shard.focus_paths), "prompt": shard.prompt}
                    for shard in self.shards
                ],
                "source_bytes": self.source_bytes,
            }
        return {
            "proposal_protocol": self.proposal_protocol,
            "protocol": "self-improve-prompt-plan-v2",
            "shards": [
                {
                    "editable_ranges": [list(item) for item in shard.editable_ranges],
                    "focus_paths": list(shard.focus_paths),
                    "prompt": shard.prompt,
                }
                for shard in self.shards
            ],
            "source_bytes": self.source_bytes,
        }

    def _json_value(self) -> dict[str, object]:
        value: dict[str, object] = {
            "baseline_files": [list(item) for item in self.baseline_files],
            "protocol_digest": self.protocol_digest,
            "shards": [
                {"focus_paths": list(shard.focus_paths), "prompt": shard.prompt}
                for shard in self.shards
            ],
            "source_bytes": self.source_bytes,
        }
        if self.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4:
            value["proposal_protocol"] = self.proposal_protocol
            value["shards"] = [
                {
                    "editable_ranges": [list(item) for item in shard.editable_ranges],
                    "focus_paths": list(shard.focus_paths),
                    "prompt": shard.prompt,
                }
                for shard in self.shards
            ]
        return value

    @classmethod
    def _from_json_value(cls, value: object) -> PromptPlan:
        legacy_fields = {"baseline_files", "protocol_digest", "shards", "source_bytes"}
        if isinstance(value, dict) and set(value) == legacy_fields:
            proposal_protocol = COMPACT_PROPOSAL_PROTOCOL_V3
            required_fields = legacy_fields
        else:
            proposal_protocol = COMPACT_PROPOSAL_PROTOCOL_V4
            required_fields = legacy_fields | {"proposal_protocol"}
        mapping = _exact_mapping(
            value,
            required=required_fields,
            optional=set(),
            label="prompt plan",
        )
        if mapping.get("proposal_protocol", proposal_protocol) != proposal_protocol:
            raise ValueError("prompt plan compact proposal protocol is unsupported")
        raw_shards = mapping["shards"]
        if not isinstance(raw_shards, list):
            raise ValueError("prompt plan shards must be a JSON array")
        shards: list[PromptShard] = []
        for raw_shard in raw_shards:
            shard_fields = {"focus_paths", "prompt"}
            if proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4:
                shard_fields.add("editable_ranges")
            shard = _exact_mapping(
                raw_shard,
                required=shard_fields,
                optional=set(),
                label="prompt shard",
            )
            raw_paths = shard["focus_paths"]
            if not isinstance(raw_paths, list) or not all(
                isinstance(path, str) for path in raw_paths
            ):
                raise ValueError("prompt shard focus_paths must be a JSON string array")
            raw_ranges = shard.get("editable_ranges", [])
            if not isinstance(raw_ranges, list) or not all(
                isinstance(item, list) and len(item) == 2 for item in raw_ranges
            ):
                raise ValueError("prompt shard editable_ranges must be a JSON pair array")
            shards.append(
                PromptShard(
                    tuple(raw_paths),
                    _required_string(shard, "prompt"),
                    tuple((item[0], item[1]) for item in raw_ranges),
                )
            )
        raw_baseline = mapping["baseline_files"]
        if not isinstance(raw_baseline, list):
            raise ValueError("prompt plan baseline_files must be a JSON array")
        baseline: list[tuple[str, str | None]] = []
        for item in raw_baseline:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or (item[1] is not None and not isinstance(item[1], str))
            ):
                raise ValueError("prompt plan baseline_files entries are invalid")
            baseline.append((item[0], item[1]))
        return cls(
            shards=tuple(shards),
            source_bytes=_non_negative_integer(mapping["source_bytes"], "source_bytes"),
            protocol_digest=_required_string(mapping, "protocol_digest"),
            baseline_files=tuple(baseline),
            proposal_protocol=proposal_protocol,
        )

    def __contains__(self, value: object) -> bool:
        """Support compatibility membership checks across every shard prompt."""
        return isinstance(value, str) and any(value in shard.prompt for shard in self.shards)

    @property
    def max_prompt_bytes(self) -> int:
        """Return the largest individual inference input."""
        return max(len(shard.prompt.encode("utf-8")) for shard in self.shards)


def _attempt_identity_digest(prompt: PromptPlan | str) -> str:
    """Bind prompt identity to the complete managed proposal protocol."""
    if isinstance(prompt, PromptPlan):
        prompt_protocol_digest = prompt.protocol_digest
        proposal_protocol = prompt.proposal_protocol
    elif isinstance(prompt, str) and prompt.strip():
        prompt_protocol_digest = _stable_digest(
            {"prompt": prompt, "protocol": "self-improve-string-prompt-v1"}
        )
        proposal_protocol = COMPACT_PROPOSAL_PROTOCOL_V3
    else:
        raise ValueError("prompt must be a non-empty string or PromptPlan")
    proposal_identity = local_proposal_attempt_identity_digest(
        prompt_protocol_digest,
        proposal_protocol=proposal_protocol,
    )
    if proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4:
        return _stable_digest(
            {
                "local_proposal_attempt_identity_digest": proposal_identity,
                "model_candidate_policy": CODE_TASK_CAPABILITY_POLICY_ID,
                "protocol": "self-improve-attempt-selection-binding-v1",
            }
        )
    return proposal_identity


def _validate_attempt_identity_digest(value: object) -> str:
    """Return one canonical attempt identity or fail closed."""
    return _validate_digest("attempt identity", value)


@dataclass(frozen=True, slots=True)
class PlanBoundProposal:
    """A proposal inseparably bound to the trusted parent prompt plan."""

    proposal: ProposalManifest
    attempt_identity_digest: str

    def __post_init__(self) -> None:
        """Reject unvalidated manifests and non-canonical plan identities."""
        if not isinstance(self.proposal, ProposalManifest):
            raise ValueError("plan-bound proposal must contain a proposal manifest")
        _validate_digest("attempt identity", self.attempt_identity_digest)


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """Final evidence, comparison, and patch identity for one local attempt."""

    comparison: ComparisonResult
    evidence: CandidateEvidence
    patch_equivalence: str
    proposal: ProposalManifest
    diagnostics: str
    attempt_identity_digest: str

    def __post_init__(self) -> None:
        """Require every approval/result to retain one canonical plan identity."""
        _validate_digest("attempt identity", self.attempt_identity_digest)


@dataclass(frozen=True, slots=True)
class ApprovedSelfImprovePlan:
    """Strict immutable execution artifact bound to one recorded approval."""

    approval_id: str
    todo_id: str
    project_id: str
    repo_root: Path | None
    task: TaskSpec
    reference: CodexReference
    prompt: PromptPlan | str
    required_output_tokens: int
    max_attempts: int
    approved: bool
    repository_binding_digest: str = ""
    attempt_identity_digest: str = ""
    approved_plan_digest: str = ""
    explicit_model_path: Path | None = None
    mechanical_proposal: ProposalManifest | None = None
    _schema_version: int = field(default=_PLAN_SCHEMA_VERSION, repr=False)

    def __post_init__(self) -> None:
        """Normalize paths and initialize identities without accepting mutable state."""
        for label, value in (
            ("approval_id", self.approval_id),
            ("todo_id", self.todo_id),
            ("project_id", self.project_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty text")
        if self._schema_version not in {
            _LEGACY_PLAN_SCHEMA_VERSION,
            _LEGACY_BOUND_PLAN_SCHEMA_VERSION,
            _PLAN_SCHEMA_VERSION,
        }:
            raise ValueError("approved plan schema_version is unsupported")
        if self.repo_root is not None:
            if not isinstance(self.repo_root, Path):
                raise ValueError("repo_root must be a pathlib.Path or None")
            object.__setattr__(self, "repo_root", self.repo_root.resolve(strict=False))
        if self.repository_binding_digest:
            _validate_digest(
                "repository_binding_digest",
                self.repository_binding_digest,
            )
            if self.explicit_model_path is not None:
                raise ValueError(
                    "repository-bound plans cannot transport an explicit model path"
                )
        elif self.repo_root is None:
            raise ValueError(
                "plan requires a legacy repository root or repository binding digest"
            )
        if self.explicit_model_path is not None:
            if not isinstance(self.explicit_model_path, Path):
                raise ValueError("explicit_model_path must be a pathlib.Path")
            object.__setattr__(
                self,
                "explicit_model_path",
                self.explicit_model_path.expanduser().resolve(strict=False),
            )
        if not isinstance(self.task, TaskSpec):
            raise ValueError("task must be an immutable TaskSpec")
        _validate_reference(self.reference)
        if not isinstance(self.prompt, (PromptPlan, str)):
            raise ValueError("prompt must be an immutable PromptPlan or string")
        if isinstance(self.prompt, str) and not self.prompt.strip():
            raise ValueError("prompt string must not be empty")
        if self.mechanical_proposal is not None and not isinstance(
            self.mechanical_proposal,
            ProposalManifest,
        ):
            raise ValueError("mechanical_proposal must be a ProposalManifest")
        if (
            isinstance(self.required_output_tokens, bool)
            or not isinstance(self.required_output_tokens, int)
            or self.required_output_tokens <= 0
        ):
            raise ValueError("required_output_tokens must be a positive integer")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 3
        ):
            raise ValueError("max_attempts must be between 1 and 3")
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a boolean")
        if not self.attempt_identity_digest:
            object.__setattr__(self, "attempt_identity_digest", _attempt_identity_digest(self.prompt))
        else:
            _validate_digest("attempt_identity_digest", self.attempt_identity_digest)
        if not self.approved_plan_digest and self.approved:
            object.__setattr__(self, "approved_plan_digest", self.identity_digest)
        elif self.approved_plan_digest:
            _validate_digest("approved_plan_digest", self.approved_plan_digest)

    @classmethod
    def approve(
        cls,
        *,
        approval_id: str,
        todo_id: str,
        project_id: str,
        repo_root: Path,
        repository_binding_digest: str = "",
        task: TaskSpec,
        reference: CodexReference,
        prompt: PromptPlan | str,
        required_output_tokens: int,
        max_attempts: int,
        explicit_model_path: Path | None = None,
        mechanical_proposal: ProposalManifest | None = None,
    ) -> ApprovedSelfImprovePlan:
        """Create one approval-bound artifact at the human release boundary."""
        return cls(
            approval_id=approval_id,
            todo_id=todo_id,
            project_id=project_id,
            repo_root=repo_root,
            task=task,
            reference=reference,
            prompt=prompt,
            required_output_tokens=required_output_tokens,
            max_attempts=max_attempts,
            approved=True,
            repository_binding_digest=repository_binding_digest,
            explicit_model_path=explicit_model_path,
            mechanical_proposal=mechanical_proposal,
            _schema_version=(
                _PLAN_SCHEMA_VERSION
                if repository_binding_digest
                or (
                    isinstance(prompt, PromptPlan)
                    and prompt.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4
                )
                else _LEGACY_PLAN_SCHEMA_VERSION
            ),
        )

    def bind_execution_repository(
        self,
        repo_root: Path,
        *,
        repository_binding_digest: str,
    ) -> ApprovedSelfImprovePlan:
        """Attach a host-local root after validating the approved logical binding."""
        self.verify_approval()
        if not self.repository_binding_digest or not hmac.compare_digest(
            self.repository_binding_digest,
            repository_binding_digest,
        ):
            raise ValueError("approved repository binding does not match execution")
        if not isinstance(repo_root, Path):
            raise ValueError("execution repo_root must be a pathlib.Path")
        try:
            canonical_root = repo_root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("execution repository is unavailable") from exc
        if not canonical_root.is_dir():
            raise ValueError("execution repository is unavailable")
        bound = replace(self, repo_root=canonical_root)
        bound.verify_approval()
        return bound

    @property
    def identity_digest(self) -> str:
        """Return the canonical digest covering every executable plan field."""
        return _stable_digest(self._identity_value())

    def verify_approval(self) -> None:
        """Fail closed unless current fields exactly match the approved identity."""
        if not self.approved:
            raise ValueError("self-improvement plan is not approved")
        if not self.approved_plan_digest or not hmac.compare_digest(
            self.approved_plan_digest,
            self.identity_digest,
        ):
            raise ValueError("approved plan identity does not match executable fields")
        expected_attempt = _attempt_identity_digest(self.prompt)
        if not hmac.compare_digest(self.attempt_identity_digest, expected_attempt):
            raise ValueError("attempt identity does not match the approved prompt")

    def to_json(self) -> str:
        """Serialize one verified plan using a stable, versioned JSON form."""
        self.verify_approval()
        payload = self._identity_value()
        payload.update(
            {
                "approved": self.approved,
                "approved_plan_digest": self.approved_plan_digest,
            }
        )
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> ApprovedSelfImprovePlan:
        """Hydrate only the exact immutable schema and re-verify its approval."""
        if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > _MAX_PLAN_BYTES:
            raise ValueError("approved plan JSON must contain bounded text")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"approved plan is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("approved plan must be a JSON object")
        schema_version = value.get("schema_version")
        common_fields = {
                "approval_id",
                "approved",
                "approved_plan_digest",
                "attempt_identity_digest",
                "explicit_model_path",
                "max_attempts",
                "mechanical_proposal",
                "project_id",
                "prompt",
                "reference",
                "required_output_tokens",
                "schema_version",
                "task",
                "todo_id",
        }
        if schema_version == _LEGACY_PLAN_SCHEMA_VERSION:
            required_fields = common_fields | {"repo_root"}
        elif schema_version == _LEGACY_BOUND_PLAN_SCHEMA_VERSION:
            required_fields = common_fields | {"repository_binding_digest"}
        elif schema_version == _PLAN_SCHEMA_VERSION:
            repository_fields = {"repo_root", "repository_binding_digest"} & set(value)
            required_fields = common_fields | (
                {"repo_root"}
                if repository_fields == {"repo_root"}
                else {"repository_binding_digest"}
            )
        else:
            raise ValueError("approved plan schema_version is unsupported")
        mapping = _exact_mapping(
            value,
            required=required_fields,
            optional=set(),
            label="approved plan",
        )
        if mapping["approved"] is not True:
            raise ValueError("approved plan artifact must carry approved=true")
        prompt = _prompt_from_json_value(mapping["prompt"])
        if (
            schema_version
            in {_LEGACY_PLAN_SCHEMA_VERSION, _LEGACY_BOUND_PLAN_SCHEMA_VERSION}
            and isinstance(prompt, PromptPlan)
            and prompt.proposal_protocol != COMPACT_PROPOSAL_PROTOCOL_V3
        ):
            raise ValueError("legacy approved plan cannot carry compact-v4 prompt state")
        explicit_path = _optional_canonical_path(
            mapping["explicit_model_path"],
            "explicit_model_path",
        )
        raw_mechanical = mapping["mechanical_proposal"]
        mechanical = (
            None
            if raw_mechanical is None
            else ProposalManifest.from_json(
                json.dumps(raw_mechanical, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            )
        )
        plan = cls(
            approval_id=_required_string(mapping, "approval_id"),
            todo_id=_required_string(mapping, "todo_id"),
            project_id=_required_string(mapping, "project_id"),
            repo_root=(
                _canonical_path(mapping["repo_root"], "repo_root")
                if schema_version == _LEGACY_PLAN_SCHEMA_VERSION
                or (
                    schema_version == _PLAN_SCHEMA_VERSION
                    and "repo_root" in mapping
                )
                else None
            ),
            task=TaskSpec._from_json_value(mapping["task"]),
            reference=_reference_from_json_value(mapping["reference"]),
            prompt=prompt,
            required_output_tokens=_positive_integer(
                mapping["required_output_tokens"],
                "required_output_tokens",
            ),
            max_attempts=_positive_integer(mapping["max_attempts"], "max_attempts"),
            approved=True,
            repository_binding_digest=(
                ""
                if schema_version == _LEGACY_PLAN_SCHEMA_VERSION
                or (
                    schema_version == _PLAN_SCHEMA_VERSION
                    and "repo_root" in mapping
                )
                else _required_string(
                    mapping,
                    "repository_binding_digest",
                )
            ),
            attempt_identity_digest=_required_string(mapping, "attempt_identity_digest"),
            approved_plan_digest=_required_string(mapping, "approved_plan_digest"),
            explicit_model_path=explicit_path,
            mechanical_proposal=mechanical,
            _schema_version=cast(int, schema_version),
        )
        plan.verify_approval()
        return plan

    def _identity_value(self) -> dict[str, object]:
        prompt_value: dict[str, object]
        if isinstance(self.prompt, PromptPlan):
            prompt_value = {"kind": "plan", "value": self.prompt._json_value()}
        else:
            prompt_value = {"kind": "string", "value": self.prompt}
        mechanical: object = None
        if self.mechanical_proposal is not None:
            mechanical = json.loads(self.mechanical_proposal.to_json())
        identity = {
            "approval_id": self.approval_id,
            "attempt_identity_digest": self.attempt_identity_digest,
            "explicit_model_path": (
                str(self.explicit_model_path) if self.explicit_model_path is not None else None
            ),
            "max_attempts": self.max_attempts,
            "mechanical_proposal": mechanical,
            "project_id": self.project_id,
            "prompt": prompt_value,
            "reference": _reference_json_value(self.reference),
            "required_output_tokens": self.required_output_tokens,
            "task": self.task._json_value(),
            "todo_id": self.todo_id,
        }
        if self._schema_version == _LEGACY_BOUND_PLAN_SCHEMA_VERSION or (
            self._schema_version == _PLAN_SCHEMA_VERSION
            and self.repository_binding_digest
        ):
            if not self.repository_binding_digest:
                raise ValueError("repository-bound plan requires its binding digest")
            identity["repository_binding_digest"] = self.repository_binding_digest
            identity["schema_version"] = self._schema_version
        else:
            if self.repo_root is None:
                raise ValueError("local plan repository root is unavailable")
            identity["repo_root"] = str(self.repo_root)
            identity["schema_version"] = self._schema_version
        return identity


class ManagedOutcomeAdapter(Protocol):
    """Durable evidence seam shared by JSON CLI and future database workers."""

    planner_store: object

    def load_failed_model_ids(
        self,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        """Return failures scoped to the exact task and prompt protocol."""

    def record_outcome(
        self,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> object:
        """Durably record one candidate result and return its identity."""


class _FailureLoader(Protocol):
    def __call__(
        self,
        store: CapabilityEvidenceStore,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]: ...


class _OutcomeRecorder(Protocol):
    def __call__(
        self,
        store: CapabilityEvidenceStore,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> int: ...


class CapabilityEvidenceOutcomeAdapter:
    """Persist managed outcomes through the established capability evidence store."""

    def __init__(
        self,
        store: CapabilityEvidenceStore,
        *,
        failure_loader: _FailureLoader = load_latest_failed_model_ids,
        outcome_recorder: _OutcomeRecorder = record_self_improve_outcome,
    ) -> None:
        """Bind an existing durable store and the canonical outcome functions."""
        self.planner_store: object = store
        self._store = store
        self._failure_loader = failure_loader
        self._outcome_recorder = outcome_recorder

    def load_failed_model_ids(
        self,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        """Load exact-identity failures through the canonical parser."""
        return self._failure_loader(
            self._store,
            task_text=task_text,
            attempt_identity_digest=attempt_identity_digest,
        )

    def record_outcome(
        self,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> int:
        """Write one canonical revision- and protocol-bound result."""
        return self._outcome_recorder(
            self._store,
            task_text=task_text,
            candidate=candidate,
            succeeded=succeeded,
            attempt_identity_digest=attempt_identity_digest,
        )


class _Reservation(Protocol):
    def mark_eligible(self, identity: ModelArtifactIdentity) -> None: ...

    def mark_failed(self, identity: ModelArtifactIdentity) -> None: ...


class _LeaseManager(Protocol):
    cache_root: Path

    def resolve_revision(self, repo_id: str) -> str: ...

    def owned_identities_for_model_ids(
        self,
        model_ids: tuple[str, ...],
    ) -> tuple[ModelArtifactIdentity, ...]: ...

    def reserve_plan(
        self,
        identities: tuple[ModelArtifactIdentity, ...],
        *,
        failure_hints: tuple[ModelArtifactIdentity, ...] = (),
    ) -> AbstractContextManager[_Reservation]: ...

    def acquire(
        self,
        task_description: str,
        *,
        explicit_path: Path | None = None,
        model_config: LocalModelConfig | None = None,
        resolved_revision: str | None = None,
    ) -> AbstractContextManager[AcquiredModel]: ...


class _ModelManagerFactory(Protocol):
    def __call__(
        self,
        *,
        event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
    ) -> _LeaseManager: ...


class _OutcomeAdapterFactory(Protocol):
    def __call__(self, cache_root: Path) -> ManagedOutcomeAdapter: ...


class _CandidatePlanner(Protocol):
    def __call__(
        self,
        task_text: str,
        output_tokens: int,
        prior_failed_model_ids: tuple[str, ...],
        hardware: HardwareInventory,
        evidence_store: CapabilityEvidenceStore,
        revision_resolver: Callable[[str], str],
        *,
        input_tokens: int | None = None,
        task_shape: CodeTaskShape | None = None,
        max_candidates: int = 3,
        on_resolution_failure: Callable[[LocalModelConfig, str], None] | None = None,
    ) -> tuple[PlannedModelCandidate, ...]: ...


class _ProposalGenerator(Protocol):
    def __call__(
        self,
        model_path: Path,
        prompt: PromptPlan | str,
        task: TaskSpec,
        reference: CodexReference,
    ) -> ProposalManifest: ...


class _AttemptEvaluator(Protocol):
    def __call__(
        self,
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult: ...


@dataclass(frozen=True, slots=True)
class ManagedRunResult:
    """Bounded service result retaining final evidence and durable identities."""

    final_result: AttemptResult
    attempts: int
    plan_identity_digest: str
    attempted_model_ids: tuple[str, ...]
    outcome_record_ids: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        """Return whether the final Codex comparison accepted the candidate."""
        return self.final_result.comparison.accepted

    @property
    def attempt_identity_digest(self) -> str:
        """Expose the final attempt identity for durable event correlation."""
        return self.final_result.attempt_identity_digest


def _default_outcome_adapter(cache_root: Path) -> ManagedOutcomeAdapter:
    evidence_path = cache_root / ".gludd" / "capability-evidence.json"
    return CapabilityEvidenceOutcomeAdapter(CapabilityEvidenceStore(str(evidence_path)))


def _default_artifact_identity(candidate: PlannedModelCandidate) -> ModelArtifactIdentity:
    return ModelArtifactIdentity(
        model_id=candidate.config.name,
        repo_id=candidate.config.repo,
        filename=candidate.config.filename,
        revision=candidate.resolved_revision,
    )


def _print_progress(message: str) -> None:
    print(message, flush=True)


_DEFAULT_MODEL_MANAGER_FACTORY = cast(_ModelManagerFactory, ModelLeaseManager)
_DEFAULT_CANDIDATE_PLANNER = cast(_CandidatePlanner, plan_model_candidates)


def _compact_v4_code_task_shape(
    plan: ApprovedSelfImprovePlan,
    prompt: PromptPlan | str,
) -> CodeTaskShape | None:
    """Derive trusted capability evidence without interpreting task prose."""
    if (
        not isinstance(prompt, PromptPlan)
        or prompt.proposal_protocol != COMPACT_PROPOSAL_PROTOCOL_V4
    ):
        return None
    focus_paths = tuple(path for shard in prompt.shards for path in shard.focus_paths)
    return CodeTaskShape(
        changed_files=len(focus_paths),
        changed_test_files=sum(
            path in plan.reference.test_files for path in focus_paths
        ),
        source_bytes=prompt.source_bytes,
    )


class ManagedSelfImproveRunner:
    """Execute one verified plan through bounded managed model attempts."""

    def __init__(
        self,
        *,
        proposal_generator: _ProposalGenerator,
        attempt_evaluator: _AttemptEvaluator,
        model_manager_factory: _ModelManagerFactory = _DEFAULT_MODEL_MANAGER_FACTORY,
        outcome_adapter_factory: _OutcomeAdapterFactory = _default_outcome_adapter,
        candidate_planner: _CandidatePlanner = _DEFAULT_CANDIDATE_PLANNER,
        hardware_probe: Callable[[], HardwareInventory] = unified_probe,
        artifact_identity: Callable[[PlannedModelCandidate], ModelArtifactIdentity] = _default_artifact_identity,
        acquisition_event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
        resolution_failure_sink: Callable[[LocalModelConfig, str], None] | None = None,
        release_sink: Callable[[AcquiredModel], None] | None = None,
        progress_sink: Callable[[str], None] = _print_progress,
        model_acquisition_error: type[BaseException] = ModelAcquisitionError,
        comparison_retry_builder: Callable[[PromptPlan, ComparisonResult, str], PromptPlan] | None = None,
        validation_retry_builder: Callable[[PromptPlan, str], PromptPlan] | None = None,
    ) -> None:
        """Inject side-effecting boundaries while retaining orchestration centrally."""
        self.proposal_generator = proposal_generator
        self.attempt_evaluator = attempt_evaluator
        self.model_manager_factory = model_manager_factory
        self.outcome_adapter_factory = outcome_adapter_factory
        self.candidate_planner = candidate_planner
        self.hardware_probe = hardware_probe
        self.artifact_identity = artifact_identity
        self.acquisition_event_sink = acquisition_event_sink
        self.resolution_failure_sink = resolution_failure_sink
        self.release_sink = release_sink
        self.progress_sink = progress_sink
        self.model_acquisition_error = model_acquisition_error
        self.comparison_retry_builder = comparison_retry_builder
        self.validation_retry_builder = validation_retry_builder

    def run(self, plan: ApprovedSelfImprovePlan) -> ManagedRunResult:
        """Run approved attempts without ever merging into a live branch."""
        if not isinstance(plan, ApprovedSelfImprovePlan):
            raise ValueError("plan must be an ApprovedSelfImprovePlan")
        plan.verify_approval()
        prompt = plan.prompt
        final: AttemptResult | None = None
        model_manager: _LeaseManager | None = None
        outcomes: ManagedOutcomeAdapter | None = None
        candidates: tuple[PlannedModelCandidate, ...] | None = None
        reservation: _Reservation | None = None
        candidate_index = 0
        attempted_models: list[str] = []
        outcome_ids: list[str] = []

        with ExitStack() as stack:
            for attempt in range(1, plan.max_attempts + 1):
                use_mechanical = attempt == 1 and plan.mechanical_proposal is not None
                candidate: PlannedModelCandidate | None = None
                candidate_identity: ModelArtifactIdentity | None = None
                if not use_mechanical:
                    if model_manager is None:
                        model_manager = self.model_manager_factory(
                            event_sink=self.acquisition_event_sink
                        )
                    if plan.explicit_model_path is None:
                        if outcomes is None:
                            outcomes = self.outcome_adapter_factory(model_manager.cache_root)
                        if candidates is None:
                            candidates, reservation = self._plan_candidates(
                                plan,
                                prompt,
                                model_manager,
                                outcomes,
                                stack,
                            )
                        if candidate_index >= len(candidates):
                            raise ModelPlanError(ModelPlanFailure.EXHAUSTED)
                        candidate = candidates[candidate_index]
                        candidate_index += 1
                        candidate_identity = self.artifact_identity(candidate)
                        attempted_models.append(candidate.config.name)

                self.progress_sink(
                    "SELF_IMPROVE_ATTEMPT_START "
                    f"attempt={attempt} "
                    f"attempt_identity_digest={plan.attempt_identity_digest}"
                )

                try:
                    proposal = self._generate_proposal(
                        plan,
                        prompt,
                        candidate,
                        model_manager,
                        use_mechanical,
                        reservation,
                        candidate_identity,
                    )
                except self.model_acquisition_error as exc:
                    failure = getattr(getattr(exc, "failure", None), "value", "unknown")
                    self.progress_sink(
                        "SELF_IMPROVE_MODEL_ACQUISITION_REJECTED "
                        f"attempt={attempt} failure={failure}"
                    )
                    raise
                except BaseException as exc:
                    if reservation is not None and candidate_identity is not None:
                        reservation.mark_failed(candidate_identity)
                    if not isinstance(exc, (RuntimeError, ValueError)):
                        raise
                    self._record_candidate_outcome(
                        plan,
                        candidate,
                        outcomes,
                        False,
                        outcome_ids,
                    )
                    self.progress_sink(
                        "SELF_IMPROVE_PROPOSAL_REJECTED "
                        f"attempt={attempt} "
                        f"{_validation_retry_feedback(exc, proposal_protocol=_proposal_protocol(prompt))}"
                    )
                    if attempt == plan.max_attempts:
                        raise
                    prompt = self._validation_retry_prompt(prompt, exc)
                    continue

                bound = PlanBoundProposal(
                    proposal=proposal,
                    attempt_identity_digest=plan.attempt_identity_digest,
                )
                try:
                    final = self.attempt_evaluator(
                        plan.task,
                        plan.reference,
                        bound,
                        attempt,
                        expected_attempt_identity_digest=plan.attempt_identity_digest,
                        merge=False,
                    )
                    approved_identity = _validate_approved_result_identity(
                        final,
                        bound,
                        plan.attempt_identity_digest,
                    )
                except BaseException:
                    if reservation is not None and candidate_identity is not None:
                        reservation.mark_failed(candidate_identity)
                    raise
                if reservation is not None and candidate_identity is not None and not final.comparison.accepted:
                    reservation.mark_failed(candidate_identity)
                self._record_candidate_outcome(
                    plan,
                    candidate,
                    outcomes,
                    final.comparison.accepted,
                    outcome_ids,
                    approved_identity=approved_identity,
                )
                self.progress_sink(
                    "SELF_IMPROVE_ATTEMPT_END "
                    f"attempt={attempt} score={final.comparison.score:.2f} "
                    f"accepted={final.comparison.accepted} "
                    f"blockers={json.dumps(final.comparison.blockers)} "
                    f"attempt_identity_digest={approved_identity}"
                )
                if final.comparison.accepted:
                    return ManagedRunResult(
                        final_result=final,
                        attempts=attempt,
                        plan_identity_digest=plan.identity_digest,
                        attempted_model_ids=tuple(attempted_models),
                        outcome_record_ids=tuple(outcome_ids),
                    )
                prompt = self._comparison_retry_prompt(
                    prompt,
                    final.comparison,
                    final.diagnostics,
                )

        if final is None:
            raise RuntimeError("no local-model attempt was executed")
        return ManagedRunResult(
            final_result=final,
            attempts=plan.max_attempts,
            plan_identity_digest=plan.identity_digest,
            attempted_model_ids=tuple(attempted_models),
            outcome_record_ids=tuple(outcome_ids),
        )

    def _plan_candidates(
        self,
        plan: ApprovedSelfImprovePlan,
        prompt: PromptPlan | str,
        manager: _LeaseManager,
        outcomes: ManagedOutcomeAdapter,
        stack: ExitStack,
    ) -> tuple[tuple[PlannedModelCandidate, ...], _Reservation]:
        prior_failed = outcomes.load_failed_model_ids(
            task_text=plan.task.objective,
            attempt_identity_digest=plan.attempt_identity_digest,
        )
        model_budget = plan.max_attempts - (1 if plan.mechanical_proposal is not None else 0)
        input_tokens = max(1, (_prompt_bytes(prompt) + 3) // 4)
        task_shape = _compact_v4_code_task_shape(plan, prompt)
        if task_shape is None:
            candidates = self.candidate_planner(
                plan.task.objective,
                plan.required_output_tokens,
                prior_failed,
                self.hardware_probe(),
                cast(CapabilityEvidenceStore, outcomes.planner_store),
                manager.resolve_revision,
                input_tokens=input_tokens,
                max_candidates=min(3, max(1, model_budget)),
                on_resolution_failure=self.resolution_failure_sink,
            )
        else:
            candidates = self.candidate_planner(
                plan.task.objective,
                plan.required_output_tokens,
                prior_failed,
                self.hardware_probe(),
                cast(CapabilityEvidenceStore, outcomes.planner_store),
                manager.resolve_revision,
                input_tokens=input_tokens,
                task_shape=task_shape,
                max_candidates=min(3, max(1, model_budget)),
                on_resolution_failure=self.resolution_failure_sink,
            )
        shape_telemetry = (
            ""
            if task_shape is None
            else (
                f" capability_floor_mb={task_shape.minimum_model_size_mb}"
                f" changed_files={task_shape.changed_files}"
                f" changed_test_files={task_shape.changed_test_files}"
                f" source_bytes={task_shape.source_bytes}"
            )
        )
        self.progress_sink(
            "SELF_IMPROVE_MODEL_PLAN "
            f"candidates={json.dumps([candidate.config.name for candidate in candidates])}"
            + shape_telemetry
        )
        if not candidates:
            raise ModelPlanError(ModelPlanFailure.EXHAUSTED)
        hints = manager.owned_identities_for_model_ids(prior_failed)
        reserved = stack.enter_context(
            manager.reserve_plan(
                tuple(self.artifact_identity(candidate) for candidate in candidates),
                failure_hints=hints,
            )
        )
        return candidates, reserved

    def _generate_proposal(
        self,
        plan: ApprovedSelfImprovePlan,
        prompt: PromptPlan | str,
        candidate: PlannedModelCandidate | None,
        manager: _LeaseManager | None,
        use_mechanical: bool,
        reservation: _Reservation | None,
        candidate_identity: ModelArtifactIdentity | None,
    ) -> ProposalManifest:
        if use_mechanical:
            if plan.mechanical_proposal is None:
                raise RuntimeError("mechanical proposal was not generated")
            return plan.mechanical_proposal
        if manager is None:
            raise RuntimeError("local model manager was not initialized")
        if plan.explicit_model_path is not None:
            acquisition = manager.acquire(
                plan.task.objective,
                explicit_path=plan.explicit_model_path,
            )
        elif candidate is not None:
            acquisition = manager.acquire(
                plan.task.objective,
                model_config=candidate.config,
                resolved_revision=candidate.resolved_revision,
            )
        else:
            raise RuntimeError("local model candidate was not selected")
        acquired_model: AcquiredModel | None = None
        try:
            with acquisition as acquired:
                acquired_model = acquired
                self.progress_sink(
                    "SELF_IMPROVE_MODEL_ACQUIRED "
                    f"model={acquired.model_id} source={acquired.source} "
                    f"revision={acquired.resolved_revision or 'explicit'} "
                    f"sha256={acquired.artifact_sha256}"
                )
                proposal = self.proposal_generator(
                    acquired.path,
                    prompt,
                    plan.task,
                    plan.reference,
                )
                if reservation is not None and candidate_identity is not None:
                    reservation.mark_eligible(candidate_identity)
                return proposal
        finally:
            if acquired_model is not None and self.release_sink is not None:
                self.release_sink(acquired_model)

    def _record_candidate_outcome(
        self,
        plan: ApprovedSelfImprovePlan,
        candidate: PlannedModelCandidate | None,
        outcomes: ManagedOutcomeAdapter | None,
        succeeded: bool,
        record_ids: list[str],
        *,
        approved_identity: str | None = None,
    ) -> None:
        if candidate is None or outcomes is None:
            return
        identity = approved_identity or plan.attempt_identity_digest
        record = outcomes.record_outcome(
            task_text=plan.task.objective,
            candidate=candidate,
            succeeded=succeeded,
            attempt_identity_digest=identity,
        )
        record_ids.append(str(record))
        self.progress_sink(
            "SELF_IMPROVE_MODEL_OUTCOME "
            f"model={candidate.config.name} succeeded={str(succeeded).lower()} "
            f"record={record} attempt_identity_digest={identity}"
        )

    def _comparison_retry_prompt(
        self,
        prompt: PromptPlan | str,
        comparison: ComparisonResult,
        diagnostics: str,
    ) -> PromptPlan | str:
        if isinstance(prompt, PromptPlan):
            if self.comparison_retry_builder is not None:
                return self.comparison_retry_builder(prompt, comparison, diagnostics)
            return build_retry_prompt_plan(prompt, comparison, diagnostics=diagnostics)
        return build_retry_prompt(prompt, comparison, diagnostics=diagnostics)

    def _validation_retry_prompt(
        self,
        prompt: PromptPlan | str,
        error: str | BaseException,
    ) -> PromptPlan | str:
        if isinstance(prompt, PromptPlan):
            if self.validation_retry_builder is not None:
                return self.validation_retry_builder(prompt, str(error))
            return _build_validation_retry_prompt_plan(prompt, error)
        return prompt + _validation_retry_suffix(error)


def build_retry_prompt_plan(
    plan: PromptPlan,
    comparison: ComparisonResult,
    *,
    diagnostics: str = "",
) -> PromptPlan:
    """Apply the same bounded retry evidence to every immutable prompt shard."""
    retry_diagnostics = (
        safe_evaluation_retry_diagnosis(diagnostics)
        if plan.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4
        else diagnostics
    )
    return PromptPlan(
        shards=tuple(
            PromptShard(
                focus_paths=shard.focus_paths,
                prompt=build_retry_prompt(
                    shard.prompt,
                    comparison,
                    diagnostics=retry_diagnostics,
                    max_diagnostic_bytes=2_048,
                    independent_candidate=(
                        plan.proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4
                    ),
                ),
                editable_ranges=shard.editable_ranges,
            )
            for shard in plan.shards
        ),
        source_bytes=plan.source_bytes,
        protocol_digest=plan.protocol_digest,
        baseline_files=plan.baseline_files,
        proposal_protocol=plan.proposal_protocol,
    )


def _proposal_protocol(prompt: PromptPlan | str) -> str:
    return (
        prompt.proposal_protocol
        if isinstance(prompt, PromptPlan)
        else COMPACT_PROPOSAL_PROTOCOL_V3
    )


def _validation_retry_feedback(
    error: str | BaseException,
    *,
    proposal_protocol: str | None = None,
) -> str:
    error_text = str(error)
    if proposal_protocol is None:
        legacy_details = {
            detail
            for detail, _kind in LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.safe_feedback
        }
        proposal_protocol = (
            COMPACT_PROPOSAL_PROTOCOL_V4
            if any(
                detail in error_text
                for detail, _kind in LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.safe_feedback
                if detail not in legacy_details
            )
            else COMPACT_PROPOSAL_PROTOCOL_V3
        )
    protocol = (
        LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
        if proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V3
        else LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
    )
    cleaned = error_text.replace("\x00", "")
    marker_details = re.findall(
        rf"{re.escape(protocol.error_marker)}[ \t]+([^\r\n]+)",
        cleaned,
    )
    parent_marker_details = re.findall(
        rf"{re.escape(protocol.parent_error_marker)}[ \t]+([^\r\n]+)",
        cleaned,
    )
    if parent_marker_details:
        source = protocol.parent_source
        candidate = parent_marker_details[-1]
    elif marker_details:
        source = protocol.marker_source
        candidate = marker_details[-1]
    else:
        source = protocol.fallback_source
        candidate = cleaned.encode("utf-8")[-protocol.fallback_tail_bytes :].decode(
            "utf-8",
            errors="replace",
        )
    feedback_type = protocol.fallback_type
    safe_detail = protocol.redacted_detail
    for expected_detail, expected_type in protocol.safe_feedback:
        if expected_detail in candidate:
            feedback_type = expected_type
            safe_detail = expected_detail
            break
    feedback = (
        f"protocol={protocol.version} type={feedback_type} "
        f"source={source} detail={safe_detail}"
    )
    telemetry = (
        _safe_compact_scope_telemetry(error)
        if isinstance(error, BaseException)
        else ""
    )
    if telemetry:
        feedback += f" telemetry={telemetry}"
    policy_telemetry = _safe_compact_policy_telemetry(candidate)
    if policy_telemetry:
        feedback += f" telemetry={policy_telemetry}"
    if len(feedback.encode("utf-8")) > protocol.max_feedback_bytes:
        return (
            f"protocol={protocol.version} type={feedback_type} "
            f"source={source} detail={safe_detail}"
        )
    return feedback


def _validation_retry_suffix(
    error: str | BaseException,
    *,
    proposal_protocol: str = COMPACT_PROPOSAL_PROTOCOL_V3,
) -> str:
    protocol = (
        LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
        if proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V3
        else LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
    )
    return (
        protocol.prompt_prefix
        + _validation_retry_feedback(error, proposal_protocol=proposal_protocol)
        + protocol.prompt_suffix
    )


def _build_validation_retry_prompt_plan(
    plan: PromptPlan,
    error: str | BaseException,
) -> PromptPlan:
    suffix = _validation_retry_suffix(
        error,
        proposal_protocol=plan.proposal_protocol,
    )
    return PromptPlan(
        shards=tuple(
            PromptShard(
                focus_paths=shard.focus_paths,
                prompt=shard.prompt + suffix,
                editable_ranges=shard.editable_ranges,
            )
            for shard in plan.shards
        ),
        source_bytes=plan.source_bytes,
        protocol_digest=plan.protocol_digest,
        baseline_files=plan.baseline_files,
        proposal_protocol=plan.proposal_protocol,
    )


def _validate_approved_result_identity(
    result: AttemptResult,
    bound_proposal: PlanBoundProposal,
    expected_attempt_identity_digest: str,
) -> str:
    expected = _validate_digest(
        "expected_attempt_identity_digest",
        expected_attempt_identity_digest,
    )
    if bound_proposal.attempt_identity_digest != expected:
        raise ValueError("proposal plan identity drifted before approval")
    if result.attempt_identity_digest != expected:
        raise ValueError("approved result plan identity drifted before outcome")
    if result.proposal != bound_proposal.proposal:
        raise ValueError("approved proposal drifted before outcome")
    return expected


def _prompt_bytes(prompt: PromptPlan | str) -> int:
    return (
        prompt.max_prompt_bytes
        if isinstance(prompt, PromptPlan)
        else len(prompt.encode("utf-8"))
    )


def _stable_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_digest(label: str, value: object) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 (64-character hexadecimal) digest")
    return value


def _validate_reference(reference: object) -> None:
    if not isinstance(reference, CodexReference):
        raise ValueError("reference must be an immutable CodexReference")
    if _SHA_RE.fullmatch(reference.baseline_sha) is None:
        raise ValueError("reference baseline_sha must be a lowercase 40-character commit")
    if _SHA_RE.fullmatch(reference.reference_sha) is None:
        raise ValueError("reference reference_sha must be a lowercase 40-character commit")
    if not isinstance(reference.changed_files, frozenset) or not reference.changed_files:
        raise ValueError("reference changed_files must be a non-empty frozenset")
    if not isinstance(reference.test_files, frozenset):
        raise ValueError("reference test_files must be a frozenset")
    if (
        isinstance(reference.changed_lines, bool)
        or not isinstance(reference.changed_lines, int)
        or reference.changed_lines < 0
    ):
        raise ValueError("reference changed_lines must be non-negative")
    _non_negative_number(reference.elapsed_seconds, "reference elapsed_seconds")


def _reference_json_value(reference: CodexReference) -> dict[str, object]:
    return {
        "baseline_sha": reference.baseline_sha,
        "changed_files": sorted(reference.changed_files),
        "changed_lines": reference.changed_lines,
        "elapsed_seconds": float(reference.elapsed_seconds),
        "reference_sha": reference.reference_sha,
        "test_files": sorted(reference.test_files),
    }


def _reference_from_json_value(value: object) -> CodexReference:
    mapping = _exact_mapping(
        value,
        required={
            "baseline_sha",
            "changed_files",
            "changed_lines",
            "elapsed_seconds",
            "reference_sha",
            "test_files",
        },
        optional=set(),
        label="reference",
    )
    changed = _string_frozenset(mapping["changed_files"], "changed_files", required=True)
    tests = _string_frozenset(mapping["test_files"], "test_files", required=False)
    reference = CodexReference(
        baseline_sha=_required_string(mapping, "baseline_sha"),
        reference_sha=_required_string(mapping, "reference_sha"),
        changed_files=changed,
        test_files=tests,
        changed_lines=_non_negative_integer(mapping["changed_lines"], "changed_lines"),
        elapsed_seconds=_non_negative_number(mapping["elapsed_seconds"], "elapsed_seconds"),
    )
    _validate_reference(reference)
    return reference


def _prompt_from_json_value(value: object) -> PromptPlan | str:
    mapping = _exact_mapping(
        value,
        required={"kind", "value"},
        optional=set(),
        label="prompt",
    )
    kind = mapping["kind"]
    if kind == "string":
        return _required_string(mapping, "value")
    if kind == "plan":
        return PromptPlan._from_json_value(mapping["value"])
    raise ValueError("prompt kind must be 'string' or 'plan'")


def _exact_mapping(
    value: object,
    *,
    required: set[str],
    optional: set[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    mapping = cast(dict[str, object], value)
    keys = set(mapping)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValueError(f"{label} is missing fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {sorted(unknown)}")
    return mapping


def _required_string(mapping: dict[str, object], key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _non_negative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} must be non-negative")
    return float(value)


def _string_frozenset(value: object, label: str, *, required: bool) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} must be a JSON string array")
    result = frozenset(value)
    if len(result) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    if required and not result:
        raise ValueError(f"{label} must not be empty")
    return result


def _canonical_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be an absolute canonical path")
    path = Path(value)
    if not path.is_absolute() or path.resolve(strict=False) != path:
        raise ValueError(f"{label} must be an absolute canonical path")
    return path


def _optional_canonical_path(value: object, label: str) -> Path | None:
    return None if value is None else _canonical_path(value, label)


__all__ = (
    "ApprovedSelfImprovePlan",
    "AttemptResult",
    "CapabilityEvidenceOutcomeAdapter",
    "ManagedOutcomeAdapter",
    "ManagedRunResult",
    "ManagedSelfImproveRunner",
    "ModelPlanError",
    "ModelPlanFailure",
    "PlanBoundProposal",
    "PromptPlan",
    "PromptShard",
    "TaskSpec",
    "_attempt_identity_digest",
    "_build_validation_retry_prompt_plan",
    "_is_safe_make_command",
    "_validate_approved_result_identity",
    "_validate_attempt_identity_digest",
    "apply_proposal",
    "build_retry_prompt_plan",
)
