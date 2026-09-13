"""Tests for narrow compatibility checks on injected proposal callbacks."""

from __future__ import annotations

import pytest

from general_ludd.self_improve._callback_compat import (
    accepts_keyword,
    accepts_timeout_keyword,
    invoke_with_optional_timeout,
    invoke_with_supported_keywords,
    validated_timeout_seconds,
)


def test_timeout_keyword_detection_accepts_named_and_variadic_callbacks() -> None:
    """Only call shapes that can receive the approved keyword are admitted."""
    def named(*, timeout_seconds: float) -> None:
        del timeout_seconds

    def variadic(**kwargs: object) -> None:
        del kwargs

    assert accepts_timeout_keyword(named)
    assert accepts_timeout_keyword(variadic)


def test_timeout_keyword_detection_rejects_legacy_and_positional_only_callbacks() -> None:
    """Legacy and positional-only callables retain their original call shape."""
    def legacy() -> None:
        return None

    def positional_only(timeout_seconds: float, /) -> None:
        del timeout_seconds

    assert not accepts_timeout_keyword(legacy)
    assert not accepts_timeout_keyword(positional_only)
    assert not accepts_timeout_keyword(object())


@pytest.mark.parametrize(
    "value",
    [True, 0, -1, 3_600.1, float("nan"), float("inf"), "30"],
)
def test_candidate_timeout_rejects_ambiguous_or_unbounded_values(value: object) -> None:
    """A routed deadline must remain finite, positive, numeric, and bounded."""
    with pytest.raises(ValueError, match=r"finite and in \(0, 3600\]"):
        validated_timeout_seconds(value)


def test_candidate_timeout_preserves_subsecond_precision() -> None:
    """Validation must not silently round the candidate's approved deadline."""
    assert validated_timeout_seconds(30.25) == 30.25


def test_optional_timeout_invocation_preserves_modern_and_legacy_call_shapes() -> None:
    """The compatibility seam forwards a deadline only when it can be received."""
    observed: list[float] = []

    def modern(value: str, *, timeout_seconds: float) -> str:
        observed.append(timeout_seconds)
        return value

    def legacy(value: str) -> str:
        return value

    assert invoke_with_optional_timeout(
        modern,
        ("modern",),
        timeout_seconds=30.25,
    ) == "modern"
    assert invoke_with_optional_timeout(
        legacy,
        ("legacy",),
        timeout_seconds=30.25,
    ) == "legacy"
    assert observed == [30.25]


def test_supported_keyword_invocation_omits_new_contract_fields_for_legacy_callbacks() -> None:
    """New envelope fields must not break trusted legacy injected callbacks."""
    observed: list[tuple[object, float]] = []

    def modern(
        value: str,
        *,
        proposal_codec: object,
        timeout_seconds: float,
    ) -> str:
        observed.append((proposal_codec, timeout_seconds))
        return value

    def legacy(value: str) -> str:
        return value

    optional = {"proposal_codec": "codec", "timeout_seconds": 30.25}
    assert accepts_keyword(modern, "proposal_codec")
    assert not accepts_keyword(legacy, "proposal_codec")
    assert invoke_with_supported_keywords(modern, ("modern",), optional) == "modern"
    assert invoke_with_supported_keywords(legacy, ("legacy",), optional) == "legacy"
    assert observed == [("codec", 30.25)]
