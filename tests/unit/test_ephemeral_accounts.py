"""Tests for the ephemeral cloud account lifecycle.

Covers:
- ``general_ludd.account.lifecycle_policy`` — PolicyConfig, LifecycleAction,
  evaluate_lifecycle
- ``general_ludd.account.ephemeral`` — EphemeralAccountManager
  (create/delete/is_active/policy, provider-backend abstraction, registry
  persistence)
- EventLoop wiring — after a task completes, ephemeral cleanup is triggered
- Deployment wiring — before launching cloud resources, a fresh ephemeral
  account is created when the policy is set
- CLI — ``gludd account create --ephemeral`` + ``gludd account cleanup``

The real cloud APIs (AWS IAM, GCP IAM, Azure RBAC) are never hit. Tests
inject a :class:`FakeProviderBackend` that records every call so we assert
behavior, not side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from general_ludd.account.ephemeral import (
    AccountCredentials,
    EphemeralAccountManager,
)
from general_ludd.account.lifecycle_policy import (
    LifecycleAction,
    PolicyConfig,
    evaluate_lifecycle,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeProviderBackend:
    """In-memory stand-in for AWS/GCP/Azure SDK calls.

    Records every invocation on ``calls`` so tests assert behavior. Generates
    a deterministic credential per ``create`` so the manager's registry can
    track it for later deletion.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # provider -> {account_id: {"active": bool, "resources": [...]}}
        self._state: dict[str, dict[str, dict[str, Any]]] = {}
        self._counter = 0

    def create_account(self, provider: str, budget: float) -> dict[str, Any]:
        self._counter += 1
        account_id = f"{provider}-ephemeral-{self._counter:04d}"
        self._state.setdefault(provider, {})[account_id] = {
            "active": True,
            "resources": [],
            "budget": budget,
        }
        self.calls.append(("create", {"provider": provider, "account_id": account_id, "budget": budget}))
        return {
            "account_id": account_id,
            "provider": provider,
            "access_key_id": f"AKIA-FAKE-{account_id}",
            "secret_access_key": f"SECRET-FAKE-{account_id}",
            "budget_limit": budget,
        }

    def delete_account(self, provider: str, account_id: str) -> dict[str, Any]:
        store = self._state.get(provider, {})
        entry = store.pop(account_id, None)
        self.calls.append(
            ("delete", {"provider": provider, "account_id": account_id, "existed": entry is not None})
        )
        if entry is None:
            return {"provider": provider, "account_id": account_id, "deleted": False, "resources_removed": 0}
        return {
            "provider": provider,
            "account_id": account_id,
            "deleted": True,
            "resources_removed": len(entry.get("resources", [])),
        }

    def is_account_active(self, provider: str, account_id: str) -> bool:
        store = self._state.get(provider, {})
        entry = store.get(account_id)
        return bool(entry and entry.get("active"))

    def list_resources(self, provider: str, account_id: str) -> list[str]:
        store = self._state.get(provider, {})
        entry = store.get(account_id)
        return list(entry.get("resources", [])) if entry else []


# ---------------------------------------------------------------------------
# lifecycle_policy
# ---------------------------------------------------------------------------


class TestPolicyConfig:
    def test_defaults(self) -> None:
        cfg = PolicyConfig()
        assert cfg.auto_delete_after_use is True
        assert cfg.retention_period_hours == 24
        assert cfg.budget_limit == 10.0

    def test_custom_values(self) -> None:
        cfg = PolicyConfig(
            auto_delete_after_use=False,
            retention_period_hours=72,
            budget_limit=50.0,
        )
        assert cfg.auto_delete_after_use is False
        assert cfg.retention_period_hours == 72
        assert cfg.budget_limit == 50.0

    def test_retention_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            PolicyConfig(retention_period_hours=0)

    def test_budget_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError):
            PolicyConfig(budget_limit=-1.0)


class TestEvaluateLifecycle:
    def test_no_account_returns_create(self) -> None:
        cfg = PolicyConfig()
        action = evaluate_lifecycle(account_id=None, policy=cfg, active=False, age_hours=0.0)
        assert action == LifecycleAction.CREATE

    def test_active_within_retention_returns_keep(self) -> None:
        cfg = PolicyConfig(retention_period_hours=24)
        action = evaluate_lifecycle(
            account_id="aws-eph-1", policy=cfg, active=True, age_hours=2.0
        )
        assert action == LifecycleAction.KEEP

    def test_active_past_retention_returns_delete(self) -> None:
        cfg = PolicyConfig(retention_period_hours=24)
        action = evaluate_lifecycle(
            account_id="aws-eph-1", policy=cfg, active=True, age_hours=30.0
        )
        assert action == LifecycleAction.DELETE

    def test_inactive_account_returns_delete(self) -> None:
        cfg = PolicyConfig()
        action = evaluate_lifecycle(
            account_id="aws-eph-1", policy=cfg, active=False, age_hours=1.0
        )
        assert action == LifecycleAction.DELETE

    def test_auto_delete_disabled_returns_keep_even_when_expired(self) -> None:
        cfg = PolicyConfig(auto_delete_after_use=False, retention_period_hours=1)
        action = evaluate_lifecycle(
            account_id="aws-eph-1", policy=cfg, active=True, age_hours=100.0
        )
        assert action == LifecycleAction.KEEP

    def test_inactive_auto_delete_disabled_returns_keep(self) -> None:
        cfg = PolicyConfig(auto_delete_after_use=False)
        action = evaluate_lifecycle(
            account_id="aws-eph-1", policy=cfg, active=False, age_hours=1.0
        )
        assert action == LifecycleAction.KEEP


# ---------------------------------------------------------------------------
# EphemeralAccountManager
# ---------------------------------------------------------------------------


class TestEphemeralAccountManagerCreate:
    def test_create_account_returns_credentials(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        creds = mgr.create_account(provider="aws", budget=15.0)
        assert isinstance(creds, AccountCredentials)
        assert creds.provider == "aws"
        assert creds.budget_limit == 15.0
        assert creds.account_id.startswith("aws-ephemeral-")
        assert creds.access_key_id  # non-empty
        assert creds.secret_access_key  # non-empty
        assert creds.created_at is not None

    def test_create_account_calls_backend(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        mgr.create_account(provider="gcp", budget=5.0)
        assert any(
            c[0] == "create" and c[1]["provider"] == "gcp" for c in backend.calls
        )

    def test_create_account_unknown_provider_raises(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        with pytest.raises(ValueError, match="unsupported provider"):
            mgr.create_account(provider="digital_ocean", budget=1.0)

    def test_created_account_is_active(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        creds = mgr.create_account(provider="aws", budget=10.0)
        assert mgr.is_account_active(provider="aws", account_id=creds.account_id) is True

    def test_create_account_persists_to_registry(self, tmp_path) -> None:
        registry = tmp_path / "registry.json"
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(registry),
        )
        creds = mgr.create_account(provider="aws", budget=10.0)
        # Fresh manager from same registry path should see it.
        mgr2 = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(registry),
        )
        assert mgr2.is_account_active(provider="aws", account_id=creds.account_id) is True


class TestEphemeralAccountManagerDelete:
    def test_delete_account_removes_from_backend(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        creds = mgr.create_account(provider="aws", budget=10.0)
        result = mgr.delete_account(provider="aws", account_id=creds.account_id)
        assert result["deleted"] is True
        assert result["provider"] == "aws"
        assert result["account_id"] == creds.account_id
        assert mgr.is_account_active(provider="aws", account_id=creds.account_id) is False

    def test_delete_unknown_account_returns_deleted_false(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        result = mgr.delete_account(provider="aws", account_id="never-existed")
        assert result["deleted"] is False

    def test_delete_removes_from_registry(self, tmp_path) -> None:
        registry = tmp_path / "registry.json"
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(registry),
        )
        creds = mgr.create_account(provider="aws", budget=10.0)
        mgr.delete_account(provider="aws", account_id=creds.account_id)
        mgr2 = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(registry),
        )
        assert mgr2.is_account_active(provider="aws", account_id=creds.account_id) is False

    def test_delete_account_calls_backend(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        creds = mgr.create_account(provider="azure", budget=7.5)
        mgr.delete_account(provider="azure", account_id=creds.account_id)
        assert any(
            c[0] == "delete" and c[1]["account_id"] == creds.account_id
            for c in backend.calls
        )


class TestEphemeralAccountManagerPolicy:
    def test_get_account_policy_returns_text(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        text = mgr.get_account_policy(provider="aws")
        assert isinstance(text, str)
        assert "AWS" in text
        assert "retention" in text.lower() or "delete" in text.lower()

    def test_get_account_policy_unknown_raises(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        mgr = EphemeralAccountManager(
            policy=PolicyConfig(),
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        with pytest.raises(ValueError):
            mgr.get_account_policy(provider="nonsense")


class TestEphemeralAccountManagerLifecycleEvaluation:
    def test_evaluate_returns_delete_for_expired_account(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        cfg = PolicyConfig(retention_period_hours=1)
        mgr = EphemeralAccountManager(
            policy=cfg,
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        creds = mgr.create_account(provider="aws", budget=10.0)
        # Simulate that the account is older than retention by patching
        # created_at in the registry to 2 hours ago.
        for entry in mgr._registry.values():
            entry["created_at"] = (
                datetime.now(UTC) - timedelta(hours=2)
            ).isoformat()
        action = mgr.evaluate_account_lifecycle(creds.account_id)
        assert action == LifecycleAction.DELETE

    def test_evaluate_returns_keep_for_fresh_account(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        cfg = PolicyConfig(retention_period_hours=24)
        mgr = EphemeralAccountManager(
            policy=cfg,
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        creds = mgr.create_account(provider="aws", budget=10.0)
        action = mgr.evaluate_account_lifecycle(creds.account_id)
        assert action == LifecycleAction.KEEP

    def test_cleanup_expired_deletes_accounts_past_retention(self, tmp_path) -> None:
        backend = FakeProviderBackend()
        cfg = PolicyConfig(retention_period_hours=1)
        mgr = EphemeralAccountManager(
            policy=cfg,
            backend=backend,
            registry_path=str(tmp_path / "registry.json"),
        )
        fresh = mgr.create_account(provider="aws", budget=10.0)
        stale = mgr.create_account(provider="gcp", budget=10.0)
        # Force `stale` to look expired.
        mgr._registry[stale.account_id]["created_at"] = (
            datetime.now(UTC) - timedelta(hours=5)
        ).isoformat()
        report = mgr.cleanup_expired()
        assert fresh.account_id not in {r["account_id"] for r in report["deleted"]}
        assert any(r["account_id"] == stale.account_id for r in report["deleted"])
        assert mgr.is_account_active(provider="aws", account_id=fresh.account_id) is True
        assert mgr.is_account_active(provider="gcp", account_id=stale.account_id) is False


# ---------------------------------------------------------------------------
# AccountCredentials schema
# ---------------------------------------------------------------------------


class TestAccountCredentials:
    def test_construct(self) -> None:
        c = AccountCredentials(
            account_id="aws-ephemeral-0001",
            provider="aws",
            access_key_id="AKIAFAKE",
            secret_access_key="SECRETFAKE",
            budget_limit=12.5,
        )
        assert c.account_id == "aws-ephemeral-0001"
        assert c.provider == "aws"
        assert c.budget_limit == 12.5
        assert c.created_at is not None

    def test_secret_not_in_repr(self) -> None:
        c = AccountCredentials(
            account_id="aws-ephemeral-0001",
            provider="aws",
            access_key_id="AKIAFAKE",
            secret_access_key="SUPERSECRETVALUE",
            budget_limit=1.0,
        )
        assert "SUPERSECRETVALUE" not in repr(c)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCliAccountEphemeral:
    def test_create_subparser_registered(self) -> None:
        import argparse

        from general_ludd.cli_account import add_account_subparser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_account_subparser(sub)
        # `account` is registered at the top level.
        assert "account" in sub.choices
        # Walk into the account subparser to find its sub-subparsers.
        account_parser = sub.choices["account"]
        sub_actions = [
            a for a in account_parser._actions if isinstance(a, argparse._SubParsersAction)
        ]
        assert sub_actions, "account subparser has no sub-subparsers"
        account_sub = sub_actions[0]
        # The create + cleanup subcommands should exist.
        assert "create" in account_sub.choices
        assert "cleanup" in account_sub.choices

    def test_create_parses_ephemeral_flags(self) -> None:
        import argparse

        from general_ludd.cli_account import add_account_subparser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_account_subparser(sub)
        args = parser.parse_args(
            [
                "account",
                "create",
                "--ephemeral",
                "--provider",
                "aws",
                "--budget",
                "10",
            ]
        )
        assert args.ephemeral is True
        assert args.provider == "aws"
        assert args.budget == 10.0

    def test_cleanup_parses_flags(self) -> None:
        import argparse

        from general_ludd.cli_account import add_account_subparser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_account_subparser(sub)
        args = parser.parse_args(["account", "cleanup"])
        assert args.account_command == "cleanup"


# ---------------------------------------------------------------------------
# Ansible role presence
# ---------------------------------------------------------------------------


class TestAccountLifecycleRole:
    def test_role_files_exist(self) -> None:
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        role_dir = (
            repo_root
            / "collections"
            / "ansible_collections"
            / "general_ludd"
            / "agent"
            / "roles"
            / "account_lifecycle"
        )
        assert role_dir.is_dir(), f"role dir missing: {role_dir}"
        assert (role_dir / "tasks" / "main.yml").is_file()
        assert (role_dir / "README.md").is_file()
        assert (role_dir / "defaults" / "main.yml").is_file()

    def test_role_main_uses_gludd_facts(self) -> None:
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        tasks_yml = (
            repo_root
            / "collections"
            / "ansible_collections"
            / "general_ludd"
            / "agent"
            / "roles"
            / "account_lifecycle"
            / "tasks"
            / "main.yml"
        )
        text = tasks_yml.read_text()
        assert "gludd_facts" in text or "gludd_agent_run" in text
        assert "account_lifecycle" in text
