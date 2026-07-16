"""TokenTreeRenderer — textual visualization of STS token delegation trees.

NF.7: surfaces parent→child token relationships and revocation cascade
impact in a human-readable tree form. Read-only: never mutates token state.

Three views:

- :meth:`TokenTreeRenderer.render_tree` — the delegation subtree rooted at
  an ``agent_id`` (parent → children → grandchildren, recursively).
- :meth:`TokenTreeRenderer.render_revocation_cascade` — a dry-run of what
  :meth:`TokenReaper.cascade_revoke` would revoke if invoked on the root,
  without actually revoking anything.
- :meth:`TokenTreeRenderer.render_active_tokens` — every live token in the
  system, grouped into per-root forests.

Output uses ASCII tree connectors (``├──``, ``└──``, ``│``) so it renders
cleanly in a terminal or a log line.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


def _status(record: object, now: datetime | None = None) -> str:
    """Classify a token record as active/revoked/expired.

    A revoked token is ``revoked`` regardless of expiry. A non-revoked token
    whose ``expires_at`` is in the past is ``expired``. Otherwise ``active``.
    """
    revoked_at = getattr(record, "revoked_at", None)
    if revoked_at is not None:
        return "revoked"
    expires_at = getattr(record, "expires_at", None)
    if expires_at is not None and (now or datetime.now(UTC)) > expires_at:
        return "expired"
    return "active"


def _format_node(record: object, now: datetime | None = None) -> str:
    """One-line summary for a token record: ``agent_id [token_id] (status)``."""
    agent_id = getattr(record, "agent_id", "?")
    token_id = getattr(record, "token_id", "?")
    status = _status(record, now=now)
    return f"{agent_id} [{token_id}] ({status})"


def _render_subtree(
    nodes: list[object],
    prefix: str,
    lines: list[str],
    now: datetime | None = None,
) -> None:
    """Append ASCII-tree-formatted lines for a list of sibling nodes."""
    for i, node in enumerate(nodes):
        is_last = i == len(nodes) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{_format_node(node, now=now)}")
        child_prefix = prefix + ("    " if is_last else "│   ")
        children = getattr(node, "_children", [])
        if children:
            _render_subtree(children, child_prefix, lines, now=now)


async def _build_children_map(
    store: TokenStore,
    parent_ids: set[str],
) -> dict[str, list[object]]:
    """Batch-resolve children for every parent in *parent_ids*.

    Returns a mapping ``parent_agent_id -> [child_records]``. Children are
    fetched via :meth:`TokenStore.list_children` (already indexed).
    """
    children_map: dict[str, list[object]] = {}
    for pid in parent_ids:
        children_map[pid] = list(await store.list_children(pid))
    return children_map


async def _fetch_descendants(
    store: TokenStore,
    root_agent_id: str,
) -> list[object]:
    """BFS-collect the full descendant set of *root_agent_id*.

    Returns a flat list; each record is annotated with a ``_children``
    attribute (the caller-built map) so :func:`_render_subtree` can walk it
    as a tree. Cycle-safe via a visited set.
    """
    visited: set[str] = set()
    all_records: list[object] = []
    children_map: dict[str, list[object]] = {}

    frontier: list[str] = [root_agent_id]
    while frontier:
        current = frontier.pop()
        if current in visited:
            continue
        visited.add(current)
        kids = list(await store.list_children(current))
        children_map[current] = kids
        for kid in kids:
            kid_agent = getattr(kid, "agent_id", None)
            if kid_agent is not None and kid_agent not in visited:
                frontier.append(kid_agent)
                all_records.append(kid)

    for record in all_records:
        agent_id = getattr(record, "agent_id", None)
        with contextlib.suppress(AttributeError, TypeError):
            record._children = children_map.get(agent_id, [])

    return all_records


class TokenTreeRenderer:
    """Render STS token delegation trees and revocation cascades as text.

    Stateless aside from the injected :class:`TokenStore`; safe to reuse
    across calls.
    """

    def __init__(self, store: TokenStore) -> None:
        self._store = store

    async def render_tree(self, parent_id: str) -> str:
        """Render the full delegation subtree rooted at *parent_id*.

        The root token is fetched via :meth:`TokenStore.get`; descendants
        are resolved transitively via :meth:`TokenStore.list_children` and
        attached to each record as ``_children`` for tree rendering.
        Returns a not-found message if *parent_id* has no token record.
        """
        now = datetime.now(UTC)
        root = await self._store.get(parent_id)
        if root is None:
            return f"token for agent_id={parent_id!r} not found"

        await _annotate_children(self._store, root, parent_id, visited=set())

        lines: list[str] = [_format_node(root, now=now)]
        root_children = getattr(root, "_children", [])
        _render_subtree(root_children, "", lines, now=now)
        return "\n".join(lines)

    async def render_revocation_cascade(self, parent_id: str) -> str:
        """Dry-run of :meth:`TokenReaper.cascade_revoke` for *parent_id*.

        Shows which tokens WOULD be revoked if the cascade were invoked,
        without touching the store. Already-revoked descendants are listed
        but not descended into.
        """
        now = datetime.now(UTC)
        lines: list[str] = [f"revocation cascade from {parent_id}:"]
        visited: set[str] = set()
        frontier: list[str] = [parent_id]
        live_count = 0
        revoked_seen = 0

        tree_lines: list[str] = []
        level_records: list[object] = []
        children_by_parent: dict[str, list[object]] = {}

        while frontier:
            current = frontier.pop()
            if current in visited:
                continue
            visited.add(current)
            children = list(await self._store.list_children(current))
            children_by_parent[current] = children
            for child in children:
                child_agent = getattr(child, "agent_id", "")
                if child_agent in visited:
                    continue
                if getattr(child, "revoked_at", None) is not None:
                    revoked_seen += 1
                    level_records.append(child)
                    continue
                live_count += 1
                level_records.append(child)
                frontier.append(child_agent)

        for record in level_records:
            with contextlib.suppress(AttributeError, TypeError):
                record._children = []

        _render_subtree(level_records, "  ", tree_lines, now=now)
        lines.extend(tree_lines)
        lines.append(
            f"summary: {live_count} live token(s) would be revoked; "
            f"{revoked_seen} already-revoked descendant(s) skipped."
        )
        return "\n".join(lines)

    async def render_active_tokens(self) -> str:
        """Render every live (non-revoked) token, grouped into per-root forests.

        Revoked tokens are excluded entirely. Expired-but-not-revoked tokens
        are included (flagged ``expired``). Roots (tokens whose
        ``parent_agent_id`` has no matching token row) start new trees.
        """
        now = datetime.now(UTC)
        all_tokens = list(await self._store.list_all())

        live_tokens = [
            t for t in all_tokens if getattr(t, "revoked_at", None) is None
        ]

        if not live_tokens:
            return "no active tokens"

        by_agent: dict[str, object] = {
            getattr(t, "agent_id", ""): t for t in live_tokens
        }
        children_map: dict[str, list[object]] = {}
        roots: list[object] = []

        for token in live_tokens:
            parent_id = getattr(token, "parent_agent_id", "")
            if parent_id in by_agent:
                children_map.setdefault(parent_id, []).append(token)
            else:
                roots.append(token)

        for token in live_tokens:
            agent_id = getattr(token, "agent_id", "")
            with contextlib.suppress(AttributeError, TypeError):
                token._children = children_map.get(agent_id, [])

        lines: list[str] = [f"active token forest ({len(live_tokens)} live):"]
        _render_subtree(roots, "", lines, now=now)
        return "\n".join(lines)


async def _annotate_children(
    store: TokenStore,
    record: object,
    agent_id: str,
    visited: set[str],
) -> None:
    """Recursively attach a ``_children`` list to *record*.

    This is a helper for :meth:`TokenTreeRenderer.render_tree` so that
    :func:`_render_subtree` can walk the tree without re-querying.
    """
    if agent_id in visited:
        with contextlib.suppress(AttributeError, TypeError):
            record._children = []
        return
    visited.add(agent_id)
    children = list(await store.list_children(agent_id))
    with contextlib.suppress(AttributeError, TypeError):
        record._children = children
    for child in children:
        child_agent = getattr(child, "agent_id", "")
        await _annotate_children(store, child, child_agent, visited)
