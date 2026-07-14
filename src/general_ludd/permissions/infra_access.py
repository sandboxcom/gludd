"""Infrastructure access policy — role-based allowlist for infra deploy/destroy.

This module defines :class:`InfraAccessPolicy` which gates infrastructure
deployment and destruction to a configurable set of roles. It follows the
same default-DENY pattern as :class:`~general_ludd.permissions.tool_permissions.ToolPermissionSpec`
and :class:`~general_ludd.permissions.capability_policy.CapabilityPolicy`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InfraAccessPolicy:
    """Role-based allowlist for infrastructure deploy/destroy operations.

    Each operation (``deploy``, ``destroy``) has its own allowlist of
    role names.  A role NOT in the allowlist is denied — there is no
    inheritance from the capability lattice here; infra operations are a
    distinct privilege dimension.

    Attributes:
        allowed_deploy_roles: Role names permitted to deploy infrastructure.
        allowed_destroy_roles: Role names permitted to destroy infrastructure.
        default_allow_all: If True, allow all roles when lists are empty.
            Default False (strict deny).
    """

    allowed_deploy_roles: frozenset[str] = frozenset()
    allowed_destroy_roles: frozenset[str] = frozenset()
    default_allow_all: bool = False

    def can_deploy(self, role: str) -> bool:
        """Check whether ``role`` may deploy infrastructure."""
        if not role or not isinstance(role, str):
            return False
        if self.default_allow_all and not self.allowed_deploy_roles:
            return True
        return role.strip() in self.allowed_deploy_roles

    def can_destroy(self, role: str) -> bool:
        """Check whether ``role`` may destroy infrastructure."""
        if not role or not isinstance(role, str):
            return False
        if self.default_allow_all and not self.allowed_destroy_roles:
            return True
        return role.strip() in self.allowed_destroy_roles


# ---------------------------------------------------------------------------
# Built-in default policy — the baseline shipped with general_ludd.
# ---------------------------------------------------------------------------

_BUILTIN_DEPLOY_ROLES: frozenset[str] = frozenset({"admin", "operator", "infra_deploy"})
_BUILTIN_DESTROY_ROLES: frozenset[str] = frozenset({"admin", "operator", "infra_destroy"})


def load_infra_access_policy(
    deploy_roles: frozenset[str] | None = None,
    destroy_roles: frozenset[str] | None = None,
) -> InfraAccessPolicy:
    """Load an :class:`InfraAccessPolicy` with optional overrides.

    Args:
        deploy_roles: Override the built-in deploy role allowlist.
        destroy_roles: Override the built-in destroy role allowlist.

    Returns:
        A new policy instance.  If no overrides are given, the built-in
        defaults (``admin``, ``operator``, ``infra_deploy`` for deploy;
        ``admin``, ``operator``, ``infra_destroy`` for destroy) apply.
    """
    return InfraAccessPolicy(
        allowed_deploy_roles=deploy_roles
        if deploy_roles is not None
        else _BUILTIN_DEPLOY_ROLES,
        allowed_destroy_roles=destroy_roles
        if destroy_roles is not None
        else _BUILTIN_DESTROY_ROLES,
    )


__all__ = [
    "InfraAccessPolicy",
    "load_infra_access_policy",
]
