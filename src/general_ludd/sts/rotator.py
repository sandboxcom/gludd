"""TokenRotator — automatic token rotation before expiry without service interruption.

NF.7 STS extension. Atomically swaps an expiring token for a fresh one with
the same scope:

  1. mint new AppRole (same scope_actions + parent_agent_id as the old record)
  2. store the new AgentTokenModel row (new token is LIVE before step 3)
  3. destroy the old AppRole + mark old record revoked

Because step 2 completes before step 3 begins, callers using the injector
pattern can hot-swap their credential reference with zero downtime — the old
token remains valid right up until the new one is observable in the store.

``needs_rotation()`` / ``rotate_all()`` provide the sweep primitive used by
the daemon's scheduled rotator loop: any live token whose ``expires_at``
falls within ``rotation_window_seconds`` of now is rotated.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.secrets.manager import AppRoleCreds, SecretsManager
    from general_ludd.sts.audit import StsAuditPipeline
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


class TokenRotationError(RuntimeError):
    """Raised when an STS token cannot be rotated."""


class TokenRotator:
    """Rotates an STS token before its TTL elapses.

    The rotation is atomic from the caller's perspective: either the new
    credential is returned AND the old one is revoked, or — on any failure
    during mint — the old token is left untouched (no service interruption).

    A best-effort revocation failure (OpenBao hiccup during ``delete_role``)
    is logged as a warning but does NOT raise: the new token is already
    live and the old AppRole's own TTL will eventually reap it.
    """

    def __init__(
        self,
        secrets_manager: SecretsManager,
        token_store: TokenStore,
        audit_pipeline: StsAuditPipeline | None = None,
        *,
        rotation_window_seconds: int = 600,
        ttl_seconds: int = 3600,
    ) -> None:
        self._secrets_manager = secrets_manager
        self._token_store = token_store
        self._audit_pipeline = audit_pipeline
        self._rotation_window_seconds = rotation_window_seconds
        self._ttl_seconds = ttl_seconds

    async def rotate(
        self,
        agent_id: str,
        *,
        new_agent_id: str | None = None,
    ) -> AppRoleCreds:
        """Rotate the token for *agent_id*.

        Args:
            agent_id: The agent whose token should be rotated.
            new_agent_id: Optional explicit ID for the rotated-in agent.
                When ``None``, a timestamped suffix is generated
                (``"{agent_id}-rot-{epoch}"``) so successive rotations
                produce distinct role names.

        Returns:
            Fresh ``AppRoleCreds`` for the new token. The new record is
            already persisted and live; the old one is revoked.

        Raises:
            TokenRotationError: no record exists, the token is already
                revoked, or the new-token mint failed. On mint failure
                the old token is NOT touched.
        """
        from general_ludd.db.models import AgentTokenModel
        from general_ludd.secrets.manager import AppRoleCreds

        old = await self._token_store.get(agent_id)
        if old is None:
            raise TokenRotationError(
                f"No token record found for agent {agent_id}"
            )
        if old.revoked_at is not None:
            raise TokenRotationError(
                f"Token for agent {agent_id} was revoked at "
                f"{old.revoked_at.isoformat()}; cannot rotate a "
                "revoked token."
            )

        try:
            scope_actions: list[str] = list(
                json.loads(old.scope_actions or "[]")
            )
        except (ValueError, TypeError) as exc:
            raise TokenRotationError(
                f"Corrupt scope_actions on token for agent {agent_id}: "
                f"{exc}"
            ) from exc

        new_id = new_agent_id or f"{agent_id}-rot-{int(time.time())}"
        new_role_name = f"agent-{new_id}"

        try:
            new_creds = self._secrets_manager.setup_approle(new_role_name)
        except Exception as exc:
            raise TokenRotationError(
                f"Failed to mint replacement token for agent {agent_id} "
                f"(role={new_role_name!r}): {self._sanitize(exc)}"
            ) from exc

        now = datetime.now(UTC)
        new_record = AgentTokenModel(
            token_id=f"tok-{new_id}",
            agent_id=new_id,
            parent_agent_id=old.parent_agent_id,
            role_name=new_role_name,
            role_id=new_creds.role_id,
            scope_hash=old.scope_hash,
            scope_actions=json.dumps(scope_actions),
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
            hydration_count=0,
        )
        await self._token_store.store(new_record)

        self._destroy_old_approle(old.role_name, agent_id)
        await self._token_store.revoke(old.token_id)

        if self._audit_pipeline is not None:
            await self._audit_pipeline.record_renew(
                token_id=new_record.token_id,
                agent_id=new_id,
                parent_agent_id=old.parent_agent_id,
            )

        logger.info(
            "STS audit — rotate: old_agent=%s old_role=%s -> "
            "new_agent=%s new_role=%s new_role_id=%s",
            agent_id,
            old.role_name,
            new_id,
            new_role_name,
            new_creds.role_id,
        )
        return AppRoleCreds(
            role_id=new_creds.role_id,
            secret_id=new_creds.secret_id,
        )

    async def rotate_all(
        self,
    ) -> list[tuple[str, AppRoleCreds]]:
        """Rotate every live token within the rotation window.

        Returns:
            List of ``(old_agent_id, new_creds)`` for each rotation that
            succeeded. Tokens that fail to rotate are logged and skipped
            (one bad token must not abort the sweep).
        """
        results: list[tuple[str, AppRoleCreds]] = []
        all_records = await self._token_store.list_all()
        for record in all_records:
            if record.revoked_at is not None:
                continue
            if not self.needs_rotation(expires_at=record.expires_at):
                continue
            try:
                creds = await self.rotate(record.agent_id)
            except TokenRotationError as exc:
                logger.warning(
                    "STS rotate_all: skipping agent=%s: %s",
                    record.agent_id,
                    exc,
                )
                continue
            results.append((record.agent_id, creds))
        return results

    def needs_rotation(
        self,
        *,
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> bool:
        """Return ``True`` if *expires_at* is within the rotation window.

        A token with ``expires_at=None`` (no TTL) never needs rotation.
        A token whose expiry has already passed also returns ``True`` so
        the sweep can recover missed rotations.
        """
        if expires_at is None:
            return False
        current = now or datetime.now(UTC)
        return (expires_at - current) <= timedelta(
            seconds=self._rotation_window_seconds
        )

    def _destroy_old_approle(self, role_name: str, agent_id: str) -> None:
        client = self._secrets_manager._client
        if client is None:
            logger.warning(
                "STS rotate: SecretsManager not connected; cannot destroy "
                "old AppRole %s for agent=%s. Relying on TTL reaper.",
                role_name,
                agent_id,
            )
            return
        try:
            client.auth.approle.delete_role(role_name)
        except Exception as exc:
            logger.warning(
                "STS rotate: failed to destroy old AppRole %s for "
                "agent=%s: %s: %s. New token is live; TTL reaper will "
                "clean up.",
                role_name,
                agent_id,
                type(exc).__name__,
                exc,
            )

    @staticmethod
    def _sanitize(exc: BaseException) -> str:
        return type(exc).__name__
