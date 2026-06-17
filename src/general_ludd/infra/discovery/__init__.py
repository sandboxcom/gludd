"""Compute resource discovery + budget-aware auto-select.

EXTENDS the existing infra layer (``infra/compute.py``, ``infra/deployment.py``,
``infra/utilization.py``) with three layers:

1. A :class:`DiscoveryProvider` ABC + normalized :class:`DiscoveredResource`.
2. A pure :func:`select_resource` mirroring ``scoring.router.AdaptiveRouter``'s
   two-pass cost-aware pick, gated against SpendLimiter/RunBudgetGuard headroom.
3. A :class:`DiscoveryService` that turns a picked resource into the EXISTING
   ``UtilizationTracker.register_endpoint`` so ``route_task`` routes to it.

PEP-562 lazy ``__getattr__`` keeps the package import cheap and SDK-free: a cloud
provider module is only loaded when its symbol is actually accessed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "DiscoveredResource",
    "DiscoveryProvider",
    "DiscoveryResult",
    "DiscoveryService",
    "DiscoverySource",
    "DiscoveryStatus",
    "ProviderNotInstalled",
    "WorkSpec",
    "build_default_registry",
    "register_discovered",
    "select_resource",
]

if TYPE_CHECKING:
    from general_ludd.infra.discovery.base import (
        DiscoveredResource,
        DiscoveryProvider,
        DiscoveryResult,
        DiscoverySource,
        DiscoveryStatus,
        ProviderNotInstalled,
        WorkSpec,
    )
    from general_ludd.infra.discovery.providers import build_default_registry
    from general_ludd.infra.discovery.selector import register_discovered, select_resource
    from general_ludd.infra.discovery.service import DiscoveryService


_BASE = {
    "DiscoveredResource",
    "DiscoveryProvider",
    "DiscoveryResult",
    "DiscoverySource",
    "DiscoveryStatus",
    "ProviderNotInstalled",
    "WorkSpec",
}


def __getattr__(name: str) -> Any:
    if name in _BASE:
        from general_ludd.infra.discovery import base

        return getattr(base, name)
    if name in ("select_resource", "register_discovered"):
        from general_ludd.infra.discovery import selector

        return getattr(selector, name)
    if name == "DiscoveryService":
        from general_ludd.infra.discovery.service import DiscoveryService

        return DiscoveryService
    if name == "build_default_registry":
        from general_ludd.infra.discovery.providers import build_default_registry

        return build_default_registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
