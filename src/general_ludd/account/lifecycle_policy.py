"""Lifecycle policy for ephemeral cloud accounts.

Decides — given a policy config and the current state of an account — whether
the account should be created, kept, or deleted. Pure functions / data classes;
no I/O. The :mod:`general_ludd.account.ephemeral` module wires this into the
:class:`EphemeralAccountManager` which actually talks to providers.

Public API:
    PolicyConfig
        auto_delete_after_use: bool
        retention_period_hours: int
        budget_limit: float
    LifecycleAction  (enum: CREATE / KEEP / DELETE)
    evaluate_lifecycle(account_id, policy, active, age_hours) -> LifecycleAction
"""

from __future__ import annotations

import enum
from typing import Protocol


class LifecycleAction(enum.Enum):
    """The lifecycle decision returned by :func:`evaluate_lifecycle`."""

    CREATE = "create"
    KEEP = "keep"
    DELETE = "delete"


class PolicyConfigLike(Protocol):
    """Structural shape :func:`evaluate_lifecycle` reads off the policy."""

    auto_delete_after_use: bool
    retention_period_hours: int


class PolicyConfig:
    """Mutable, plain-Python config for ephemeral account lifecycles.

    Kept as a small class (not a pydantic model) so it can be constructed from
    arbitrary dict / YAML loaders without a dep on pydantic at this layer —
    mirrors the lightweight dataclass style used by other account modules.

    Raises:
        ValueError: on construction if ``retention_period_hours`` is non-positive
            or ``budget_limit`` is negative.
    """

    __slots__ = ("auto_delete_after_use", "budget_limit", "retention_period_hours")

    def __init__(
        self,
        *,
        auto_delete_after_use: bool = True,
        retention_period_hours: int = 24,
        budget_limit: float = 10.0,
    ) -> None:
        if retention_period_hours <= 0:
            raise ValueError(
                f"retention_period_hours must be > 0, got {retention_period_hours}"
            )
        if budget_limit < 0:
            raise ValueError(f"budget_limit must be >= 0, got {budget_limit}")
        self.auto_delete_after_use = auto_delete_after_use
        self.retention_period_hours = retention_period_hours
        self.budget_limit = budget_limit

    def to_dict(self) -> dict[str, object]:
        return {
            "auto_delete_after_use": self.auto_delete_after_use,
            "retention_period_hours": self.retention_period_hours,
            "budget_limit": self.budget_limit,
        }

    def __repr__(self) -> str:
        return (
            "PolicyConfig("
            f"auto_delete_after_use={self.auto_delete_after_use}, "
            f"retention_period_hours={self.retention_period_hours}, "
            f"budget_limit={self.budget_limit})"
        )


def evaluate_lifecycle(
    *,
    account_id: str | None,
    policy: PolicyConfigLike,
    active: bool,
    age_hours: float,
) -> LifecycleAction:
    """Decide what to do with an ephemeral account.

    Args:
        account_id: The provider-scoped account id, or ``None`` if no account
            has been created yet for the workload in question.
        policy: Anything with ``auto_delete_after_use`` and
            ``retention_period_hours`` attributes (typically :class:`PolicyConfig`).
        active: Whether the account currently exists / is active on the provider.
        age_hours: Wall-clock age of the account in hours (ignored when no
            account exists).

    Returns:
        CREATE — no account exists yet.
        KEEP   — account exists and either auto-delete is off OR it is within
                 the retention window.
        DELETE — account exists, auto-delete is on, and EITHER the account is
                 inactive OR has aged past the retention window.
    """
    # 1. No account yet -> caller should provision one.
    if account_id is None:
        return LifecycleAction.CREATE

    # 2. Operator has disabled auto-delete; never tear down automatically.
    if not policy.auto_delete_after_use:
        return LifecycleAction.KEEP

    # 3. Auto-delete is on but the account is already gone/inactive on the
    #    provider side — tell the caller to clean up its registry entry.
    if not active:
        return LifecycleAction.DELETE

    # 4. Auto-delete is on, account is active, but past retention -> tear down.
    if age_hours >= policy.retention_period_hours:
        return LifecycleAction.DELETE

    # 5. Active and within retention -> keep using it.
    return LifecycleAction.KEEP
