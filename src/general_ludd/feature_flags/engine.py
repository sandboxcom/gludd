"""High-level feature flag engine with overrides, gradual rollout, and audit.

Extends the core feature flag system (__init__.py) with:
- Multi-level flag overrides (user, group, global)
- Gradual rollout with automatic stage progression (canary -> beta -> stable)
- Audit trail logging for flag evaluations
- Batch warm-up evaluation for cold-start optimization
"""

from __future__ import annotations

import enum
import threading
import time
from typing import Any

from general_ludd.feature_flags import FeatureFlag, FlagEvaluationResult, FlagEvaluator


class OverrideLevel(enum.IntEnum):
    """Rank a flag override from global to user-specific."""

    GLOBAL = 0
    GROUP = 1
    USER = 2


class Override:
    """Represent a scoped, auditable feature flag override."""

    def __init__(
        self,
        flag_name: str,
        value: bool,
        level: OverrideLevel,
        target: str = "",
        reason: str = "",
    ) -> None:
        """Initialize an override for a flag and optional target."""
        self.flag_name = flag_name
        self.value = value
        self.level = level
        self.target = target
        self.reason = reason
        self.created_at = time.time()

    def matches(self, flag_name: str, entity: dict[str, Any]) -> bool:
        """Return whether this override applies to the flag and entity."""
        if self.flag_name != flag_name:
            return False
        if self.level == OverrideLevel.GLOBAL:
            return True
        if self.level == OverrideLevel.GROUP:
            return entity.get("group") == self.target
        if self.level == OverrideLevel.USER:
            return entity.get("id") == self.target or entity.get("user_id") == self.target
        return False


class OverrideStore:
    """Store and resolve feature flag overrides by precedence."""

    def __init__(self) -> None:
        """Initialize an empty, thread-safe override store."""
        self._overrides: dict[str, list[Override]] = {}
        self._lock = threading.RLock()

    def set(self, override: Override) -> None:
        """Add an override and preserve precedence order."""
        with self._lock:
            self._overrides.setdefault(override.flag_name, []).append(override)
            self._overrides[override.flag_name].sort(key=lambda o: o.level.value, reverse=True)

    def remove(self, flag_name: str, target: str | None = None) -> int:
        """Remove matching overrides and return the number removed."""
        with self._lock:
            if flag_name not in self._overrides:
                return 0
            if target is None:
                removed = len(self._overrides.pop(flag_name, []))
                return removed
            before = len(self._overrides[flag_name])
            self._overrides[flag_name] = [o for o in self._overrides[flag_name] if o.target != target]
            if not self._overrides[flag_name]:
                del self._overrides[flag_name]
            return before - len(self._overrides.get(flag_name, []))

    def resolve(self, flag_name: str, entity: dict[str, Any]) -> bool | None:
        """Return the highest-precedence matching value, if any."""
        with self._lock:
            candidates = self._overrides.get(flag_name, [])
            for override in candidates:
                if override.matches(flag_name, entity):
                    return override.value
        return None

    def list_for_flag(self, flag_name: str) -> list[Override]:
        """Return a copy of the overrides registered for a flag."""
        with self._lock:
            return list(self._overrides.get(flag_name, []))

    def clear(self) -> None:
        """Remove every registered override."""
        with self._lock:
            self._overrides.clear()


class RolloutStage(enum.Enum):
    """Name the supported gradual-rollout stages."""

    CANARY = "canary"
    BETA = "beta"
    STABLE = "stable"


class GradualRollout:
    """Manage staged percentage rollout for one feature flag."""

    def __init__(
        self,
        flag_name: str,
        stages: list[tuple[RolloutStage, float]],
        progression_condition: str = "days_elapsed",
        progression_value: float = 7.0,
    ) -> None:
        """Initialize a rollout with at least one ordered stage."""
        if not stages:
            raise ValueError("at least one rollout stage required")
        self.flag_name = flag_name
        self.stages = stages
        self.current_stage_index = 0
        self.started_at = time.time()
        self.progression_condition = progression_condition
        self.progression_value = progression_value
        self._lock = threading.RLock()

    @property
    def current_stage(self) -> RolloutStage:
        """Return the active rollout stage."""
        with self._lock:
            return self.stages[self.current_stage_index][0]

    @property
    def current_percentage(self) -> float:
        """Return the active rollout percentage."""
        with self._lock:
            return self.stages[self.current_stage_index][1]

    def advance(self) -> bool:
        """Advance an elapsed-time rollout when its threshold is met."""
        with self._lock:
            if self.current_stage_index + 1 >= len(self.stages):
                return False
            elapsed = time.time() - self.started_at
            if self.progression_condition == "days_elapsed":
                required = self.progression_value * 86400
                if elapsed < required:
                    return False
                self.current_stage_index += 1
                self.started_at = time.time()
                return True
            if self.progression_condition == "manual":
                return False
            return False

    def force_stage(self, stage: RolloutStage) -> None:
        """Select a configured rollout stage immediately."""
        with self._lock:
            for idx, (s, _) in enumerate(self.stages):
                if s == stage:
                    self.current_stage_index = idx
                    self.started_at = time.time()
                    return
            raise ValueError(f"stage {stage} not in rollout stages")

    def to_feature_flag(self, default: bool = False, **kwargs: Any) -> FeatureFlag:
        """Build a feature flag at the current rollout percentage."""
        return FeatureFlag(
            name=self.flag_name,
            default=default,
            rollout_percentage=self.current_percentage,
            **kwargs,
        )


class AuditEntry:
    """Record one feature flag evaluation outcome."""

    def __init__(
        self,
        flag_name: str,
        entity_id: str,
        enabled: bool,
        reason: str,
        timestamp: float | None = None,
    ) -> None:
        """Initialize an immutable-by-convention audit record."""
        self.flag_name = flag_name
        self.entity_id = entity_id
        self.enabled = enabled
        self.reason = reason
        self.timestamp = timestamp or time.time()


class FlagAuditLog:
    """Retain a bounded, queryable history of flag evaluations."""

    def __init__(self, max_entries: int = 10000) -> None:
        """Initialize a bounded, thread-safe audit log."""
        self._entries: list[AuditEntry] = []
        self._max_entries = max_entries
        self._lock = threading.RLock()

    def record(self, entry: AuditEntry) -> None:
        """Append an entry and trim records beyond the configured bound."""
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries :]

    def query(
        self,
        flag_name: str | None = None,
        entity_id: str | None = None,
        since: float | None = None,
    ) -> list[AuditEntry]:
        """Return entries matching all supplied filters."""
        with self._lock:
            results = list(self._entries)
        if flag_name is not None:
            results = [e for e in results if e.flag_name == flag_name]
        if entity_id is not None:
            results = [e for e in results if e.entity_id == entity_id]
        if since is not None:
            results = [e for e in results if e.timestamp >= since]
        return results

    def stats(self, flag_name: str | None = None) -> dict[str, Any]:
        """Summarize enabled and disabled evaluations."""
        entries = self.query(flag_name=flag_name)
        if not entries:
            return {"total": 0, "enabled": 0, "disabled": 0, "ratio": 0.0}
        enabled = sum(1 for e in entries if e.enabled)
        total = len(entries)
        return {
            "total": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "ratio": enabled / total if total else 0.0,
        }

    def clear(self) -> None:
        """Remove every retained audit entry."""
        with self._lock:
            self._entries.clear()


class FlagEngine:
    """Coordinate evaluation, overrides, rollouts, and audit logging."""

    def __init__(
        self,
        evaluator: FlagEvaluator | None = None,
        overrides: OverrideStore | None = None,
        audit_log: FlagAuditLog | None = None,
    ) -> None:
        """Initialize the engine with injectable stateful collaborators."""
        self.evaluator = evaluator or FlagEvaluator([])
        self.overrides = overrides or OverrideStore()
        self.audit_log = audit_log or FlagAuditLog()
        self._rollouts: dict[str, GradualRollout] = {}
        self._lock = threading.RLock()

    def register(self, *flags: FeatureFlag) -> None:
        """Register feature flags with the underlying evaluator."""
        self.evaluator.register(*flags)

    def register_rollout(self, rollout: GradualRollout) -> None:
        """Register or replace a gradual rollout."""
        with self._lock:
            self._rollouts[rollout.flag_name] = rollout

    def advance_rollouts(self) -> dict[str, bool]:
        """Advance eligible rollouts and synchronize flag percentages."""
        with self._lock:
            results: dict[str, bool] = {}
            for name, rollout in self._rollouts.items():
                results[name] = rollout.advance()
                if results[name]:
                    flag = self.evaluator.get_flag(name)
                    if flag is not None:
                        flag.rollout_percentage = rollout.current_percentage
            return results

    def evaluate(
        self,
        flag_name: str,
        entity: dict[str, Any],
        entity_id: str = "",
    ) -> FlagEvaluationResult:
        """Evaluate a flag with override and rollout precedence."""
        eid = entity_id or entity.get("id", "")

        override_result = self.overrides.resolve(flag_name, entity)
        if override_result is not None:
            result = FlagEvaluationResult(
                flag_name,
                override_result,
                f"override: {flag_name} = {override_result}",
            )
        else:
            rollout = self._rollouts.get(flag_name)
            if rollout is not None:
                flag = rollout.to_feature_flag(default=True)
                temp_evaluator = FlagEvaluator([flag])
                result = temp_evaluator.evaluate(flag_name, entity, entity_id)
                if result.enabled:
                    result = FlagEvaluationResult(
                        flag_name,
                        True,
                        f"rollout {rollout.current_stage.value}: flag enabled "
                        f"at {rollout.current_percentage}%",
                    )
            else:
                result = self.evaluator.evaluate(flag_name, entity, entity_id)

        self.audit_log.record(AuditEntry(flag_name, eid, result.enabled, result.reason))
        return result

    def evaluate_all(self, entity: dict[str, Any], entity_id: str = "") -> dict[str, FlagEvaluationResult]:
        """Evaluate every registered flag and rollout for one entity."""
        eid = entity_id or entity.get("id", "")
        results: dict[str, FlagEvaluationResult] = {}
        for name in self.evaluator.list_flags():
            results[name] = self.evaluate(name, entity, eid)
        with self._lock:
            for name in self._rollouts:
                if name not in results:
                    results[name] = self.evaluate(name, entity, eid)
        return results

    def is_enabled(self, flag_name: str, entity: dict[str, Any], entity_id: str = "") -> bool:
        """Return the enabled decision for a flag evaluation."""
        return self.evaluate(flag_name, entity, entity_id).enabled

    def warm_up(self, entity_batch: list[dict[str, Any]]) -> dict[str, dict[str, bool]]:
        """Evaluate all registered flags for a batch of entities."""
        result: dict[str, dict[str, bool]] = {}
        for entity in entity_batch:
            eid = entity.get("id", str(hash(frozenset(entity.items()))))
            result[eid] = {}
            for name in self.evaluator.list_flags():
                result[eid][name] = self.is_enabled(name, entity)
        return result
