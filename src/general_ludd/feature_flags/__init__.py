"""Feature flag and rollout system.

Provides boolean and percentage-based feature flags with:
- Per-flag default values and overrides
- Percentage rollouts keyed on a stable entity identifier
- Attribute-targeting rules (user ID, group, environment, etc.)
- Flag dependency chains (flag A requires flag B enabled)
- Thread-safe evaluation with consistent hashing
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any

SCHEMA_VERSION = "1.0"


def _stable_hash(key: str, seed: str = "") -> int:
    """Deterministic integer hash in [0, 10_000) for percentage splitting."""
    raw = f"{seed}:{key}".encode()
    return int(hashlib.sha256(raw).hexdigest()[-8:], 16) % 10_000


class TargetingRule:
    """A rule that targets a specific set of entities.

    ``attribute`` is matched against ``values`` using the given ``operator``.
    Supported operators: ``"eq"``, ``"in"``, ``"neq"``, ``"regex"``.
    When ``invert`` is True, a match means the rule *excludes* the entity.
    """

    def __init__(
        self,
        attribute: str,
        values: Any,
        operator: str = "eq",
        invert: bool = False,
    ) -> None:
        if operator not in ("eq", "in", "neq", "regex"):
            raise ValueError(f"Unsupported operator: {operator}")
        self.attribute = attribute
        self.values = values
        self.operator = operator
        self.invert = invert

    def matches(self, entity: dict[str, Any]) -> bool:
        val = entity.get(self.attribute)
        result: bool
        if self.operator == "eq":
            result = val == self.values
        elif self.operator == "neq":
            result = val != self.values
        elif self.operator == "in":
            result = val in self.values if isinstance(self.values, (list, tuple, set)) else val == self.values
        elif self.operator == "regex":
            import re

            result = bool(re.search(str(self.values), str(val))) if val is not None else False
        else:
            result = False
        return not result if self.invert else result


class FeatureFlag:
    """A named feature flag with default value and optional rollout configuration."""

    def __init__(
        self,
        name: str,
        default: bool = False,
        description: str = "",
        rollout_percentage: float = 100.0,
        targeting_rules: list[TargetingRule] | None = None,
        dependencies: list[str] | None = None,
        depends_on_all: bool = True,
    ) -> None:
        if not name or not name.isidentifier():
            raise ValueError(f"flag name must be a valid identifier, got {name!r}")
        if rollout_percentage < 0.0 or rollout_percentage > 100.0:
            raise ValueError(f"rollout_percentage must be in [0, 100], got {rollout_percentage}")
        self.name = name
        self.default = default
        self.description = description
        self.rollout_percentage = rollout_percentage
        self.targeting_rules = targeting_rules or []
        self.dependencies = dependencies or []
        self.depends_on_all = depends_on_all


class FlagEvaluationResult:
    """The result of evaluating a single flag for a given entity."""

    def __init__(
        self,
        flag_name: str,
        enabled: bool,
        reason: str,
        depended_on: list[str] | None = None,
    ) -> None:
        self.flag_name = flag_name
        self.enabled = enabled
        self.reason = reason
        self.depended_on = depended_on or []


class FlagEvaluator:
    """Thread-safe feature flag evaluator.

    Evaluates flags against an entity dict, considering:
    1. Targeting rules (explicit match → enable/disable)
    2. Percentage rollout (hash-based deterministic split)
    3. Default value (fallback)
    4. Dependency chains (flag A requires flag B enabled)
    """

    def __init__(self, flags: list[FeatureFlag]) -> None:
        self._flags: dict[str, FeatureFlag] = {}
        self._lock = threading.RLock()
        self.register(*flags)

    def register(self, *flags: FeatureFlag) -> None:
        with self._lock:
            for flag in flags:
                self._flags[flag.name] = flag

    def unregister(self, name: str) -> None:
        with self._lock:
            self._flags.pop(name, None)

    def get_flag(self, name: str) -> FeatureFlag | None:
        with self._lock:
            return self._flags.get(name)

    def list_flags(self) -> list[str]:
        with self._lock:
            return sorted(self._flags.keys())

    def evaluate(
        self,
        flag_name: str,
        entity: dict[str, Any],
        entity_id: str = "",
    ) -> FlagEvaluationResult:
        """Evaluate a single flag for the given entity.

        Returns ``FlagEvaluationResult`` with enabled/disabled status and
        a human-readable reason string.
        """
        with self._lock:
            flag = self._flags.get(flag_name)
        if flag is None:
            return FlagEvaluationResult(flag_name, False, f"flag {flag_name!r} not registered")

        sid = entity_id or entity.get("id", str(hash(frozenset(entity.items()))))

        # Check targeting rules first — matching means the entity is targeted
        targeting_matched = False
        for rule in flag.targeting_rules:
            if rule.matches(entity):
                targeting_matched = True
                break

        if targeting_matched:
            return FlagEvaluationResult(
                flag_name,
                True,
                f"targeting rule matched: {flag.targeting_rules[0].attribute} "
                f"{flag.targeting_rules[0].operator} {flag.targeting_rules[0].values}",
            )


        # If targeting rules exist but none matched, entity is excluded
        if flag.targeting_rules:
            return FlagEvaluationResult(
                flag_name,
                False,
                "no targeting rules matched",
            )

        # Percentage rollout
        if flag.rollout_percentage < 100.0:
            bucket = _stable_hash(sid, seed=flag.name)
            threshold = int(flag.rollout_percentage * 100)
            if bucket >= threshold:
                return FlagEvaluationResult(
                    flag_name,
                    False,
                    f"rollout disabled: bucket {bucket} >= threshold {threshold} ({flag.rollout_percentage}% rollout)",
                )

        # Check dependencies
        if flag.dependencies:
            depended_on: list[str] = []
            all_enabled = True
            for dep_name in flag.dependencies:
                dep_result = self.evaluate(dep_name, entity, entity_id)
                depended_on.append(dep_name)
                if not dep_result.enabled:
                    all_enabled = False
                    if flag.depends_on_all:
                        return FlagEvaluationResult(
                            flag_name,
                            False,
                            f"dependency {dep_name!r} not enabled (depends_on_all=True)",
                            depended_on=depended_on,
                        )
            if not flag.depends_on_all and not all_enabled:
                return FlagEvaluationResult(
                    flag_name,
                    False,
                    "no dependencies enabled (depends_on_all=False)",
                    depended_on=depended_on,
                )

        # All conditions met — flag is enabled
        if flag.rollout_percentage < 100.0 or flag.dependencies or flag.targeting_rules:
            return FlagEvaluationResult(
                flag_name,
                True,
                "all conditions met: flag enabled",
            )
        return FlagEvaluationResult(
            flag_name,
            flag.default,
            f"default value: {flag.default}",
        )

    def evaluate_all(self, entity: dict[str, Any], entity_id: str = "") -> dict[str, FlagEvaluationResult]:
        """Evaluate all registered flags for the given entity."""
        with self._lock:
            names = sorted(self._flags.keys())
        return {name: self.evaluate(name, entity, entity_id) for name in names}

    def is_enabled(self, flag_name: str, entity: dict[str, Any], entity_id: str = "") -> bool:
        """Convenience: return True if the flag is enabled for this entity."""
        return self.evaluate(flag_name, entity, entity_id).enabled

    def percentage_rollout_for_entity(self, flag_name: str, entity_id: str) -> bool:
        """Check if an entity falls within the rollout percentage for a flag.

        Returns True even if targeting rules would otherwise exclude the entity
        — this method checks ONLY the percentage split, not targeting or deps.
        """
        with self._lock:
            flag = self._flags.get(flag_name)
        if flag is None:
            return False
        bucket = _stable_hash(entity_id, seed=flag.name)
        threshold = int(flag.rollout_percentage * 100)
        return bucket < threshold

    def resolve_chain(self, flag_name: str, entity: dict[str, Any], entity_id: str = "") -> list[FlagEvaluationResult]:
        """Resolve a flag and all its transitive dependencies in order.

        Returns the full chain from root dependencies up to the requested flag.
        """
        with self._lock:
            flag = self._flags.get(flag_name)
        if flag is None:
            return []
        results: list[FlagEvaluationResult] = []
        seen: set[str] = set()

        def _resolve(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            with self._lock:
                f = self._flags.get(name)
            if f is None:
                return
            for dep in f.dependencies:
                _resolve(dep)
            results.append(self.evaluate(name, entity, entity_id))

        for dep in flag.dependencies:
            _resolve(dep)
        _resolve(flag_name)
        return results
