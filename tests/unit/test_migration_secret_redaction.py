"""Regression: secret migration must not log raw exceptions (they can embed values).

On a backend write failure, migrate_profile_secrets logs only type(exc).__name__,
never str(exc) — a vault/backend error can quote the rejected secret value.
"""
from __future__ import annotations

import logging
from typing import Any

import general_ludd.secrets.migration as migration_module
from general_ludd.secrets.migration import migrate_profile_secrets


class _FakeManager:
    """Minimal manager matching the write_secret/register_alias interface."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def write_secret(self, vault_path: str, data: dict[str, Any]) -> None:
        raise self._exc

    def register_alias(self, alias: Any) -> None:  # pragma: no cover - not reached
        pass


class _PatchedEnvResolver:
    def resolve(self, alias_name: str) -> str | None:
        return "sk-SECRET123" if alias_name == "MY_API_KEY" else None


def test_write_failure_does_not_leak_secret(monkeypatch, caplog) -> None:
    # The backend error text embeds the secret value (a real vault failure mode).
    mgr = _FakeManager(RuntimeError("rejected value sk-SECRET123"))
    monkeypatch.setattr(migration_module, "EnvSecretsManager", _PatchedEnvResolver)
    profiles = [{"model_profile_id": "m1", "credential_alias": "MY_API_KEY"}]

    with caplog.at_level(logging.WARNING):
        result = migrate_profile_secrets(mgr, profiles)

    assert "MY_API_KEY" in result["skipped"]
    assert result["migrated"] == 0
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "sk-SECRET123" not in blob
    assert "rejected value" not in blob
    # The exception TYPE is still logged for debuggability.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("RuntimeError" in r.getMessage() for r in warnings)
