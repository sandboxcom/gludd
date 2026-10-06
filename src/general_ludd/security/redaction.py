"""Bounded, JSON-safe redaction before persistence, hashing, or logging.

The boundary in this module deliberately never calls ``repr`` or ``str`` on an
unknown object.  A caller receives an already-sanitized JSON value plus
explicit metadata and canonical bytes; raw input therefore need not cross a
serialization or hashing boundary first.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, TypeAlias, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]
SensitiveKeyAction: TypeAlias = Literal["replace", "drop"]

REDACTED_VALUE: Final[str] = "[REDACTED]"
UNSUPPORTED_VALUE: Final[str] = "[REDACTED:unsupported]"

_SECRET_KEY_MARKERS: Final[tuple[str, ...]] = (
    "access_key",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "passwd",
    "password",
    "private_key",
    "psk",
    "secret",
    "session_token",
    "set_cookie",
    "token",
)
_HIDDEN_REASONING_MARKERS: Final[tuple[str, ...]] = (
    "analysis",
    "chain_of_thought",
    "hidden_reasoning",
    "internal_monologue",
    "reasoning",
    "thinking",
)
_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<url>[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+)"
)
_CREDENTIAL_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(\b(?:api[_-]?key|authorization|credential|passwd|password|psk|secret|token)"
    r"\b\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_AUTH_SCHEME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"
)


@dataclass(frozen=True, slots=True)
class RedactionLimits:
    """Pinned shape and byte ceilings for one redaction operation."""

    max_depth: int = 16
    max_items: int = 10_000
    max_string_chars: int = 65_536
    max_total_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        """Reject limits that could disable or unreasonably widen the boundary."""
        bounds = (
            (self.max_depth, 1, 64),
            (self.max_items, 1, 1_000_000),
            (self.max_string_chars, 1, 1_000_000),
            (self.max_total_bytes, 64, 67_108_864),
        )
        if any(type(value) is not int or not low <= value <= high for value, low, high in bounds):
            raise ValueError("redaction limits are invalid")


@dataclass(frozen=True, slots=True)
class RedactionMetadata:
    """Content-free evidence describing every destructive transformation."""

    redaction_count: int
    redaction_kinds: tuple[str, ...]
    truncation_count: int
    truncation_kinds: tuple[str, ...]
    stored_bytes: int

    @property
    def truncated(self) -> bool:
        """Return whether one or more configured bounds changed the value."""
        return self.truncation_count > 0

    def as_dict(self) -> dict[str, object]:
        """Return JSON-safe metadata without any source content."""
        return {
            "redaction_count": self.redaction_count,
            "redaction_kinds": list(self.redaction_kinds),
            "truncation_count": self.truncation_count,
            "truncation_kinds": list(self.truncation_kinds),
            "truncated": self.truncated,
            "stored_bytes": self.stored_bytes,
        }


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """A sanitized value and the only canonical bytes callers should persist."""

    value: JsonValue
    metadata: RedactionMetadata
    _canonical_bytes: bytes = field(repr=False, compare=False)

    def canonical_json_bytes(self) -> bytes:
        """Return stable UTF-8 JSON produced only after redaction."""
        return self._canonical_bytes


@dataclass(slots=True)
class _Metrics:
    redaction_count: int = 0
    redaction_kinds: set[str] = field(default_factory=set)
    truncation_count: int = 0
    truncation_kinds: set[str] = field(default_factory=set)
    visited_items: int = 0

    def redact(self, kind: str) -> None:
        self.redaction_count += 1
        self.redaction_kinds.add(kind)

    def truncate(self, kind: str) -> None:
        self.truncation_count += 1
        self.truncation_kinds.add(kind)


def _canonical_json(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


def _sensitive_key_kind(key: str) -> str | None:
    normalized = _normalized_key(key)
    if any(marker in normalized for marker in _HIDDEN_REASONING_MARKERS):
        return "hidden_reasoning"
    if any(marker in normalized for marker in _SECRET_KEY_MARKERS):
        return "secret_key"
    return None


def _redact_url(match: re.Match[str], metrics: _Metrics) -> str:
    raw_url = match.group("url")
    trailing = ""
    while raw_url.endswith((".", ",", ")", "]", "}")):
        trailing = raw_url[-1] + trailing
        raw_url = raw_url[:-1]
    try:
        split = urlsplit(raw_url)
        hostname = split.hostname
        port = split.port
    except ValueError:
        metrics.redact("credential_url")
        return "[REDACTED:credential_url]" + trailing

    changed = split.username is not None or split.password is not None
    netloc = split.netloc
    if changed:
        if hostname is None:
            metrics.redact("credential_url")
            return "[REDACTED:credential_url]" + trailing
        rendered_host = f"[{hostname}]" if ":" in hostname else hostname
        rendered_port = "" if port is None else f":{port}"
        netloc = f"{REDACTED_VALUE}@{rendered_host}{rendered_port}"

    query_pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(split.query, keep_blank_values=True):
        if _sensitive_key_kind(key) is not None:
            query_pairs.append((key, REDACTED_VALUE))
            changed = True
        else:
            query_pairs.append((key, value))
    query = urlencode(query_pairs)

    fragment = split.fragment
    if _CREDENTIAL_ASSIGNMENT_RE.search(fragment):
        fragment = REDACTED_VALUE
        changed = True

    if not changed:
        return raw_url + trailing
    metrics.redact("credential_url")
    return urlunsplit((split.scheme, netloc, split.path, query, fragment)) + trailing


def _redact_text(value: str, metrics: _Metrics, limits: RedactionLimits) -> str:
    string_was_truncated = len(value) > limits.max_string_chars
    if string_was_truncated:
        metrics.truncate("string")
        value = value[: limits.max_string_chars]
    redacted = _URL_RE.sub(lambda match: _redact_url(match, metrics), value)

    assignment_count = 0

    def replace_assignment(match: re.Match[str]) -> str:
        nonlocal assignment_count
        assignment_count += 1
        return f"{match.group(1)}{REDACTED_VALUE}"

    redacted = _CREDENTIAL_ASSIGNMENT_RE.sub(replace_assignment, redacted)
    auth_count = 0

    def replace_auth(match: re.Match[str]) -> str:
        nonlocal auth_count
        auth_count += 1
        return f"{match.group(1)} {REDACTED_VALUE}"

    redacted = _AUTH_SCHEME_RE.sub(replace_auth, redacted)
    for _ in range(assignment_count + auth_count):
        metrics.redact("credential_text")

    if len(redacted) > limits.max_string_chars:
        if not string_was_truncated:
            metrics.truncate("string")
        return redacted[: limits.max_string_chars]
    return redacted


def redact_for_persistence(
    value: object,
    *,
    limits: RedactionLimits | None = None,
    sensitive_key_action: SensitiveKeyAction = "replace",
) -> RedactionResult:
    """Return bounded JSON data with secrets and hidden reasoning removed.

    Unknown objects become a fixed marker without invoking object-controlled
    string conversion.  ``drop`` exists only for callers whose established API
    omits credential-bearing keys; the canonical capture form uses ``replace``.
    """
    active_limits = limits or RedactionLimits()
    if sensitive_key_action not in {"replace", "drop"}:
        raise ValueError("sensitive_key_action is invalid")
    metrics = _Metrics()
    active_containers: set[int] = set()

    def visit(current: object, depth: int) -> JsonValue:
        if depth > active_limits.max_depth:
            metrics.truncate("depth")
            return "[TRUNCATED:depth]"

        if current is None or type(current) in (bool, int):
            return cast(JsonScalar, current)
        if type(current) is float:
            if math.isfinite(current):
                return current
            metrics.redact("unsupported_type")
            return UNSUPPORTED_VALUE
        if type(current) is str:
            return _redact_text(current, metrics, active_limits)

        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active_containers:
                metrics.truncate("cycle")
                return "[TRUNCATED:cycle]"
            active_containers.add(identity)
            safe_mapping: dict[str, JsonValue] = {}
            try:
                try:
                    iterator = iter(current.items())
                    while True:
                        try:
                            raw_key, child = next(iterator)
                        except StopIteration:
                            break
                        if metrics.visited_items >= active_limits.max_items:
                            metrics.truncate("items")
                            break
                        metrics.visited_items += 1
                        if type(raw_key) is not str:
                            metrics.redact("unsupported_key")
                            continue
                        key = raw_key
                        sensitive_kind = _sensitive_key_kind(key)
                        if sensitive_kind is not None:
                            metrics.redact(sensitive_kind)
                            if sensitive_key_action == "replace":
                                safe_mapping[key] = REDACTED_VALUE
                            continue
                        safe_mapping[key] = visit(child, depth + 1)
                except Exception:
                    metrics.redact("unsupported_type")
                    return UNSUPPORTED_VALUE
            finally:
                active_containers.remove(identity)
            return safe_mapping

        if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            identity = id(current)
            if identity in active_containers:
                metrics.truncate("cycle")
                return "[TRUNCATED:cycle]"
            active_containers.add(identity)
            safe_sequence: list[JsonValue] = []
            try:
                try:
                    iterator = iter(current)
                    while True:
                        try:
                            child = next(iterator)
                        except StopIteration:
                            break
                        if metrics.visited_items >= active_limits.max_items:
                            metrics.truncate("items")
                            break
                        metrics.visited_items += 1
                        safe_sequence.append(visit(child, depth + 1))
                except Exception:
                    metrics.redact("unsupported_type")
                    return UNSUPPORTED_VALUE
            finally:
                active_containers.remove(identity)
            return safe_sequence

        metrics.redact("unsupported_type")
        return UNSUPPORTED_VALUE

    safe_value = visit(value, 0)
    canonical = _canonical_json(safe_value)
    if len(canonical) > active_limits.max_total_bytes:
        metrics.truncate("total_bytes")
        safe_value = "[TRUNCATED:total_bytes]"
        canonical = _canonical_json(safe_value)

    metadata = RedactionMetadata(
        redaction_count=metrics.redaction_count,
        redaction_kinds=tuple(sorted(metrics.redaction_kinds)),
        truncation_count=metrics.truncation_count,
        truncation_kinds=tuple(sorted(metrics.truncation_kinds)),
        stored_bytes=len(canonical),
    )
    return RedactionResult(
        value=safe_value,
        metadata=metadata,
        _canonical_bytes=canonical,
    )


__all__ = [
    "REDACTED_VALUE",
    "RedactionLimits",
    "RedactionMetadata",
    "RedactionResult",
    "redact_for_persistence",
]
