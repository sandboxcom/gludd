"""Focused contracts for extracted decision-capture support helpers."""

from __future__ import annotations

import pytest

from general_ludd.decision_codification.capture_support import (
    bounded_private_identifier,
    correlation_digest,
)


def test_private_identifier_validation_never_echoes_rejected_content() -> None:
    """Bounds failures remain content-free while valid private IDs round-trip."""
    assert (
        bounded_private_identifier(
            "private-capture-id",
            max_bytes=64,
            error_type=RuntimeError,
        )
        == "private-capture-id"
    )

    with pytest.raises(RuntimeError, match="exceeds its bound") as exc_info:
        bounded_private_identifier(
            "do-not-echo-this-value",
            max_bytes=4,
            error_type=RuntimeError,
        )
    assert "do-not-echo-this-value" not in str(exc_info.value)


def test_correlation_digest_is_stable_and_domain_separates_labels() -> None:
    """The same scoped input is stable while a different label cannot collide."""
    def digest(label: str) -> str:
        return correlation_digest(
            correlation_key=b"0123456789abcdef",
            project_id="project",
            policy_digest="sha256:" + "a" * 64,
            label=label,
            values=("private-id",),
        )

    first = digest("capture")

    assert digest("capture") == first
    assert digest("task") != first
    assert len(first) == 64
