"""Immutable data contracts shared by managed self-improvement orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V3,
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
    CandidateEvidence,
    CodexReference,
    CompactSpanProposal,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.managed_prompt_contracts import PromptShard
from general_ludd.self_improve.model_candidates import ModelCandidateProvider

_MAX_TASK_BYTES: Final = 262_144
_FORBIDDEN_COMMAND_CHARS: Final = frozenset(";|&$()<>\n\r")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_TASK_RE: Final = re.compile(r"^S[0-9]+(?:\.[0-9]+)?$")


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
    sampling_profile: str = DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID
    repair_proposals: tuple[CompactSpanProposal, ...] = field(
        default=(),
        repr=False,
    )
    repair_diagnosis_path_sha256: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        """Require immutable non-overlapping shards and stable protocol identity."""
        if not isinstance(self.shards, tuple) or not self.shards:
            raise ValueError("prompt plan must contain at least one shard in an immutable tuple")
        if (
            isinstance(self.source_bytes, bool)
            or not isinstance(self.source_bytes, int)
            or self.source_bytes < 0
        ):
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
        if not isinstance(self.sampling_profile, str) or self.sampling_profile not in {
            DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
            COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
        }:
            raise ValueError("prompt plan sampling profile is unsupported")
        if (
            self.sampling_profile != DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID
            and self.proposal_protocol != COMPACT_PROPOSAL_PROTOCOL_V4
        ):
            raise ValueError("repair sampling profile requires compact-v4")
        if not isinstance(self.repair_proposals, tuple) or not all(
            isinstance(item, CompactSpanProposal) for item in self.repair_proposals
        ):
            raise ValueError("repair proposals must be an immutable compact tuple")
        if self.repair_proposals:
            repair_paths = tuple(item.focus_path for item in self.repair_proposals)
            repair_path_digests = {
                hashlib.sha256(path.encode("utf-8")).hexdigest()
                for path in repair_paths
            }
            if (
                self.proposal_protocol != COMPACT_PROPOSAL_PROTOCOL_V4
                or self.sampling_profile
                != COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID
                or repair_paths != tuple(paths)
                or self.repair_diagnosis_path_sha256 not in repair_path_digests
            ):
                raise ValueError(
                    "repair proposals must match every compact-v4 shard in order"
                )
        elif self.repair_diagnosis_path_sha256:
            raise ValueError("repair diagnosis path requires compact repair proposals")
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
        value: dict[str, object] = {
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
        if self.sampling_profile != DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID:
            value["sampling_profile"] = self.sampling_profile
        return value

    def _json_value(self) -> dict[str, object]:
        if self.repair_proposals:
            raise ValueError("ephemeral compact repair state cannot be serialized")
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
            if self.sampling_profile != DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID:
                value["sampling_profile"] = self.sampling_profile
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
            optional=(
                {"sampling_profile"}
                if proposal_protocol == COMPACT_PROPOSAL_PROTOCOL_V4
                else set()
            ),
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
            sampling_profile=cast(
                str,
                mapping.get(
                    "sampling_profile",
                    DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
                ),
            ),
        )

    def __contains__(self, value: object) -> bool:
        """Support compatibility membership checks across every shard prompt."""
        return isinstance(value, str) and any(value in shard.prompt for shard in self.shards)

    @property
    def max_prompt_bytes(self) -> int:
        """Return the largest individual inference input."""
        return max(len(shard.prompt.encode("utf-8")) for shard in self.shards)


def _validate_attempt_identity_digest(value: object) -> str:
    """Return one canonical attempt identity or fail closed."""
    return _validate_digest("attempt identity", value)


@dataclass(frozen=True, slots=True)
class PlanBoundProposal:
    """A proposal inseparably bound to the trusted parent prompt plan."""

    proposal: ProposalManifest
    attempt_identity_digest: str
    policy_digest: str = ""

    def __post_init__(self) -> None:
        """Reject unvalidated manifests and non-canonical plan identities."""
        if not isinstance(self.proposal, ProposalManifest):
            raise ValueError("plan-bound proposal must contain a proposal manifest")
        _validate_digest("attempt identity", self.attempt_identity_digest)
        if self.policy_digest:
            _validate_digest("policy_digest", self.policy_digest)


@dataclass(frozen=True, slots=True)
class GeneratedProposal:
    """One validated manifest with optional in-memory compact-v4 repair material."""

    proposal: ProposalManifest = field(repr=False)
    compact_proposals: tuple[CompactSpanProposal, ...] = field(default=(), repr=False)
    evaluated_result: AttemptResult | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    selected_candidate_identity_digest: str = ""
    selected_candidate_provider: ModelCandidateProvider | None = None
    candidate_plan_digest: str = ""
    routed_local_accepted: bool | None = None

    def __post_init__(self) -> None:
        """Bind compact repair material to exactly the expanded manifest paths."""
        if not isinstance(self.proposal, ProposalManifest):
            raise ValueError("generated proposal must contain a proposal manifest")
        if not isinstance(self.compact_proposals, tuple) or not all(
            isinstance(item, CompactSpanProposal) for item in self.compact_proposals
        ):
            raise ValueError("generated compact proposals must be an immutable tuple")
        if self.compact_proposals:
            compact_paths = tuple(item.focus_path for item in self.compact_proposals)
            manifest_paths = tuple(edit.path for edit in self.proposal.edits)
            if compact_paths != manifest_paths:
                raise ValueError(
                    "generated compact proposals must match manifest paths in order"
                )
        if self.evaluated_result is not None and (
            not isinstance(self.evaluated_result, AttemptResult)
            or self.evaluated_result.proposal != self.proposal
        ):
            raise ValueError("pre-evaluated result must match the generated proposal")
        route_fields = (
            self.selected_candidate_identity_digest,
            self.selected_candidate_provider,
            self.candidate_plan_digest,
        )
        if any(field_value not in {"", None} for field_value in route_fields):
            _validate_digest(
                "selected_candidate_identity_digest",
                self.selected_candidate_identity_digest,
            )
            _validate_digest("candidate_plan_digest", self.candidate_plan_digest)
            if not isinstance(
                self.selected_candidate_provider,
                ModelCandidateProvider,
            ):
                raise ValueError(
                    "selected_candidate_provider must be a ModelCandidateProvider"
                )
            if self.evaluated_result is None:
                raise ValueError("routed proposals require their evaluated result")
        if self.routed_local_accepted is not None and not isinstance(
            self.routed_local_accepted,
            bool,
        ):
            raise ValueError("routed_local_accepted must be an explicit boolean")


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
        raise ValueError(
            f"{label} must be a lowercase SHA-256 (64-character hexadecimal) digest"
        )
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


def _string_frozenset(
    value: object,
    label: str,
    *,
    required: bool,
) -> frozenset[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
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


def _optional_absolute_path(value: object, label: str) -> Path | None:
    """Validate one lexical absolute path while preserving a final symlink name."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be an absolute canonical path")
    path = Path(value)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        raise ValueError(f"{label} must be an absolute canonical path")
    return path


_COMPATIBILITY_MODULE: Final = "general_ludd.self_improve.managed_runner"
AttemptResult.__module__ = _COMPATIBILITY_MODULE
GeneratedProposal.__module__ = _COMPATIBILITY_MODULE
PlanBoundProposal.__module__ = _COMPATIBILITY_MODULE
PromptPlan.__module__ = _COMPATIBILITY_MODULE
TaskSpec.__module__ = _COMPATIBILITY_MODULE


__all__ = (
    "AttemptResult",
    "GeneratedProposal",
    "PlanBoundProposal",
    "PromptPlan",
    "TaskSpec",
)
