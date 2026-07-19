"""TokenQuotaEnforcer — limit token counts per agent, per project, per scope.

NF.7 STS quota layer. Acts as a pre-mint gate: before TokenMinter mints a new
AppRole credential, the enforcer verifies that minting it would not exceed any
configured quota. A violation raises :class:`QuotaViolation` and the mint is
aborted (the caller surfaces a HumanTodo or rejects the dispatch).

Three quota dimensions:

- ``max_tokens_per_agent`` — cap on simultaneously-active tokens owned by a
  single ``agent_id``. Prevents one runaway agent from minting unbounded creds.
- ``max_active_tokens_per_project`` — cap on simultaneously-active tokens
  associated with a single ``project_id``. Bounds blast radius per project.
- ``max_scope_width`` — cap on the number of distinct scope actions a single
  token may carry. Prevents over-broad (admin-equivalent) tokens.

A token is "active" iff it is not revoked (``revoked_at IS NULL``) and not
expired (``expires_at IS NULL OR expires_at >= now``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


class QuotaViolation(Exception):
    """A token mint would exceed a configured quota.

    The ``dimension`` attribute names which limit was hit
    (``"scope"``, ``"agent"``, or ``"project"``).
    """

    def __init__(self, dimension: str, message: str) -> None:
        super().__init__(message)
        self.dimension = dimension


@dataclass(frozen=True)
class QuotaConfig:
    """Immutable quota thresholds.

    Attributes:
        max_tokens_per_agent: Maximum simultaneously-active tokens per agent.
        max_active_tokens_per_project: Maximum simultaneously-active tokens
            per project. Must be >= ``max_tokens_per_agent`` to be coherent.
        max_scope_width: Maximum number of distinct actions in one token's
            scope. ``0`` disables the scope-width check.
    """

    max_tokens_per_agent: int = 5
    max_active_tokens_per_project: int = 100
    max_scope_width: int = 32


def _is_active(
    row: Any,
    *,
    revoked_attr: str = "revoked_at",
    expires_attr: str = "expires_at",
    now: datetime | None = None,
) -> bool:
    """True iff *row* represents a live (non-revoked, non-expired) token."""
    revoked: object = getattr(row, revoked_attr, None)
    if revoked is not None:
        return False
    expires: object = getattr(row, expires_attr, None)
    if expires is None:
        return True
    assert isinstance(expires, datetime), (
        f"expires_at must be datetime, got {type(expires).__name__}"
    )
    return expires >= (now or datetime.now(UTC))


@runtime_checkable
class QuotaBackend(Protocol):
    """Counts of active tokens, by agent and by project.

    Concrete implementations:

    - :class:`InMemoryQuotaBackend` — default; tracks mints/revokes in dicts.
      Use for tests and for the in-process daemon where the enforcer itself
      is the source of truth for quota accounting.
    - :class:`StoreQuotaBackend` — derives counts from a :class:`TokenStore`
      query against ``AgentTokenModel`` rows. Use when the DB is authoritative.

    Read-only backends (``StoreQuotaBackend``) MAY leave ``record_mint`` /
    ``record_revoke`` as no-ops: the DB is updated elsewhere, and the next
    ``active_count_for_*`` call reflects the change.
    """

    async def active_count_for_agent(self, agent_id: str) -> int:
        ...

    async def active_count_for_project(self, project_id: str) -> int:
        ...

    async def record_mint(
        self, token_id: str, agent_id: str, project_id: str
    ) -> None:
        ...

    async def record_revoke(
        self, token_id: str, agent_id: str, project_id: str
    ) -> None:
        ...


class InMemoryQuotaBackend:
    """In-process quota accounting backed by two dicts.

    Maintains ``{agent_id: {token_id}}`` and ``{project_id: {token_id}}``.
    ``record_mint`` adds to both; ``record_revoke`` removes from both. Counts
    are the set cardinalities, so duplicate mints of the same token_id are
    idempotent and revoking an unknown token_id is a no-op.
    """

    def __init__(self) -> None:
        self._agent_tokens: dict[str, set[str]] = {}
        self._project_tokens: dict[str, set[str]] = {}

    async def active_count_for_agent(self, agent_id: str) -> int:
        return len(self._agent_tokens.get(agent_id, set()))

    async def active_count_for_project(self, project_id: str) -> int:
        return len(self._project_tokens.get(project_id, set()))

    async def record_mint(
        self, token_id: str, agent_id: str, project_id: str
    ) -> None:
        self._agent_tokens.setdefault(agent_id, set()).add(token_id)
        self._project_tokens.setdefault(project_id, set()).add(token_id)
        logger.debug(
            "quota record_mint: token=%s agent=%s project=%s "
            "(agent_count=%d project_count=%d)",
            token_id,
            agent_id,
            project_id,
            len(self._agent_tokens[agent_id]),
            len(self._project_tokens[project_id]),
        )

    async def record_revoke(
        self, token_id: str, agent_id: str, project_id: str
    ) -> None:
        self._agent_tokens.get(agent_id, set()).discard(token_id)
        self._project_tokens.get(project_id, set()).discard(token_id)
        logger.debug(
            "quota record_revoke: token=%s agent=%s project=%s",
            token_id,
            agent_id,
            project_id,
        )


class StoreQuotaBackend:
    """Derives active-token counts from a :class:`TokenStore`.

    Reads ``list_all()`` and filters to active rows (non-revoked, non-expired).
    Because :class:`AgentTokenModel` has no ``project_id`` column, a
    ``project_of`` callable maps each row to its project identifier. This keeps
    the quota layer decoupled from the DB schema and lets callers derive the
    project from agent-id conventions, an external mapping, or a future column.

    ``record_mint`` / ``record_revoke`` are no-ops: the DB is the source of
    truth and is updated by :class:`~general_ludd.sts.store.TokenStore` /
    :class:`~general_ludd.sts.revoker.TokenRevoker`.
    """

    def __init__(
        self,
        store: TokenStore,
        *,
        project_of: Callable[[Any], str],
    ) -> None:
        self._store = store
        self._project_of = project_of

    async def list_active(self) -> list[Any]:
        rows = await self._store.list_all()
        return [r for r in rows if _is_active(r)]

    async def active_count_for_agent(self, agent_id: str) -> int:
        active = await self.list_active()
        return sum(1 for r in active if r.agent_id == agent_id)

    async def active_count_for_project(self, project_id: str) -> int:
        active = await self.list_active()
        return sum(1 for r in active if self._project_of(r) == project_id)

    async def record_mint(
        self, token_id: str, agent_id: str, project_id: str
    ) -> None:
        # No-op: the DB is authoritative; counts derive from queries.
        return None

    async def record_revoke(
        self, token_id: str, agent_id: str, project_id: str
    ) -> None:
        # No-op: revocation is persisted by TokenStore.revoke / TokenRevoker.
        return None


class TokenQuotaEnforcer:
    """Pre-mint gate that enforces :class:`QuotaConfig` limits.

    Call :meth:`check` before minting to validate that a new token would not
    exceed any quota. Call :meth:`record_mint` after a successful mint to
    update the backend's accounting (``check`` is applied first, atomically).
    Call :meth:`record_revoke` when a token is destroyed to free its slot.
    """

    def __init__(
        self,
        config: QuotaConfig | None = None,
        backend: QuotaBackend | None = None,
    ) -> None:
        self._config = config or QuotaConfig()
        self._backend: QuotaBackend = backend or InMemoryQuotaBackend()

    @property
    def config(self) -> QuotaConfig:
        return self._config

    @property
    def backend(self) -> QuotaBackend:
        return self._backend

    async def check(
        self,
        agent_id: str,
        project_id: str,
        scope_actions: set[str] | list[str],
    ) -> None:
        """Raise :class:`QuotaViolation` if a new token would exceed a quota.

        Idempotent and side-effect-free: safe to call multiple times. Does not
        mutate backend state; pair with :meth:`record_mint` to commit.

        Args:
            agent_id: The agent that would own the new token.
            project_id: The project the token is scoped to.
            scope_actions: The action set the token would carry. A list is
                accepted and normalized to a set internally.

        Raises:
            QuotaViolation: with ``.dimension`` of ``"scope"``, ``"agent"``,
                or ``"project"`` naming the breached limit.
        """
        actions = set(scope_actions)

        if (
            self._config.max_scope_width > 0
            and len(actions) > self._config.max_scope_width
        ):
            raise QuotaViolation(
                "scope",
                f"scope width {len(actions)} exceeds maximum "
                f"{self._config.max_scope_width} for agent={agent_id}",
            )

        agent_count = await self._backend.active_count_for_agent(agent_id)
        if agent_count >= self._config.max_tokens_per_agent:
            raise QuotaViolation(
                "agent",
                f"agent {agent_id} has {agent_count} active tokens, "
                f"limit is {self._config.max_tokens_per_agent}",
            )

        project_count = await self._backend.active_count_for_project(project_id)
        if project_count >= self._config.max_active_tokens_per_project:
            raise QuotaViolation(
                "project",
                f"project {project_id} has {project_count} active tokens, "
                f"limit is {self._config.max_active_tokens_per_project}",
            )

    async def record_mint(
        self,
        token_id: str,
        agent_id: str,
        project_id: str,
        scope_actions: set[str] | list[str],
    ) -> None:
        """Check quotas, then record the mint in the backend.

        Raises :class:`QuotaViolation` (without recording) if any limit is
        breached. On success, delegates to ``backend.record_mint``.
        """
        await self.check(agent_id, project_id, scope_actions)
        await self._backend.record_mint(token_id, agent_id, project_id)
        logger.info(
            "quota mint recorded: token=%s agent=%s project=%s scope_width=%d",
            token_id,
            agent_id,
            project_id,
            len(set(scope_actions)),
        )

    async def record_revoke(
        self, token_id: str, agent_id: str, project_id: str
    ) -> None:
        """Record a revocation, freeing the agent/project slot."""
        await self._backend.record_revoke(token_id, agent_id, project_id)
        logger.info(
            "quota revoke recorded: token=%s agent=%s project=%s",
            token_id,
            agent_id,
            project_id,
        )


__all__ = [
    "InMemoryQuotaBackend",
    "QuotaBackend",
    "QuotaConfig",
    "QuotaViolation",
    "StoreQuotaBackend",
    "TokenQuotaEnforcer",
]
