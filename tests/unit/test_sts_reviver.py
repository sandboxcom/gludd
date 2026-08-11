"""Tests for TokenReviver — agent rehydration secret-id rotation (sts/reviver.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from general_ludd.sts.reviver import TokenRevivalError, TokenReviver


@dataclass
class _Record:
    agent_id: str
    token_id: str
    role_name: str
    role_id: str
    parent_agent_id: str = "root"
    revoked_at: datetime | None = None


class _FakeSecretsManager:
    def __init__(
        self,
        secret_id_return: str = "fresh-secret-abc",
        fail_for: set[str] | None = None,
    ) -> None:
        self._secret_id_return = secret_id_return
        self._fail_for = fail_for or set()
        self.rotations: list[str] = []

    def rotate_approle_secret_id(self, role_name: str) -> str:
        if role_name in self._fail_for:
            raise RuntimeError(f"Vault error rotating {role_name}")
        self.rotations.append(role_name)
        return self._secret_id_return


class _FakeStore:
    def __init__(self, records: dict[str, _Record] | None = None) -> None:
        self._records = records or {}
        self.hydration_increments: list[str] = []

    async def get(self, agent_id: str) -> _Record | None:
        return self._records.get(agent_id)

    async def increment_hydration(self, agent_id: str) -> None:
        self.hydration_increments.append(agent_id)


class _FakeAudit:
    def __init__(self) -> None:
        self.revive_events: list[dict[str, str]] = []

    async def record_revive(
        self,
        token_id: str,
        agent_id: str,
        parent_agent_id: str,
    ) -> None:
        self.revive_events.append(
            {
                "token_id": token_id,
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
            }
        )


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------
class TestReviveSuccess:
    @pytest.mark.asyncio
    async def test_returns_fresh_creds_with_same_role_id(self) -> None:
        record = _Record("a1", "t1", "my-role", "rid-1")
        store = _FakeStore({"a1": record})
        secrets = _FakeSecretsManager(secret_id_return="new-sid")
        reviver = TokenReviver(secrets, store)

        creds = await reviver.revive("a1")

        assert creds.role_id == "rid-1"
        assert creds.secret_id == "new-sid"

    @pytest.mark.asyncio
    async def test_calls_rotate_with_record_role_name(self) -> None:
        record = _Record("a1", "t1", "role-x", "rid-1")
        store = _FakeStore({"a1": record})
        secrets = _FakeSecretsManager()
        reviver = TokenReviver(secrets, store)

        await reviver.revive("a1")

        assert secrets.rotations == ["role-x"]

    @pytest.mark.asyncio
    async def test_increments_hydration_on_success(self) -> None:
        record = _Record("a1", "t1", "r", "rid-1")
        store = _FakeStore({"a1": record})
        reviver = TokenReviver(_FakeSecretsManager(), store)

        await reviver.revive("a1")

        assert store.hydration_increments == ["a1"]

    @pytest.mark.asyncio
    async def test_emits_audit_event_when_pipeline_provided(self) -> None:
        record = _Record("a1", "t1", "r", "rid-1", parent_agent_id="pa")
        store = _FakeStore({"a1": record})
        audit = _FakeAudit()
        reviver = TokenReviver(_FakeSecretsManager(), store, audit_pipeline=audit)

        await reviver.revive("a1")

        assert audit.revive_events == [{"token_id": "t1", "agent_id": "a1", "parent_agent_id": "pa"}]

    @pytest.mark.asyncio
    async def test_no_audit_event_when_pipeline_is_none(self) -> None:
        record = _Record("a1", "t1", "r", "rid-1")
        store = _FakeStore({"a1": record})
        reviver = TokenReviver(_FakeSecretsManager(), store, audit_pipeline=None)

        await reviver.revive("a1")


# ---------------------------------------------------------------------------
# Error paths — TokenRevivalError
# ---------------------------------------------------------------------------
class TestReviveErrors:
    @pytest.mark.asyncio
    async def test_raises_when_no_record_found(self) -> None:
        reviver = TokenReviver(_FakeSecretsManager(), _FakeStore())

        with pytest.raises(TokenRevivalError, match="No token record found"):
            await reviver.revive("missing-agent")

    @pytest.mark.asyncio
    async def test_raises_when_token_already_revoked(self) -> None:
        record = _Record("a1", "t1", "r", "rid-1", revoked_at=datetime.now(UTC))
        store = _FakeStore({"a1": record})
        reviver = TokenReviver(_FakeSecretsManager(), store)

        with pytest.raises(TokenRevivalError, match="was revoked at"):
            await reviver.revive("a1")

    @pytest.mark.asyncio
    async def test_raises_when_secrets_rotation_fails(self) -> None:
        record = _Record("a1", "t1", "bad-role", "rid-1")
        store = _FakeStore({"a1": record})
        secrets = _FakeSecretsManager(fail_for={"bad-role"})

        reviver = TokenReviver(secrets, store)

        with pytest.raises(TokenRevivalError, match="bad-role"):
            await reviver.revive("a1")

    @pytest.mark.asyncio
    async def test_error_message_includes_agent_id(self) -> None:
        record = _Record("agent-42", "t1", "bad-role", "rid-1")
        store = _FakeStore({"agent-42": record})
        secrets = _FakeSecretsManager(fail_for={"bad-role"})

        reviver = TokenReviver(secrets, store)

        with pytest.raises(TokenRevivalError, match="agent-42"):
            await reviver.revive("agent-42")

    @pytest.mark.asyncio
    async def test_no_hydration_increment_on_error(self) -> None:
        record = _Record("a1", "t1", "bad-role", "rid-1")
        store = _FakeStore({"a1": record})
        secrets = _FakeSecretsManager(fail_for={"bad-role"})

        reviver = TokenReviver(secrets, store)

        with pytest.raises(TokenRevivalError):
            await reviver.revive("a1")

        assert store.hydration_increments == []

    @pytest.mark.asyncio
    async def test_revive_does_not_emit_audit_on_rotation_failure(self) -> None:
        record = _Record("a1", "t1", "bad-role", "rid-1")
        store = _FakeStore({"a1": record})
        secrets = _FakeSecretsManager(fail_for={"bad-role"})
        audit = _FakeAudit()

        reviver = TokenReviver(secrets, store, audit_pipeline=audit)

        with pytest.raises(TokenRevivalError):
            await reviver.revive("a1")

        assert audit.revive_events == []


# ---------------------------------------------------------------------------
# _sanitize helper
# ---------------------------------------------------------------------------
class TestSanitize:
    def test_returns_exception_type_name(self) -> None:
        exc = ValueError("sensitive secret data here")
        assert TokenReviver._sanitize(exc) == "ValueError"

    def test_returns_name_for_custom_exception(self) -> None:
        class CustomDBError(Exception):
            pass

        assert TokenReviver._sanitize(CustomDBError("details")) == "CustomDBError"

    def test_works_on_base_exception(self) -> None:
        assert TokenReviver._sanitize(Exception("generic")) == "Exception"
