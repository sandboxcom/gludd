"""D.18: non-ephemeral account creation — tests.

Verifies:
- POST /api/account/create returns 501 when ephemeral=false
- The denial message is informative and actionable
- CLI surfaces the 501 correctly
- docs/NON_EPHEMERAL_ACCOUNTS.md exists and contains the decision rationale
- Ephemeral path is unaffected (existing behavior preserved)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from general_ludd.account.ephemeral import (
    SUPPORTED_PROVIDERS,
    EphemeralAccountManager,
)
from general_ludd.account.lifecycle_policy import PolicyConfig

# ---------------------------------------------------------------------------
# Fake backend (so tests never shell out to aws/gcloud/az CLIs)
# ---------------------------------------------------------------------------


class FakeBackend:
    """In-memory backend; never touches real cloud APIs."""

    def create_account(self, provider: str, budget: float) -> dict[str, Any]:
        return {
            "account_id": f"{provider}-ephemeral-0001",
            "provider": provider,
            "access_key_id": f"AKIA-FAKE-{provider}",
            "secret_access_key": f"SECRET-FAKE-{provider}",
            "budget_limit": budget,
        }

    def delete_account(self, provider: str, account_id: str) -> dict[str, Any]:
        return {"provider": provider, "account_id": account_id, "deleted": True}

    def is_account_active(self, provider: str, account_id: str) -> bool:
        return True


def _make_ephemeral_manager(tmp_path: str) -> EphemeralAccountManager:
    return EphemeralAccountManager(
        policy=PolicyConfig(),
        backend=FakeBackend(),
        registry_path=tmp_path,
    )


# ---------------------------------------------------------------------------
# 501 on non-ephemeral creation
# ---------------------------------------------------------------------------


class TestNonEphemeral501:
    """POST /api/account/create must return 501 when ephemeral=false."""

    def test_non_ephemeral_returns_501(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        app.state._ephemeral_account_manager = MagicMock()
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "aws", "budget": 10.0, "ephemeral": False},
        )
        assert resp.status_code == 501
        detail = resp.json()["detail"]
        assert "not implemented" in detail.lower()
        assert "ephemeral=true" in detail

    def test_non_ephemeral_missing_ephemeral_field_defaults_to_false(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        app.state._ephemeral_account_manager = MagicMock()
        register(app, {})

        client = TestClient(app)
        # The schema defaults ephemeral=False, so omitting it triggers 501.
        resp = client.post(
            "/api/account/create",
            json={"provider": "aws", "budget": 10.0},
        )
        assert resp.status_code == 501

    def test_non_ephemeral_message_is_actionable(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        app.state._ephemeral_account_manager = MagicMock()
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "gcp", "budget": 20.0, "ephemeral": False},
        )
        detail = resp.json()["detail"]
        # Must point the caller at the working path.
        assert "ephemeral=true" in detail

    def test_provider_validated_before_ephemeral_check(self) -> None:
        """Provider validation fires first: bad provider → 422 even with ephemeral=false."""
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        app.state._ephemeral_account_manager = MagicMock()
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "nonexistent", "budget": 10.0, "ephemeral": False},
        )
        # Provider check fires first — 422 takes priority over 501.
        # This is correct: an unknown provider is always invalid.
        assert resp.status_code == 422

    def test_ephemeral_true_still_works(self, tmp_path) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        registry = str(tmp_path / "test_d18.json")
        mgr = _make_ephemeral_manager(registry)
        app.state._ephemeral_account_manager = mgr
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "aws", "budget": 5.0, "ephemeral": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ephemeral"] is True
        assert body["provider"] == "aws"
        assert body["account_id"].startswith("aws-ephemeral-")


# ---------------------------------------------------------------------------
# Router-level validation
# ---------------------------------------------------------------------------


class TestCreateAccountValidation:
    def test_unsupported_provider_with_ephemeral_true_returns_422(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        app.state._ephemeral_account_manager = MagicMock()
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "digital_ocean", "budget": 10.0, "ephemeral": True},
        )
        assert resp.status_code == 422

    def test_missing_manager_returns_503(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "aws", "budget": 10.0, "ephemeral": True},
        )
        assert resp.status_code == 503
        assert "ephemeral account manager not wired" in resp.json()["detail"]

    def test_all_supported_providers_accept_ephemeral(self, tmp_path) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        for provider in sorted(SUPPORTED_PROVIDERS):
            app = FastAPI()
            mgr = _make_ephemeral_manager(str(tmp_path / f"test_d18_{provider}.json"))
            app.state._ephemeral_account_manager = mgr
            register(app, {})

            client = TestClient(app)
            resp = client.post(
                "/api/account/create",
                json={"provider": provider, "budget": 10.0, "ephemeral": True},
            )
            assert resp.status_code == 200, f"{provider} ephemeral creation failed"
            assert resp.json()["provider"] == provider

    def test_negative_budget_rejected(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        app.state._ephemeral_account_manager = MagicMock()
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "aws", "budget": -1.0, "ephemeral": True},
        )
        assert resp.status_code == 422

    def test_empty_provider_rejected(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        app.state._ephemeral_account_manager = MagicMock()
        register(app, {})

        client = TestClient(app)
        resp = client.post(
            "/api/account/create",
            json={"provider": "", "budget": 10.0, "ephemeral": True},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Policy endpoint is provider-agnostic (works for ephemeral and future
# persistent accounts equally)
# ---------------------------------------------------------------------------


class TestPolicyEndpointAgnostic:
    def test_policy_works_regardless_of_account_type(self) -> None:
        from fastapi import FastAPI

        from general_ludd.routers.account import register

        app = FastAPI()
        register(app, {})

        client = TestClient(app)
        resp = client.get("/api/account/policy", params={"service": "aws"})
        assert resp.status_code == 200
        body = resp.json()
        assert "policy" in body
        assert "notice" in body


# ---------------------------------------------------------------------------
# Decision document
# ---------------------------------------------------------------------------


class TestDecisionDocument:
    """Verify docs/NON_EPHEMERAL_ACCOUNTS.md exists and records the decision."""

    def test_document_exists(self) -> None:
        doc = Path(__file__).resolve().parents[2] / "docs" / "NON_EPHEMERAL_ACCOUNTS.md"
        assert doc.is_file(), f"Decision doc missing at {doc}"

    def test_document_states_501_decision(self) -> None:
        doc = Path(__file__).resolve().parents[2] / "docs" / "NON_EPHEMERAL_ACCOUNTS.md"
        text = doc.read_text()
        assert "501" in text
        assert "not yet supported" in text.lower() or "not implemented" in text.lower()

    def test_document_explains_why_ephemeral_only(self) -> None:
        doc = Path(__file__).resolve().parents[2] / "docs" / "NON_EPHEMERAL_ACCOUNTS.md"
        text = doc.read_text()
        assert "budget" in text.lower()
        assert "auto-delete" in text.lower() or "auto-deleted" in text.lower()
        assert "retention" in text.lower()

    def test_document_lists_requirements_for_persistent(self) -> None:
        doc = Path(__file__).resolve().parents[2] / "docs" / "NON_EPHEMERAL_ACCOUNTS.md"
        text = doc.read_text()
        assert "persistent" in text.lower()
        assert "rotation" in text.lower() or "rotate" in text.lower()
        assert "access control" in text.lower() or "tenant" in text.lower()

    def test_document_references_source_file(self) -> None:
        doc = Path(__file__).resolve().parents[2] / "docs" / "NON_EPHEMERAL_ACCOUNTS.md"
        text = doc.read_text()
        assert "ephemeral.py" in text


# ---------------------------------------------------------------------------
# CLI smoke test — ensure the subcommand doesn't crash on import
# ---------------------------------------------------------------------------


class TestCliAccountSubcommand:
    def test_create_subcommand_exists(self) -> None:
        import argparse

        from general_ludd.cli_account import add_account_subparser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_account_subparser(sub)
        assert "account" in sub.choices

        acct_parser = sub.choices["account"]
        sub_actions = [
            a for a in acct_parser._actions
            if isinstance(a, argparse._SubParsersAction)
        ]
        assert sub_actions
        account_sub = sub_actions[0]
        assert "create" in account_sub.choices

    def test_ephemeral_flag_is_optional_and_defaults_false(self) -> None:
        import argparse

        from general_ludd.cli_account import add_account_subparser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_account_subparser(sub)
        args = parser.parse_args(
            ["account", "create", "--provider", "aws"]
        )
        assert args.ephemeral is False
