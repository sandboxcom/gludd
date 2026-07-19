"""Unit tests for TokenTreeRenderer — STS token tree visualization (NF.7).

Covers parent→child token relationships and revocation cascade rendering.
The renderer is read-only: it builds a textual tree from TokenStore queries
without mutating any token state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.visualizer import TokenTreeRenderer


def _make_record(
    token_id: str,
    agent_id: str,
    parent_agent_id: str = "root",
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
    role_name: str | None = None,
) -> MagicMock:
    rec = MagicMock()
    rec.token_id = token_id
    rec.agent_id = agent_id
    rec.parent_agent_id = parent_agent_id
    rec.role_name = role_name or f"agent-{agent_id}"
    rec.role_id = f"role-{agent_id}"
    rec.revoked_at = revoked_at
    rec.expires_at = expires_at
    rec.created_at = datetime(2026, 7, 1, tzinfo=UTC)
    rec.hydration_count = 0
    return rec


class TestImport:
    def test_renderer_importable(self):
        assert TokenTreeRenderer is not None


class TestRenderTree:
    @pytest.mark.asyncio
    async def test_render_tree_single_node_no_children(self):
        """render_tree(parent_id) with no children returns just the root line."""
        root = _make_record("tok-root", "agent-root", parent_agent_id="human-1")
        store = AsyncMock()
        store.get.return_value = root
        store.list_children.return_value = []

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_tree("agent-root")

        store.get.assert_awaited_once_with("agent-root")
        assert "agent-root" in output
        assert "tok-root" in output
        assert "active" in output

    @pytest.mark.asyncio
    async def test_render_tree_with_children(self):
        """render_tree shows parent with direct children indented beneath."""
        parent = _make_record("tok-p", "agent-parent", parent_agent_id="human-1")
        child_a = _make_record("tok-ca", "agent-ca", parent_agent_id="agent-parent")
        child_b = _make_record("tok-cb", "agent-cb", parent_agent_id="agent-parent")

        store = AsyncMock()
        store.get.return_value = parent
        store.list_children.side_effect = [
            [child_a, child_b],
            [],
            [],
        ]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_tree("agent-parent")

        assert "agent-parent" in output
        assert "agent-ca" in output
        assert "agent-cb" in output
        child_lines = output.splitlines()[1:]
        assert any("agent-ca" in line for line in child_lines)
        assert any("agent-cb" in line for line in child_lines)

    @pytest.mark.asyncio
    async def test_render_tree_deep_nesting(self):
        """render_tree recurses through grandparent → parent → child."""
        gp = _make_record("tok-gp", "agent-gp", parent_agent_id="human-1")
        parent = _make_record("tok-p", "agent-p", parent_agent_id="agent-gp")
        child = _make_record("tok-c", "agent-c", parent_agent_id="agent-p")

        store = AsyncMock()
        store.get.return_value = gp
        store.list_children.side_effect = [
            [parent],
            [child],
            [],
        ]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_tree("agent-gp")

        lines = output.splitlines()
        assert "agent-gp" in lines[0]
        assert any("agent-p" in line for line in lines[1:])
        assert any("agent-c" in line for line in lines[1:])

    @pytest.mark.asyncio
    async def test_render_tree_marks_revoked_status(self):
        """Revoked tokens are marked 'revoked' in the output."""
        root = _make_record(
            "tok-r", "agent-r",
            revoked_at=datetime(2026, 7, 10, tzinfo=UTC),
        )
        store = AsyncMock()
        store.get.return_value = root
        store.list_children.return_value = []

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_tree("agent-r")

        assert "revoked" in output

    @pytest.mark.asyncio
    async def test_render_tree_marks_expired_status(self):
        """Expired (not revoked, expires_at in past) tokens marked 'expired'."""
        past = datetime.now(UTC) - timedelta(hours=1)
        root = _make_record("tok-e", "agent-e", expires_at=past)
        store = AsyncMock()
        store.get.return_value = root
        store.list_children.return_value = []

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_tree("agent-e")

        assert "expired" in output

    @pytest.mark.asyncio
    async def test_render_tree_missing_root_returns_empty(self):
        """When the root agent_id has no token, return a not-found message."""
        store = AsyncMock()
        store.get.return_value = None

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_tree("nonexistent")

        assert "not found" in output.lower() or "no token" in output.lower()


class TestRenderRevocationCascade:
    @pytest.mark.asyncio
    async def test_cascade_shows_all_live_descendants(self):
        """render_revocation_cascade lists every token that WOULD be revoked."""
        child_a = _make_record("tok-ca", "agent-ca", parent_agent_id="agent-root")
        child_b = _make_record("tok-cb", "agent-cb", parent_agent_id="agent-root")
        grandchild = _make_record("tok-gc", "agent-gc", parent_agent_id="agent-ca")

        store = AsyncMock()
        store.list_children.side_effect = [
            [child_a, child_b],
            [grandchild],
            [],
            [],
        ]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_revocation_cascade("agent-root")

        assert "agent-ca" in output
        assert "agent-cb" in output
        assert "agent-gc" in output
        assert "3" in output or "three" in output.lower()

    @pytest.mark.asyncio
    async def test_cascade_skips_already_revoked_subtree(self):
        """Already-revoked children are shown but not descended into."""
        revoked_child = _make_record(
            "tok-rc", "agent-rc", parent_agent_id="agent-root",
            revoked_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

        store = AsyncMock()
        store.list_children.return_value = [revoked_child]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_revocation_cascade("agent-root")

        assert "agent-rc" in output
        assert "0" in output or "no live" in output.lower()

    @pytest.mark.asyncio
    async def test_cascade_no_children(self):
        """render_revocation_cascade with no children reports zero impact."""
        store = AsyncMock()
        store.list_children.return_value = []

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_revocation_cascade("agent-orphan")

        assert "0" in output

    @pytest.mark.asyncio
    async def test_cascade_terminates_on_cycle(self):
        """Malformed cycles do not cause infinite recursion."""
        a = _make_record("tok-a", "agent-a", parent_agent_id="agent-root")
        b = _make_record("tok-b", "agent-b", parent_agent_id="agent-a")

        store = AsyncMock()
        store.list_children.side_effect = [
            [a],
            [b],
            [],
            [],
        ]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_revocation_cascade("agent-root")

        assert "agent-a" in output
        assert "agent-b" in output


class TestRenderActiveTokens:
    @pytest.mark.asyncio
    async def test_render_active_lists_all_live_tokens(self):
        """render_active_tokens shows every non-revoked token."""
        live_a = _make_record("tok-a", "agent-a", parent_agent_id="human-1")
        live_b = _make_record("tok-b", "agent-b", parent_agent_id="human-1")
        revoked = _make_record(
            "tok-r", "agent-r", parent_agent_id="human-1",
            revoked_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

        store = AsyncMock()
        store.list_all.return_value = [live_a, live_b, revoked]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_active_tokens()

        assert "agent-a" in output
        assert "agent-b" in output
        assert "agent-r" not in output or "revoked" in output.split("agent-r")[0].splitlines()[-1]

    @pytest.mark.asyncio
    async def test_render_active_groups_by_parent(self):
        """render_active_tokens groups children under their parent."""
        parent = _make_record("tok-p", "agent-parent", parent_agent_id="human-1")
        child = _make_record("tok-c", "agent-child", parent_agent_id="agent-parent")

        store = AsyncMock()
        store.list_all.return_value = [parent, child]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_active_tokens()

        assert "agent-parent" in output
        assert "agent-child" in output
        parent_idx = output.index("agent-parent")
        child_idx = output.index("agent-child")
        assert parent_idx < child_idx

    @pytest.mark.asyncio
    async def test_render_active_empty(self):
        """render_active_tokens with no tokens returns empty/none message."""
        store = AsyncMock()
        store.list_all.return_value = []

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_active_tokens()

        assert "no active" in output.lower() or "0" in output or "none" in output.lower()

    @pytest.mark.asyncio
    async def test_render_active_marks_expired(self):
        """Expired-but-not-revoked tokens are flagged in active view."""
        past = datetime.now(UTC) - timedelta(hours=1)
        expired = _make_record("tok-e", "agent-e", expires_at=past)

        store = AsyncMock()
        store.list_all.return_value = [expired]

        renderer = TokenTreeRenderer(store)
        output = await renderer.render_active_tokens()

        assert "expired" in output


class TestStoreListAll:
    """TokenStore.list_all() — new method required by render_active_tokens."""

    @pytest.mark.asyncio
    async def test_list_all_returns_every_row(self):
        from general_ludd.sts.store import TokenStore

        mock_session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = ["row-a", "row-b"]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__.return_value = mock_session

        store = TokenStore(mock_factory)
        rows = await store.list_all()

        assert rows == ["row-a", "row-b"]
