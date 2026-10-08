"""Bounded, JSON-safe redaction before persistence, hashing, or logging.

The boundary in this module deliberately never calls ``repr`` or ``str`` on an
unknown object.  A caller receives an already-sanitized JSON value plus
explicit metadata and canonical bytes; raw input therefore need not cross a
serialization or hashing boundary first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from general_ludd.security.redaction_core import (
    REDACTED_VALUE,
    JsonValue,
    SensitiveKeyAction,
    redact_value,
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
        if any(
            type(value) is not int or not low <= value <= high
            for value, low, high in bounds
        ):
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
    core_result = redact_value(
        value,
        limits=limits or RedactionLimits(),
        sensitive_key_action=sensitive_key_action,
    )
    metadata = RedactionMetadata(
        redaction_count=core_result.redaction_count,
        redaction_kinds=core_result.redaction_kinds,
        truncation_count=core_result.truncation_count,
        truncation_kinds=core_result.truncation_kinds,
        stored_bytes=len(core_result.canonical_bytes),
    )
    return RedactionResult(
        value=core_result.value,
        metadata=metadata,
        _canonical_bytes=core_result.canonical_bytes,
    )


__all__ = [
    "REDACTED_VALUE",
    "RedactionLimits",
    "RedactionMetadata",
    "RedactionResult",
    "redact_for_persistence",
]
