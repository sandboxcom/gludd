"""Public API for the self_update package.

Re-exports the router's public symbols so callers can import directly from
``general_ludd.self_update`` without knowing the module layout.
"""

from __future__ import annotations

from general_ludd.self_update.router import (
    DEFAULT_SUBSYSTEM_MAP,
    UpdatePlan,
    UpdateRequest,
    UpdateRequestRouter,
    UpdateTarget,
)

__all__ = [
    "DEFAULT_SUBSYSTEM_MAP",
    "UpdatePlan",
    "UpdateRequest",
    "UpdateRequestRouter",
    "UpdateTarget",
]
