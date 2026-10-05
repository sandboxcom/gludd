"""Stable type contracts for the self-update module rollback boundary."""

from __future__ import annotations

import importlib

import general_ludd.self_update.module_snapshot as module_snapshot


def test_module_snapshot_type_identity_survives_engine_reload() -> None:
    """Reloading rollback behavior must not invalidate stored snapshots."""
    original_type = module_snapshot.ModuleSnapshot

    reloaded = importlib.reload(module_snapshot)

    assert reloaded.ModuleSnapshot is original_type
