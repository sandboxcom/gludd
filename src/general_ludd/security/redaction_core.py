"""Internal bounded traversal used by the persistence redaction boundary.

Keeping shape traversal separate from the public result models makes the
security boundary easier to audit: this module owns source inspection and the
facade in :mod:`general_ludd.security.redaction` owns returned evidence.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, TypeAlias, cast
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
_TOKEN_USAGE_KEYS: Final[frozenset[str]] = frozenset({"input", "output"})
_MAX_TOKEN_USAGE_COUNT: Final[int] = 9_223_372_036_854_775_807
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


class _RedactionLimits(Protocol):
    """Structural bounds accepted from the public redaction facade."""

    @property
    def max_depth(self) -> int: ...

    @property
    def max_items(self) -> int: ...

    @property
    def max_string_chars(self) -> int: ...

    @property
    def max_total_bytes(self) -> int: ...


@dataclass(frozen=True, slots=True)
class CoreRedactionResult:
    """Internal sanitized value, counters, and canonical representation."""

    value: JsonValue
    redaction_count: int
    redaction_kinds: tuple[str, ...]
    truncation_count: int
    truncation_kinds: tuple[str, ...]
    canonical_bytes: bytes


@dataclass(slots=True)
class _Metrics:
    """Mutable counters shared by every branch of a bounded traversal."""

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


def _is_safe_token_usage_counts(key: str, value: object) -> bool:
    """Accept only the exact, bounded completion-usage counter shape."""
    if key != "tokens" or type(value) is not dict:
        return False
    counts = cast(dict[object, object], value)
    if frozenset(counts) != _TOKEN_USAGE_KEYS:
        return False
    return all(
        type(count) is int and 0 <= count <= _MAX_TOKEN_USAGE_COUNT
        for count in counts.values()
    )


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


def _redact_text(value: str, metrics: _Metrics, limits: _RedactionLimits) -> str:
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


@dataclass(slots=True)
class _Traversal:
    """Walk supported containers while enforcing a single shared budget."""

    limits: _RedactionLimits
    sensitive_key_action: SensitiveKeyAction
    metrics: _Metrics = field(default_factory=_Metrics)
    active_containers: set[int] = field(default_factory=set)

    def visit(self, current: object, depth: int = 0) -> JsonValue:
        """Return a JSON value without invoking unknown-object conversion."""
        if depth > self.limits.max_depth:
            self.metrics.truncate("depth")
            return "[TRUNCATED:depth]"

        if current is None or type(current) in (bool, int):
            return cast(JsonScalar, current)
        if type(current) is float:
            if math.isfinite(current):
                return current
            self.metrics.redact("unsupported_type")
            return UNSUPPORTED_VALUE
        if type(current) is str:
            return _redact_text(current, self.metrics, self.limits)
        if isinstance(current, Mapping):
            return self._visit_mapping(current, depth)
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            return self._visit_sequence(current, depth)
        self.metrics.redact("unsupported_type")
        return UNSUPPORTED_VALUE

    def _visit_mapping(self, current: Mapping[object, object], depth: int) -> JsonValue:
        identity = id(current)
        if identity in self.active_containers:
            self.metrics.truncate("cycle")
            return "[TRUNCATED:cycle]"
        self.active_containers.add(identity)
        safe_mapping: dict[str, JsonValue] = {}
        try:
            try:
                iterator = iter(current.items())
                while True:
                    try:
                        raw_key, child = next(iterator)
                    except StopIteration:
                        break
                    if self.metrics.visited_items >= self.limits.max_items:
                        self.metrics.truncate("items")
                        break
                    self.metrics.visited_items += 1
                    if type(raw_key) is not str:
                        self.metrics.redact("unsupported_key")
                        continue
                    key = raw_key
                    sensitive_kind = _sensitive_key_kind(key)
                    if sensitive_kind is not None and not _is_safe_token_usage_counts(
                        key, child
                    ):
                        self.metrics.redact(sensitive_kind)
                        if self.sensitive_key_action == "replace":
                            safe_mapping[key] = REDACTED_VALUE
                        continue
                    safe_mapping[key] = self.visit(child, depth + 1)
            except Exception:
                self.metrics.redact("unsupported_type")
                return UNSUPPORTED_VALUE
        finally:
            self.active_containers.remove(identity)
        return safe_mapping

    def _visit_sequence(self, current: Sequence[object], depth: int) -> JsonValue:
        identity = id(current)
        if identity in self.active_containers:
            self.metrics.truncate("cycle")
            return "[TRUNCATED:cycle]"
        self.active_containers.add(identity)
        safe_sequence: list[JsonValue] = []
        try:
            try:
                iterator = iter(current)
                while True:
                    try:
                        child = next(iterator)
                    except StopIteration:
                        break
                    if self.metrics.visited_items >= self.limits.max_items:
                        self.metrics.truncate("items")
                        break
                    self.metrics.visited_items += 1
                    safe_sequence.append(self.visit(child, depth + 1))
            except Exception:
                self.metrics.redact("unsupported_type")
                return UNSUPPORTED_VALUE
        finally:
            self.active_containers.remove(identity)
        return safe_sequence


def redact_value(
    value: object,
    *,
    limits: _RedactionLimits,
    sensitive_key_action: SensitiveKeyAction,
) -> CoreRedactionResult:
    """Redact one value and return content-free traversal evidence."""
    if sensitive_key_action not in {"replace", "drop"}:
        raise ValueError("sensitive_key_action is invalid")
    traversal = _Traversal(limits, sensitive_key_action)
    safe_value = traversal.visit(value)
    canonical = _canonical_json(safe_value)
    if len(canonical) > limits.max_total_bytes:
        traversal.metrics.truncate("total_bytes")
        safe_value = "[TRUNCATED:total_bytes]"
        canonical = _canonical_json(safe_value)
    metrics = traversal.metrics
    return CoreRedactionResult(
        value=safe_value,
        redaction_count=metrics.redaction_count,
        redaction_kinds=tuple(sorted(metrics.redaction_kinds)),
        truncation_count=metrics.truncation_count,
        truncation_kinds=tuple(sorted(metrics.truncation_kinds)),
        canonical_bytes=canonical,
    )


__all__ = [
    "REDACTED_VALUE",
    "JsonValue",
    "SensitiveKeyAction",
    "redact_value",
]
