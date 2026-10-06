"""Tests for the canonical persistence redaction boundary."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

import pytest

from general_ludd.security.redaction import (
    REDACTED_VALUE,
    RedactionLimits,
    redact_for_persistence,
)


def test_nested_credentials_and_hidden_reasoning_are_redacted_before_serialization() -> None:
    raw = {
        "safe": "visible",
        "headers": {
            "Authorization": "Bearer top-secret-token",
            "X-Request-ID": "request-1",
        },
        "steps": [
            {"client_secret": "client-secret", "result": True},
            {"reasoning_content": "private chain of thought", "answer": 42},
        ],
    }

    result = redact_for_persistence(raw)
    serialized = result.canonical_json_bytes()

    assert result.value == {
        "safe": "visible",
        "headers": {
            "Authorization": REDACTED_VALUE,
            "X-Request-ID": "request-1",
        },
        "steps": [
            {"client_secret": REDACTED_VALUE, "result": True},
            {"reasoning_content": REDACTED_VALUE, "answer": 42},
        ],
    }
    assert b"top-secret-token" not in serialized
    assert b"client-secret" not in serialized
    assert b"private chain of thought" not in serialized
    assert result.metadata.redaction_count == 3
    assert result.metadata.redaction_kinds == ("hidden_reasoning", "secret_key")
    assert result.metadata.truncated is False


def test_credential_url_and_assignment_text_keep_safe_identity() -> None:
    value = {
        "endpoint": "https://alice:p%40ss@example.test:8443/v1?q=ok&api_key=url-secret#fragment",
        "detail": "request failed: token=detail-secret safe=kept",
    }

    result = redact_for_persistence(value)
    serialized = result.canonical_json_bytes().decode("utf-8")

    assert "example.test:8443/v1" in serialized
    assert "q=ok" in serialized
    assert "alice" not in serialized
    assert "p%40ss" not in serialized
    assert "url-secret" not in serialized
    assert "detail-secret" not in serialized
    assert "safe=kept" in serialized
    assert set(result.metadata.redaction_kinds) == {
        "credential_text",
        "credential_url",
    }


def test_cycle_and_unsupported_object_never_use_object_repr() -> None:
    @dataclass
    class Dangerous:
        value: str

        def __repr__(self) -> str:
            return f"Dangerous({self.value})"

    cyclic: list[object] = []
    cyclic.append(cyclic)
    result = redact_for_persistence(
        {"cycle": cyclic, "object": Dangerous("repr-secret")}
    )
    serialized = result.canonical_json_bytes()

    assert b"repr-secret" not in serialized
    assert result.value == {
        "cycle": ["[TRUNCATED:cycle]"],
        "object": "[REDACTED:unsupported]",
    }
    assert result.metadata.redaction_kinds == ("unsupported_type",)
    assert result.metadata.truncation_kinds == ("cycle",)


@pytest.mark.parametrize(
    ("limits", "value", "kind"),
    [
        (RedactionLimits(max_depth=1), {"a": {"b": {"c": 1}}}, "depth"),
        (RedactionLimits(max_items=2), [1, 2, 3], "items"),
        (RedactionLimits(max_string_chars=4), "abcdefgh", "string"),
        (
            RedactionLimits(max_total_bytes=64, max_string_chars=1_000),
            {"large": "x" * 100},
            "total_bytes",
        ),
    ],
)
def test_all_bounds_are_explicit_in_metadata(
    limits: RedactionLimits,
    value: object,
    kind: str,
) -> None:
    result = redact_for_persistence(value, limits=limits)

    assert result.metadata.truncated is True
    assert kind in result.metadata.truncation_kinds
    assert result.metadata.truncation_count >= 1
    assert len(result.canonical_json_bytes()) <= limits.max_total_bytes


def test_drop_mode_preserves_existing_webhook_key_filter_contract() -> None:
    result = redact_for_persistence(
        {"api_key": "gone", "safe": {"password": "gone", "ok": 1}},
        sensitive_key_action="drop",
    )

    assert result.value == {"safe": {"ok": 1}}
    assert result.metadata.redaction_count == 2


def test_mapping_keys_must_be_strings_and_fail_closed_without_content() -> None:
    result = redact_for_persistence({"safe": 1, 2: "must-not-survive"})

    assert result.value == {"safe": 1}
    assert result.metadata.redaction_kinds == ("unsupported_key",)
    assert b"must-not-survive" not in result.canonical_json_bytes()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_depth": 0},
        {"max_items": 0},
        {"max_string_chars": 0},
        {"max_total_bytes": 63},
    ],
)
def test_invalid_limit_construction_is_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="redaction limits are invalid"):
        RedactionLimits(**kwargs)


def test_metadata_is_json_safe_and_reports_stored_size() -> None:
    result = redact_for_persistence(
        {"password": "secret", "text": "abcdef"},
        limits=RedactionLimits(max_string_chars=3),
    )

    metadata = result.metadata.as_dict()
    assert json.loads(json.dumps(metadata)) == metadata
    assert metadata["redaction_count"] == 1
    assert metadata["truncated"] is True
    assert metadata["stored_bytes"] == len(result.canonical_json_bytes())


def test_malformed_credential_url_is_replaced_wholesale() -> None:
    result = redact_for_persistence("https://user:password@[bad-host/path")

    assert result.value == "[REDACTED:credential_url]"
    assert result.metadata.redaction_kinds == ("credential_url",)


def test_credential_url_preserves_trailing_sentence_punctuation() -> None:
    result = redact_for_persistence("failed (https://user:password@example.test/path).")
    serialized = result.canonical_json_bytes()

    assert b"user" not in serialized
    assert b"password" not in serialized
    assert serialized.endswith(b").\"")


def test_fragment_credentials_and_authorization_text_are_redacted() -> None:
    result = redact_for_persistence(
        "https://example.test/callback#token=fragment-secret Bearer bearer-secret"
    )
    serialized = result.canonical_json_bytes()

    assert b"fragment-secret" not in serialized
    assert b"bearer-secret" not in serialized
    assert set(result.metadata.redaction_kinds) == {
        "credential_text",
        "credential_url",
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_content_free_unsupported_values(value: float) -> None:
    result = redact_for_persistence(value)

    assert result.value == "[REDACTED:unsupported]"
    assert result.metadata.redaction_kinds == ("unsupported_type",)


def test_mapping_cycle_is_bounded() -> None:
    value: dict[str, object] = {}
    value["self"] = value

    result = redact_for_persistence(value)

    assert result.value == {"self": "[TRUNCATED:cycle]"}
    assert result.metadata.truncation_kinds == ("cycle",)


def test_invalid_sensitive_key_action_is_rejected_without_source_content() -> None:
    with pytest.raises(ValueError, match="sensitive_key_action is invalid"):
        redact_for_persistence({"password": "must-not-appear"}, sensitive_key_action="erase")  # type: ignore[arg-type]


class _BrokenMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise RuntimeError("mapping-secret")

    def __iter__(self) -> Iterator[str]:
        yield "key"

    def __len__(self) -> int:
        return 1


class _BrokenSequence(Sequence[object]):
    def __getitem__(self, index: int) -> object:
        raise RuntimeError("sequence-secret")

    def __len__(self) -> int:
        return 1


@pytest.mark.parametrize("value", [_BrokenMapping(), _BrokenSequence()])
def test_failing_container_protocols_return_content_free_marker(value: object) -> None:
    result = redact_for_persistence(value)

    assert result.value == "[REDACTED:unsupported]"
    assert b"secret" not in result.canonical_json_bytes()
    assert result.metadata.redaction_kinds == ("unsupported_type",)
