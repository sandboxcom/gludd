"""Alembic contract for durable managed-promotion fencing state."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic/versions/044_add_managed_self_improve_promotions.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("promotion_migration_044", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_extends_single_head_and_defines_upgrade_downgrade() -> None:
    module = _load()

    assert module.revision == "044"
    assert module.down_revision == "043"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_migration_pins_promotion_table_and_fencing_columns() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert '"managed_self_improve_promotions"' in source
    for column in (
        "artifact_digest",
        "plan_identity_digest",
        "attempt_identity_digest",
        "fencing_token",
        "lease_owner",
        "lease_expires_at",
        "worktree_branch",
        "development_commit",
        "marker",
    ):
        assert f'"{column}"' in source
