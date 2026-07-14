"""SubagentTokenInjector — mint token at dispatch time and inject into subagent env.

At dispatch_one, mints an STS token via TokenMinter, stores via TokenStore,
and sets ``GLUDD_STS_ROLE_ID`` + ``GLUDD_STS_SECRET_ID`` env vars on the
subagent's process environment. Reuses the existing ``bind_tools_on_dispatch``
injection pattern from ``AgentDispatcher.dispatch_one``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.agents.dispatcher import AgentDispatcher
    from general_ludd.sts.minter import TokenMinter
    from general_ludd.sts.store import TokenStore


class SubagentTokenInjector:
    """Injects per-agent STS tokens into the subagent dispatch flow.

    Placeholder — full wiring deferred to Phase P1 closeout when the
    ``dispatch_one`` injection point is integrated.
    """

    def __init__(
        self,
        minter: TokenMinter,
        store: TokenStore,
        dispatcher: AgentDispatcher,
    ) -> None:
        self._minter = minter
        self._store = store
        self._dispatcher = dispatcher
