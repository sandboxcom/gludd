"""Routing roles — re-export TaskRole from the leaf schema module.

TaskRole is defined in general_ludd.schemas.benchmark to keep the dependency
flowing routing_roles -> schemas (no back-edge / cycle). This module re-exports
it so existing `from general_ludd.routing_roles.roles import TaskRole` imports
and the routing_roles package export keep working.
"""

from __future__ import annotations

from general_ludd.schemas.benchmark import TaskRole

__all__ = ["TaskRole"]
