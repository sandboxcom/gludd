"""Job specification schema with a fail-closed ingress boundary."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


@dataclass(frozen=True, slots=True)
class JobIngressLimits:
    """Pinned resource and shape limits for one worker generation.

    Operator values may narrow or moderately widen the defaults, but immutable
    hard ceilings prevent a configuration typo from disabling the boundary.
    ``from_environment`` is called once at module import so in-flight requests
    never observe a partially changed policy during a rolling replacement.
    """

    max_depth: int = 16
    max_collection_items: int = 10_000
    max_serialized_bytes: int = 1_048_576
    max_identifier_chars: int = 128
    max_playbook_chars: int = 255
    max_queue_chars: int = 128

    _ENV_FIELDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("max_depth", "GLUDD_JOB_INGRESS_MAX_DEPTH"),
        ("max_collection_items", "GLUDD_JOB_INGRESS_MAX_COLLECTION_ITEMS"),
        ("max_serialized_bytes", "GLUDD_JOB_INGRESS_MAX_SERIALIZED_BYTES"),
        ("max_identifier_chars", "GLUDD_JOB_INGRESS_MAX_IDENTIFIER_CHARS"),
        ("max_playbook_chars", "GLUDD_JOB_INGRESS_MAX_PLAYBOOK_CHARS"),
        ("max_queue_chars", "GLUDD_JOB_INGRESS_MAX_QUEUE_CHARS"),
    )
    _SAFE_BOUNDS: ClassVar[dict[str, tuple[int, int]]] = {
        "max_depth": (2, 64),
        "max_collection_items": (16, 100_000),
        "max_serialized_bytes": (256, 8_388_608),
        "max_identifier_chars": (16, 256),
        "max_playbook_chars": (16, 1_024),
        "max_queue_chars": (8, 256),
    }

    def __post_init__(self) -> None:
        env_by_field = dict(self._ENV_FIELDS)
        for field_name, (minimum, maximum) in self._SAFE_BOUNDS.items():
            value = getattr(self, field_name)
            env_name = env_by_field[field_name]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(
                    f"{env_name} must be an integer from {minimum} through {maximum}"
                )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> JobIngressLimits:
        """Load validated limits from an environment snapshot.

        Invalid values abort startup instead of silently reverting to a more
        permissive default. Passing a mapping makes configuration validation
        deterministic without mutating process-global environment state.
        """

        source = os.environ if environ is None else environ
        values: dict[str, int] = {}
        for field_name, env_name in cls._ENV_FIELDS:
            raw = source.get(env_name)
            if raw is None:
                continue
            try:
                values[field_name] = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{env_name} must be an integer") from None
        return cls(**values)


# This immutable snapshot is shared by every request handled by the worker.
# Applying new values therefore uses a rolling worker replacement, which keeps
# old and new policies internally consistent and permits immediate rollback.
JOB_INGRESS_LIMITS = JobIngressLimits.from_environment()

_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_QUEUE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_PLAYBOOK_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def _validate_payload_bounds(payload: dict[str, object], limits: JobIngressLimits) -> None:
    """Reject excessive or non-JSON payloads before Pydantic field coercion."""

    collection_items = 0
    serialized_bytes = 0
    active_containers: set[int] = set()

    def add_serialized_bytes(size: int) -> None:
        nonlocal serialized_bytes
        serialized_bytes += size
        if serialized_bytes > limits.max_serialized_bytes:
            raise ValueError("job payload serialized bytes exceed configured limit")

    def add_scalar(value: object) -> None:
        # Character count is a zero-allocation lower bound for UTF-8 bytes. It
        # rejects a giant string before JSON escaping can allocate a second
        # giant copy; the remaining scalar serialization is therefore bounded.
        if isinstance(value, str) and len(value) > limits.max_serialized_bytes:
            raise ValueError("job payload serialized bytes exceed configured limit")
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError("job payload must have a finite JSON serialization") from exc
        add_serialized_bytes(len(encoded))

    def visit(value: object, depth: int) -> None:
        nonlocal collection_items
        if depth > limits.max_depth:
            raise ValueError("job payload nesting depth exceeds configured limit")

        value_type = type(value)
        # Pydantic models commonly pass ``StrEnum``/``IntEnum`` members between
        # trusted internal boundaries. They retain the exact JSON scalar
        # semantics of their base class and are normalized by field validation.
        if value is None or isinstance(value, (str, int, float, bool)):
            add_scalar(value)
            return

        if value_type is dict:
            mapping = cast(dict[object, object], value)
            identity = id(mapping)
            if identity in active_containers:
                raise ValueError("job payload cycle is forbidden")
            active_containers.add(identity)
            try:
                add_serialized_bytes(2)  # opening and closing braces
                collection_items += len(mapping)
                if collection_items > limits.max_collection_items:
                    raise ValueError("job payload collection items exceed configured limit")
                for index, (key, child) in enumerate(mapping.items()):
                    if index:
                        add_serialized_bytes(1)  # comma
                    if type(key) is not str:
                        raise ValueError("job payload mapping keys must be strings")
                    add_scalar(key)
                    add_serialized_bytes(1)  # colon
                    visit(child, depth + 1)
            finally:
                active_containers.remove(identity)
            return

        if value_type in (list, tuple):
            sequence = cast(list[object] | tuple[object, ...], value)
            identity = id(sequence)
            if identity in active_containers:
                raise ValueError("job payload cycle is forbidden")
            active_containers.add(identity)
            try:
                add_serialized_bytes(2)  # opening and closing brackets
                collection_items += len(sequence)
                if collection_items > limits.max_collection_items:
                    raise ValueError("job payload collection items exceed configured limit")
                for index, child in enumerate(sequence):
                    if index:
                        add_serialized_bytes(1)  # comma
                    visit(child, depth + 1)
            finally:
                active_containers.remove(identity)
            return

        raise ValueError("job payload values must be JSON-compatible built-in types")

    visit(payload, 0)


def _required_string(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    todo_id: str | None = None
    return_id: str | None = None
    project_id: str | None = None
    playbook: str
    queue: str
    work_type: str = "unknown"
    resource_profile: str = "low_resource"
    model_profile: str | None = None
    prompt_profile: str | None = None
    vars_namespace_refs: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None
    budget_context: dict[str, object] = Field(default_factory=dict)
    candidate_todos: list[str] = Field(default_factory=list)
    artifact_summaries: list[str] = Field(default_factory=list)
    plan_artifact: str | None = None
    prompt_text: str | None = None
    skill_body: str | None = None
    ansible_roles_path: str | None = None
    templates_dir: str | None = None
    timeout: float | None = None
    human_input: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_ingress(cls, value: object) -> object:
        if type(value) is not dict:
            raise ValueError("job payload must be a plain mapping")
        payload = cast(dict[str, object], value)
        _validate_payload_bounds(payload, JOB_INGRESS_LIMITS)
        return value

    @field_validator("timeout", mode="before")
    @classmethod
    def _validate_timeout(cls, v: object) -> object:
        if v is None:
            return v
        if not isinstance(v, (int, float, str)):
            raise ValueError("timeout must be a number or None")
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError("timeout must be a number or None") from None
        if fv <= 0:
            raise ValueError(f"timeout must be positive (got {fv})")
        return fv

    @field_validator("job_id", mode="before")
    @classmethod
    def _validate_job_id(cls, value: object) -> str:
        cleaned = _required_string(value, "job_id")
        if len(cleaned) > JOB_INGRESS_LIMITS.max_identifier_chars:
            raise ValueError("job_id exceeds configured character limit")
        if _JOB_ID_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("job_id must contain only letters, digits, hyphen, and underscore")
        return cleaned

    @field_validator("playbook", mode="before")
    @classmethod
    def _validate_playbook(cls, value: object) -> str:
        cleaned = _required_string(value, "playbook")
        if len(cleaned) > JOB_INGRESS_LIMITS.max_playbook_chars:
            raise ValueError("playbook exceeds configured character limit")
        if cleaned.startswith("/") or "\\" in cleaned or "\x00" in cleaned:
            raise ValueError("playbook must be a safe relative POSIX path")
        segments = cleaned.split("/")
        if any(
            not segment
            or segment in {".", ".."}
            or _PLAYBOOK_SEGMENT_PATTERN.fullmatch(segment) is None
            for segment in segments
        ):
            raise ValueError("playbook must contain only safe relative path segments")
        return cleaned

    @field_validator("queue", mode="before")
    @classmethod
    def _validate_queue(cls, value: object) -> str:
        cleaned = _required_string(value, "queue")
        if len(cleaned) > JOB_INGRESS_LIMITS.max_queue_chars:
            raise ValueError("queue exceeds configured character limit")
        if _QUEUE_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("queue must be an identifier-like slug")
        return cleaned
