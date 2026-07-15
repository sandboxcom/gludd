"""TokenReviver — on hydration re-entry, mint a fresh secret_id for the same AppRole.

Phase P3: reads stored AgentTokenModel, calls
``SecretsManager.rotate_approle_secret_id()``, and injects the fresh
credential into the rehydrated subagent's environment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.secrets.manager import AppRoleCreds, SecretsManager
    from general_ludd.sts.audit import StsAuditPipeline
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


class TokenRevivalError(RuntimeError):
    """Raised when an STS token cannot be revived."""


class TokenReviver:
    """Revives an STS token on agent rehydration.

    Reads the stored ``AgentTokenModel``, rotates the AppRole secret_id
    (keeping the same role_name → same permissions), and returns fresh
    ``AppRoleCreds`` for injection into the rehydrated agent.

    Audit events are recorded via an optional
    :class:`~general_ludd.sts.audit.StsAuditPipeline`.
    """

    def __init__(
        self,
        secrets_manager: SecretsManager,
        token_store: TokenStore,
        audit_pipeline: StsAuditPipeline | None = None,
    ) -> None:
        self._secrets_manager = secrets_manager
        self._token_store = token_store
        self._audit_pipeline = audit_pipeline

    async def revive(self, agent_id: str) -> AppRoleCreds:
        """Mint a fresh secret_id for *agent_id*'s existing AppRole.

        Returns:
            ``AppRoleCreds(role_id, fresh_secret_id)`` with the same
            ``role_id`` as the original token.

        Raises:
            TokenRevivalError: if no token record exists for *agent_id*,
                if the token is already revoked, or if the AppRole no
                longer exists in OpenBao.
        """
        from general_ludd.secrets.manager import AppRoleCreds

        record = await self._token_store.get(agent_id)
        if record is None:
            raise TokenRevivalError(
                f"No token record found for agent {agent_id}"
            )
        if record.revoked_at is not None:
            raise TokenRevivalError(
                f"Token for agent {agent_id} was revoked at "
                f"{record.revoked_at.isoformat()}; cannot revive a "
                "revoked token."
            )

        try:
            fresh_secret_id = self._secrets_manager.rotate_approle_secret_id(
                record.role_name
            )
        except Exception as exc:
            raise TokenRevivalError(
                f"Failed to rotate secret_id for role "
                f"{record.role_name!r} (agent={agent_id}): "
                f"{self._sanitize(exc)}"
            ) from exc

        await self._token_store.increment_hydration(agent_id)

        if self._audit_pipeline is not None:
            await self._audit_pipeline.record_revive(
                token_id=record.token_id,
                agent_id=agent_id,
                parent_agent_id=record.parent_agent_id,
            )

        logger.info(
            "STS audit — revive: agent=%s role=%s role_id=%s",
            agent_id,
            record.role_name,
            record.role_id,
        )
        return AppRoleCreds(
            role_id=record.role_id,
            secret_id=fresh_secret_id,
        )

    @staticmethod
    def _sanitize(exc: BaseException) -> str:
        return type(exc).__name__
