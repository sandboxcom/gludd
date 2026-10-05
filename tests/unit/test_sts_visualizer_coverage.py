"""Typed cycle and helper coverage for STS token visualization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from general_ludd.db.models import AgentTokenModel
from general_ludd.sts.store import TokenStore
from general_ludd.sts.visualizer import (
    TokenTreeRenderer,
    _annotate_children,
    _build_children_map,
    _fetch_descendants,
)


@dataclass
class _Node:
    """Mutable token row used by traversal tests."""

    agent_id: str
    token_id: str
    parent_agent_id: str = ""
    revoked_at: datetime | None = None
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=1))
    _children: list[object] = field(default_factory=list)


class _Store:
    """Return deterministic child lists for graph traversals."""

    def __init__(self, children: dict[str, list[_Node]]) -> None:
        self.children = children

    async def list_children(self, agent_id: str) -> list[_Node]:
        """Return children for an agent."""
        return self.children.get(agent_id, [])


def _store(children: dict[str, list[_Node]]) -> TokenStore:
    """Cast the focused traversal seam to the production protocol."""
    return cast(TokenStore, _Store(children))


@pytest.mark.asyncio
async def test_build_children_map_batches_every_parent() -> None:
    """Resolve every requested parent without mutating token state."""
    first = _Node("first", "t1")
    second = _Node("second", "t2")
    result = await _build_children_map(
        _store({"root-a": [first], "root-b": [second]}),
        {"root-a", "root-b"},
    )
    assert {key: [node.agent_id for node in value] for key, value in result.items()} == {
        "root-a": ["first"],
        "root-b": ["second"],
    }


@pytest.mark.asyncio
async def test_fetch_descendants_is_cycle_and_duplicate_safe() -> None:
    """Skip duplicate frontier entries and annotate a finite graph."""
    duplicate_a = _Node("child", "t1", parent_agent_id="root")
    duplicate_b = _Node("child", "t2", parent_agent_id="root")
    grandchild = _Node("grandchild", "t3", parent_agent_id="child")
    records = await _fetch_descendants(
        _store({"root": [duplicate_a, duplicate_b], "child": [grandchild], "grandchild": [duplicate_a]}),
        "root",
    )
    assert [record.agent_id for record in records] == ["child", "child", "grandchild"]
    assert duplicate_a._children == [grandchild]


@pytest.mark.asyncio
async def test_annotate_children_terminates_self_cycle() -> None:
    """Attach an empty child list when recursion revisits an agent."""
    root = _Node("root", "root-token")
    self_child = _Node("root", "child-token", parent_agent_id="root")
    await _annotate_children(
        _store({"root": [self_child]}),
        cast(AgentTokenModel, root),
        "root",
        visited=set(),
    )
    assert root._children == [self_child]
    assert self_child._children == []


@pytest.mark.asyncio
async def test_revocation_cascade_skips_self_cycle() -> None:
    """Ignore a child whose agent is already the current visited root."""
    self_child = _Node("root", "self-token", parent_agent_id="root")
    renderer = TokenTreeRenderer(_store({"root": [self_child]}))
    output = await renderer.render_revocation_cascade("root")
    assert "0 live token(s) would be revoked" in output
    assert "self-token" not in output
