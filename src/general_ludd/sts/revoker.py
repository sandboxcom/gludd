"""TokenRevoker — destroy the AppRole + tokens on agent death/completion.

Phase P3/P4: calls ``SecretsManager._client.auth.approle.delete_role()``
to destroy the AppRole and all associated tokens.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.secrets.manager import SecretsManager
    from general_ludd.sts.audit import StsAuditPipeline
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


class TokenRevoker:
    """Revokes an STS token by destroying its AppRole.

    On agent death or completion, destroys the per-agent OpenBao AppRole
    and marks the ``AgentTokenModel.revoked_at`` timestamp.

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

    async def revoke(self, agent_id: str) -> None:
        """Destroy the OpenBao AppRole for *agent_id* and mark the token revoked.

        Idempotent: a missing token record or an already-revoked token is
        treated as a no-op (warning logged, no exception raised).
        """
        record = await self._token_store.get(agent_id)
        if record is None:
            logger.warning(
                "STS revoke: no token record for agent=%s; skipping.",
                agent_id,
            )
            return
        if record.revoked_at is not None:
            logger.warning(
                "STS revoke: token for agent=%s already revoked at %s.",
                agent_id,
                record.revoked_at.isoformat(),
            )
            return

        self._destroy_approle(record.role_name, agent_id)

        await self._token_store.revoke(record.token_id)

        if self._audit_pipeline is not None:
            await self._audit_pipeline.record_revoke(
                token_id=record.token_id,
                agent_id=agent_id,
                parent_agent_id=record.parent_agent_id,
            )

        logger.info(
            "STS audit — revoke: agent=%s role=%s role_id=%s token_id=%s",
            agent_id,
            record.role_name,
            record.role_id,
            record.token_id,
        )

    def _destroy_approle(self, role_name: str, agent_id: str) -> None:
        client = self._secrets_manager._client
        if client is None:
            raise RuntimeError(
                "SecretsManager not connected; cannot destroy AppRole "
                f"{role_name!r} for agent {agent_id}."
            )
        try:
            client.auth.approle.delete_role(role_name)
        except Exception as exc:
            logger.warning(
                "STS revoke: failed to destroy AppRole %s for agent=%s: %s",
                role_name,
                agent_id,
                type(exc).__name__,
            )
