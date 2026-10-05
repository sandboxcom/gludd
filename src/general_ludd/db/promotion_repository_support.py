"""Validation and SQL-dialect support for managed promotion persistence."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import ManagedSelfImprovePromotionModel

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_ERROR_BYTES = 4096


def _require_aware(label: str, value: datetime) -> None:
    """Require one timezone-aware transaction timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_text(label: str, value: str, *, maximum: int) -> str:
    """Return bounded, non-empty, control-safe text."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    normalized = value.strip()
    if "\x00" in normalized or len(normalized.encode("utf-8")) > maximum:
        raise ValueError(f"{label} exceeds its safe bound")
    return normalized


def _require_digest(label: str, value: str) -> str:
    """Return one canonical SHA-256 digest."""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


def _dialect_insert(session: AsyncSession) -> Any:
    """Return a conflict-aware insert for the two supported databases."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return postgresql_insert(ManagedSelfImprovePromotionModel)
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(ManagedSelfImprovePromotionModel)
    raise ValueError(f"promotion persistence does not support SQL dialect {dialect!r}")
