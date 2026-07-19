"""TokenReaper — sweep expired tokens and cascade-revoke child subtrees.

Phase P5: closes the ``expire(TTL)`` branch of the token lifecycle diagram
(spec §4), which P1-P4 leave unhandled. Also implements the parent→child
revocation cascade required by the "capability non-escalation" rule (spec §2):
when a parent's token dies, every child token minted under it MUST be revoked
too — otherwise a subagent could outlive its delegator and keep capabilities
that were only valid transitively.

Two entry points:

- :meth:`TokenReaper.reap_expired` — periodic sweep. Finds every live token
  whose TTL has elapsed and revokes it via :class:`TokenRevoker`, emitting an
  ``expire`` audit event for each.

- :meth:`TokenReaper.cascade_revoke` — fan-out revoke. Given a parent
  ``agent_id``, finds all children minted under it, revokes the live ones,
  and recurses transitively. Defensive cycle-detection guarantees termination
  even if ``parent_agent_id`` pointers are malformed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.sts.audit import StsAuditPipeline
    from general_ludd.sts.revoker import TokenRevoker
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


class TokenReaper:
    """Reaps TTL-expired STS tokens and cascades revocation through child subtrees.

    Composes :class:`TokenStore` (queries) and :class:`TokenRevoker`
    (destroys AppRoles + marks revoked_at). An optional
    :class:`StsAuditPipeline` records ``expire`` events for each reaped
    token; cascade revokes reuse the existing ``revoke`` event emitted by
    the underlying ``TokenRevoker``.
    """

    def __init__(
        self,
        store: TokenStore,
        revoker: TokenRevoker,
        audit_pipeline: StsAuditPipeline | None = None,
    ) -> None:
        self._store = store
        self._revoker = revoker
        self._audit_pipeline = audit_pipeline

    async def reap_expired(self, now: datetime | None = None) -> int:
        """Revoke every live token whose ``expires_at`` is in the past.

        Returns the count of tokens successfully reaped. Failures revoking
        individual tokens are logged and do not abort the sweep — a single
        bad AppRole should not leave other expired tokens live.

        Emits one ``expire`` audit event per successfully reaped token.
        """
        if now is None:
            now = datetime.now(UTC)

        expired = await self._store.list_expired(now)
        reaped = 0
        for record in expired:
            if record.revoked_at is not None:
                continue
            try:
                await self._revoker.revoke(record.agent_id)
            except Exception as exc:
                logger.warning(
                    "STS reap: revoke failed for agent=%s token=%s: %s: %s",
                    record.agent_id,
                    record.token_id,
                    type(exc).__name__,
                    exc,
                )
                continue

            if self._audit_pipeline is not None:
                try:
                    await self._audit_pipeline.record_expire(
                        token_id=record.token_id,
                        agent_id=record.agent_id,
                        parent_agent_id=record.parent_agent_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "STS reap: audit emit failed for agent=%s: %s: %s",
                        record.agent_id,
                        type(exc).__name__,
                        exc,
                    )

            reaped += 1

        if expired:
            logger.info(
                "STS reap — expired=%d reaped=%d at=%s",
                len(expired),
                reaped,
                now.isoformat(),
            )
        return reaped

    async def cascade_revoke(self, parent_agent_id: str) -> int:
        """Revoke the entire delegation subtree rooted at *parent_agent_id*.

        For every live (non-revoked) child of *parent_agent_id*, the child
        is revoked via :class:`TokenRevoker` and its own subtree is then
        explored transitively. Already-revoked children are pruned: their
        subtrees are NOT descended into (a dead delegation chain cannot
        still have live descendants).

        A ``visited`` set of agent_ids guarantees termination even if the
        ``parent_agent_id`` column contains a cycle (defensive against data
        corruption — the column is normally a forest rooted at human
        agents).

        Returns the total number of tokens revoked across all levels.
        """
        revoked_total = 0
        visited: set[str] = set()
        frontier: list[str] = [parent_agent_id]

        while frontier:
            current_parent = frontier.pop()
            children = await self._store.list_children(current_parent)
            for child in children:
                if child.agent_id in visited:
                    continue
                visited.add(child.agent_id)

                if child.revoked_at is not None:
                    continue

                try:
                    await self._revoker.revoke(child.agent_id)
                except Exception as exc:
                    logger.warning(
                        "STS cascade: revoke failed for agent=%s token=%s: %s: %s",
                        child.agent_id,
                        child.token_id,
                        type(exc).__name__,
                        exc,
                    )
                    continue

                revoked_total += 1
                frontier.append(child.agent_id)

        if revoked_total:
            logger.info(
                "STS cascade — root=%s revoked=%d",
                parent_agent_id,
                revoked_total,
            )
        return revoked_total
