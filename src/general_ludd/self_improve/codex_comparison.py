"""Bounded local-model proposal comparison against a Codex reference."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

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

_COMPACT_PROPOSAL_PROTOCOL_VERSION = "self-improve-compact-proposal-v3"
_COMPACT_PROPOSAL_TOKENS = 1024
_COMPACT_MAX_CONTENT_BYTES = 3072
_COMPACT_FOCUS_PATH_MARKER = "GLUDD_SELF_IMPROVE_FOCUS_PATH="
_COMPACT_COMMIT_MESSAGE = "fix: apply bounded self-improvement proposal"
_STRUCTURED_CANARY_TOKENS = 32
_DETERMINISTIC_DECODE_TEMPERATURE = 0.0
_DETERMINISTIC_DECODE_SEED = 0
_STRUCTURED_OUTPUT_REQUIRE_STOP = True
_STRUCTURED_CANARY_PROMPT = 'Return {"ok":true}.'
_STRUCTURED_CANARY_EXPECTED: dict[str, object] = {"ok": True}
_COMPACT_ROOT_FIELDS = frozenset({"e"})
_COMPACT_EDIT_FIELDS = frozenset({"a", "z"})
_COMPACT_MAX_EDITS = 16
_COMPACT_OPERATION_BY_EMPTY_TEXT = {
    (False, False): "replace",
    (False, True): "delete",
    (True, False): "create",
}
_STRICT_PARENT_DECODER_VERSION = "proposal-manifest-strict-v3"
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
            "maxItems": _COMPACT_MAX_EDITS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["a", "z"],
                "properties": {
                    "a": {"type": "string"},
                    "z": {"type": "string"},
                },
            },
        },
    },
}
_COMPACT_SYSTEM_PROMPT = (
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


LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL = ModelAttemptOutcomeProtocol(
    version="self-improve-model-attempt-outcome-v2",
    acquisition_failure="terminal_typed_no_model_outcome",
    plan_exhaustion="terminal_typed_no_attempt_or_model_outcome",
    outcome_eligibility="candidate_reached_proposal_generation",
)


LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL = ValidationRetryProtocol(
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


def local_proposal_attempt_identity_digest(prompt_protocol_digest: str) -> str:
    """Bind one prompt plan to the complete managed local-output protocol."""
    if (
        not isinstance(prompt_protocol_digest, str)
        or _PROTOCOL_DIGEST_RE.fullmatch(prompt_protocol_digest) is None
    ):
        raise ValueError(
            "prompt protocol digest must be a lowercase 64-character SHA-256"
        )
    operation_policy = [
        {
            "new_text_empty": new_empty,
            "old_text_empty": old_empty,
            "operation": operation,
        }
        for (old_empty, new_empty), operation in sorted(
            _COMPACT_OPERATION_BY_EMPTY_TEXT.items()
        )
    ]
    payload: dict[str, object] = {
        "attempt_protocol": "self-improve-local-attempt-v1",
        "prompt_protocol_digest": prompt_protocol_digest,
        "validation_retry": asdict(LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL),
        "model_attempt_outcome": asdict(LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL),
        "compact_output": {
            "protocol_version": _COMPACT_PROPOSAL_PROTOCOL_VERSION,
            "schema": _COMPACT_PROPOSAL_JSON_SCHEMA,
            "system_prompt": _COMPACT_SYSTEM_PROMPT,
            "max_content_bytes": _COMPACT_MAX_CONTENT_BYTES,
            "trusted_focus_path_marker": _COMPACT_FOCUS_PATH_MARKER,
            "trusted_commit_message": _COMPACT_COMMIT_MESSAGE,
        },
        "structured_canary": {
            "expected": _STRUCTURED_CANARY_EXPECTED,
            "prompt": _STRUCTURED_CANARY_PROMPT,
            "schema": _STRUCTURED_CANARY_SCHEMA,
        },
        "output_token_policy": {
            "canary_max_tokens": _STRUCTURED_CANARY_TOKENS,
            "proposal_max_tokens": _COMPACT_PROPOSAL_TOKENS,
            "require_stop": _STRUCTURED_OUTPUT_REQUIRE_STOP,
            "safe_finish_reasons": sorted(_SAFE_FINISH_REASONS),
            "seed": _DETERMINISTIC_DECODE_SEED,
            "temperature": _DETERMINISTIC_DECODE_TEMPERATURE,
        },
        "strict_decoder_semantics": {
            "authoritative_manifest_schema": _PROPOSAL_JSON_SCHEMA,
            "batch_protocol": _PROPOSAL_BATCH_PROTOCOL,
            "command_max_bytes": _MAX_COMMAND_BYTES,
            "content_max_bytes": _MAX_CONTENT_BYTES,
            "edit_fields": sorted(_COMPACT_EDIT_FIELDS),
            "max_commands": _MAX_COMMANDS,
            "max_compact_edits": _COMPACT_MAX_EDITS,
            "max_manifest_edits": _MAX_EDITS,
            "max_tests": _MAX_TESTS,
            "operation_by_empty_text": operation_policy,
            "parent_decoder_version": _STRICT_PARENT_DECODER_VERSION,
            "path_policy": "confined-pure-posix-v1",
            "precondition_policy": "sequential-exact-trusted-baseline-v1",
            "root_fields": sorted(_COMPACT_ROOT_FIELDS),
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
    ) -> object: ...


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


class _LlamaCppRuntime(Protocol):
    """Typed optional llama.cpp module boundary."""

    Llama: _ModelFactory

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
class ProposalContract:
    """Trusted immutable fields omitted from the compact model response."""

    baseline_sha: str
    task_id: str
    tests: tuple[str, ...]
    make_commands: tuple[str, ...]

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

    def to_json(self) -> str:
        """Serialize the trusted contract for one confined worker exchange."""
        return json.dumps(
            {
                "baseline_sha": self.baseline_sha,
                "task_id": self.task_id,
                "tests": list(self.tests),
                "make_commands": list(self.make_commands),
            },
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
        if not isinstance(value, dict) or set(value) != required:
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
        )


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
        if value["schema_version"] != 1:
            raise ValueError("schema_version must be 1")

        baseline_sha = value["baseline_sha"]
        if not isinstance(baseline_sha, str) or not _SHA_RE.fullmatch(baseline_sha):
            raise ValueError("baseline_sha must be exactly 40 lowercase hex characters")
        task_id = value["task_id"]
        if not isinstance(task_id, str) or not _TASK_RE.fullmatch(task_id):
            raise ValueError("task_id must use the canonical S<number>[.<number>] form")

        edits_raw = value["edits"]
        if not isinstance(edits_raw, list) or not edits_raw or len(edits_raw) > _MAX_EDITS:
            raise ValueError(f"edits must contain 1..{_MAX_EDITS} entries")
        edits: list[ProposalEdit] = []
        seen_edits: set[tuple[str, str, str, str]] = set()
        content_bytes = 0
        for item in edits_raw:
            required_edit_fields = {"operation", "path", "old_text", "new_text"}
            if not isinstance(item, dict) or set(item) != required_edit_fields:
                raise ValueError(
                    "each edit must contain exactly operation, path, old_text, and new_text"
                )
            operation = item["operation"]
            path = item["path"]
            old_text = item["old_text"]
            new_text = item["new_text"]
            if operation not in {"replace", "create", "delete"}:
                raise ValueError(f"unsupported edit operation: {operation!r}")
            if not isinstance(path, str) or not _safe_relative_path(path):
                raise ValueError(
                    f"edit path is not canonical, repository-relative, and confined: {path!r}"
                )
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise ValueError(f"edit text must be UTF-8 text: {path}")
            if operation == "replace" and (
                not old_text or old_text == new_text
            ):
                raise ValueError("replace requires distinct non-empty old_text")
            if operation == "create" and (old_text or not new_text):
                raise ValueError("create requires empty old_text and non-empty new_text")
            if operation == "delete" and (not old_text or new_text):
                raise ValueError("delete requires non-empty old_text and empty new_text")
            identity = (operation, path, old_text, new_text)
            if identity in seen_edits:
                raise ValueError(f"duplicate edit operation: {path}")
            seen_edits.add(identity)
            content_bytes += len(old_text.encode("utf-8"))
            content_bytes += len(new_text.encode("utf-8"))
            edits.append(
                ProposalEdit(
                    operation=operation,
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                )
            )
        if content_bytes > _MAX_CONTENT_BYTES:
            raise ValueError(f"proposal edit content exceeds {_MAX_CONTENT_BYTES} bytes")

        tests = _parse_path_list(value["tests"], "test path", _MAX_TESTS)
        commands = _parse_make_commands(value["make_commands"])

        commit_message = value["commit_message"]
        if (
            not isinstance(commit_message, str)
            or not commit_message.strip()
            or "\n" in commit_message
            or len(commit_message.encode("utf-8")) > 200
        ):
            raise ValueError("commit_message must be one bounded non-empty line")

        return cls(
            schema_version=1,
            baseline_sha=baseline_sha,
            task_id=task_id,
            edits=tuple(edits),
            tests=tests,
            make_commands=tuple(commands),
            commit_message=commit_message.strip(),
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


def _parent_proposal_error(detail: str) -> ValueError:
    """Create one path-free parent-validation error for typed retry feedback."""
    return ValueError(
        f"{LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.parent_error_marker} {detail}"
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

    edits: list[dict[str, str]] = []
    for manifest, focus_paths in zip(manifests, expected_path_groups, strict=True):
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
                "schema_version": 1,
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


def build_retry_prompt(
    task: str,
    comparison: ComparisonResult,
    *,
    diagnostics: str = "",
    max_diagnostic_bytes: int = 4096,
) -> str:
    """Build bounded, secret-redacted evidence for a subsequent local attempt."""
    if (
        isinstance(max_diagnostic_bytes, bool)
        or not isinstance(max_diagnostic_bytes, int)
        or not 1 <= max_diagnostic_bytes <= 4096
    ):
        raise ValueError("max_diagnostic_bytes must be an integer from 1 through 4096")
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
    return (
        f"{task}\n\n"
        f"Previous proposal score: {comparison.score:.2f}/100.\n"
        f"Required corrections: {gaps}.\n"
        f"{failure_evidence}"
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


def bind_compact_focus_path(prompt: str, focus_path: str) -> str:
    """Bind one parent-trusted path to a compact single-file prompt."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("compact prompt must be non-empty")
    if _COMPACT_FOCUS_PATH_MARKER in prompt:
        raise ValueError("compact prompt already contains a focus-path marker")
    if not isinstance(focus_path, str) or not _safe_relative_path(focus_path):
        raise ValueError("compact focus path is not repository-relative and confined")
    return f"{_COMPACT_FOCUS_PATH_MARKER}{focus_path}\n{prompt}"


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
    return text


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
        if not isinstance(item, dict) or set(item) != _COMPACT_EDIT_FIELDS:
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
        self._n_gpu_layers = n_gpu_layers
        self._model: _LocalModel | None = None
        self._structured_canary_passed = False

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

    def _run_structured_canary(self, model: _ChatLocalModel) -> None:
        """Prove the retained model can finish a tiny schema before task decoding."""
        if self._structured_canary_passed:
            return
        output = model.create_chat_completion(
            messages=[
                {"role": "system", "content": _COMPACT_SYSTEM_PROMPT},
                {"role": "user", "content": _STRUCTURED_CANARY_PROMPT},
            ],
            max_tokens=_STRUCTURED_CANARY_TOKENS,
            temperature=_DETERMINISTIC_DECODE_TEMPERATURE,
            seed=_DETERMINISTIC_DECODE_SEED,
            response_format={
                "type": "json_object",
                "schema": _STRUCTURED_CANARY_SCHEMA,
            },
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
        self._structured_canary_passed = True

    def propose(
        self,
        prompt: str,
        *,
        contract: ProposalContract | None = None,
    ) -> ProposalManifest:
        """Run deterministic decode and parse one bounded proposal."""
        model = self._load_model()
        if hasattr(model, "create_chat_completion"):
            chat_model = cast("_ChatLocalModel", model)
            if contract is not None:
                self._run_structured_canary(chat_model)
                output = chat_model.create_chat_completion(
                    messages=[
                        {"role": "system", "content": _COMPACT_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=_COMPACT_PROPOSAL_TOKENS,
                    temperature=_DETERMINISTIC_DECODE_TEMPERATURE,
                    seed=_DETERMINISTIC_DECODE_SEED,
                    response_format={
                        "type": "json_object",
                        "schema": _COMPACT_PROPOSAL_JSON_SCHEMA,
                    },
                )
                text = _completion_text(
                    output,
                    phase="proposal",
                    budget=_COMPACT_PROPOSAL_TOKENS,
                    require_stop=_STRUCTURED_OUTPUT_REQUIRE_STOP,
                )
                return _decode_compact_proposal(
                    text,
                    contract,
                    focus_path=_trusted_compact_focus_path(prompt),
                )
            output = chat_model.create_chat_completion(
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
                response_format={
                    "type": "json_object",
                    "schema": _PROPOSAL_JSON_SCHEMA,
                },
            )
        else:
            if contract is not None:
                raise ValueError(
                    "compact structured proposal requires chat-completion support"
                )
            output = model(
                prompt,
                max_tokens=4096,
                temperature=0.0,
                echo=False,
            )
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
    "LocalProposalGateway",
    "PlannerFeedbackExchange",
    "bind_compact_focus_path",
    "build_retry_prompt",
    "compare_with_codex",
    "decode_prompt_batch",
    "decode_proposal_batch",
    "encode_proposal_batch",
    "local_proposal_attempt_identity_digest",
    "merge_proposal_manifests",
]
