"""Bounded local-model proposal comparison against a Codex reference."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import NotRequired, Protocol, TypedDict, cast, runtime_checkable

from general_ludd.self_improve.model_lifecycle import ModelArtifactIdentity

_MAX_EDITS = 32
_MAX_TESTS = 64
_MAX_COMMANDS = 32
_MAX_CONTENT_BYTES = 1_048_576
_MAX_COMMAND_BYTES = 4096
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_TASK_RE = re.compile(r"^S[0-9]+(?:\.[0-9]+)?$")
_SHELL_METACHARACTERS = frozenset(";|&$()<>\n\r")
_SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b(token|psk|password|secret)=([^\s]+)")
_PROMPT_BATCH_MARKER = "GLUDD_SELF_IMPROVE_PROMPT_BATCH_V1\n"
_PROMPT_BATCH_PROTOCOL = "self-improve-local-prompt-batch-v1"
_PROPOSAL_BATCH_PROTOCOL = "self-improve-local-proposal-batch-v1"
_COMPACT_SPAN_BATCH_PROTOCOL = "self-improve-local-proposal-batch-v2"
_MAX_PROMPT_BATCH_SHARDS = 32
_MAX_PROMPT_BATCH_BYTES = 262_144
_MAX_PROMPT_SHARD_BYTES = 16_384
_PROTOCOL_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PLANNER_FEEDBACK_SCHEMA_VERSION = 1
_PLANNER_FEEDBACK_KIND = "self-improve-planner-feedback"
_PLANNER_FEEDBACK_SOURCE_KIND = "managed-self-improve-result"
_MAX_PLANNER_FEEDBACK_BYTES = 65_536
_MAX_TASK_OBJECTIVE_BYTES = 32_768
_MAX_BLOCKERS = 32
_MAX_BLOCKER_BYTES = 128
_SNAPSHOT_MANIFEST_SCHEMA_VERSION = 2
_MAX_SNAPSHOT_CONTENT_BYTES = 8_391_680


_PROPOSAL_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "baseline_sha",
        "task_id",
        "edits",
        "tests",
        "make_commands",
        "commit_message",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "baseline_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "task_id": {"type": "string", "pattern": "^S[0-9.]+$"},
        "edits": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_EDITS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation", "path", "old_text", "new_text"],
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["replace", "create", "delete"],
                    },
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
            },
        },
        "tests": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_TESTS,
            "items": {"type": "string"},
        },
        "make_commands": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_COMMANDS,
            "items": {
                "type": "string",
                "pattern": "^make [^;|&$()<>\\n\\r]+$",
            },
        },
        "commit_message": {"type": "string"},
    },
}

COMPACT_PROPOSAL_PROTOCOL_V3 = "self-improve-compact-proposal-v3"
COMPACT_PROPOSAL_PROTOCOL_V4 = "self-improve-compact-proposal-v4"
COMPACT_PROPOSAL_CONTRACT_TRANSPORT_PROTOCOL = (
    "self-improve-local-proposal-contract-file-v2"
)
_LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION = COMPACT_PROPOSAL_PROTOCOL_V3
_COMPACT_PROPOSAL_PROTOCOL_VERSION = COMPACT_PROPOSAL_PROTOCOL_V4
_COMPACT_PROPOSAL_TOKENS = 1024
_COMPACT_SPAN_PROPOSAL_TOKENS = 4096
_COMPACT_MAX_CONTENT_BYTES = 3072
_COMPACT_MAX_UTF8_BYTES_PER_CODEPOINT = 4
_COMPACT_MAX_STRING_CODEPOINTS = (
    _COMPACT_MAX_CONTENT_BYTES // _COMPACT_MAX_UTF8_BYTES_PER_CODEPOINT
)
_COMPACT_SPAN_MAX_OLD_LINES = 64
_COMPACT_SPAN_MAX_NEW_LINES = 64
_COMPACT_SPAN_MAX_CHANGED_LINES = 96
_COMPACT_FOCUS_PATH_MARKER = "GLUDD_SELF_IMPROVE_FOCUS_PATH="
_COMPACT_EDITABLE_RANGES_MARKER = "GLUDD_SELF_IMPROVE_EDITABLE_RANGES="
_COMPACT_MAX_SCOPE_MARKER_BYTES = _MAX_PROMPT_SHARD_BYTES
_COMPACT_MAX_SCOPE_COORDINATES = 2048
_COMPACT_MAX_DIAGNOSTIC_RANGES = 4
_COMPACT_MAX_SCOPE_TELEMETRY_BYTES = 256
_COMPACT_COMMIT_MESSAGE = "fix: apply bounded self-improvement proposal"
_STRUCTURED_CANARY_TOKENS = 32
_DETERMINISTIC_DECODE_TEMPERATURE = 0.0
_DETERMINISTIC_DECODE_SEED = 0
DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID = "deterministic-greedy-v1"
COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID = (
    "compact-v4-syntax-repair-span-provenance-v6"
)
COMPACT_V4_REPAIR_SEED_DERIVATION_POLICY_ID = (
    "compact-v4-repair-seed-context-sha256-indexed-prefix31-v2"
)
COMPACT_V4_REPAIR_CANDIDATE_LIMIT = 3
COMPACT_V4_REPAIR_CANDIDATE_FEEDBACK_POLICY_ID = (
    "compact-v4-repair-chain-previous-compact-syntax-v1"
)
COMPACT_V4_REPAIR_SHARD_STATE_POLICY_ID = (
    "compact-v4-repair-freeze-valid-shards-immutable-snapshot-v1"
)
COMPACT_V4_REPAIR_SHARD_PROMPT_POLICY_ID = (
    "compact-v4-repair-one-path-role-frozen-siblings-v1"
)
COMPACT_V4_REPAIR_SPAN_PROVENANCE_POLICY_ID = (
    "compact-v4-repair-output-line-unique-span-freeze-v1"
)
_COMPACT_V4_SYNTAX_REPAIR_TEMPERATURE = 0.8
_COMPACT_V4_SYNTAX_REPAIR_TOP_P = 0.95
_COMPACT_V4_SYNTAX_REPAIR_TOP_K = 40
_COMPACT_V4_MAX_DERIVED_SEED = (2**31) - 1
_STRUCTURED_OUTPUT_REQUIRE_STOP = True
_LEGACY_STRUCTURED_DECODING_MODE = "llama-cpp-bounded-span-grammar-v3"
_STRUCTURED_DECODING_MODE = "llama-cpp-bounded-span-grammar-v5"
_MODEL_VISIBLE_PROMPT_POLICY = "validated-parent-metadata-stripped-v1"
_COMPACT_COMPARISON_RETRY_POLICY = "independent-trusted-baseline-smallest-diff-v1"
_COMPACT_LINE_MATERIALIZATION_POLICY = "trusted-eol-full-snapshot-v1"
_STRUCTURED_CANARY_PROMPT = 'Return {"ok":true}.'
_STRUCTURED_CANARY_EXPECTED: dict[str, object] = {"ok": True}
_COMPACT_ROOT_FIELDS = frozenset({"e"})
_LEGACY_COMPACT_EDIT_FIELDS = frozenset({"a", "z"})
_COMPACT_EDIT_FIELDS = frozenset({"s", "n", "z"})
_COMPACT_MAX_EDITS = 16
_COMPACT_SPAN_MAX_EDITS = 4
_COMPACT_OPERATION_BY_EMPTY_TEXT = {
    (False, False): "replace",
    (False, True): "delete",
    (True, False): "create",
}
_LEGACY_STRICT_PARENT_DECODER_VERSION = "proposal-manifest-strict-v3"
_STRICT_PARENT_DECODER_VERSION = "proposal-manifest-strict-v4-snapshot-lines-v6"
_SAFE_FINISH_REASONS = frozenset(
    {"stop", "length", "tool_calls", "function_call", "content_filter"}
)
_STRUCTURED_CANARY_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean", "const": True}},
}
_COMPACT_PROPOSAL_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["e"],
    "properties": {
        "e": {
            "type": "array",
            "minItems": 1,
            "maxItems": _COMPACT_SPAN_MAX_EDITS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["s", "n", "z"],
                "properties": {
                    "s": {"type": "integer", "minimum": 1},
                    "n": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": _COMPACT_SPAN_MAX_OLD_LINES,
                    },
                    "z": {
                        "type": "string",
                        "maxLength": _COMPACT_MAX_STRING_CODEPOINTS,
                    },
                },
            },
        },
    },
}


def _validated_compact_editable_ranges(
    ranges: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    """Validate one bounded canonical set of 1-based half-open shown ranges."""
    if not isinstance(ranges, tuple):
        raise ValueError("compact editable ranges must be an immutable tuple")
    previous_end = 1
    coordinates: set[int] = set()
    for item in ranges:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in item
            )
        ):
            raise ValueError("compact editable ranges must contain integer pairs")
        start, end = item
        if start > _MAX_CONTENT_BYTES + 1 or end > _MAX_CONTENT_BYTES + 1:
            raise ValueError(
                "compact editable ranges are outside bounded baseline coordinates"
            )
        if start < 1 or end <= start or start < previous_end:
            raise ValueError("compact editable ranges must be ordered half-open ranges")
        if end - start + 1 > _COMPACT_MAX_SCOPE_COORDINATES:
            raise ValueError(
                "compact scope coordinate enum exceeds "
                f"{_COMPACT_MAX_SCOPE_COORDINATES} entries"
            )
        coordinates.update(range(start, end + 1))
        if len(coordinates) > _COMPACT_MAX_SCOPE_COORDINATES:
            raise ValueError(
                "compact scope coordinate enum exceeds "
                f"{_COMPACT_MAX_SCOPE_COORDINATES} entries"
            )
        previous_end = end
    return ranges


def _compact_proposal_schema_for_ranges(
    ranges: tuple[tuple[int, int], ...],
) -> dict[str, object]:
    """Specialize compact-v4 s/n bounds from one trusted prompt shard."""
    validated = _validated_compact_editable_ranges(ranges)
    coordinates = sorted(
        {coordinate for start, end in validated for coordinate in range(start, end + 1)}
    ) or [1]
    max_old_lines = min(
        _COMPACT_SPAN_MAX_OLD_LINES,
        max((end - start for start, end in validated), default=0),
    )
    schema = copy.deepcopy(_COMPACT_PROPOSAL_JSON_SCHEMA)
    root_properties = cast(dict[str, object], schema["properties"])
    edits = cast(dict[str, object], root_properties["e"])
    edit_slots = min(_COMPACT_SPAN_MAX_EDITS, len(coordinates))
    edits["maxItems"] = edit_slots
    item = cast(dict[str, object], edits["items"])
    properties = cast(dict[str, object], item["properties"])
    properties["s"] = {"type": "integer", "enum": coordinates}
    properties["n"] = {
        "type": "integer",
        "minimum": 0,
        "maximum": max_old_lines,
    }
    properties["z"] = {
        "type": "string",
        "maxLength": _COMPACT_MAX_STRING_CODEPOINTS,
    }
    return schema


_COMPACT_SYSTEM_PROMPT = (
    "Return one compact JSON object with only e; stop immediately after its closing }. "
    f"Each e item has only s, n, z. Emit the fewest complete edits, at most "
    f"{_COMPACT_SPAN_MAX_EDITS}; never repeat an edit, coordinate, or unchanged context. "
    "Prefer increasing s; the parent sorts valid non-overlapping snapshot spans. It owns path, "
    "baseline, commit metadata, and line-separator bytes. s is the 1-based baseline "
    "start, n is old lines consumed, and z is logical replacement-line content without "
    "labels; the parent preserves trusted LF/CRLF and final-newline boundaries, so do not "
    "add unchanged neighboring lines. Choose s only from the per-shard grammar enum. "
    f"Each edit may consume at most {_COMPACT_SPAN_MAX_OLD_LINES} old lines and "
    f"contain at most {_COMPACT_SPAN_MAX_NEW_LINES} replacement lines. Across every "
    f"shard, emit at most {_COMPACT_SPAN_MAX_CHANGED_LINES} changed lines, counting "
    "old lines consumed plus replacement lines. "
    "For a shown Lx..Ly section, n=0 may insert with s=x..y+1 only; never select a "
    "boundary wholly inside a hidden gap. Empty z deletes consumed lines; s=1, n=0 "
    "creates an absent file. The edit must change content. Each z may contain at most "
    f"{_COMPACT_MAX_STRING_CODEPOINTS} Unicode code points; across all edits, z may "
    "contain at most 3,072 UTF-8 bytes total. Never copy protocol metadata, file "
    "markers, numbered-line labels, or shell/environment assignments into z. For a "
    "Python focus file, z must remain syntactically valid in its shown indentation."
)

_LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["e"],
    "properties": {
        "e": {
            "type": "array",
            "minItems": 1,
            "maxItems": _COMPACT_MAX_EDITS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["a", "z"],
                "properties": {"a": {"type": "string"}, "z": {"type": "string"}},
            },
        }
    },
}
_LEGACY_COMPACT_SYSTEM_PROMPT = (
    "Return exactly one compact JSON object with only e and no prose. "
    "e is an array whose entries contain only a and z. The trusted parent supplies "
    "the one focus path and commit message; never emit either. a is exact old text and "
    "z is new text. For a replacement, a and z must be distinct non-empty strings. "
    "Empty a creates and empty z deletes; they cannot both be empty. Across all edits, "
    "a and z may contain at most 3,072 UTF-8 bytes total. Use the shortest unique exact "
    "replacement and never reproduce a whole file."
)


@dataclass(frozen=True)
class ValidationRetryProtocol:
    """Identity-bearing safe feedback contract for proposal-validation retries."""

    version: str
    error_marker: str
    marker_source: str
    parent_error_marker: str
    parent_source: str
    fallback_source: str
    fallback_tail_bytes: int
    max_feedback_bytes: int
    fallback_type: str
    redacted_detail: str
    prompt_prefix: str
    prompt_suffix: str
    safe_feedback: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ModelAttemptOutcomeProtocol:
    """Identity-bearing boundary between lifecycle failures and model outcomes."""

    version: str
    acquisition_failure: str
    plan_exhaustion: str
    outcome_eligibility: str


@dataclass(frozen=True)
class EvaluationDiagnosisProtocol:
    """Identity-bearing bounded lifecycle and retry-evidence contract."""

    version: str
    schema_version: int
    max_event_bytes: int
    max_diagnosis_bytes: int
    max_duration_ms: int
    max_coordinate: int
    phase_kinds: tuple[tuple[str, str], ...]
    diagnosis_failure_classes: tuple[str, ...]
    syntax_categories: tuple[str, ...]
    failure_hypothesis: str
    unavailable_hypothesis: str


LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL = ModelAttemptOutcomeProtocol(
    version="self-improve-model-attempt-outcome-v2",
    acquisition_failure="terminal_typed_no_model_outcome",
    plan_exhaustion="terminal_typed_no_attempt_or_model_outcome",
    outcome_eligibility="candidate_reached_proposal_generation",
)


EVALUATION_DIAGNOSIS_PROTOCOL = EvaluationDiagnosisProtocol(
    version="self-improve-evaluation-diagnosis-v2",
    schema_version=3,
    max_event_bytes=384,
    max_diagnosis_bytes=768,
    max_duration_ms=3_600_000,
    max_coordinate=2_097_153,
    phase_kinds=(
        ("apply", "filesystem_apply"),
        ("syntax_preflight", "syntax_preflight"),
        ("approved_make", "approved_make"),
        ("test_count", "approved_test_count"),
        ("stage", "repository_stage"),
        ("commit", "repository_commit"),
        ("clean", "repository_clean"),
        ("patch_equivalence", "patch_equivalence"),
        ("merge", "repository_merge"),
        ("cleanup", "worktree_cleanup"),
        ("comparison", "quality_comparison"),
        ("evaluation", "unknown"),
    ),
    diagnosis_failure_classes=(
        "apply_failed",
        "python_syntax",
        "python_path",
        "python_read",
        "python_size",
        "python_encoding",
        "make_failed",
        "test_count_failed",
        "stage_failed",
        "commit_failed",
        "clean_failed",
        "patch_equivalence_failed",
        "merge_failed",
        "cleanup_failed",
        "quality_rejected",
        "diagnosis_unavailable",
    ),
    syntax_categories=(
        "python_syntax",
        "python_path",
        "python_read",
        "python_size",
        "python_encoding",
    ),
    failure_hypothesis="approved evaluation failed; correct only the typed phase",
    unavailable_hypothesis="evaluation diagnosis was unavailable",
)


LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL = ValidationRetryProtocol(
    version="self-improve-validation-retry-v3",
    error_marker="SELF_IMPROVE_LOCAL_PROPOSAL_ERROR",
    marker_source="proposal_error",
    parent_error_marker="SELF_IMPROVE_PARENT_PROPOSAL_ERROR",
    parent_source="parent_validation",
    fallback_source="worker_tail",
    fallback_tail_bytes=512,
    max_feedback_bytes=256,
    fallback_type="proposal_validation",
    redacted_detail="<redacted>",
    prompt_prefix="\nPrevious output failed strict proposal validation: ",
    prompt_suffix="\nReturn a complete object satisfying every required field.",
    safe_feedback=(
        ("replace requires distinct non-empty old_text", "edit_replace_contract"),
        (
            "create requires empty old_text and non-empty new_text",
            "edit_create_contract",
        ),
        (
            "delete requires non-empty old_text and empty new_text",
            "edit_delete_contract",
        ),
        ("compact edit must change content", "edit_content_contract"),
        (
            "compact proposal is not one complete JSON object",
            "proposal_json_contract",
        ),
        ("compact proposal must contain exactly e", "proposal_root_contract"),
        (
            "each compact edit must contain exactly a and z",
            "edit_shape_contract",
        ),
        ("compact edit content exceeds 3072 bytes", "edit_content_budget"),
        ("compact edit text fields must be strings", "edit_text_contract"),
        (
            "local model exhausted the proposal token budget before completion",
            "decode_budget",
        ),
        ("local model did not complete structured output", "decode_completion"),
        ("local model response has no proposal text", "decode_empty"),
        ("proposal batch protocol identity drifted", "protocol_identity"),
        (
            "proposal batch count does not match the prompt plan",
            "proposal_batch_count",
        ),
        (
            "replace old_text must occur exactly once in trusted baseline",
            "edit_replace_precondition",
        ),
        (
            "create target must be absent in trusted baseline",
            "edit_create_precondition",
        ),
        (
            "delete old_text must equal the complete trusted baseline file",
            "edit_delete_precondition",
        ),
        (
            "proposal shard edits must cover the exact focus paths",
            "proposal_scope",
        ),
    ),
)

LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL = ValidationRetryProtocol(
    version="self-improve-validation-retry-v5",
    error_marker=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.error_marker,
    marker_source=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.marker_source,
    parent_error_marker=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.parent_error_marker,
    parent_source=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.parent_source,
    fallback_source=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.fallback_source,
    fallback_tail_bytes=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.fallback_tail_bytes,
    max_feedback_bytes=512,
    fallback_type=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.fallback_type,
    redacted_detail=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.redacted_detail,
    prompt_prefix=LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.prompt_prefix,
    prompt_suffix=(
        "\nUse only an s value permitted by the per-shard JSON grammar. "
        "For shown Lx-Ly, use insertion s=x..y+1; never a hidden gap. "
        f"Return at most {_COMPACT_SPAN_MAX_EDITS} non-overlapping edits, at most "
        f"{_COMPACT_SPAN_MAX_OLD_LINES} old and {_COMPACT_SPAN_MAX_NEW_LINES} new "
        f"lines per edit, and {_COMPACT_SPAN_MAX_CHANGED_LINES} changed lines total."
    ),
    safe_feedback=(
        *LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.safe_feedback,
        (
            "compact span coordinates must be integers, not booleans",
            "edit_span_coordinate",
        ),
        ("compact span start line must be positive", "edit_span_coordinate"),
        ("compact span old line count must be non-negative", "edit_span_coordinate"),
        (
            f"compact proposal edits must contain 1..{_COMPACT_SPAN_MAX_EDITS} entries",
            "edit_span_count",
        ),
        ("compact spans must not overlap", "edit_span_overlap"),
        (
            "compact spans must use distinct start coordinates",
            "edit_span_duplicate",
        ),
        ("each compact edit must contain exactly n, s, and z", "edit_span_shape"),
        ("compact span new text must be a string", "edit_span_text"),
        ("compact span new text exceeds 3072 bytes", "edit_content_budget"),
        (
            f"compact span old lines exceed {_COMPACT_SPAN_MAX_OLD_LINES}",
            "edit_line_budget",
        ),
        (
            f"compact span new lines exceed {_COMPACT_SPAN_MAX_NEW_LINES}",
            "edit_line_budget",
        ),
        (
            f"compact span changed lines exceed {_COMPACT_SPAN_MAX_CHANGED_LINES}",
            "edit_line_budget",
        ),
        ("compact span must change content", "edit_content_contract"),
        (
            "compact span is outside trusted baseline lines",
            "edit_span_precondition",
        ),
        (
            "compact span must consume only explicitly shown baseline lines",
            "edit_span_scope",
        ),
        (
            "compact insertion must use s from the first shown line through one past "
            "the last shown line of one contiguous section",
            "edit_span_scope",
        ),
        (
            "compact-v4 proposal is not one complete JSON object",
            "proposal_json_contract",
        ),
        ("compact absent file create must use s=1 and n=0", "edit_create_contract"),
        (
            "compact absent file must not advertise editable baseline ranges",
            "edit_create_contract",
        ),
        (
            "compact span has no unique bounded baseline anchor",
            "edit_span_precondition",
        ),
        ("compact derived anchors must not overlap", "edit_span_precondition"),
    ),
)


def compact_v4_syntax_repair_sampling_identity() -> dict[str, object]:
    """Return the exact repair-only sampler policy bound into attempt identity."""
    return {
        "profile": COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
        "seed_derivation": COMPACT_V4_REPAIR_SEED_DERIVATION_POLICY_ID,
        "candidate_limit": COMPACT_V4_REPAIR_CANDIDATE_LIMIT,
        "candidate_feedback": COMPACT_V4_REPAIR_CANDIDATE_FEEDBACK_POLICY_ID,
        "temperature": _COMPACT_V4_SYNTAX_REPAIR_TEMPERATURE,
        "top_k": _COMPACT_V4_SYNTAX_REPAIR_TOP_K,
        "top_p": _COMPACT_V4_SYNTAX_REPAIR_TOP_P,
        "shard_state": COMPACT_V4_REPAIR_SHARD_STATE_POLICY_ID,
        "shard_prompt": COMPACT_V4_REPAIR_SHARD_PROMPT_POLICY_ID,
        "span_provenance": COMPACT_V4_REPAIR_SPAN_PROVENANCE_POLICY_ID,
    }


def _compact_output_identity(selected_protocol: str, *, legacy: bool) -> dict[str, object]:
    """Describe the model-visible compact output contract."""
    return {
        "protocol_version": selected_protocol,
        "schema": (
            _LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA if legacy else _COMPACT_PROPOSAL_JSON_SCHEMA
        ),
        "system_prompt": _LEGACY_COMPACT_SYSTEM_PROMPT if legacy else _COMPACT_SYSTEM_PROMPT,
        "max_content_bytes": _COMPACT_MAX_CONTENT_BYTES,
        "trusted_focus_path_marker": _COMPACT_FOCUS_PATH_MARKER,
        "trusted_editable_ranges_marker": None if legacy else _COMPACT_EDITABLE_RANGES_MARKER,
        "trusted_commit_message": _COMPACT_COMMIT_MESSAGE,
        "model_visible_prompt_policy": None if legacy else _MODEL_VISIBLE_PROMPT_POLICY,
    }


def _structured_decoding_identity(*, legacy: bool) -> dict[str, object]:
    """Describe grammar-bound decoding without changing digest material."""
    proposal_schema = (
        _LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA if legacy else _COMPACT_PROPOSAL_JSON_SCHEMA
    )
    return {
        "mode": _LEGACY_STRUCTURED_DECODING_MODE if legacy else _STRUCTURED_DECODING_MODE,
        "proposal_schema_strategy": (
            "static-v3" if legacy else "parent-enum-coordinate-four-items-line-and-codepoint-bounds-v6"
        ),
        "max_scope_coordinates": None if legacy else _COMPACT_MAX_SCOPE_COORDINATES,
        "max_scope_marker_bytes": None if legacy else _COMPACT_MAX_SCOPE_MARKER_BYTES,
        "scope_marker_encoding": None if legacy else "canonical-ascii-json-v1",
        "canary_grammar_schema_sha256": hashlib.sha256(
            json.dumps(
                _STRUCTURED_CANARY_SCHEMA,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "proposal_grammar_schema_sha256": hashlib.sha256(
            json.dumps(
                proposal_schema,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    }


def _strict_decoder_identity(*, legacy: bool) -> dict[str, object]:
    """Describe parent-side validation and materialization semantics."""
    operation_policy = [
        {"new_text_empty": new, "old_text_empty": old, "operation": operation}
        for (old, new), operation in sorted(_COMPACT_OPERATION_BY_EMPTY_TEXT.items())
    ]
    identity: dict[str, object] = {
        "authoritative_manifest_schema": _PROPOSAL_JSON_SCHEMA,
        "batch_protocol": _PROPOSAL_BATCH_PROTOCOL if legacy else _COMPACT_SPAN_BATCH_PROTOCOL,
        "command_max_bytes": _MAX_COMMAND_BYTES,
        "content_max_bytes": _MAX_CONTENT_BYTES,
        "edit_fields": sorted(_LEGACY_COMPACT_EDIT_FIELDS if legacy else _COMPACT_EDIT_FIELDS),
        "max_commands": _MAX_COMMANDS,
        "max_compact_edits": _COMPACT_MAX_EDITS if legacy else _COMPACT_SPAN_MAX_EDITS,
        "max_manifest_edits": _MAX_EDITS,
        "max_tests": _MAX_TESTS,
        "operation_by_empty_text": operation_policy if legacy else None,
        "parent_decoder_version": (
            _LEGACY_STRICT_PARENT_DECODER_VERSION if legacy else _STRICT_PARENT_DECODER_VERSION
        ),
        "path_policy": "confined-pure-posix-v1",
        "precondition_policy": (
            "sequential-exact-trusted-baseline-v1"
            if legacy
            else "numbered-shown-lines-to-complete-exact-snapshot-v2"
        ),
        "root_fields": sorted(_COMPACT_ROOT_FIELDS),
    }
    if not legacy:
        identity.update(
            coordinate_base=1,
            insertion_boundary_policy="closed-boundaries-of-shown-half-open-range",
            comparison_retry_policy=_COMPACT_COMPARISON_RETRY_POLICY,
            line_materialization_policy=_COMPACT_LINE_MATERIALIZATION_POLICY,
            line_count_policy="python-splitlines-additions-plus-deletions-v1",
            max_changed_lines=_COMPACT_SPAN_MAX_CHANGED_LINES,
            max_new_lines_per_edit=_COMPACT_SPAN_MAX_NEW_LINES,
            max_old_lines_per_edit=_COMPACT_SPAN_MAX_OLD_LINES,
            max_snapshot_content_bytes=_MAX_SNAPSHOT_CONTENT_BYTES,
            parent_manifest_schema_version=_SNAPSHOT_MANIFEST_SCHEMA_VERSION,
            span_order="parent-canonical-start-nonoverlap-v2",
        )
    return identity


def local_proposal_attempt_identity_digest(
    prompt_protocol_digest: str,
    *,
    proposal_protocol: str | None = None,
) -> str:
    """Bind one prompt plan to the complete managed local-output protocol."""
    if (
        not isinstance(prompt_protocol_digest, str)
        or _PROTOCOL_DIGEST_RE.fullmatch(prompt_protocol_digest) is None
    ):
        raise ValueError("prompt protocol digest must be a lowercase 64-character SHA-256")
    selected_protocol = proposal_protocol or _COMPACT_PROPOSAL_PROTOCOL_VERSION
    if selected_protocol not in {
        _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION,
        _COMPACT_PROPOSAL_PROTOCOL_VERSION,
    }:
        raise ValueError("compact proposal protocol is unsupported")
    legacy = selected_protocol == _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION
    payload: dict[str, object] = {
        "attempt_protocol": "self-improve-local-attempt-v1",
        "prompt_protocol_digest": prompt_protocol_digest,
        "validation_retry": asdict(
            LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
            if legacy
            else LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL
        ),
        "model_attempt_outcome": asdict(LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL),
        "compact_output": _compact_output_identity(selected_protocol, legacy=legacy),
        "structured_canary": {
            "expected": _STRUCTURED_CANARY_EXPECTED,
            "prompt": _STRUCTURED_CANARY_PROMPT,
            "schema": _STRUCTURED_CANARY_SCHEMA,
        },
        "structured_decoding": _structured_decoding_identity(legacy=legacy),
        "output_token_policy": {
            "canary_max_tokens": _STRUCTURED_CANARY_TOKENS,
            "proposal_max_tokens": _COMPACT_PROPOSAL_TOKENS if legacy else _COMPACT_SPAN_PROPOSAL_TOKENS,
            "require_stop": _STRUCTURED_OUTPUT_REQUIRE_STOP,
            "safe_finish_reasons": sorted(_SAFE_FINISH_REASONS),
            "seed": _DETERMINISTIC_DECODE_SEED,
            "temperature": _DETERMINISTIC_DECODE_TEMPERATURE,
        },
        "strict_decoder_semantics": _strict_decoder_identity(legacy=legacy),
    }
    if not legacy:
        payload["evaluation_diagnosis"] = asdict(EVALUATION_DIAGNOSIS_PROTOCOL)
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@runtime_checkable
class _LocalModel(Protocol):
    """Minimal llama.cpp-compatible inference protocol."""

    def __call__(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        echo: bool,
    ) -> object: ...


@runtime_checkable
class _ChatLocalModel(Protocol):
    """llama.cpp chat-completion interface used for constrained JSON."""

    def create_chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        seed: int,
        response_format: dict[str, object],
        grammar: object | None = None,
        top_p: float = 0.95,
        top_k: int = 40,
    ) -> object: ...


class _ProposalSamplingArguments(TypedDict):
    """Exact optional llama.cpp proposal sampler keyword arguments."""

    temperature: float
    seed: int
    top_p: NotRequired[float]
    top_k: NotRequired[int]


def _proposal_sampling_arguments(
    profile: str,
    *,
    sampling_seed: int | None = None,
) -> _ProposalSamplingArguments:
    """Resolve one trusted profile to finite llama.cpp sampler arguments."""
    if profile == DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID:
        if sampling_seed is not None:
            raise ValueError("greedy proposal contract must not carry a sampling seed")
        return {
            "temperature": _DETERMINISTIC_DECODE_TEMPERATURE,
            "seed": _DETERMINISTIC_DECODE_SEED,
        }
    if profile == COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID:
        if (
            isinstance(sampling_seed, bool)
            or not isinstance(sampling_seed, int)
            or not 1 <= sampling_seed <= _COMPACT_V4_MAX_DERIVED_SEED
        ):
            raise ValueError("repair proposal contract requires a derived sampling seed")
        return {
            "temperature": _COMPACT_V4_SYNTAX_REPAIR_TEMPERATURE,
            "top_p": _COMPACT_V4_SYNTAX_REPAIR_TOP_P,
            "top_k": _COMPACT_V4_SYNTAX_REPAIR_TOP_K,
            "seed": sampling_seed,
        }
    raise ValueError("proposal contract sampling profile is unsupported")


@runtime_checkable
class _ModelFactory(Protocol):
    """Typed constructor boundary for one local model."""

    def __call__(
        self,
        *,
        model_path: str,
        n_ctx: int,
        verbose: bool,
        n_gpu_layers: int = 0,
    ) -> _LocalModel: ...


_GrammarFactory = Callable[[dict[str, object]], object]


@runtime_checkable
class _LlamaGrammarType(Protocol):
    """Typed class-level JSON-schema grammar constructor."""

    def from_json_schema(
        self,
        json_schema: str,
        *,
        verbose: bool = True,
    ) -> object:
        """Compile one JSON schema into a llama.cpp sampler grammar."""
        ...


class _LlamaCppRuntime(Protocol):
    """Typed optional llama.cpp module boundary."""

    Llama: _ModelFactory
    LlamaGrammar: _LlamaGrammarType

    def llama_supports_gpu_offload(self) -> bool:
        """Return whether this exact runtime was built with GPU offload."""
        ...


@dataclass(frozen=True)
class ProposalEdit:
    """One exact, confined patch operation on a repository-relative file."""

    operation: str
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class CompactLineSpan:
    """One model-selected range over parent-numbered immutable baseline lines."""

    start_line: int
    old_line_count: int
    new_text: str

    def __post_init__(self) -> None:
        """Reject ambiguous coordinates and unbounded model-authored text."""
        if isinstance(self.start_line, bool) or isinstance(self.old_line_count, bool):
            raise ValueError("compact span coordinates must be integers, not booleans")
        if not isinstance(self.start_line, int) or not isinstance(
            self.old_line_count, int
        ):
            raise ValueError("compact span coordinates must be integers, not booleans")
        if self.start_line < 1:
            raise ValueError("compact span start line must be positive")
        if self.old_line_count < 0:
            raise ValueError("compact span old line count must be non-negative")
        if self.old_line_count > _COMPACT_SPAN_MAX_OLD_LINES:
            raise ValueError(
                f"compact span old lines exceed {_COMPACT_SPAN_MAX_OLD_LINES}; "
                f"received_old_lines=>{_COMPACT_SPAN_MAX_OLD_LINES} "
                f"max_old_lines={_COMPACT_SPAN_MAX_OLD_LINES}"
            )
        if not isinstance(self.new_text, str):
            raise ValueError("compact span new text must be a string")
        new_lines = len(self.new_text.splitlines())
        if new_lines > _COMPACT_SPAN_MAX_NEW_LINES:
            raise ValueError(
                f"compact span new lines exceed {_COMPACT_SPAN_MAX_NEW_LINES}; "
                f"received_new_lines=>{_COMPACT_SPAN_MAX_NEW_LINES} "
                f"max_new_lines={_COMPACT_SPAN_MAX_NEW_LINES}"
            )
        if self.old_line_count == 0 and not self.new_text:
            raise ValueError("compact span must change content")


@dataclass(frozen=True)
class CompactSpanProposal:
    """Strict v4 model output with one parent-derived focus path."""

    focus_path: str
    edits: tuple[CompactLineSpan, ...]

    def __post_init__(self) -> None:
        """Validate one bounded, canonical, single-file span proposal."""
        if not isinstance(self.focus_path, str) or not _safe_relative_path(
            self.focus_path
        ):
            raise ValueError("compact focus path is not repository-relative and confined")
        if (
            not isinstance(self.edits, tuple)
            or not 1 <= len(self.edits) <= _COMPACT_SPAN_MAX_EDITS
            or not all(isinstance(edit, CompactLineSpan) for edit in self.edits)
        ):
            detail = (
                ""
                if not isinstance(self.edits, tuple)
                or len(self.edits) <= _COMPACT_SPAN_MAX_EDITS
                else (
                    f"; received_edits=>{_COMPACT_SPAN_MAX_EDITS} "
                    f"max_edits={_COMPACT_SPAN_MAX_EDITS}"
                )
            )
            raise ValueError(
                "compact proposal edits must contain "
                f"1..{_COMPACT_SPAN_MAX_EDITS} entries{detail}"
            )
        content_bytes = sum(len(edit.new_text.encode("utf-8")) for edit in self.edits)
        if content_bytes > _COMPACT_MAX_CONTENT_BYTES:
            raise ValueError(
                f"compact span new text exceeds {_COMPACT_MAX_CONTENT_BYTES} bytes; "
                f"received_edits={len(self.edits)} "
                f"received_content_bytes=>{_COMPACT_MAX_CONTENT_BYTES} "
                f"max_edits={_COMPACT_SPAN_MAX_EDITS} "
                f"max_content_bytes={_COMPACT_MAX_CONTENT_BYTES}"
            )
        previous_start: int | None = None
        previous_end = 0
        for edit in self.edits:
            start = edit.start_line - 1
            if previous_start is not None and start == previous_start:
                raise ValueError("compact spans must use distinct start coordinates")
            if previous_start is not None and start < previous_end:
                raise ValueError("compact spans must not overlap")
            previous_start = start
            previous_end = start + edit.old_line_count

    def _json_value(self) -> dict[str, object]:
        return {
            "focus_path": self.focus_path,
            "e": [
                {"s": edit.start_line, "n": edit.old_line_count, "z": edit.new_text}
                for edit in self.edits
            ],
        }


def compact_v4_repair_shard_state_digest(
    proposals: Sequence[CompactSpanProposal],
) -> str:
    """Hash bounded frozen proposal state without exposing model-authored text."""
    if (
        isinstance(proposals, (str, bytes))
        or len(proposals) > _MAX_PROMPT_BATCH_SHARDS
        or not all(isinstance(item, CompactSpanProposal) for item in proposals)
    ):
        raise ValueError("compact-v4 repair shard state is invalid or unbounded")
    paths = tuple(item.focus_path for item in proposals)
    if len(paths) != len(set(paths)):
        raise ValueError("compact-v4 repair shard state paths must be unique")
    encoded = json.dumps(
        {
            "frozen_proposals": [item._json_value() for item in proposals],
            "protocol": COMPACT_V4_REPAIR_SHARD_STATE_POLICY_ID,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProposalContract:
    """Trusted immutable fields omitted from the compact model response."""

    baseline_sha: str
    task_id: str
    tests: tuple[str, ...]
    make_commands: tuple[str, ...]
    proposal_protocol: str = _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION
    sampling_profile: str = DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID
    sampling_seed: int | None = None
    sampling_context_sha256: str = ""
    sampling_candidate_index: int = 0
    repair_state_sha256: str = ""

    def __post_init__(self) -> None:
        """Reject malformed contract values before they reach local inference."""
        if not isinstance(self.baseline_sha, str) or not _SHA_RE.fullmatch(
            self.baseline_sha
        ):
            raise ValueError(
                "proposal contract baseline_sha must be 40 lowercase hex characters"
            )
        if not isinstance(self.task_id, str) or not _TASK_RE.fullmatch(self.task_id):
            raise ValueError("proposal contract task_id is not canonical")
        if not isinstance(self.tests, tuple):
            raise ValueError("proposal contract tests must be a tuple")
        if not isinstance(self.make_commands, tuple):
            raise ValueError("proposal contract make_commands must be a tuple")
        if _parse_path_list(list(self.tests), "test path", _MAX_TESTS) != self.tests:
            raise ValueError("proposal contract tests are not canonical")
        if _parse_make_commands(list(self.make_commands)) != self.make_commands:
            raise ValueError("proposal contract make_commands are not canonical")
        if not isinstance(self.proposal_protocol, str) or self.proposal_protocol not in {
            _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION,
            _COMPACT_PROPOSAL_PROTOCOL_VERSION,
        }:
            raise ValueError("proposal contract compact protocol is unsupported")
        if not isinstance(self.sampling_profile, str) or self.sampling_profile not in {
            DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
            COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
        }:
            raise ValueError("proposal contract sampling profile is unsupported")
        if (
            self.sampling_profile != DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID
            and self.proposal_protocol != _COMPACT_PROPOSAL_PROTOCOL_VERSION
        ):
            raise ValueError("repair sampling profile requires compact-v4")
        if (
            isinstance(self.sampling_candidate_index, bool)
            or not isinstance(self.sampling_candidate_index, int)
            or not 0
            <= self.sampling_candidate_index
            < COMPACT_V4_REPAIR_CANDIDATE_LIMIT
        ):
            raise ValueError(
                "proposal contract sampling candidate is outside its fixed bound"
            )
        if self.sampling_profile == DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID:
            if self.sampling_candidate_index != 0:
                raise ValueError(
                    "greedy proposal contract sampling candidate must be zero"
                )
            if (
                self.sampling_seed is not None
                or self.sampling_context_sha256
                or self.repair_state_sha256
            ):
                raise ValueError("greedy proposal contract must not carry repair context")
        elif (
            isinstance(self.sampling_seed, bool)
            or not isinstance(self.sampling_seed, int)
            or not 1 <= self.sampling_seed <= _COMPACT_V4_MAX_DERIVED_SEED
            or not isinstance(self.sampling_context_sha256, str)
            or _PROTOCOL_DIGEST_RE.fullmatch(self.sampling_context_sha256) is None
            or not isinstance(self.repair_state_sha256, str)
            or _PROTOCOL_DIGEST_RE.fullmatch(self.repair_state_sha256) is None
        ):
            raise ValueError("repair proposal contract requires derived sampling context")

    @classmethod
    def for_request(
        cls,
        *,
        request: str,
        baseline_sha: str,
        task_id: str,
        tests: tuple[str, ...],
        make_commands: tuple[str, ...],
        proposal_protocol: str = _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION,
        sampling_profile: str = DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
        sampling_candidate_index: int = 0,
        repair_state_sha256: str = "",
    ) -> ProposalContract:
        """Construct a contract whose repair seed commits to canonical request bytes."""
        if (
            isinstance(sampling_candidate_index, bool)
            or not isinstance(sampling_candidate_index, int)
            or not 0
            <= sampling_candidate_index
            < COMPACT_V4_REPAIR_CANDIDATE_LIMIT
        ):
            raise ValueError(
                "proposal contract sampling candidate is outside its fixed bound"
            )
        if (
            sampling_profile == DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID
            and sampling_candidate_index != 0
        ):
            raise ValueError("greedy proposal contract sampling candidate must be zero")
        base = cls(
            baseline_sha=baseline_sha,
            task_id=task_id,
            tests=tests,
            make_commands=make_commands,
            proposal_protocol=proposal_protocol,
        )
        if sampling_profile == DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID:
            return base
        if sampling_profile != COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID:
            raise ValueError("proposal contract sampling profile is unsupported")
        selected_repair_state_sha256 = (
            repair_state_sha256
            if repair_state_sha256
            else compact_v4_repair_shard_state_digest(())
        )
        if _PROTOCOL_DIGEST_RE.fullmatch(selected_repair_state_sha256) is None:
            raise ValueError("repair proposal contract requires canonical shard state")
        context_sha256, seed = _derive_repair_sampling_context(
            request,
            baseline_sha=base.baseline_sha,
            task_id=base.task_id,
            tests=base.tests,
            make_commands=base.make_commands,
            proposal_protocol=base.proposal_protocol,
            sampling_profile=sampling_profile,
            sampling_candidate_index=sampling_candidate_index,
            repair_state_sha256=selected_repair_state_sha256,
        )
        return cls(
            baseline_sha=base.baseline_sha,
            task_id=base.task_id,
            tests=base.tests,
            make_commands=base.make_commands,
            proposal_protocol=base.proposal_protocol,
            sampling_profile=sampling_profile,
            sampling_seed=seed,
            sampling_context_sha256=context_sha256,
            sampling_candidate_index=sampling_candidate_index,
            repair_state_sha256=selected_repair_state_sha256,
        )

    def verify_sampling_context(self, request: str) -> int:
        """Recompute one transported repair context before local model creation."""
        if self.sampling_profile == DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID:
            return _DETERMINISTIC_DECODE_SEED
        context_sha256, seed = _derive_repair_sampling_context(
            request,
            baseline_sha=self.baseline_sha,
            task_id=self.task_id,
            tests=self.tests,
            make_commands=self.make_commands,
            proposal_protocol=self.proposal_protocol,
            sampling_profile=self.sampling_profile,
            sampling_candidate_index=self.sampling_candidate_index,
            repair_state_sha256=self.repair_state_sha256,
        )
        if (
            self.sampling_context_sha256 != context_sha256
            or self.sampling_seed != seed
        ):
            raise ValueError("proposal contract sampling context mismatch")
        return seed

    def to_json(self) -> str:
        """Serialize the trusted contract for one confined worker exchange."""
        value: dict[str, object] = {
            "baseline_sha": self.baseline_sha,
            "task_id": self.task_id,
            "tests": list(self.tests),
            "make_commands": list(self.make_commands),
        }
        if self.proposal_protocol != _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION:
            value["proposal_protocol"] = self.proposal_protocol
        if self.sampling_profile != DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID:
            value["sampling_profile"] = self.sampling_profile
            value["sampling_seed"] = self.sampling_seed
            value["sampling_context_sha256"] = self.sampling_context_sha256
            value["sampling_candidate_index"] = self.sampling_candidate_index
            value["repair_state_sha256"] = self.repair_state_sha256
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> ProposalContract:
        """Parse one exact trusted contract object."""
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"proposal contract is not valid JSON: {exc}") from exc
        required = {"baseline_sha", "task_id", "tests", "make_commands"}
        allowed = required | {
            "proposal_protocol",
            "sampling_context_sha256",
            "sampling_profile",
            "sampling_seed",
            "sampling_candidate_index",
            "repair_state_sha256",
        }
        if (
            not isinstance(value, dict)
            or not required.issubset(value)
            or not set(value).issubset(allowed)
        ):
            raise ValueError("proposal contract fields are incomplete or unknown")
        baseline_sha = value["baseline_sha"]
        task_id = value["task_id"]
        tests = value["tests"]
        make_commands = value["make_commands"]
        if not isinstance(baseline_sha, str) or not isinstance(task_id, str):
            raise ValueError("proposal contract identity fields must be strings")
        if not isinstance(tests, list) or not all(
            isinstance(item, str) for item in tests
        ):
            raise ValueError("proposal contract tests must be a string list")
        if not isinstance(make_commands, list) or not all(
            isinstance(item, str) for item in make_commands
        ):
            raise ValueError("proposal contract make_commands must be a string list")
        return cls(
            baseline_sha=baseline_sha,
            task_id=task_id,
            tests=tuple(tests),
            make_commands=tuple(make_commands),
            proposal_protocol=cast(
                str,
                value.get(
                    "proposal_protocol",
                    _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION,
                ),
            ),
            sampling_profile=cast(
                str,
                value.get("sampling_profile", DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID),
            ),
            sampling_seed=cast(int | None, value.get("sampling_seed")),
            sampling_context_sha256=cast(
                str,
                value.get("sampling_context_sha256", ""),
            ),
            sampling_candidate_index=cast(
                int,
                value.get("sampling_candidate_index", 0),
            ),
            repair_state_sha256=cast(str, value.get("repair_state_sha256", "")),
        )


def _validated_proposal_identity(value: dict[str, object]) -> tuple[int, str, str]:
    """Validate the fixed proposal envelope and return its trusted identity."""
    required = {
        "schema_version",
        "baseline_sha",
        "task_id",
        "edits",
        "tests",
        "make_commands",
        "commit_message",
    }
    unknown = set(value) - required
    missing = required - set(value)
    if unknown:
        raise ValueError(f"proposal has unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"proposal is missing fields: {sorted(missing)}")
    schema_version = value["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in {1, _SNAPSHOT_MANIFEST_SCHEMA_VERSION}
    ):
        raise ValueError("schema_version must be 1 or 2")
    baseline_sha = value["baseline_sha"]
    if not isinstance(baseline_sha, str) or not _SHA_RE.fullmatch(baseline_sha):
        raise ValueError("baseline_sha must be exactly 40 lowercase hex characters")
    task_id = value["task_id"]
    if not isinstance(task_id, str) or not _TASK_RE.fullmatch(task_id):
        raise ValueError("task_id must use the canonical S<number>[.<number>] form")
    return schema_version, baseline_sha, task_id


def _validated_proposal_edits(
    edits_raw: object,
    *,
    schema_version: int,
) -> tuple[ProposalEdit, ...]:
    """Parse bounded proposal edits while preserving their input order."""
    if not isinstance(edits_raw, list) or not edits_raw or len(edits_raw) > _MAX_EDITS:
        raise ValueError(f"edits must contain 1..{_MAX_EDITS} entries")
    edits: list[ProposalEdit] = []
    seen_edits: set[tuple[str, str, str, str]] = set()
    content_bytes = 0
    for item in edits_raw:
        required_fields = {"operation", "path", "old_text", "new_text"}
        if not isinstance(item, dict) or set(item) != required_fields:
            raise ValueError(
                "each edit must contain exactly operation, path, old_text, and new_text"
            )
        operation, path = item["operation"], item["path"]
        old_text, new_text = item["old_text"], item["new_text"]
        if operation not in {"replace", "create", "delete"}:
            raise ValueError(f"unsupported edit operation: {operation!r}")
        if not isinstance(path, str) or not _safe_relative_path(path):
            raise ValueError(
                f"edit path is not canonical, repository-relative, and confined: {path!r}"
            )
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ValueError(f"edit text must be UTF-8 text: {path}")
        if operation == "replace" and schema_version == 1 and (
            not old_text or old_text == new_text
        ):
            raise ValueError("replace requires distinct non-empty old_text")
        if (
            operation == "replace"
            and schema_version == _SNAPSHOT_MANIFEST_SCHEMA_VERSION
            and old_text == new_text
        ):
            raise ValueError("snapshot replace requires distinct complete file text")
        if operation == "create" and (old_text or not new_text):
            raise ValueError("create requires empty old_text and non-empty new_text")
        if operation == "delete" and (not old_text or new_text):
            raise ValueError("delete requires non-empty old_text and empty new_text")
        identity = (operation, path, old_text, new_text)
        if identity in seen_edits:
            raise ValueError(f"duplicate edit operation: {path}")
        seen_edits.add(identity)
        content_bytes += len(old_text.encode("utf-8")) + len(new_text.encode("utf-8"))
        edits.append(ProposalEdit(operation, path, old_text, new_text))
    content_limit = _MAX_CONTENT_BYTES if schema_version == 1 else _MAX_SNAPSHOT_CONTENT_BYTES
    if content_bytes > content_limit:
        raise ValueError(f"proposal edit content exceeds {content_limit} bytes")
    return tuple(edits)


def _validated_commit_message(value: object) -> str:
    """Return one normalized bounded commit subject."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\n" in value
        or len(value.encode("utf-8")) > 200
    ):
        raise ValueError("commit_message must be one bounded non-empty line")
    return value.strip()


@dataclass(frozen=True)
class ProposalManifest:
    """Strict, bounded local-model proposal with no direct tool authority."""

    schema_version: int
    baseline_sha: str
    task_id: str
    edits: tuple[ProposalEdit, ...]
    tests: tuple[str, ...]
    make_commands: tuple[str, ...]
    commit_message: str

    def to_json(self) -> str:
        """Serialize the validated proposal for an isolated worker exchange."""
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "baseline_sha": self.baseline_sha,
                "task_id": self.task_id,
                "edits": [
                    {
                        "operation": edit.operation,
                        "path": edit.path,
                        "old_text": edit.old_text,
                        "new_text": edit.new_text,
                    }
                    for edit in self.edits
                ],
                "tests": list(self.tests),
                "make_commands": list(self.make_commands),
                "commit_message": self.commit_message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> ProposalManifest:
        """Parse one strict proposal object and reject ambiguous model output."""
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"proposal is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("proposal must be a JSON object")
        schema_version, baseline_sha, task_id = _validated_proposal_identity(value)
        edits = _validated_proposal_edits(
            value["edits"],
            schema_version=schema_version,
        )
        tests = _parse_path_list(value["tests"], "test path", _MAX_TESTS)
        commands = _parse_make_commands(value["make_commands"])
        return cls(
            schema_version=schema_version,
            baseline_sha=baseline_sha,
            task_id=task_id,
            edits=edits,
            tests=tests,
            make_commands=tuple(commands),
            commit_message=_validated_commit_message(value["commit_message"]),
        )

    def validate_paths(self, repo_root: Path) -> None:
        """Reject edit or test paths whose canonical identity escapes the root."""
        canonical_root = repo_root.resolve(strict=True)
        for path in (*[edit.path for edit in self.edits], *self.tests):
            candidate = (canonical_root / path).resolve(strict=False)
            if not candidate.is_relative_to(canonical_root):
                raise ValueError(f"proposal path escapes repository root: {path}")


def encode_prompt_batch(
    prompts: Sequence[str],
    *,
    protocol_digest: str,
) -> str:
    """Serialize bounded ordered prompts for one retained local worker."""
    if isinstance(prompts, (str, bytes)) or not 1 <= len(prompts) <= _MAX_PROMPT_BATCH_SHARDS:
        raise ValueError(
            f"prompt batch must contain 1..{_MAX_PROMPT_BATCH_SHARDS} prompts"
        )
    if _PROTOCOL_DIGEST_RE.fullmatch(protocol_digest) is None:
        raise ValueError("prompt batch protocol digest must be lowercase SHA-256")
    normalized: list[str] = []
    for prompt in prompts:
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt.encode("utf-8")) > _MAX_PROMPT_SHARD_BYTES
        ):
            raise ValueError(
                f"each prompt batch item must contain 1..{_MAX_PROMPT_SHARD_BYTES} bytes"
            )
        normalized.append(prompt)
    payload = json.dumps(
        {
            "protocol": _PROMPT_BATCH_PROTOCOL,
            "protocol_digest": protocol_digest,
            "prompts": normalized,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    serialized = _PROMPT_BATCH_MARKER + payload
    if len(serialized.encode("utf-8")) > _MAX_PROMPT_BATCH_BYTES:
        raise ValueError(f"prompt batch exceeds {_MAX_PROMPT_BATCH_BYTES} bytes")
    return serialized


def decode_prompt_batch(raw: str) -> tuple[tuple[str, ...], str | None]:
    """Parse a batch request, or preserve a legacy single-string request."""
    if not raw.startswith(_PROMPT_BATCH_MARKER):
        return (raw,), None
    try:
        value = json.loads(raw.removeprefix(_PROMPT_BATCH_MARKER))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"prompt batch is not valid JSON: {exc}") from exc
    required = {"protocol", "protocol_digest", "prompts"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("prompt batch must contain exactly protocol, digest, and prompts")
    if value["protocol"] != _PROMPT_BATCH_PROTOCOL:
        raise ValueError("prompt batch protocol is unsupported")
    prompts = value["prompts"]
    digest = value["protocol_digest"]
    if not isinstance(prompts, list) or not isinstance(digest, str):
        raise ValueError("prompt batch prompts and protocol digest have invalid types")
    normalized = tuple(prompts)
    encode_prompt_batch(normalized, protocol_digest=digest)
    return normalized, digest


def _derive_repair_sampling_context(
    request: str,
    *,
    baseline_sha: str,
    task_id: str,
    tests: tuple[str, ...],
    make_commands: tuple[str, ...],
    proposal_protocol: str,
    sampling_profile: str,
    sampling_candidate_index: int,
    repair_state_sha256: str,
) -> tuple[str, int]:
    """Commit a finite llama seed to canonical immutable repair inputs."""
    if not isinstance(request, str):
        raise ValueError("repair sampling requires a canonical prompt batch")
    prompts, protocol_digest = decode_prompt_batch(request)
    if (
        protocol_digest is None
        or encode_prompt_batch(prompts, protocol_digest=protocol_digest) != request
    ):
        raise ValueError("repair sampling requires a canonical prompt batch")
    canonical_context = json.dumps(
        {
            "contract": {
                "baseline_sha": baseline_sha,
                "make_commands": list(make_commands),
                "proposal_protocol": proposal_protocol,
                "repair_state_sha256": repair_state_sha256,
                "sampling_profile": sampling_profile,
                "task_id": task_id,
                "tests": list(tests),
            },
            "prompt_batch_sha256": hashlib.sha256(request.encode("utf-8")).hexdigest(),
            "protocol": COMPACT_V4_REPAIR_SEED_DERIVATION_POLICY_ID,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    context_sha256 = hashlib.sha256(canonical_context).hexdigest()
    seed_digest = hashlib.sha256(
        f"{context_sha256}:{sampling_candidate_index}".encode("ascii")
    ).digest()
    seed = max(
        int.from_bytes(seed_digest[:4], "big") & _COMPACT_V4_MAX_DERIVED_SEED,
        1,
    )
    return context_sha256, seed


def encode_proposal_batch(
    manifests: Sequence[ProposalManifest],
    *,
    protocol_digest: str,
) -> str:
    """Serialize independently validated shard proposals as one batch result."""
    if (
        isinstance(manifests, (str, bytes))
        or not 1 <= len(manifests) <= _MAX_PROMPT_BATCH_SHARDS
        or any(not isinstance(manifest, ProposalManifest) for manifest in manifests)
    ):
        raise ValueError(
            f"proposal batch must contain 1..{_MAX_PROMPT_BATCH_SHARDS} manifests"
        )
    if _PROTOCOL_DIGEST_RE.fullmatch(protocol_digest) is None:
        raise ValueError("proposal batch protocol digest must be lowercase SHA-256")
    return json.dumps(
        {
            "protocol": _PROPOSAL_BATCH_PROTOCOL,
            "protocol_digest": protocol_digest,
            "proposals": [json.loads(manifest.to_json()) for manifest in manifests],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_proposal_batch(
    raw: str,
    *,
    expected_protocol_digest: str,
    expected_count: int,
) -> tuple[ProposalManifest, ...]:
    """Validate a retained worker response before the strict parent merge."""
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or not 1 <= expected_count <= _MAX_PROMPT_BATCH_SHARDS
    ):
        raise ValueError("expected proposal count is outside the batch bound")
    if _PROTOCOL_DIGEST_RE.fullmatch(expected_protocol_digest) is None:
        raise ValueError("expected proposal protocol digest must be lowercase SHA-256")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"proposal batch is not valid JSON: {exc}") from exc
    required = {"protocol", "protocol_digest", "proposals"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(
            "proposal batch must contain exactly protocol, digest, and proposals"
        )
    if value["protocol"] != _PROPOSAL_BATCH_PROTOCOL:
        raise ValueError("proposal batch protocol is unsupported")
    if value["protocol_digest"] != expected_protocol_digest:
        raise ValueError("proposal batch protocol identity drifted")
    proposals = value["proposals"]
    if not isinstance(proposals, list) or len(proposals) != expected_count:
        raise ValueError("proposal batch count does not match the prompt plan")
    return tuple(
        ProposalManifest.from_json(
            json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        for item in proposals
    )


def encode_compact_span_batch(
    proposals: Sequence[CompactSpanProposal],
    *,
    protocol_digest: str,
) -> str:
    """Serialize strict compact-v4 spans without expanding trusted preimages."""
    if (
        isinstance(proposals, (str, bytes))
        or not 1 <= len(proposals) <= _MAX_PROMPT_BATCH_SHARDS
        or any(not isinstance(proposal, CompactSpanProposal) for proposal in proposals)
    ):
        raise ValueError(
            f"compact span batch must contain 1..{_MAX_PROMPT_BATCH_SHARDS} proposals"
        )
    if _PROTOCOL_DIGEST_RE.fullmatch(protocol_digest) is None:
        raise ValueError("compact span batch protocol digest must be lowercase SHA-256")
    return json.dumps(
        {
            "protocol": _COMPACT_SPAN_BATCH_PROTOCOL,
            "protocol_digest": protocol_digest,
            "proposals": [proposal._json_value() for proposal in proposals],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_compact_span_batch(
    raw: str,
    *,
    expected_protocol_digest: str,
    expected_count: int,
) -> tuple[CompactSpanProposal, ...]:
    """Validate an atomic compact-v4 worker batch before parent expansion."""
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or not 1 <= expected_count <= _MAX_PROMPT_BATCH_SHARDS
    ):
        raise ValueError("expected compact span count is outside the batch bound")
    if _PROTOCOL_DIGEST_RE.fullmatch(expected_protocol_digest) is None:
        raise ValueError("expected compact span protocol digest must be lowercase SHA-256")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"compact span batch is not valid JSON: {exc}") from exc
    required = {"protocol", "protocol_digest", "proposals"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(
            "compact span batch must contain exactly protocol, digest, and proposals"
        )
    if value["protocol"] != _COMPACT_SPAN_BATCH_PROTOCOL:
        raise ValueError("compact span batch protocol is unsupported")
    if value["protocol_digest"] != expected_protocol_digest:
        raise ValueError("proposal batch protocol identity drifted")
    raw_proposals = value["proposals"]
    if not isinstance(raw_proposals, list) or len(raw_proposals) != expected_count:
        raise ValueError("proposal batch count does not match the prompt plan")
    proposals: list[CompactSpanProposal] = []
    for item in raw_proposals:
        if not isinstance(item, dict) or set(item) != {"focus_path", "e"}:
            raise ValueError("compact span batch proposal fields are incomplete or unknown")
        focus_path = item["focus_path"]
        if not isinstance(focus_path, str):
            raise ValueError("compact span batch focus path must be text")
        proposals.append(
            _decode_compact_span_proposal(
                json.dumps(
                    {"e": item["e"]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                focus_path=focus_path,
            )
        )
    return tuple(proposals)


@dataclass(frozen=True)
class _CompactSpanScopeEvidence:
    """Parent-derived, model-text-free evidence for one rejected coordinate."""

    path_sha256: str
    start_line: int
    old_line_count: int
    editable_ranges: tuple[tuple[int, int], ...]


class CompactSpanScopeError(ValueError):
    """Typed parent rejection that carries only safe coordinate evidence."""

    def __init__(self, detail: str, evidence: _CompactSpanScopeEvidence) -> None:
        """Initialize one bounded rejection with private trusted evidence."""
        super().__init__(
            f"{LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.parent_error_marker} {detail}"
        )
        self.evidence = evidence


def _bounded_scope_coordinate(value: int) -> str:
    """Render feasible line coordinates exactly and classify impossible magnitudes."""
    if value <= _MAX_CONTENT_BYTES + 1:
        return str(value)
    return f">{_MAX_CONTENT_BYTES + 1}"


def _safe_compact_scope_telemetry(error: BaseException) -> str:
    """Return bounded typed scope evidence, excluding path, source, z, and output."""
    if not isinstance(error, CompactSpanScopeError):
        return ""
    evidence = error.evidence
    range_limit = min(_COMPACT_MAX_DIAGNOSTIC_RANGES, len(evidence.editable_ranges))
    for displayed in range(range_limit, -1, -1):
        selected = evidence.editable_ranges[:displayed]
        remaining = len(evidence.editable_ranges) - displayed
        omitted = f",+{remaining}" if remaining else ""
        sections = ",".join(f"[{start},{end})" for start, end in selected)
        boundaries = ",".join(f"[{start},{end}]" for start, end in selected)
        value = (
            f"path_sha256={evidence.path_sha256} "
            f"received_s={_bounded_scope_coordinate(evidence.start_line)} "
            f"received_n={_bounded_scope_coordinate(evidence.old_line_count)} "
            f"sections={sections or '[]'}{omitted} "
            f"boundaries={boundaries or '[]'}{omitted}"
        )
        if len(value.encode("utf-8")) <= _COMPACT_MAX_SCOPE_TELEMETRY_BYTES:
            return value
    raise RuntimeError("compact scope telemetry cannot fit its fixed protocol bound")


def _safe_compact_policy_telemetry(detail: str) -> str:
    """Extract only fixed bounded count states from a compact-v4 rejection."""
    line_budgets = (
        (
            f"compact span old lines exceed {_COMPACT_SPAN_MAX_OLD_LINES}; "
            f"received_old_lines=>{_COMPACT_SPAN_MAX_OLD_LINES} "
            f"max_old_lines={_COMPACT_SPAN_MAX_OLD_LINES}",
            f"received_old_lines=>{_COMPACT_SPAN_MAX_OLD_LINES} "
            f"max_old_lines={_COMPACT_SPAN_MAX_OLD_LINES}",
        ),
        (
            f"compact span new lines exceed {_COMPACT_SPAN_MAX_NEW_LINES}; "
            f"received_new_lines=>{_COMPACT_SPAN_MAX_NEW_LINES} "
            f"max_new_lines={_COMPACT_SPAN_MAX_NEW_LINES}",
            f"received_new_lines=>{_COMPACT_SPAN_MAX_NEW_LINES} "
            f"max_new_lines={_COMPACT_SPAN_MAX_NEW_LINES}",
        ),
        (
            f"compact span changed lines exceed {_COMPACT_SPAN_MAX_CHANGED_LINES}; "
            f"received_changed_lines=>{_COMPACT_SPAN_MAX_CHANGED_LINES} "
            f"max_changed_lines={_COMPACT_SPAN_MAX_CHANGED_LINES}",
            f"received_changed_lines=>{_COMPACT_SPAN_MAX_CHANGED_LINES} "
            f"max_changed_lines={_COMPACT_SPAN_MAX_CHANGED_LINES}",
        ),
    )
    for expected, telemetry in line_budgets:
        if detail == expected:
            return telemetry
    count_detail = (
        "compact proposal edits must contain "
        f"1..{_COMPACT_SPAN_MAX_EDITS} entries; "
        f"received_edits=>{_COMPACT_SPAN_MAX_EDITS} "
        f"max_edits={_COMPACT_SPAN_MAX_EDITS}"
    )
    if detail == count_detail:
        return (
            f"received_edits=>{_COMPACT_SPAN_MAX_EDITS} "
            f"max_edits={_COMPACT_SPAN_MAX_EDITS}"
        )
    content_prefix = (
        f"compact span new text exceeds {_COMPACT_MAX_CONTENT_BYTES} bytes; "
        "received_edits="
    )
    content_suffix = (
        f" received_content_bytes=>{_COMPACT_MAX_CONTENT_BYTES} "
        f"max_edits={_COMPACT_SPAN_MAX_EDITS} "
        f"max_content_bytes={_COMPACT_MAX_CONTENT_BYTES}"
    )
    if not detail.startswith(content_prefix) or not detail.endswith(content_suffix):
        return ""
    received = detail[len(content_prefix) : -len(content_suffix)]
    if received not in {str(value) for value in range(1, _COMPACT_SPAN_MAX_EDITS + 1)}:
        return ""
    return (
        f"received_edits={received} "
        f"received_content_bytes=>{_COMPACT_MAX_CONTENT_BYTES} "
        f"max_edits={_COMPACT_SPAN_MAX_EDITS} "
        f"max_content_bytes={_COMPACT_MAX_CONTENT_BYTES}"
    )


def _parent_span_scope_error(
    detail: str,
    *,
    focus_path: str,
    span: CompactLineSpan,
    editable_ranges: tuple[tuple[int, int], ...],
) -> ValueError:
    """Create one typed rejection using only trusted path/range and numeric fields."""
    return CompactSpanScopeError(
        detail,
        _CompactSpanScopeEvidence(
            path_sha256=hashlib.sha256(focus_path.encode("utf-8")).hexdigest(),
            start_line=span.start_line,
            old_line_count=span.old_line_count,
            editable_ranges=editable_ranges,
        ),
    )


def _parent_proposal_error(detail: str) -> ValueError:
    """Create one path-free parent-validation error for typed retry feedback."""
    return ValueError(
        f"{LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.parent_error_marker} {detail}"
    )


def _shown_line_numbers(
    ranges: tuple[tuple[int, int], ...],
    *,
    line_count: int,
) -> frozenset[int]:
    """Validate and expand bounded 1-based half-open shown-line ranges."""
    if not isinstance(ranges, tuple):
        raise _parent_proposal_error("trusted editable ranges must be immutable")
    shown: set[int] = set()
    previous_end = 1
    for item in ranges:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in item)
        ):
            raise _parent_proposal_error("trusted editable ranges are malformed")
        start, end = item
        if start < 1 or end <= start or end > line_count + 1 or start < previous_end:
            raise _parent_proposal_error("trusted editable ranges are malformed")
        shown.update(range(start, end))
        previous_end = end
    return frozenset(shown)


def _line_ending(value: str) -> str | None:
    """Return one terminal line separator without inspecting model-authored content."""
    if value.endswith("\r\n"):
        return "\r\n"
    if value.endswith("\n"):
        return "\n"
    if value.endswith("\r"):
        return "\r"
    return None


def _trusted_line_ending(lines: list[str]) -> str:
    """Choose the first immutable baseline separator, defaulting empty files to LF."""
    for line in lines:
        ending = _line_ending(line)
        if ending is not None:
            return ending
    return "\n"


def _normalized_compact_replacement(
    new_text: str,
    *,
    line_ending: str,
    terminal_line: bool,
) -> str:
    """Render model-authored logical lines with a trusted boundary convention."""
    if not new_text:
        return ""
    rendered = line_ending.join(new_text.splitlines())
    return rendered + line_ending if terminal_line else rendered


def _materialize_compact_snapshot(
    baseline: str,
    lines: list[str],
    spans: tuple[CompactLineSpan, ...],
) -> str:
    """Apply immutable line coordinates once and preserve trusted newline semantics."""
    if not baseline:
        return spans[0].new_text
    line_count = len(lines)
    line_ending = _trusted_line_ending(lines)
    baseline_has_final_newline = _line_ending(baseline) is not None
    cursor = 0
    pieces: list[str] = []
    for span in spans:
        start = span.start_line - 1
        end = start + span.old_line_count
        pieces.extend(lines[cursor:start])
        if (
            span.new_text
            and span.old_line_count == 0
            and start == line_count
            and pieces
            and _line_ending(pieces[-1]) is None
        ):
            pieces.append(line_ending)
        pieces.append(
            _normalized_compact_replacement(
                span.new_text,
                line_ending=line_ending,
                terminal_line=end < line_count or baseline_has_final_newline,
            )
        )
        cursor = end
    pieces.extend(lines[cursor:])
    materialized = "".join(pieces)
    materialized_ending = _line_ending(materialized)
    if materialized and baseline_has_final_newline and materialized_ending is None:
        return materialized + line_ending
    if materialized and not baseline_has_final_newline and materialized_ending is not None:
        return materialized[: -len(materialized_ending)]
    return materialized


def _compact_create_edit(
    proposal: CompactSpanProposal,
    editable_ranges: tuple[tuple[int, int], ...],
) -> dict[str, str]:
    """Validate and materialize a compact proposal for an absent file."""
    if editable_ranges:
        raise _parent_proposal_error(
            "compact absent file must not advertise editable baseline ranges"
        )
    if (
        len(proposal.edits) != 1
        or proposal.edits[0].start_line != 1
        or proposal.edits[0].old_line_count != 0
        or not proposal.edits[0].new_text
    ):
        raise _parent_proposal_error("compact absent file create must use s=1 and n=0")
    return {
        "operation": "create",
        "path": proposal.focus_path,
        "old_text": "",
        "new_text": proposal.edits[0].new_text,
    }


def _compact_snapshot_edit(
    proposal: CompactSpanProposal,
    baseline: str,
    editable_ranges: tuple[tuple[int, int], ...],
) -> dict[str, str]:
    """Validate compact spans and materialize one trusted baseline snapshot."""
    lines = baseline.splitlines(keepends=True)
    line_count = len(lines)
    shown = _shown_line_numbers(editable_ranges, line_count=line_count)
    line_ending = _trusted_line_ending(lines)
    baseline_has_final_newline = _line_ending(baseline) is not None
    for span in proposal.edits:
        start = span.start_line - 1
        end = start + span.old_line_count
        if start > line_count or end > line_count:
            raise _parent_proposal_error("compact span is outside trusted baseline lines")
        if span.old_line_count:
            consumed = frozenset(
                range(span.start_line, span.start_line + span.old_line_count)
            )
            if not consumed.issubset(shown):
                raise _parent_proposal_error(
                    "compact span must consume only explicitly shown baseline lines"
                )
            normalized = _normalized_compact_replacement(
                span.new_text,
                line_ending=line_ending,
                terminal_line=end < line_count or baseline_has_final_newline,
            )
            if "".join(lines[start:end]) == normalized:
                raise _parent_proposal_error("compact span must change content")
            continue
        boundary_is_shown = any(
            range_start <= span.start_line <= range_end
            for range_start, range_end in editable_ranges
        )
        empty_file_boundary = line_count == 0 and not editable_ranges and span.start_line == 1
        if not boundary_is_shown and not empty_file_boundary:
            raise _parent_span_scope_error(
                "compact insertion must use s from the first shown line through "
                "one past the last shown line of one contiguous section",
                focus_path=proposal.focus_path,
                span=span,
                editable_ranges=editable_ranges,
            )
    materialized = _materialize_compact_snapshot(baseline, lines, proposal.edits)
    if materialized == baseline:
        raise _parent_proposal_error("compact span must change content")
    return {
        "operation": "replace" if materialized else "delete",
        "path": proposal.focus_path,
        "old_text": baseline,
        "new_text": materialized,
    }


def _manifest_from_compact_span_proposal(
    proposal: CompactSpanProposal,
    *,
    contract: ProposalContract,
    baseline: str | None,
    editable_ranges: tuple[tuple[int, int], ...],
) -> ProposalManifest:
    """Compile one trusted-path v4 shard into the unchanged exact manifest schema."""
    edit = (
        _compact_create_edit(proposal, editable_ranges)
        if baseline is None
        else _compact_snapshot_edit(proposal, baseline, editable_ranges)
    )
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": _SNAPSHOT_MANIFEST_SCHEMA_VERSION,
                "baseline_sha": contract.baseline_sha,
                "task_id": contract.task_id,
                "edits": [edit],
                "tests": list(contract.tests),
                "make_commands": list(contract.make_commands),
                "commit_message": _COMPACT_COMMIT_MESSAGE,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def expand_compact_span_proposals(
    proposals: Sequence[CompactSpanProposal],
    *,
    contract: ProposalContract,
    expected_path_groups: tuple[tuple[str, ...], ...],
    expected_baseline_files: Mapping[str, str | None],
    expected_editable_ranges: tuple[tuple[tuple[int, int], ...], ...],
) -> ProposalManifest:
    """Expand every v4 shard against trusted snapshots, then use the strict merger."""
    if (
        isinstance(proposals, (str, bytes))
        or len(proposals) != len(expected_path_groups)
        or len(proposals) != len(expected_editable_ranges)
        or not proposals
    ):
        raise _parent_proposal_error("proposal shard count does not match the prompt plan")
    if not all(isinstance(proposal, CompactSpanProposal) for proposal in proposals):
        raise _parent_proposal_error("compact span batch contains an invalid proposal")
    content_bytes = sum(
        len(edit.new_text.encode("utf-8"))
        for proposal in proposals
        for edit in proposal.edits
    )
    if content_bytes > _COMPACT_MAX_CONTENT_BYTES:
        raise _parent_proposal_error(
            f"compact span new text exceeds {_COMPACT_MAX_CONTENT_BYTES} bytes; "
            f"received_shards={len(proposals)} "
            f"received_content_bytes=>{_COMPACT_MAX_CONTENT_BYTES} "
            f"max_content_bytes={_COMPACT_MAX_CONTENT_BYTES}"
        )
    changed_lines = sum(
        edit.old_line_count + len(edit.new_text.splitlines())
        for proposal in proposals
        for edit in proposal.edits
    )
    if changed_lines > _COMPACT_SPAN_MAX_CHANGED_LINES:
        raise _parent_proposal_error(
            f"compact span changed lines exceed {_COMPACT_SPAN_MAX_CHANGED_LINES}; "
            f"received_changed_lines=>{_COMPACT_SPAN_MAX_CHANGED_LINES} "
            f"max_changed_lines={_COMPACT_SPAN_MAX_CHANGED_LINES}"
        )
    manifests: list[ProposalManifest] = []
    for proposal, focus_paths, editable_ranges in zip(
        proposals,
        expected_path_groups,
        expected_editable_ranges,
        strict=True,
    ):
        if len(focus_paths) != 1 or proposal.focus_path != focus_paths[0]:
            raise _parent_proposal_error(
                "proposal shard edits must cover the exact focus paths"
            )
        if proposal.focus_path not in expected_baseline_files:
            raise _parent_proposal_error(
                "trusted baseline files must cover the exact proposal paths"
            )
        manifests.append(
            _manifest_from_compact_span_proposal(
                proposal,
                contract=contract,
                baseline=expected_baseline_files[proposal.focus_path],
                editable_ranges=editable_ranges,
            )
        )
    return merge_proposal_manifests(
        tuple(manifests),
        expected_path_groups=expected_path_groups,
        expected_baseline_sha=contract.baseline_sha,
        expected_task_id=contract.task_id,
        expected_tests=contract.tests,
        expected_make_commands=contract.make_commands,
        expected_baseline_files=expected_baseline_files,
    )


def _validate_proposal_preconditions(
    proposal: ProposalManifest,
    expected_baseline_files: Mapping[str, str | None],
) -> None:
    """Validate every edit sequentially against an immutable parent snapshot."""
    expected_paths = {edit.path for edit in proposal.edits}
    if set(expected_baseline_files) != expected_paths:
        raise _parent_proposal_error(
            "trusted baseline files must cover the exact proposal paths"
        )
    planned: dict[str, str | None] = {}
    for path, content in expected_baseline_files.items():
        if not isinstance(path, str) or not _safe_relative_path(path):
            raise _parent_proposal_error("trusted baseline contains an unsafe path")
        if content is not None and not isinstance(content, str):
            raise _parent_proposal_error("trusted baseline content must be UTF-8 text")
        planned[path] = content

    for edit in proposal.edits:
        current = planned[edit.path]
        if edit.operation == "replace":
            if proposal.schema_version == _SNAPSHOT_MANIFEST_SCHEMA_VERSION:
                if current is None or current != edit.old_text:
                    raise _parent_proposal_error(
                        "replace old_text must equal the complete trusted snapshot"
                    )
                planned[edit.path] = edit.new_text
            else:
                if current is None or current.count(edit.old_text) != 1:
                    raise _parent_proposal_error(
                        "replace old_text must occur exactly once in trusted baseline"
                    )
                planned[edit.path] = current.replace(
                    edit.old_text,
                    edit.new_text,
                    1,
                )
        elif edit.operation == "create":
            if current is not None:
                raise _parent_proposal_error(
                    "create target must be absent in trusted baseline"
                )
            planned[edit.path] = edit.new_text
        elif edit.operation == "delete":
            if current is None or current != edit.old_text:
                raise _parent_proposal_error(
                    "delete old_text must equal the complete trusted baseline file"
                )
            planned[edit.path] = None
        else:
            raise _parent_proposal_error("proposal operation is unsupported")


def merge_proposal_manifests(
    manifests: tuple[ProposalManifest, ...],
    *,
    expected_path_groups: tuple[tuple[str, ...], ...],
    expected_baseline_sha: str,
    expected_task_id: str,
    expected_tests: tuple[str, ...],
    expected_make_commands: tuple[str, ...],
    expected_baseline_files: Mapping[str, str | None] | None = None,
) -> ProposalManifest:
    """Merge disjoint shard manifests without weakening the final schema."""
    if not manifests or len(manifests) != len(expected_path_groups):
        raise ValueError("proposal shard count does not match the prompt plan")
    if not _SHA_RE.fullmatch(expected_baseline_sha):
        raise ValueError("expected baseline identity is invalid")
    if not _TASK_RE.fullmatch(expected_task_id):
        raise ValueError("expected task identity is invalid")
    if not expected_tests or len(set(expected_tests)) != len(expected_tests):
        raise ValueError("expected tests must be a non-empty unique identity set")
    if not expected_make_commands:
        raise ValueError("expected Make commands must not be empty")

    expected_paths: set[str] = set()
    for group in expected_path_groups:
        if not group:
            raise ValueError("every prompt shard must have focus paths")
        if len(set(group)) != len(group) or expected_paths.intersection(group):
            raise ValueError("prompt shard focus paths must be disjoint")
        if any(not _safe_relative_path(path) for path in group):
            raise ValueError("prompt shard focus path is unsafe")
        expected_paths.update(group)

    manifest_schema_version = manifests[0].schema_version
    edits: list[dict[str, str]] = []
    for manifest, focus_paths in zip(manifests, expected_path_groups, strict=True):
        if manifest.schema_version != manifest_schema_version:
            raise ValueError("proposal shard manifest schema drifted")
        if manifest.baseline_sha != expected_baseline_sha:
            raise ValueError("proposal shard baseline identity drifted")
        if manifest.task_id != expected_task_id:
            raise ValueError("proposal shard task identity drifted")
        actual_paths = {edit.path for edit in manifest.edits}
        if actual_paths != set(focus_paths):
            raise ValueError("proposal shard edits must cover the exact focus paths")
        if (
            len(manifest.tests) != len(expected_tests)
            or frozenset(manifest.tests) != frozenset(expected_tests)
        ):
            raise ValueError("proposal shard test identity drifted")
        if manifest.make_commands != expected_make_commands:
            raise ValueError("proposal shard Make command identity drifted")
        edits.extend(
            {
                "operation": edit.operation,
                "path": edit.path,
                "old_text": edit.old_text,
                "new_text": edit.new_text,
            }
            for edit in manifest.edits
        )

    if {str(edit["path"]) for edit in edits} != expected_paths:
        raise ValueError("merged proposal does not cover every expected path")
    merged = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": manifest_schema_version,
                "baseline_sha": expected_baseline_sha,
                "task_id": expected_task_id,
                "edits": edits,
                "tests": list(expected_tests),
                "make_commands": list(expected_make_commands),
                "commit_message": manifests[0].commit_message,
            }
        )
    )
    if expected_baseline_files is not None:
        _validate_proposal_preconditions(merged, expected_baseline_files)
    return merged


@dataclass(frozen=True)
class CandidateEvidence:
    """Deterministic gate and repository evidence for one applied proposal."""

    changed_files: frozenset[str]
    tests_passed: bool
    warnings: int
    coverage_aggregate: float
    coverage_min_file: float
    ruff_passed: bool
    mypy_passed: bool
    docstrings_passed: bool
    markdown_passed: bool
    cleanup_passed: bool
    commit_count: int
    worktree_clean: bool
    elapsed_seconds: float
    changed_lines: int = 0


@dataclass(frozen=True)
class CodexReference:
    """Independent Codex patch boundary used as the comparison oracle."""

    baseline_sha: str
    reference_sha: str
    changed_files: frozenset[str]
    test_files: frozenset[str]
    changed_lines: int
    elapsed_seconds: float


@dataclass(frozen=True)
class ComparisonResult:
    """Scored parity result and deterministic retry feedback."""

    accepted: bool
    score: float
    blockers: tuple[str, ...]
    changed_file_precision: float
    changed_file_recall: float


def _reject_feedback_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject ambiguous duplicate JSON keys in a planner exchange."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"planner feedback contains duplicate field: {key}")
        result[key] = value
    return result


def _feedback_mapping(
    value: object,
    fields: frozenset[str],
    *,
    label: str,
) -> dict[str, object]:
    """Return an exact JSON mapping or reject missing and unknown fields."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"planner feedback {label} fields are incomplete or unknown")
    return cast(dict[str, object], value)


def _feedback_digest(value: object, label: str) -> str:
    """Validate one immutable SHA-256 identity in a planner exchange."""
    if not isinstance(value, str) or _PROTOCOL_DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"planner feedback {label} must be lowercase SHA-256")
    return value


def _feedback_outcome(value: object) -> ComparisonResult:
    """Hydrate and validate the comparison-only outcome subdocument."""
    mapping = _feedback_mapping(
        value,
        frozenset(
            {
                "accepted",
                "blockers",
                "changed_file_precision",
                "changed_file_recall",
                "score",
            }
        ),
        label="outcome",
    )
    accepted = mapping["accepted"]
    score = mapping["score"]
    precision = mapping["changed_file_precision"]
    recall = mapping["changed_file_recall"]
    blockers = mapping["blockers"]
    if not isinstance(accepted, bool):
        raise ValueError("planner feedback accepted outcome must be a boolean")
    for label, metric, maximum in (
        ("score", score, 100.0),
        ("changed_file_precision", precision, 1.0),
        ("changed_file_recall", recall, 1.0),
    ):
        if (
            isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not math.isfinite(float(metric))
            or not 0.0 <= float(metric) <= maximum
        ):
            raise ValueError(f"planner feedback {label} is outside its valid range")
    if not isinstance(blockers, list) or len(blockers) > _MAX_BLOCKERS:
        raise ValueError("planner feedback blockers must be a bounded JSON list")
    normalized_blockers = tuple(blockers)
    if any(
        not isinstance(item, str)
        or not item
        or item.strip() != item
        or len(item.encode("utf-8")) > _MAX_BLOCKER_BYTES
        for item in normalized_blockers
    ) or len(set(normalized_blockers)) != len(normalized_blockers):
        raise ValueError("planner feedback blockers must be unique bounded text")
    normalized_score = float(cast(int | float, score))
    normalized_precision = float(cast(int | float, precision))
    normalized_recall = float(cast(int | float, recall))
    if accepted is not (normalized_score == 100.0 and not normalized_blockers):
        raise ValueError("planner feedback acceptance contradicts its comparison")
    return ComparisonResult(
        accepted=accepted,
        score=normalized_score,
        blockers=normalized_blockers,
        changed_file_precision=normalized_precision,
        changed_file_recall=normalized_recall,
    )


@dataclass(frozen=True, slots=True)
class PlannerFeedbackExchange:
    """Exact immutable bridge from a managed result into model planning."""

    plan_identity_digest: str
    attempt_identity_digest: str
    attempt_number: int
    model_identity: ModelArtifactIdentity
    task_id: str
    task_objective: str
    outcome: ComparisonResult
    source_artifact_digest: str

    def __post_init__(self) -> None:
        """Reconcile every plan, attempt, model, task, and outcome field."""
        _feedback_digest(self.plan_identity_digest, "plan identity digest")
        _feedback_digest(self.attempt_identity_digest, "attempt identity digest")
        _feedback_digest(self.source_artifact_digest, "source artifact digest")
        if (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
            or not 1 <= self.attempt_number <= 32
        ):
            raise ValueError("planner feedback attempt_number must be between 1 and 32")
        if not isinstance(self.model_identity, ModelArtifactIdentity):
            raise ValueError("planner feedback model_identity must be immutable")
        if not isinstance(self.task_id, str) or _TASK_RE.fullmatch(self.task_id) is None:
            raise ValueError("planner feedback task_id is not canonical")
        if (
            not isinstance(self.task_objective, str)
            or not self.task_objective
            or self.task_objective.strip() != self.task_objective
            or "\x00" in self.task_objective
            or len(self.task_objective.encode("utf-8")) > _MAX_TASK_OBJECTIVE_BYTES
        ):
            raise ValueError("planner feedback task_objective must be bounded text")
        if not isinstance(self.outcome, ComparisonResult):
            raise ValueError("planner feedback outcome must be a ComparisonResult")
        _feedback_outcome(self._outcome_value())

    def to_json(self) -> str:
        """Serialize the exact exchange as bounded canonical JSON."""
        encoded = json.dumps(
            self._json_value(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > _MAX_PLANNER_FEEDBACK_BYTES:
            raise ValueError("planner feedback exchange exceeds its byte bound")
        return encoded

    @classmethod
    def from_json(cls, raw: str) -> PlannerFeedbackExchange:
        """Hydrate one canonical exchange and reject schema ambiguity."""
        if (
            not isinstance(raw, str)
            or not raw
            or len(raw.encode("utf-8")) > _MAX_PLANNER_FEEDBACK_BYTES
        ):
            raise ValueError("planner feedback exchange must be bounded JSON text")
        try:
            value = json.loads(raw, object_pairs_hook=_reject_feedback_duplicate_fields)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"planner feedback exchange is not valid JSON: {exc}") from exc
        mapping = _feedback_mapping(
            value,
            frozenset(
                {
                    "attempt_identity_digest",
                    "attempt_number",
                    "kind",
                    "model_identity",
                    "outcome",
                    "plan_identity_digest",
                    "schema_version",
                    "source",
                    "task",
                }
            ),
            label="root",
        )
        if mapping["schema_version"] != _PLANNER_FEEDBACK_SCHEMA_VERSION:
            raise ValueError("planner feedback schema_version is unsupported")
        if mapping["kind"] != _PLANNER_FEEDBACK_KIND:
            raise ValueError("planner feedback kind is unsupported")
        model = _feedback_mapping(
            mapping["model_identity"],
            frozenset({"filename", "model_id", "repo_id", "revision"}),
            label="model_identity",
        )
        task = _feedback_mapping(
            mapping["task"],
            frozenset({"objective", "task_id"}),
            label="task",
        )
        source = _feedback_mapping(
            mapping["source"],
            frozenset({"artifact_digest", "kind"}),
            label="source",
        )
        if source["kind"] != _PLANNER_FEEDBACK_SOURCE_KIND:
            raise ValueError("planner feedback source kind is unsupported")
        if not all(isinstance(model[field], str) for field in model):
            raise ValueError("planner feedback model identity fields must be strings")
        if not isinstance(task["task_id"], str) or not isinstance(task["objective"], str):
            raise ValueError("planner feedback task fields must be strings")
        return cls(
            plan_identity_digest=_feedback_digest(
                mapping["plan_identity_digest"], "plan identity digest"
            ),
            attempt_identity_digest=_feedback_digest(
                mapping["attempt_identity_digest"], "attempt identity digest"
            ),
            attempt_number=cast(int, mapping["attempt_number"]),
            model_identity=ModelArtifactIdentity(
                model_id=cast(str, model["model_id"]),
                repo_id=cast(str, model["repo_id"]),
                filename=cast(str, model["filename"]),
                revision=cast(str, model["revision"]),
            ),
            task_id=task["task_id"],
            task_objective=task["objective"],
            outcome=_feedback_outcome(mapping["outcome"]),
            source_artifact_digest=_feedback_digest(
                source["artifact_digest"], "source artifact digest"
            ),
        )

    def _outcome_value(self) -> dict[str, object]:
        return {
            "accepted": self.outcome.accepted,
            "blockers": list(self.outcome.blockers),
            "changed_file_precision": self.outcome.changed_file_precision,
            "changed_file_recall": self.outcome.changed_file_recall,
            "score": self.outcome.score,
        }

    def _json_value(self) -> dict[str, object]:
        return {
            "attempt_identity_digest": self.attempt_identity_digest,
            "attempt_number": self.attempt_number,
            "kind": _PLANNER_FEEDBACK_KIND,
            "model_identity": {
                "filename": self.model_identity.filename,
                "model_id": self.model_identity.model_id,
                "repo_id": self.model_identity.repo_id,
                "revision": self.model_identity.revision,
            },
            "outcome": self._outcome_value(),
            "plan_identity_digest": self.plan_identity_digest,
            "schema_version": _PLANNER_FEEDBACK_SCHEMA_VERSION,
            "source": {
                "artifact_digest": self.source_artifact_digest,
                "kind": _PLANNER_FEEDBACK_SOURCE_KIND,
            },
            "task": {"objective": self.task_objective, "task_id": self.task_id},
        }


def compare_with_codex(
    proposal: ProposalManifest,
    evidence: CandidateEvidence,
    reference: CodexReference,
) -> ComparisonResult:
    """Compare all proposed changes and gate evidence to the Codex reference."""
    blockers: list[str] = []
    if proposal.baseline_sha != reference.baseline_sha:
        blockers.append("baseline identity")
    if not evidence.tests_passed:
        blockers.append("tests")
    if evidence.warnings:
        blockers.append("warnings")
    if evidence.coverage_aggregate < 85.0:
        blockers.append("aggregate coverage")
    if evidence.coverage_min_file < 75.0:
        blockers.append("per-file coverage")
    if not evidence.ruff_passed:
        blockers.append("ruff")
    if not evidence.mypy_passed:
        blockers.append("mypy")
    if not evidence.docstrings_passed:
        blockers.append("docstrings")
    if not evidence.markdown_passed:
        blockers.append("markdown")
    if not evidence.cleanup_passed:
        blockers.append("resource cleanup")
    if evidence.commit_count != 1:
        blockers.append("atomic commit")
    if not evidence.worktree_clean:
        blockers.append("clean worktree")

    reference_files = reference.changed_files
    candidate_files = evidence.changed_files
    intersection = reference_files & candidate_files
    precision = len(intersection) / len(candidate_files) if candidate_files else 0.0
    recall = len(intersection) / len(reference_files) if reference_files else 1.0
    if precision < 1.0:
        blockers.append("changed-file precision")
    if recall < 1.0:
        blockers.append("changed-file recall")

    proposed_tests = frozenset(proposal.tests)
    if not reference.test_files <= proposed_tests:
        blockers.append("reference test coverage")

    score = 100.0
    score -= (1.0 - precision) * 20.0
    score -= (1.0 - recall) * 25.0
    score -= max(0, len(blockers) - int(precision < 1.0) - int(recall < 1.0)) * 5.0
    if (
        evidence.changed_lines > 0
        and reference.changed_lines > 0
        and evidence.changed_lines > reference.changed_lines * 1.5
    ):
        blockers.append("diff size")
        score -= min(10.0, 10.0 * evidence.changed_lines / reference.changed_lines / 4.0)
    if (
        reference.elapsed_seconds > 0
        and evidence.elapsed_seconds > reference.elapsed_seconds * 2.0
    ):
        blockers.append("tool efficiency")
        score -= min(10.0, evidence.elapsed_seconds / reference.elapsed_seconds)

    ordered_blockers = tuple(dict.fromkeys(blockers))
    score = round(max(0.0, score), 2)
    return ComparisonResult(
        accepted=not ordered_blockers and score == 100.0,
        score=score,
        blockers=ordered_blockers,
        changed_file_precision=precision,
        changed_file_recall=recall,
    )


_EVALUATION_DIAGNOSIS_FIELDS = frozenset(
    {
        "category",
        "column",
        "command_kind",
        "command_sha256",
        "duration_ms",
        "exit_code",
        "failure_class",
        "finish_reason",
        "finished",
        "hypothesis",
        "line",
        "path_sha256",
        "phase",
        "protocol",
        "schema_version",
    }
)


def _unavailable_evaluation_diagnosis() -> str:
    """Return fixed fail-closed retry evidence without copying rejected input."""
    protocol = EVALUATION_DIAGNOSIS_PROTOCOL
    return json.dumps(
        {
            "category": "none",
            "column": 0,
            "command_kind": "unknown",
            "command_sha256": hashlib.sha256(protocol.version.encode("ascii")).hexdigest(),
            "duration_ms": 0,
            "exit_code": 1,
            "failure_class": "diagnosis_unavailable",
            "finish_reason": "unknown",
            "finished": True,
            "hypothesis": protocol.unavailable_hypothesis,
            "line": 0,
            "path_sha256": "",
            "phase": "evaluation",
            "protocol": protocol.version,
            "schema_version": protocol.schema_version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def safe_evaluation_retry_diagnosis(diagnostics: object) -> str:
    """Return canonical typed evaluation evidence or one fixed redacted fallback."""
    protocol = EVALUATION_DIAGNOSIS_PROTOCOL
    fallback = _unavailable_evaluation_diagnosis()
    if not isinstance(diagnostics, str):
        return fallback
    try:
        encoded = diagnostics.encode("ascii")
    except UnicodeEncodeError:
        return fallback
    if not encoded or len(encoded) > protocol.max_diagnosis_bytes:
        return fallback
    try:
        value = json.loads(diagnostics)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return fallback
    if not isinstance(value, dict) or set(value) != _EVALUATION_DIAGNOSIS_FIELDS:
        return fallback
    phase = value.get("phase")
    command_kind = value.get("command_kind")
    command_digest = value.get("command_sha256")
    duration_ms = value.get("duration_ms")
    exit_code = value.get("exit_code")
    failure_class = value.get("failure_class")
    category = value.get("category")
    path_digest = value.get("path_sha256")
    line = value.get("line")
    column = value.get("column")
    syntax_categories = frozenset(protocol.syntax_categories)
    no_syntax_context = (
        category == "none"
        and path_digest == ""
        and line == 0
        and column == 0
        and isinstance(failure_class, str)
        and failure_class not in syntax_categories
    )
    syntax_context = (
        isinstance(category, str)
        and category in syntax_categories
        and category == failure_class
        and isinstance(path_digest, str)
        and _PROTOCOL_DIGEST_RE.fullmatch(path_digest) is not None
        and not isinstance(line, bool)
        and isinstance(line, int)
        and 0 <= line <= protocol.max_coordinate
        and not isinstance(column, bool)
        and isinstance(column, int)
        and 0 <= column <= protocol.max_coordinate
    )
    expected_hypothesis = (
        protocol.unavailable_hypothesis
        if failure_class == "diagnosis_unavailable"
        else protocol.failure_hypothesis
    )
    if (
        (phase, command_kind) not in protocol.phase_kinds
        or not isinstance(command_digest, str)
        or _PROTOCOL_DIGEST_RE.fullmatch(command_digest) is None
        or isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or not 0 <= duration_ms <= protocol.max_duration_ms
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code == 0
        or not -255 <= exit_code <= 255
        or failure_class not in protocol.diagnosis_failure_classes
        or not (no_syntax_context or syntax_context)
        or value.get("finish_reason") != "unknown"
        or value.get("finished") is not True
        or value.get("hypothesis") != expected_hypothesis
        or value.get("protocol") != protocol.version
        or value.get("schema_version") != protocol.schema_version
    ):
        return fallback
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return canonical if canonical == diagnostics else fallback


def build_retry_prompt(
    task: str,
    comparison: ComparisonResult,
    *,
    diagnostics: str = "",
    max_diagnostic_bytes: int = 4096,
    independent_candidate: bool = False,
) -> str:
    """Build bounded, secret-redacted evidence for a subsequent local attempt."""
    if (
        isinstance(max_diagnostic_bytes, bool)
        or not isinstance(max_diagnostic_bytes, int)
        or not 1 <= max_diagnostic_bytes <= 4096
    ):
        raise ValueError("max_diagnostic_bytes must be an integer from 1 through 4096")
    if not isinstance(independent_candidate, bool):
        raise ValueError("independent_candidate must be a boolean")
    gaps = ", ".join(comparison.blockers) if comparison.blockers else "none"
    redacted = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        diagnostics.replace("\x00", ""),
    )
    raw_tail = redacted.encode("utf-8")[-max_diagnostic_bytes:]
    diagnostic_tail = raw_tail.decode("utf-8", errors="replace")
    failure_evidence = (
        f"\nExact bounded failure evidence:\n{diagnostic_tail}\n"
        if diagnostic_tail
        else ""
    )
    retry_direction = (
        "The prior candidate failed. Solve the approved task independently from the "
        "trusted baseline; do not repair or infer unseen candidate output. Preserve the "
        "smallest correct diff.\n"
        if independent_candidate
        else ""
    )
    return (
        f"{task}\n\n"
        f"Previous proposal score: {comparison.score:.2f}/100.\n"
        f"Required corrections: {gaps}.\n"
        f"{failure_evidence}"
        f"{retry_direction}"
        "Do not broaden the changed-file set beyond the Codex reference. "
        "Return only the strict proposal JSON object."
    )


def _safe_finish_reason(choice: Mapping[object, object]) -> str:
    """Return one allowlisted finish classification without model-controlled text."""
    raw = choice.get("finish_reason")
    return raw if isinstance(raw, str) and raw in _SAFE_FINISH_REASONS else "unknown"


def _safe_token_count(output: Mapping[object, object], field: str) -> str:
    """Return a non-negative token count or an explicit unknown marker."""
    usage = output.get("usage")
    value = usage.get(field) if isinstance(usage, Mapping) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    return "unknown"


def _trusted_compact_editable_ranges(
    prompt: str,
) -> tuple[tuple[int, int], ...] | None:
    """Read only one canonical leading parent scope marker, never source labels."""
    first_line = prompt.partition("\n")[0]
    if not first_line.startswith(_COMPACT_EDITABLE_RANGES_MARKER):
        if _COMPACT_EDITABLE_RANGES_MARKER in prompt:
            raise ValueError("compact editable-range marker must be the first prompt line")
        return None
    if prompt.count(_COMPACT_EDITABLE_RANGES_MARKER) != 1:
        raise ValueError("compact prompt must contain exactly one editable-range marker")
    if len(first_line.encode("utf-8")) > _COMPACT_MAX_SCOPE_MARKER_BYTES:
        raise ValueError(
            "compact editable-range marker exceeds "
            f"{_COMPACT_MAX_SCOPE_MARKER_BYTES} bytes"
        )
    if not first_line.isascii():
        raise ValueError("compact editable-range marker must be ASCII")
    encoded = first_line.removeprefix(_COMPACT_EDITABLE_RANGES_MARKER)
    try:
        value = json.loads(encoded)
    except (RecursionError, ValueError) as exc:
        raise ValueError("compact editable-range marker is not canonical JSON") from exc
    if not isinstance(value, list) or not all(
        isinstance(item, list) and len(item) == 2 for item in value
    ):
        raise ValueError("compact editable-range marker must contain integer pairs")
    ranges = tuple((item[0], item[1]) for item in value)
    validated = _validated_compact_editable_ranges(ranges)
    canonical = json.dumps(
        [list(item) for item in validated],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if encoded != canonical:
        raise ValueError("compact editable-range marker is not canonical JSON")
    return validated


def bind_compact_focus_path(
    prompt: str,
    focus_path: str,
    *,
    editable_ranges: tuple[tuple[int, int], ...] | None = None,
) -> str:
    """Bind one parent-trusted path and optional exact scope to a compact prompt."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("compact prompt must be non-empty")
    if _COMPACT_FOCUS_PATH_MARKER in prompt:
        raise ValueError("compact prompt already contains a focus-path marker")
    if not isinstance(focus_path, str) or not _safe_relative_path(focus_path):
        raise ValueError("compact focus path is not repository-relative and confined")
    path_bound = f"{_COMPACT_FOCUS_PATH_MARKER}{focus_path}\n{prompt}"
    if editable_ranges is None:
        return path_bound
    if _COMPACT_EDITABLE_RANGES_MARKER in prompt:
        raise ValueError("compact prompt already contains an editable-range marker")
    validated = _validated_compact_editable_ranges(editable_ranges)
    encoded = json.dumps(
        [list(item) for item in validated],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    marker_line = f"{_COMPACT_EDITABLE_RANGES_MARKER}{encoded}"
    if len(marker_line.encode("ascii")) > _COMPACT_MAX_SCOPE_MARKER_BYTES:
        raise ValueError(
            "compact editable-range marker exceeds "
            f"{_COMPACT_MAX_SCOPE_MARKER_BYTES} bytes"
        )
    return (
        f"{marker_line}\n"
        f"{_COMPACT_FOCUS_PATH_MARKER}{focus_path}\n"
        f"{prompt}"
    )


def _trusted_compact_focus_path(prompt: str) -> str:
    """Recover exactly one parent-authored focus path, never model output."""
    candidates = tuple(
        line.removeprefix(_COMPACT_FOCUS_PATH_MARKER)
        for line in prompt.splitlines()
        if line.startswith(_COMPACT_FOCUS_PATH_MARKER)
    )
    if len(candidates) != 1 or not _safe_relative_path(candidates[0]):
        raise ValueError("compact prompt must contain exactly one trusted focus path")
    return candidates[0]


def _model_visible_compact_prompt(prompt: str) -> str:
    """Remove validated parent-only bindings before sending a prompt to the model."""
    _trusted_compact_editable_ranges(prompt)
    focus_path = _trusted_compact_focus_path(prompt)
    first_line, separator, remainder = prompt.partition("\n")
    if first_line.startswith(_COMPACT_EDITABLE_RANGES_MARKER):
        if not separator:
            raise ValueError("compact prompt has no model-visible task")
        first_line, separator, remainder = remainder.partition("\n")
    expected_focus = f"{_COMPACT_FOCUS_PATH_MARKER}{focus_path}"
    if first_line != expected_focus or not separator or not remainder.strip():
        raise ValueError("compact parent bindings are not in canonical leading order")
    return remainder


def _completion_text(
    output: object,
    *,
    phase: str,
    budget: int,
    require_stop: bool,
) -> str:
    """Extract completion text with secret-safe finish and token diagnostics."""
    if not isinstance(output, Mapping):
        raise ValueError("local model returned a non-object response")
    choices = output.get("choices")
    if (
        not isinstance(choices, list)
        or not choices
        or not isinstance(choices[0], Mapping)
    ):
        raise ValueError("local model response has no choices")
    choice = choices[0]
    finish = _safe_finish_reason(choice)
    diagnostic = (
        f"phase={phase} finish={finish} "
        f"prompt_tokens={_safe_token_count(output, 'prompt_tokens')} "
        f"completion_tokens={_safe_token_count(output, 'completion_tokens')} "
        f"total_tokens={_safe_token_count(output, 'total_tokens')} "
        f"budget={budget}"
    )
    print(f"SELF_IMPROVE_LOCAL_DECODE {diagnostic}", flush=True)
    if finish == "length":
        raise ValueError(
            "local model exhausted the proposal token budget before completion; "
            + diagnostic
        )
    if require_stop and finish != "stop":
        raise ValueError("local model did not complete structured output; " + diagnostic)
    text = choice.get("text")
    if not isinstance(text, str):
        message = choice.get("message")
        text = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("local model response has no proposal text; " + diagnostic)
    encoded_text = text.encode("utf-8")
    print(
        "SELF_IMPROVE_LOCAL_OUTPUT "
        f"phase={phase} output_bytes={len(encoded_text)} "
        f"output_sha256={hashlib.sha256(encoded_text).hexdigest()}",
        flush=True,
    )
    return text


def _decode_compact_span_proposal(
    raw: str,
    *,
    focus_path: str,
) -> CompactSpanProposal:
    """Parse one strict compact-v4 response without trusting model coordinates."""
    if not isinstance(focus_path, str) or not _safe_relative_path(focus_path):
        raise ValueError("compact focus path is not repository-relative and confined")
    stripped = raw.strip()
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            "compact-v4 proposal is not one complete JSON object; "
            f"output_bytes={len(stripped.encode('utf-8'))}"
        ) from exc
    if not isinstance(value, dict) or set(value) != _COMPACT_ROOT_FIELDS:
        raise ValueError("compact proposal must contain exactly e")
    edits_raw = value["e"]
    if (
        not isinstance(edits_raw, list)
        or not 1 <= len(edits_raw) <= _COMPACT_SPAN_MAX_EDITS
    ):
        detail = (
            ""
            if not isinstance(edits_raw, list)
            or len(edits_raw) <= _COMPACT_SPAN_MAX_EDITS
            else (
                f"; received_edits=>{_COMPACT_SPAN_MAX_EDITS} "
                f"max_edits={_COMPACT_SPAN_MAX_EDITS}"
            )
        )
        raise ValueError(
            "compact proposal edits must contain "
            f"1..{_COMPACT_SPAN_MAX_EDITS} entries{detail}"
        )
    edits: list[CompactLineSpan] = []
    for item in edits_raw:
        if not isinstance(item, dict) or set(item) != _COMPACT_EDIT_FIELDS:
            raise ValueError("each compact edit must contain exactly n, s, and z")
        start_line = item["s"]
        old_line_count = item["n"]
        new_text = item["z"]
        if isinstance(start_line, bool) or isinstance(old_line_count, bool):
            raise ValueError("compact span coordinates must be integers, not booleans")
        if not isinstance(start_line, int) or not isinstance(old_line_count, int):
            raise ValueError("compact span coordinates must be integers, not booleans")
        if not isinstance(new_text, str):
            raise ValueError("compact span new text must be a string")
        edits.append(
            CompactLineSpan(
                start_line=start_line,
                old_line_count=old_line_count,
                new_text=new_text,
            )
        )
    canonical_edits = tuple(sorted(edits, key=lambda edit: edit.start_line))
    return CompactSpanProposal(focus_path=focus_path, edits=canonical_edits)


def _decode_compact_proposal(
    raw: str,
    contract: ProposalContract,
    *,
    focus_path: str,
) -> ProposalManifest:
    """Expand parent-owned fields and revalidate one compact single-file object."""
    if not isinstance(focus_path, str) or not _safe_relative_path(focus_path):
        raise ValueError("compact focus path is not repository-relative and confined")
    stripped = raw.strip()
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            "compact proposal is not one complete JSON object; "
            f"output_bytes={len(stripped.encode('utf-8'))}"
        ) from exc
    if not isinstance(value, dict) or set(value) != _COMPACT_ROOT_FIELDS:
        raise ValueError("compact proposal must contain exactly e")
    edits_raw = value["e"]
    if (
        not isinstance(edits_raw, list)
        or not 1 <= len(edits_raw) <= _COMPACT_MAX_EDITS
    ):
        raise ValueError(
            f"compact proposal edits must contain 1..{_COMPACT_MAX_EDITS} entries"
        )
    edits: list[dict[str, object]] = []
    content_bytes = 0
    for item in edits_raw:
        if not isinstance(item, dict) or set(item) != _LEGACY_COMPACT_EDIT_FIELDS:
            raise ValueError("each compact edit must contain exactly a and z")
        old_text = item["a"]
        new_text = item["z"]
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ValueError("compact edit text fields must be strings")
        content_bytes += len(old_text.encode("utf-8"))
        content_bytes += len(new_text.encode("utf-8"))
        if content_bytes > _COMPACT_MAX_CONTENT_BYTES:
            raise ValueError(
                f"compact edit content exceeds {_COMPACT_MAX_CONTENT_BYTES} bytes"
            )
        operation = _COMPACT_OPERATION_BY_EMPTY_TEXT.get(
            (not old_text, not new_text)
        )
        if operation is None:
            raise ValueError("compact edit must change content")
        edits.append(
            {
                "operation": operation,
                "path": focus_path,
                "old_text": old_text,
                "new_text": new_text,
            }
        )
    expanded = json.dumps(
        {
            "schema_version": 1,
            "baseline_sha": contract.baseline_sha,
            "task_id": contract.task_id,
            "edits": edits,
            "tests": list(contract.tests),
            "make_commands": list(contract.make_commands),
            "commit_message": _COMPACT_COMMIT_MESSAGE,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ProposalManifest.from_json(expanded)


class LocalProposalGateway:
    """Generate strict proposal JSON with one explicit local GGUF model."""

    def __init__(
        self,
        model_path: Path,
        *,
        model_factory: _ModelFactory | None = None,
        grammar_factory: _GrammarFactory | None = None,
        n_gpu_layers: int | None = None,
    ) -> None:
        """Bind one GGUF and an optional hardware-derived GPU offload boundary."""
        if not model_path.is_file():
            raise FileNotFoundError(f"local GGUF is not readable: {model_path}")
        if (
            isinstance(n_gpu_layers, bool)
            or not isinstance(n_gpu_layers, (int, type(None)))
            or (n_gpu_layers is not None and n_gpu_layers < -1)
        ):
            raise ValueError("n_gpu_layers must be None or an integer of at least -1")
        self._model_path = model_path
        self._model_factory = model_factory or _default_model_factory
        self._grammar_factory = grammar_factory
        if self._grammar_factory is None and model_factory is None:
            self._grammar_factory = _default_json_schema_grammar
        self._n_gpu_layers = n_gpu_layers
        self._model: _LocalModel | None = None
        self._structured_canary_protocols: set[str] = set()

    def _load_model(self) -> _LocalModel:
        """Lazily construct exactly one retained model instance."""
        if self._model is None:
            if self._n_gpu_layers is None:
                self._model = self._model_factory(
                    model_path=str(self._model_path),
                    n_ctx=0,
                    verbose=False,
                )
            else:
                self._model = self._model_factory(
                    model_path=str(self._model_path),
                    n_ctx=0,
                    verbose=False,
                    n_gpu_layers=self._n_gpu_layers,
                )
        return self._model

    def _grammar_for_schema(self, schema: dict[str, object]) -> object | None:
        """Build a per-call grammar when the production or injected helper is bound."""
        if self._grammar_factory is None:
            return None
        grammar = self._grammar_factory(schema)
        if grammar is None:
            raise RuntimeError("llama.cpp JSON-schema grammar construction returned no grammar")
        return grammar

    def _run_structured_canary(
        self,
        model: _ChatLocalModel,
        proposal_protocol: str,
    ) -> None:
        """Prove the retained model can finish a tiny schema before task decoding."""
        if proposal_protocol in self._structured_canary_protocols:
            return
        system_prompt = (
            _LEGACY_COMPACT_SYSTEM_PROMPT
            if proposal_protocol == _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION
            else _COMPACT_SYSTEM_PROMPT
        )
        schema = _STRUCTURED_CANARY_SCHEMA
        output = model.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _STRUCTURED_CANARY_PROMPT},
            ],
            max_tokens=_STRUCTURED_CANARY_TOKENS,
            temperature=_DETERMINISTIC_DECODE_TEMPERATURE,
            seed=_DETERMINISTIC_DECODE_SEED,
            response_format={
                "type": "json_object",
                "schema": schema,
            },
            grammar=self._grammar_for_schema(schema),
        )
        try:
            text = _completion_text(
                output,
                phase="canary",
                budget=_STRUCTURED_CANARY_TOKENS,
                require_stop=_STRUCTURED_OUTPUT_REQUIRE_STOP,
            )
            value = json.loads(text)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"local structured-output canary failed: {exc}") from exc
        if value != _STRUCTURED_CANARY_EXPECTED:
            raise ValueError(
                "local structured-output canary failed: response did not match contract"
            )
        self._structured_canary_protocols.add(proposal_protocol)

    def _propose_compact(
        self,
        model: _ChatLocalModel,
        prompt: str,
        contract: ProposalContract,
    ) -> ProposalManifest | CompactSpanProposal:
        """Decode one contract-bound compact proposal through chat completion."""
        self._run_structured_canary(model, contract.proposal_protocol)
        legacy = contract.proposal_protocol == _LEGACY_COMPACT_PROPOSAL_PROTOCOL_VERSION
        if legacy:
            schema = _LEGACY_COMPACT_PROPOSAL_JSON_SCHEMA
        else:
            scope_ranges = _trusted_compact_editable_ranges(prompt)
            schema = (
                _COMPACT_PROPOSAL_JSON_SCHEMA
                if scope_ranges is None
                else _compact_proposal_schema_for_ranges(scope_ranges)
            )
        sampling_arguments = _proposal_sampling_arguments(
            contract.sampling_profile,
            sampling_seed=contract.sampling_seed,
        )
        output = model.create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": _LEGACY_COMPACT_SYSTEM_PROMPT if legacy else _COMPACT_SYSTEM_PROMPT,
                },
                {"role": "user", "content": _model_visible_compact_prompt(prompt)},
            ],
            max_tokens=_COMPACT_PROPOSAL_TOKENS if legacy else _COMPACT_SPAN_PROPOSAL_TOKENS,
            response_format={"type": "json_object", "schema": schema},
            grammar=self._grammar_for_schema(schema),
            **sampling_arguments,
        )
        budget = _COMPACT_PROPOSAL_TOKENS if legacy else _COMPACT_SPAN_PROPOSAL_TOKENS
        text = _completion_text(
            output,
            phase="proposal",
            budget=budget,
            require_stop=_STRUCTURED_OUTPUT_REQUIRE_STOP,
        )
        focus_path = _trusted_compact_focus_path(prompt)
        if not legacy:
            return _decode_compact_span_proposal(text, focus_path=focus_path)
        return _decode_compact_proposal(text, contract, focus_path=focus_path)

    def _uncontracted_output(self, model: _LocalModel, prompt: str) -> object:
        """Run the historical unconstrained proposal adapter."""
        if hasattr(model, "create_chat_completion"):
            chat_model = cast("_ChatLocalModel", model)
            schema = _PROPOSAL_JSON_SCHEMA
            return chat_model.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Return exactly one valid JSON proposal object. "
                            "Do not emit markdown or prose."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.0,
                seed=0,
                response_format={"type": "json_object", "schema": schema},
                grammar=self._grammar_for_schema(schema),
            )
        return model(prompt, max_tokens=4096, temperature=0.0, echo=False)

    def propose(
        self,
        prompt: str,
        *,
        contract: ProposalContract | None = None,
    ) -> ProposalManifest | CompactSpanProposal:
        """Run deterministic decode and parse one bounded proposal."""
        model = self._load_model()
        if contract is not None:
            if not hasattr(model, "create_chat_completion"):
                raise ValueError(
                    "compact structured proposal requires chat-completion support"
                )
            return self._propose_compact(cast("_ChatLocalModel", model), prompt, contract)
        output = self._uncontracted_output(model, prompt)
        text = _completion_text(
            output,
            phase="proposal",
            budget=4096,
            require_stop=False,
        )
        return ProposalManifest.from_json(_extract_json_object(text))


def _safe_relative_path(raw: str) -> bool:
    if not raw or "\\" in raw or "\x00" in raw:
        return False
    path = PurePosixPath(raw)
    if not path.parts or str(path) != raw:
        return False
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.parts[0] not in {".git", ".venv"}
    )


def _parse_make_commands(value: object) -> tuple[str, ...]:
    """Parse one bounded list of Make-only tool steps."""
    if not isinstance(value, list) or not value or len(value) > _MAX_COMMANDS:
        raise ValueError(f"make_commands must contain 1..{_MAX_COMMANDS} entries")
    commands: list[str] = []
    for command in value:
        if not isinstance(command, str) or not command.startswith("make "):
            raise ValueError("every tool step must be a make command")
        if len(command.encode("utf-8")) > _MAX_COMMAND_BYTES:
            raise ValueError("make command exceeds the bounded command size")
        if any(token in command for token in _SHELL_METACHARACTERS):
            raise ValueError("make command contains a forbidden shell metacharacter")
        commands.append(command)
    return tuple(commands)


def _parse_path_list(value: object, label: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ValueError(f"{label}s must contain 1..{maximum} entries")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _safe_relative_path(item):
            raise ValueError(f"{label} is not repository-relative and confined: {item!r}")
        result.append(item)
    if len(set(result)) != len(result):
        raise ValueError(f"duplicate {label}")
    return tuple(result)


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    diagnostic = (
        f"output_bytes={len(stripped.encode('utf-8'))} "
        f"has_json_start={start >= 0} has_json_end={end >= start}"
    )
    if start < 0:
        raise ValueError(f"local model response has no JSON start: {diagnostic}")
    if end < start:
        raise ValueError(f"local model response contains incomplete JSON: {diagnostic}")
    return stripped[start : end + 1]


def _load_llama_cpp_runtime() -> _LlamaCppRuntime:
    """Load the optional local-inference runtime through one typed seam."""
    try:
        runtime = importlib.import_module("llama_cpp")
    except ImportError as exc:
        raise RuntimeError(
            "llama.cpp runtime is unavailable; run make sync-llama-cpp "
            "SYNC_LLAMA_CPP_VALIDATE_ONLY=0"
        ) from exc
    return cast("_LlamaCppRuntime", runtime)


def _default_json_schema_grammar(schema: dict[str, object]) -> object:
    """Compile trusted schema through llama-cpp-python's locked grammar helper."""
    runtime = _load_llama_cpp_runtime()
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        return runtime.LlamaGrammar.from_json_schema(encoded, verbose=False)
    except (AttributeError, OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("llama.cpp JSON-schema grammar construction failed") from exc


def _default_model_factory(
    *,
    model_path: str,
    n_ctx: int,
    verbose: bool,
    n_gpu_layers: int | None = None,
) -> _LocalModel:
    runtime = _load_llama_cpp_runtime()
    try:
        supports_gpu_offload = runtime.llama_supports_gpu_offload() is True
    except (AttributeError, OSError, RuntimeError):
        supports_gpu_offload = False
    requested_gpu_layers = -1 if n_gpu_layers is None else n_gpu_layers
    effective_gpu_layers = requested_gpu_layers if supports_gpu_offload else 0
    return runtime.Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        verbose=verbose,
        n_gpu_layers=effective_gpu_layers,
    )


__all__ = [
    "COMPACT_PROPOSAL_CONTRACT_TRANSPORT_PROTOCOL",
    "COMPACT_PROPOSAL_PROTOCOL_V3",
    "COMPACT_PROPOSAL_PROTOCOL_V4",
    "COMPACT_V4_REPAIR_CANDIDATE_FEEDBACK_POLICY_ID",
    "COMPACT_V4_REPAIR_CANDIDATE_LIMIT",
    "COMPACT_V4_REPAIR_SEED_DERIVATION_POLICY_ID",
    "COMPACT_V4_REPAIR_SHARD_PROMPT_POLICY_ID",
    "COMPACT_V4_REPAIR_SHARD_STATE_POLICY_ID",
    "COMPACT_V4_REPAIR_SPAN_PROVENANCE_POLICY_ID",
    "COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID",
    "DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID",
    "EVALUATION_DIAGNOSIS_PROTOCOL",
    "CompactLineSpan",
    "CompactSpanProposal",
    "CompactSpanScopeError",
    "LocalProposalGateway",
    "PlannerFeedbackExchange",
    "bind_compact_focus_path",
    "build_retry_prompt",
    "compact_v4_repair_shard_state_digest",
    "compact_v4_syntax_repair_sampling_identity",
    "compare_with_codex",
    "decode_compact_span_batch",
    "decode_prompt_batch",
    "decode_proposal_batch",
    "encode_compact_span_batch",
    "encode_proposal_batch",
    "expand_compact_span_proposals",
    "local_proposal_attempt_identity_digest",
    "merge_proposal_manifests",
    "safe_evaluation_retry_diagnosis",
]
