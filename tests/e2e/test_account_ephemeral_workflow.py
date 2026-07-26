"""End-to-end account lifecycle workflows for ephemeral cloud accounts.

These tests drive the public manager and deployment hooks together with a
deterministic backend.  They intentionally cover both retention outcomes and
the no-op hook branches so branch coverage reflects real lifecycle behavior.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


class _Backend:
    def __init__(self) -> None:
        self.active: dict[str, bool] = {}
        self.deleted: list[tuple[str, str]] = []
        self._next = 0

    def create_account(self, provider: str, budget: float) -> dict[str, object]:
        self._next += 1
        account_id = f"{provider}-e2e-{self._next}"
        self.active[account_id] = True
        return {
            "account_id": account_id,
            "provider": provider,
            "access_key_id": f"key-{self._next}",
            "secret_access_key": f"secret-{self._next}",
            "budget_limit": budget,
        }

    def delete_account(self, provider: str, account_id: str) -> dict[str, object]:
        self.deleted.append((provider, account_id))
        existed = self.active.pop(account_id, None) is not None
        return {"provider": provider, "account_id": account_id, "deleted": existed}

    def is_account_active(self, provider: str, account_id: str) -> bool:
        return self.active.get(account_id, False)


def test_ephemeral_manager_cleanup_persists_registry_and_redacts_secret(tmp_path: Path) -> None:
    from general_ludd.account.ephemeral import EphemeralAccountManager
    from general_ludd.account.lifecycle_policy import LifecycleAction, PolicyConfig

    backend = _Backend()
    registry = tmp_path / "ephemeral.json"
    manager = EphemeralAccountManager(
        policy=PolicyConfig(retention_period_hours=1),
        backend=backend,
        registry_path=str(registry),
    )
    old = manager.create_account(provider="aws", budget=7.5)
    current = manager.create_account(provider="gcp", budget=3.0)

    old_entry = manager._registry[old.account_id]
    old_entry["created_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    assert manager.evaluate_account_lifecycle(old.account_id) is LifecycleAction.DELETE
    assert manager.evaluate_account_lifecycle(current.account_id) is LifecycleAction.KEEP
    assert manager.is_account_active(provider="aws", account_id=old.account_id)
    assert not manager.is_account_active(provider="aws", account_id="unknown")

    listed = manager.list_accounts()
    assert all("secret_access_key" not in entry for entry in listed)
    report = manager.cleanup_expired()
    assert [item["account_id"] for item in report["deleted"]] == [old.account_id]
    assert report["kept"] == [current.account_id]
    assert registry.exists()
    persisted = json.loads(registry.read_text())
    assert old.account_id not in persisted
    assert current.account_id in persisted

    reloaded = EphemeralAccountManager(
        backend=backend,
        registry_path=str(registry),
    )
    assert [entry["account_id"] for entry in reloaded.list_accounts()] == [current.account_id]


def test_ephemeral_deploy_and_reconcile_hooks_cover_noop_and_cleanup_paths(tmp_path: Path) -> None:
    from general_ludd.account.ephemeral import (
        EphemeralAccountManager,
        maybe_create_ephemeral_for_deploy,
        maybe_delete_ephemeral_after_task,
    )
    from general_ludd.account.lifecycle_policy import PolicyConfig

    backend = _Backend()
    manager = EphemeralAccountManager(
        backend=backend,
        registry_path=str(tmp_path / "hooks.json"),
    )
    metadata: dict[str, object] = {}

    assert maybe_create_ephemeral_for_deploy(
        provider="aws", policy=None, metadata=metadata, manager=manager
    ) == (None, None)
    assert maybe_create_ephemeral_for_deploy(
        provider="other", policy=PolicyConfig(), metadata=metadata, manager=manager
    ) == (None, None)
    assert maybe_delete_ephemeral_after_task(manager=None, metadata=None) is None
    assert maybe_delete_ephemeral_after_task(manager=manager, metadata=None) is None

    policy = PolicyConfig(auto_delete_after_use=True, budget_limit=12.0)
    hooked_manager, creds = maybe_create_ephemeral_for_deploy(
        provider="azure", policy=policy, metadata=metadata, manager=manager
    )
    assert hooked_manager is manager
    assert creds is not None
    assert metadata["ephemeral_account_id"] == creds.account_id
    assert metadata["ephemeral_provider"] == "azure"

    result = maybe_delete_ephemeral_after_task(manager=manager, metadata=metadata)
    assert result == {
        "provider": "azure",
        "account_id": creds.account_id,
        "deleted": True,
    }
    assert maybe_delete_ephemeral_after_task(manager=manager, metadata={}) is None


def test_ephemeral_manager_rejects_unknown_provider_and_corrupt_registry(tmp_path: Path) -> None:
    from general_ludd.account.ephemeral import EphemeralAccountManager

    registry = tmp_path / "broken.json"
    registry.write_text("not-json")
    manager = EphemeralAccountManager(backend=_Backend(), registry_path=str(registry))
    assert manager.list_accounts() == []
    with pytest.raises(ValueError, match="unsupported provider"):
        manager.create_account(provider="on-prem", budget=1.0)

