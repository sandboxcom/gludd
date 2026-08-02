"""SubagentTokenInjector — mint token at dispatch time and inject into subagent env.

At dispatch_one, mints an STS token via TokenMinter, stores via TokenStore,
and sets ``GLUDD_STS_ROLE_ID`` + ``GLUDD_STS_SECRET_ID`` env vars on the
subagent's process environment. Reuses the existing ``bind_tools_on_dispatch``
injection pattern from ``AgentDispatcher.dispatch_one``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.agents.dispatcher import AgentDispatcher
    from general_ludd.agents.types import AgentTask
    from general_ludd.sts.minter import TokenMinter
    from general_ludd.sts.revoker import TokenRevoker
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


class SubagentTokenInjector:
    """Injects per-agent STS tokens into the subagent dispatch flow.

    Called from ``AgentDispatcher.dispatch_one`` before the executor runs.
    Mints a fresh token, stores it via ``TokenStore``, and populates
    ``task.env`` with ``GLUDD_STS_ROLE_ID`` + ``GLUDD_STS_SECRET_ID``.
    """

    def __init__(
        self,
        minter: TokenMinter,
        store: TokenStore,
        dispatcher: AgentDispatcher,
        *,
        revoker: TokenRevoker | None = None,
    ) -> None:
        self._minter = minter
        self._store = store
        self._dispatcher = dispatcher
        self._revoker = revoker

    async def enrich(self, task: AgentTask) -> None:
        """Mint a token for *task* and inject STS env vars into ``task.env``.

        Stores the ``AgentTokenModel`` record via ``TokenStore`` and sets
        ``GLUDD_STS_ROLE_ID`` + ``GLUDD_STS_SECRET_ID`` on ``task.env``
        so the executor can propagate them to the subagent process.
        """
        parent_agent_id = (task.invoker_name or task.parent_task_id or "root")
        creds = await self._minter.mint(
            agent_id=task.task_id,
            parent_agent_id=parent_agent_id,
            scope=None,
        )
        from general_ludd.db.models import AgentTokenModel

        record = AgentTokenModel(
            token_id=f"tok-{task.task_id}",
            agent_id=task.task_id,
            parent_agent_id=parent_agent_id,
            role_name=f"agent-{task.task_id}",
            role_id=creds.role_id,
            scope_hash="",
        )
        await self._store.store(record)

        task.env["GLUDD_STS_ROLE_ID"] = creds.role_id
        task.env["GLUDD_STS_SECRET_ID"] = creds.secret_id
        logger.debug(
            "STS inject: agent=%s role_id=%s token_id=%s",
            task.task_id,
            creds.role_id,
            record.token_id,
        )

    async def env_vars(self, agent_id: str, parent_agent_id: str) -> dict[str, str]:
        if self._minter is None:
            return {}
        creds = await self._minter.mint(
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        return {
            "GLUDD_STS_ROLE_ID": str(creds.role_id),
            "GLUDD_STS_SECRET_ID": str(creds.secret_id),
            "GLUDD_STS_TOKEN_ID": f"tok-{agent_id}",
        }

    async def finalize(self, agent_id: str, *, terminal_state: str) -> None:
        """Revoke credentials after any terminal dispatch outcome.

        The dispatcher invokes this from its ``finally`` block, covering
        completion, failure, cancellation, and batch timeout.  Deployments
        without a revoker retain the historical no-op behavior while the
        OpenBao TTL remains the final safety bound.
        """

        if self._revoker is None:
            return
        await self._revoker.revoke(agent_id, terminal_state=terminal_state)
