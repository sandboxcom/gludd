"""Zero-downtime deployment (ZDD) rollout: shadow→canary→full enforcement.

The ZDDRollout wraps SmallModelTaskPolicy.authorize() and gates enforcement
progressively.  Shadow mode records what the policy would decide without
acting on it.  Canary stages enforce on a hash-bucketed fraction of traffic.
Full enforces 100%.  Rollback returns to shadow immediately — no process
restart, no queue drain, no warm-up.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum

from general_ludd.routing_roles.small_model_policy import (
    CapabilityEvidence,
    DispatchAction,
    DispatchDecision,
    ModelIdentity,
    SmallModelTaskPolicy,
    SmallModelTaskSpec,
)

_STAGES_ORDERED: tuple[RolloutStage, ...] = ()  # placeholder — built after the class exists


class RolloutStage(StrEnum):
    """Ordered enforcement stages.  Iteration order IS the progression."""

    SHADOW = "shadow"
    CANARY_1 = "canary_1"
    CANARY_10 = "canary_10"
    CANARY_50 = "canary_50"
    FULL = "full"
    ROLLBACK = "rollback"

    @property
    def canary_pct(self) -> int:
        """Percentage of traffic this stage enforces (0=never, 100=always)."""
        _pct: dict[RolloutStage, int] = {
            RolloutStage.SHADOW: 0,
            RolloutStage.CANARY_1: 1,
            RolloutStage.CANARY_10: 10,
            RolloutStage.CANARY_50: 50,
            RolloutStage.FULL: 100,
            RolloutStage.ROLLBACK: 0,
        }
        return _pct[self]


# _STAGES_ORDERED is used by advance() ; built after RolloutStage is defined.
_STAGES_ORDERED = (
    RolloutStage.SHADOW,
    RolloutStage.CANARY_1,
    RolloutStage.CANARY_10,
    RolloutStage.CANARY_50,
    RolloutStage.FULL,
)


@dataclass
class ZDDRollout:
    """Progressive policy enforcement from shadow observation to full rollout.

    Usage::

        rollout = ZDDRollout()

        # Shadow — observe, do not enforce
        decision = rollout.authorize(policy, task, identity, evidence)

        # Advance through canary stages
        rollout.advance()           # → canary_1
        rollout.advance()           # → canary_10
        rollout.advance()           # → canary_50
        rollout.advance()           # → full

        # Emergency rollback
        rollout.rollback()

        # Inspect observations
        for obs in rollout.observations:
            print(obs)
    """

    stage: RolloutStage = RolloutStage.SHADOW
    seed: int | None = None
    _observations: list[dict[str, object]] = field(default_factory=list, repr=False)

    def authorize(
        self,
        policy: SmallModelTaskPolicy,
        task: SmallModelTaskSpec,
        model_identity: ModelIdentity,
        evidence: Sequence[CapabilityEvidence],
    ) -> DispatchDecision:
        """Delegate to the policy, then gate the decision by stage."""
        underlying = policy.authorize(task, model_identity, evidence)
        enforced = self._should_enforce(task)

        observation: dict[str, object] = {
            "stage": self.stage.value,
            "task_id": task.task_id,
            "task_kind": task.task_kind,
            "model_profile_id": model_identity.model_profile_id,
            "underlying_approved": underlying.approved,
            "underlying_reason": underlying.reason,
            "enforced": enforced,
        }
        self._observations.append(observation)

        if not enforced:
            return DispatchDecision(
                action=DispatchAction.ESCALATE,
                task_fingerprint=task.fingerprint,
                reason=f"{self.stage.value}_not_enforcing",
                max_attempts=0,
            )

        return underlying

    def advance(self, target: RolloutStage | None = None) -> None:
        """Move to the next stage, or to *target* if given.

        Raises ValueError if *target* is earlier in the progression than
        the current stage.
        """
        current_idx = self._stage_index()
        if target is None:
            if current_idx + 1 < len(_STAGES_ORDERED):
                self.stage = _STAGES_ORDERED[current_idx + 1]
            return

        target_idx = self._index_of(target)
        if target_idx < current_idx:
            raise ValueError(f"Cannot advance backward from {self.stage.value} to {target.value}")
        if target_idx == current_idx:
            return
        self.stage = target

    def rollback(self) -> None:
        """Immediate rollback to disengaged enforcement."""
        self.stage = RolloutStage.ROLLBACK

    @property
    def observations(self) -> list[dict[str, object]]:
        """Read-only view of recorded authorization observations."""
        return deepcopy(self._observations)

    def clear_observations(self) -> None:
        """Reset the observation log."""
        self._observations.clear()

    def summary(self) -> dict[str, object]:
        """Aggregate counts for the current stage's observations."""
        total = len(self._observations)
        enforced = sum(1 for o in self._observations if o.get("enforced", False))
        escalated = total - enforced
        return {
            "stage": self.stage.value,
            "total_observations": total,
            "enforced_count": enforced,
            "escalated_count": escalated,
        }

    # ------------------------------------------------------------------
    #  internal helpers
    # ------------------------------------------------------------------

    def _should_enforce(self, task: SmallModelTaskSpec) -> bool:
        pct = self.stage.canary_pct
        if pct == 0:
            return False
        if pct == 100:
            return True
        bucket = _hash_bucket(task.task_id, self.seed)
        return bucket < pct

    def _stage_index(self) -> int:
        """Return the position of the current stage in the progression.

        ROLLBACK is not in the ordered progression — return -1 so that
        advance() moves it to index 0 (SHADOW).  SHADOW is index 0.
        """
        if self.stage is RolloutStage.ROLLBACK:
            return -1
        if self.stage is RolloutStage.SHADOW:
            return 0
        try:
            return _STAGES_ORDERED.index(self.stage)
        except ValueError:
            return 0

    @staticmethod
    def _index_of(stage: RolloutStage) -> int:
        if stage is RolloutStage.ROLLBACK or stage is RolloutStage.SHADOW:
            return 0
        try:
            return _STAGES_ORDERED.index(stage)
        except ValueError:
            return 0


def _hash_bucket(task_id: str, seed: int | None = None) -> int:
    """Map a task_id to a deterministic bucket in [0, 99]."""
    payload = task_id.encode()
    if seed is not None:
        payload += f":{seed}".encode()
    digest = hashlib.sha256(payload).digest()
    # Use first 4 bytes as an unsigned 32-bit integer modulo 100.
    val = int.from_bytes(digest[:4], "big")
    return val % 100


__all__ = [
    "RolloutStage",
    "ZDDRollout",
]
