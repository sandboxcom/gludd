"""Focused contracts for managed-promotion repository support helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from general_ludd.db.models import ManagedSelfImprovePromotionModel
from general_ludd.db.promotion_repository_support import (
    _dialect_insert,
    _require_aware,
    _require_digest,
    _require_text,
)


def test_support_validators_preserve_canonical_values() -> None:
    """Valid support values pass through without normalization drift."""
    aware = datetime(2029, 1, 1, tzinfo=UTC)

    _require_aware("now", aware)
    assert _require_text("owner", " worker-one ", maximum=32) == "worker-one"
    assert _require_digest("artifact", "a" * 64) == "a" * 64


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: _require_aware("now", datetime(2029, 1, 1)), "timezone-aware"),
        (lambda: _require_text("owner", " ", maximum=32), "non-empty text"),
        (lambda: _require_text("owner", "x" * 33, maximum=32), "safe bound"),
        (lambda: _require_digest("artifact", "A" * 64), "lowercase hex"),
    ],
)
def test_support_validators_reject_invalid_values(
    call: Any,
    message: str,
) -> None:
    """Invalid boundary values retain the repository's stable diagnostics."""
    with pytest.raises(ValueError, match=message):
        call()


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_dialect_insert_uses_supported_conflict_aware_builder(dialect: str) -> None:
    """Both supported engines receive their native conflict-aware insert."""
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name=dialect))
    )

    statement = _dialect_insert(cast(Any, session))

    assert cast(Any, statement.table).name == cast(
        Any, ManagedSelfImprovePromotionModel.__table__
    ).name


def test_dialect_insert_rejects_unknown_database() -> None:
    """An unknown database fails closed before any persistence statement."""
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    )

    with pytest.raises(ValueError, match="does not support SQL dialect"):
        _dialect_insert(cast(Any, session))
