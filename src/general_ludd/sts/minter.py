"""TokenMinter — mint per-subagent AppRole credentials via OpenBao.

Integrates with ``SecretsManager.setup_approle`` to create an AppRole
named ``agent-{agent_id}`` and return ``AppRoleCreds(role_id, secret_id)``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from general_ludd.sts.narrowing import CapabilityNarrowing, OpenBaoPolicyRenderer

if TYPE_CHECKING:
    from general_ludd.permissions.tool_permissions import (
        CapabilityLattice,
        ToolAction,
    )
    from general_ludd.secrets.manager import AppRoleCreds, SecretsManager

logger = logging.getLogger(__name__)


class TokenMinter:
    """Mints per-agent OpenBao AppRole credentials.

    Calls ``SecretsManager.setup_approle(role_name=f"agent-{agent_id}")``
    to create the AppRole + secret_id pair. Capability narrowing applies
    the parent's lattice intersection to the child's requested scope.

    Audit events are logged for every mint operation.
    """

    def __init__(self, secrets_manager: SecretsManager) -> None:
        self._secrets_manager = secrets_manager

    async def mint(
        self,
        agent_id: str,
        parent_agent_id: str,
        scope: object | None = None,
        *,
        parent_lattice: CapabilityLattice | None = None,
        child_actions: set[ToolAction] | None = None,
        parent_role: str = "admin",
    ) -> AppRoleCreds:
        """Mint AppRole credentials for *agent_id*.

        Args:
            agent_id: The child agent to mint creds for.
            parent_agent_id: The parent whose capability lattice constrains
                the child.
            scope: Reserved for future structured scope object (P3+).
            parent_lattice: The parent's CapabilityLattice. When provided,
                child_actions are narrowed via CapabilityNarrowing before
                minting.
            child_actions: The set of ToolAction values the child requests.
                Narrowed to parent's grantable subset when parent_lattice
                is provided.
            parent_role: The role within parent_lattice to evaluate against
                (default ``"admin"``).

        Returns:
            ``AppRoleCreds(role_id, secret_id)`` from the OpenBao backend.
        """
        role_name = f"agent-{agent_id}"
        narrowed: set[str] | None = None

        if parent_lattice is not None and child_actions is not None:
            narrowing = CapabilityNarrowing(parent_lattice)
            narrowed = narrowing.narrow(set(child_actions), parent_role=parent_role)
            logger.info(
                "STS mint: narrowed scope agent=%s parent=%s role=%s "
                "requested=%d granted=%d",
                agent_id,
                parent_agent_id,
                parent_role,
                len(child_actions),
                len(narrowed),
            )
            dropped = {
                str(a.value if hasattr(a, "value") else a)
                for a in child_actions
            } - narrowed
            if dropped:
                logger.warning(
                    "STS mint: dropped actions for agent=%s (parent=%s): %s",
                    agent_id,
                    parent_agent_id,
                    sorted(dropped),
                )

        creds = self._secrets_manager.setup_approle(role_name)

        logger.info(
            "STS audit — mint: agent=%s parent=%s role=%s role_id=%s "
            "narrowed_scope=%s",
            agent_id,
            parent_agent_id,
            role_name,
            creds.role_id,
            sorted(narrowed) if narrowed is not None else None,
        )
        return creds

    def render_policy(self, actions: Iterable[object], role_name: str) -> str:
        """Render an OpenBao HCL policy fragment for *actions*.

        Convenience wrapper around :class:`OpenBaoPolicyRenderer.render`
        for callers that already have a narrowed action set.
        """
        return OpenBaoPolicyRenderer.render(actions, role_name=role_name)
