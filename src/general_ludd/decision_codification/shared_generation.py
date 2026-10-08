"""PostgreSQL-backed, artifact-authenticated generation state for many hosts."""

from __future__ import annotations

from general_ludd.decision_codification.shared_generation_base import (
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_rollback import (
    _PostgresGenerationRollback,
)


class PostgresGenerationStore(_PostgresGenerationRollback):
    """Serialize exact generation transitions through existing SQLAlchemy rows."""


__all__ = ["PostgresGenerationStore", "SharedGenerationStoreError"]
