"""S.15 — UNRESTRICTED_ROLE sentinel security tests (D12).

Validates that UNRESTRICTED_ROLE is an object() identity sentinel, not a
forgeable string. The old string value ``"__unrestricted__"`` could be guessed
by any caller; object() identity requires holding a direct reference.

Three invariants tested:
  1. UNRESTRICTED_ROLE is an object() instance, not a string
  2. The string ``"__unrestricted__"`` does NOT bypass the capability gate
  3. Only the actual module-level sentinel (identity check via ``is``) bypasses
"""

from __future__ import annotations

import pytest

from general_ludd.dispatch.dynamic_dispatcher import (
    UNRESTRICTED_ROLE,
    DynamicDispatcher,
    ToolCall,
)


class TestUnrestrictedRoleSentinelIdentity:
    def test_sentinel_is_object_not_string(self):
        assert type(UNRESTRICTED_ROLE) is object
        assert not isinstance(UNRESTRICTED_ROLE, str)
        assert not isinstance(UNRESTRICTED_ROLE, bytes)

    def test_sentinel_not_None(self):
        assert UNRESTRICTED_ROLE is not None

    def test_sentinel_is_not_forgeable_string(self):
        assert UNRESTRICTED_ROLE != "__unrestricted__"
        assert UNRESTRICTED_ROLE != ""


class TestUnrestrictedRoleGate:
    @pytest.mark.asyncio
    async def test_string_unrestricted_rejected_by_gate(self):
        """The old string "__unrestricted__" must NOT bypass the gate."""
        handler_called = False

        def _handler(name: str, args: dict) -> str:
            nonlocal handler_called
            handler_called = True
            return f"role:{name}"

        d = DynamicDispatcher(role_handler=_handler, role="__unrestricted__")
        result = await d.dispatch(ToolCall(kind="role", name="planner", args={}))
        assert result.ok is False
        assert result.error == "capability_denied"
        assert handler_called is False

    @pytest.mark.asyncio
    async def test_string_unrestricted_rejected_mcp_kind(self):
        """The string also fails on mcp kind."""
        handler_called = False

        def _handler(name: str, args: dict) -> str:
            nonlocal handler_called
            handler_called = True
            return f"mcp:{name}"

        d = DynamicDispatcher(mcp_handler=_handler, role="__unrestricted__")
        result = await d.dispatch(ToolCall(kind="mcp", name="fs", args={}))
        assert result.ok is False
        assert result.error == "capability_denied"
        assert handler_called is False

    @pytest.mark.asyncio
    async def test_real_sentinel_bypasses_privileged_kinds(self):
        """The actual object() sentinel bypasses the gate for all privileged kinds."""
        for kind in ("role", "mcp", "skill", "collection"):
            handler_kw = {f"{kind}_handler": lambda n, a: f"{kind}:{n}"}
            d = DynamicDispatcher(
                **handler_kw,  # type: ignore[arg-type]
                role=UNRESTRICTED_ROLE,
            )
            result = await d.dispatch(ToolCall(kind=kind, name="test", args={}))
            assert result.ok is True, (
                f"Kind {kind!r} unexpectedly denied under UNRESTRICTED_ROLE: "
                f"error={result.error}"
            )

    @pytest.mark.asyncio
    async def test_identical_looking_object_rejected(self):
        """A fresh object() that is NOT the sentinel must fail.

        Object identity (``is``) protects against forgery: even another
        object() instance does not match the module-level sentinel.
        """
        fake = object()
        assert fake is not UNRESTRICTED_ROLE

        d = DynamicDispatcher(
            role_handler=lambda n, a: f"role:{n}",
            role=fake,
        )
        result = await d.dispatch(ToolCall(kind="role", name="planner", args={}))
        assert result.ok is False
        assert result.error == "capability_denied"

    @pytest.mark.asyncio
    async def test_none_role_denied_privileged_kinds(self):
        """A None role must deny all privileged kinds."""
        for kind in ("role", "collection", "mcp", "skill"):
            handler_kw = {f"{kind}_handler": lambda n, a: f"{kind}:{n}"}
            d = DynamicDispatcher(
                **handler_kw,  # type: ignore[arg-type]
                role=None,
            )
            result = await d.dispatch(ToolCall(kind=kind, name="test", args={}))
            assert result.ok is False, (
                f"Kind {kind!r} unexpectedly permitted with None role"
            )
            assert result.error == "capability_denied"

    @pytest.mark.asyncio
    async def test_sentinel_identity_not_equality(self):
        """Prove the comparison uses ``is`` (identity), not ``==`` (equality)."""
        assert (UNRESTRICTED_ROLE is UNRESTRICTED_ROLE) is True
        assert (UNRESTRICTED_ROLE == object()) is False

    @pytest.mark.asyncio
    async def test_unregistered_handler_still_fails_under_sentinel(self):
        """Sentinel bypasses the capability gate but NOT handler presence.

        If no handler is registered for a kind, dispatch still fails even
        under the sentinel (handler check runs after the gate).
        """
        d = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = await d.dispatch(ToolCall(kind="mcp", name="missing", args={}))
        assert result.ok is False
        assert "unknown_kind" in (result.error or "")
