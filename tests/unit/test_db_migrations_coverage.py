"""Fail-closed planning branches for database migrations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from alembic.util.exc import CommandError

from general_ludd.db.migrations import get_alembic_config, plan_migration


def test_plan_migration_rejects_repository_without_head_revision() -> None:
    """Planning cannot proceed when the migration graph has no unique head."""
    script = MagicMock()
    script.get_current_head.return_value = None

    with (
        patch("general_ludd.db.migrations.ScriptDirectory.from_config", return_value=script),
        pytest.raises(RuntimeError, match="No head revision found"),
    ):
        plan_migration(get_alembic_config())


def test_plan_migration_propagates_non_sqlite_offline_error() -> None:
    """Only the documented SQLite batch limitation may become a diagnostic plan."""
    config = get_alembic_config("postgresql://localhost/gludd")
    script = MagicMock()
    script.get_current_head.return_value = "head"
    environment = MagicMock()
    environment.__enter__.return_value.run_migrations.side_effect = CommandError("unsupported dialect")

    with (
        patch("general_ludd.db.migrations.ScriptDirectory.from_config", return_value=script),
        patch("general_ludd.db.migrations.EnvironmentContext", return_value=environment),
        pytest.raises(CommandError, match="unsupported dialect"),
    ):
        plan_migration(config)


@pytest.mark.parametrize(
    ("current_revision", "expected_pending"),
    [(None, 1), ("old", 2), ("head", 0)],
)
def test_plan_migration_uses_injected_connection_revision(
    current_revision: str | None,
    expected_pending: int,
) -> None:
    """A caller-owned connection yields exact behind, unborn, and at-head counts."""
    config = get_alembic_config("postgresql://localhost/gludd")
    config.attributes["connection"] = MagicMock()
    script = MagicMock()
    script.get_current_head.return_value = "head"
    script.get_heads.return_value = ["head"]
    script.iterate_revisions.return_value = ["first", "second"]
    migration_context = MagicMock()
    migration_context.get_current_revision.return_value = current_revision

    with (
        patch("general_ludd.db.migrations.ScriptDirectory.from_config", return_value=script),
        patch("general_ludd.db.migrations.EnvironmentContext"),
        patch(
            "general_ludd.db.migrations.MigrationContext.configure",
            return_value=migration_context,
        ),
    ):
        result = plan_migration(config)

    assert result.current_rev == current_revision
    assert result.pending_count == expected_pending
