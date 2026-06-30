"""Terraform state backend selection (design doc §10 #2 — RESOLVED).

A deployment above the configured cost threshold with a reachable OpenBao
backend uses a remote state backend that persists ``.tfstate`` into OpenBao's
KV store at ``secret/data/gludd/tfstate/<deployment-id>``; everything else
falls back to the per-deployment tempdir local backend.

The selection rule is the proposal from §10 of TERRAFORM_INFRA_STRUCTURE.md:

    remote for any deployment with ``MAX_COST > $threshold`` AND OpenBao
    reachable; local otherwise.

Remote state is safer for long-lived clusters (state survives the tempdir
being cleaned up and is centrally audit-logged); local is simpler for
ephemeral ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol


class _HealthChecking(Protocol):
    def health_check(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class StateBackendConfig:
    """A selected state backend.

    Two kinds are supported:

    * ``local``     — ``.tfstate`` lives at ``path`` inside the per-deployment
                      tempdir (the default ephemeral behaviour).
    * ``openbao_kv``— state is read/written through OpenBao's KV v2 store at
                      ``path`` (e.g. ``secret/data/gludd/tfstate/<dep-id>``).
                      The rendered backend block uses the Terraform ``http``
                      backend pointed at the OpenBao KV endpoint.
    """

    kind: str
    path: str


class StateBackendSelector:
    """Pick a local-vs-remote state backend for a ComputeConfig deployment."""

    def __init__(
        self,
        openbao_client: Any,
        secrets_manager: _HealthChecking,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._openbao_client = openbao_client
        self._secrets_manager = secrets_manager
        cfg = config or {}
        # Deployments whose MAX_COST exceeds this threshold use remote state
        # (when OpenBao is reachable); the rest stay local.
        self.cost_threshold_usd: float = float(cfg.get("cost_threshold_usd", 50.0))

    def select(
        self,
        compute_config: Any,
        deployment_id: str | None = None,
    ) -> StateBackendConfig:
        """Return the backend config for a given deployment.

        Falls back to a local backend whenever OpenBao is unreachable, even if
        the deployment is above the cost threshold — failing closed here would
        block ephemeral clusters on a dev box with no OpenBao running, which is
        worse than the local-state risk the rule is meant to mitigate.
        """
        max_cost = float(getattr(compute_config, "max_cost_usd", 0.0))
        if max_cost > self.cost_threshold_usd and self._openbao_reachable():
            dep_id = deployment_id or uuid.uuid4().hex[:12]
            return StateBackendConfig(
                kind="openbao_kv",
                path=f"secret/data/gludd/tfstate/{dep_id}",
            )
        return StateBackendConfig(kind="local", path="terraform.tfstate")

    def _openbao_reachable(self) -> bool:
        try:
            return bool(self._secrets_manager.health_check())
        except Exception:
            return False


def render_backend_block(config: StateBackendConfig) -> str:
    """Emit the ``terraform { backend "..." {} }`` block for a backend config.

    For ``openbao_kv`` the block uses the Terraform ``http`` backend pointed at
    the OpenBao KV v2 endpoint; for ``local`` it emits the standard local
    backend with the given ``path``.
    """
    if config.kind == "local":
        return (
            'terraform {\n'
            '  backend "local" {\n'
            f'    path = "{config.path}"\n'
            '  }\n'
            '}\n'
        )
    if config.kind == "openbao_kv":
        return (
            'terraform {\n'
            '  backend "http" {\n'
            f'    address = "{config.path}"\n'
            '  }\n'
            '}\n'
        )
    raise ValueError(f"unknown state backend kind: {config.kind!r}")
