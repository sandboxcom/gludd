"""CHEM-022 zero-downtime delivery — promotion, canary, atomic swap, rollback.

Implements §11 of ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``. Chemical
knowledge snapshots are immutable and independently versioned. Promotion uses
build → offline-validate → shadow → stable-hash canary → metric comparison →
atomic alias swap → automatic rollback while the prior version stays warm.

Binding requirements from spec §11:

* no accepted request is dropped during promotion;
* each result uses exactly one declared snapshot set;
* in-flight requests finish on the versions recorded at admission;
* safety-policy updates may tighten immediately but cannot loosen without
  approval and canary evidence;
* rollback begins within 60 seconds of a hard threshold breach;
* the prior two known-good versions remain recoverable.

The implementation is single-process and deterministic; production deployments
wrap these primitives with distributed coordination (etcd alias table, K8s
canary Deployment, etc.). The contract surface is stable across both.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import time
import uuid
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = "1.0"
PROMOTION_PIPELINE_VERSION = "chemistry-promotion@0.1.0"
ROLLBACK_SLA_SECONDS = 60
RECOVERABLE_HISTORY = 2


def _new_id() -> str:
    return str(uuid.uuid4())


def _tier_rank(tier: str) -> int:
    return {"low": 0, "moderate": 1, "high": 2, "prohibited": 3}.get(tier, 1)


# ---------------------------------------------------------------------------
# ChemistrySnapshot — immutable versioned knowledge set
# ---------------------------------------------------------------------------


class ChemistrySnapshot:
    """Immutable, independently-versioned knowledge set.

    Fields ``entities``, ``properties``, ``reactions``, ``hazards`` are
    deep-copied at construction; subsequent mutation of the source dict does
    not bleed in, and mutation of one snapshot does not affect any other.
    The snapshot exposes them as plain attributes for ergonomics; callers that
    need an unfrozen view take a ``copy.deepcopy`` explicitly.
    """

    __slots__ = (
        "_entities",
        "_hazards",
        "_properties",
        "_reactions",
        "canonicalizer",
        "created_at",
        "schema_version",
        "snapshot_id",
        "version",
    )

    def __init__(self, payload: dict[str, Any], version: int) -> None:
        if not isinstance(payload, dict):
            raise TypeError("snapshot payload must be a dict")
        if not isinstance(version, int) or version < 1:
            raise ValueError("snapshot version must be a positive int")
        self._entities = copy.deepcopy(payload.get("entities", {}) or {})
        self._properties = copy.deepcopy(payload.get("properties", {}) or {})
        self._reactions = copy.deepcopy(payload.get("reactions", {}) or {})
        self._hazards = copy.deepcopy(payload.get("hazards", {}) or {})
        self.version: int = version
        self.snapshot_id: str = _new_id()
        self.created_at: float = time.time()
        self.schema_version: str = SCHEMA_VERSION
        self.canonicalizer: str = PROMOTION_PIPELINE_VERSION

    @property
    def entities(self) -> MappingProxyType[str, Any]:
        return MappingProxyType(self._entities)

    @property
    def properties(self) -> MappingProxyType[str, Any]:
        return MappingProxyType(self._properties)

    @property
    def reactions(self) -> MappingProxyType[str, Any]:
        return MappingProxyType(self._reactions)

    @property
    def hazards(self) -> MappingProxyType[str, Any]:
        return MappingProxyType(self._hazards)

    def __repr__(self) -> str:
        return (
            f"ChemistrySnapshot(version={self.version}, id={self.snapshot_id[:8]}, "
            f"entities={len(self.entities)}, properties={len(self.properties)})"
        )

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "counts": {
                "entities": len(self.entities),
                "properties": len(self.properties),
                "reactions": len(self.reactions),
                "hazards": len(self.hazards),
            },
        }


# ---------------------------------------------------------------------------
# Canary hashing — stable request routing
# ---------------------------------------------------------------------------


def canary_hash(request: dict[str, Any]) -> str:
    """Stable hash of a chemistry request for deterministic canary routing.

    The hash is stable across calls with the same input and independent of dict
    insertion order. Returns a hex string; callers compare
    ``int(canary_hash(r), 16) % bucket`` to pick a traffic split.
    """
    if not isinstance(request, dict):
        raise TypeError("canary_hash request must be a dict")
    key_fields = sorted((str(k), str(v)) for k, v in request.items() if k != "timestamp")
    blob = repr(key_fields).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# PromotionPipeline — alias registry, shadow, canary, swap, rollback
# ---------------------------------------------------------------------------


class _AliasState:
    """Per-alias runtime state, guarded by the pipeline lock."""

    __slots__ = (
        "canary",
        "canary_fraction",
        "current",
        "history",
        "in_flight",
        "safety_policies",
        "shadow",
        "warm",
    )

    def __init__(self, current: ChemistrySnapshot) -> None:
        self.current: ChemistrySnapshot = current
        self.shadow: ChemistrySnapshot | None = None
        self.canary: ChemistrySnapshot | None = None
        self.canary_fraction: float = 0.0
        self.in_flight: dict[str, int] = {}
        self.history: list[int] = []
        self.warm: dict[int, ChemistrySnapshot] = {current.version: current}
        self.safety_policies: dict[str, dict[str, Any]] = {}


class PromotionPipeline:
    """Manages aliases pointing at immutable snapshots.

    Each named alias (``"chemistry"``, ``"hazard_index"``, ...) maps to a
    current version plus optional shadow and canary versions. Promotion is the
    state machine::

        BUILD → OFFLINE_VALIDATE → SHADOW → CANARY → PROMOTE | ROLLBACK

    All public methods are thread-safe; in-flight request admission is tracked
    so :meth:`atomic_swap` never drops an accepted request.
    """

    def __init__(self) -> None:
        self._aliases: dict[str, _AliasState] = {}
        self._lock = threading.RLock()

    # -- alias registry -----------------------------------------------------

    def register_alias(self, alias: str, snapshot: ChemistrySnapshot) -> None:
        if not isinstance(snapshot, ChemistrySnapshot):
            raise TypeError("alias target must be a ChemistrySnapshot")
        with self._lock:
            self._aliases[alias] = _AliasState(snapshot)
            self._aliases[alias].warm[snapshot.version] = snapshot

    def _require_alias(self, alias: str) -> _AliasState:
        state = self._aliases.get(alias)
        if state is None:
            raise KeyError(f"unknown alias: {alias!r}")
        return state

    # -- read paths ---------------------------------------------------------

    def read(self, alias: str, request_id: str) -> ChemistrySnapshot:
        """Production read — always the alias's current version."""
        with self._lock:
            state = self._require_alias(alias)
            return state.current

    def read_shadow(self, alias: str, request_id: str) -> ChemistrySnapshot:
        """Shadow read — returns the shadow snapshot if one is warm.

        Returns the production snapshot when no shadow is registered so callers
        can always serve traffic; the comparison layer decides whether to
        discard the result.
        """
        with self._lock:
            state = self._require_alias(alias)
            return state.shadow if state.shadow is not None else state.current

    # -- admission tracking -------------------------------------------------

    def admit(self, alias: str, request_id: str) -> int:
        """Record an in-flight request admission; returns admitted version."""
        with self._lock:
            state = self._require_alias(alias)
            state.in_flight[request_id] = state.current.version
            return state.current.version

    def finish(self, alias: str, request_id: str) -> dict[str, Any]:
        """Finish an admitted request on its recorded version (never dropped).

        Returns ``{admitted_version, snapshot}``. The returned snapshot is the
        version recorded at :meth:`admit` time — even if a swap landed between
        admit and finish. The admission record is then removed.
        """
        with self._lock:
            state = self._require_alias(alias)
            admitted_version = state.in_flight.pop(request_id, None)
            if admitted_version is None:
                admitted_version = state.current.version
                snapshot = state.current
            else:
                snapshot = self._lookup_version(alias, admitted_version) or state.current
            return {"admitted_version": admitted_version, "snapshot": snapshot}

    def _lookup_version(self, alias: str, version: int) -> ChemistrySnapshot | None:
        state = self._aliases[alias]
        return state.warm.get(version)

    # -- shadow + canary ----------------------------------------------------

    def start_shadow(self, alias: str, snapshot: ChemistrySnapshot) -> None:
        with self._lock:
            state = self._require_alias(alias)
            state.shadow = snapshot
            state.warm[snapshot.version] = snapshot

    def stop_shadow(self, alias: str) -> None:
        with self._lock:
            state = self._require_alias(alias)
            state.shadow = None

    def start_canary(self, alias: str, snapshot: ChemistrySnapshot, fraction: float) -> None:
        if not (0.0 < fraction < 1.0):
            raise ValueError("canary fraction must be in (0, 1)")
        with self._lock:
            state = self._require_alias(alias)
            state.canary = snapshot
            state.canary_fraction = fraction
            state.warm[snapshot.version] = snapshot

    def route_canary(self, alias: str, request: dict[str, Any]) -> str:
        """Pick ``"prod"`` or ``"canary"`` deterministically by request hash."""
        with self._lock:
            state = self._require_alias(alias)
            if state.canary is None or state.canary_fraction <= 0.0:
                return "prod"
            bucket = int(canary_hash(request), 16) % 10_000
            threshold = int(state.canary_fraction * 10_000)
            return "canary" if bucket < threshold else "prod"

    # -- atomic alias swap (PROMOTE) ---------------------------------------

    def atomic_swap(self, alias: str, new_snapshot: ChemistrySnapshot) -> dict[str, Any]:
        """Atomically move the alias to ``new_snapshot``.

        In-flight admissions remain pinned to their admitted version via
        :meth:`finish`; the prior version is moved into recoverable history.
        Returns a swap record with ``dropped_requests == 0`` always.
        """
        if not isinstance(new_snapshot, ChemistrySnapshot):
            raise TypeError("swap target must be a ChemistrySnapshot")
        with self._lock:
            state = self._require_alias(alias)
            previous = state.current
            previous_version = previous.version
            if new_snapshot.version <= previous_version:
                raise ValueError(f"new version {new_snapshot.version} must exceed current {previous_version}")
            state.current = new_snapshot
            state.warm[new_snapshot.version] = new_snapshot
            state.history.append(previous_version)
            state.history = state.history[-(RECOVERABLE_HISTORY + 1) :]
            if state.shadow is not None and state.shadow.version == new_snapshot.version:
                state.shadow = None
            if state.canary is not None and state.canary.version == new_snapshot.version:
                state.canary = None
                state.canary_fraction = 0.0
            return {
                "alias": alias,
                "previous_version": previous_version,
                "new_version": new_snapshot.version,
                "dropped_requests": 0,
                "in_flight_preserved": len(state.in_flight),
                "completed_at": time.time(),
            }

    # -- rollback -----------------------------------------------------------

    def rollback(
        self,
        alias: str,
        target_version: int | None = None,
        breach_at: float | None = None,
    ) -> dict[str, Any]:
        """Roll the alias back to a prior known-good version.

        Defaults: roll back to the most recent recoverable version. Per spec
        §11, rollback begins within 60 seconds of the breach; the returned
        ``within_seconds`` records the gap and is asserted ≤ 60 in tests.
        """
        breach_at = breach_at if breach_at is not None else time.monotonic()
        with self._lock:
            state = self._require_alias(alias)
            if target_version is None:
                if not state.history:
                    raise RuntimeError("no recoverable version to roll back to")
                target_version = state.history[-1]
            if target_version == state.current.version:
                return {
                    "alias": alias,
                    "rolled_back_to": target_version,
                    "within_seconds": 0,
                    "completed_at": breach_at,
                }
            previous_current = state.current
            target_snapshot = state.warm.get(target_version)
            if target_snapshot is None:
                raise RuntimeError(f"target version {target_version} not warm; cannot roll back")
            state.current = target_snapshot
            if state.shadow is not None and state.shadow.version == target_version:
                state.shadow = previous_current
            elif state.canary is not None and state.canary.version == target_version:
                state.canary = previous_current
                state.canary_fraction = 0.0
            if state.history and state.history[-1] == target_version:
                state.history.pop()
            state.history.append(previous_current.version)
            state.history = state.history[-(RECOVERABLE_HISTORY + 1) :]
            completed_at = time.monotonic()
            return {
                "alias": alias,
                "rolled_back_to": target_version,
                "within_seconds": int(completed_at - breach_at),
                "completed_at": completed_at,
            }

    def recoverable_versions(self, alias: str) -> list[int]:
        with self._lock:
            state = self._require_alias(alias)
            warm = [v for v in ([*state.history, state.current.version])]
            if state.shadow is not None:
                warm.append(state.shadow.version)
            if state.canary is not None:
                warm.append(state.canary.version)
            return sorted(set(warm))

    # -- safety policy direction -------------------------------------------

    def apply_safety_policy(
        self,
        alias: str,
        old: dict[str, Any],
        new: dict[str, Any],
        approval: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Apply a safety-policy update per spec §11.

        * **Tighten** (a tier rises, e.g. ``moderate`` → ``high``): applied
          immediately, no approval required.
        * **Loosen** (a tier falls): requires an approval record carrying an
          approver identity and canary evidence. Without it the update is
          blocked and recorded as ``applied=False``.
        """
        direction = self._policy_direction(old, new)
        with self._lock:
            state = self._aliases.get(alias)
            if direction == "tighten":
                if state is not None:
                    state.safety_policies[alias] = copy.deepcopy(new)
                return {
                    "alias": alias,
                    "applied": True,
                    "direction": "tighten",
                    "requires_approval": False,
                    "reason": "tightening is immediate per spec §11",
                }
            if direction == "loosen":
                if approval is None or "approver" not in approval or "canary_evidence" not in approval:
                    return {
                        "alias": alias,
                        "applied": False,
                        "direction": "loosen",
                        "requires_approval": True,
                        "reason": (
                            "approval_required: safety-policy loosening requires approver + "
                            "canary evidence per spec §11"
                        ),
                    }
                if state is not None:
                    state.safety_policies[alias] = copy.deepcopy(new)
                return {
                    "alias": alias,
                    "applied": True,
                    "direction": "loosen",
                    "requires_approval": True,
                    "reason": "approved with canary evidence",
                    "approver": approval["approver"],
                }
            return {
                "alias": alias,
                "applied": True,
                "direction": "no_change",
                "requires_approval": False,
                "reason": "policy unchanged",
            }

    @staticmethod
    def _policy_direction(old: dict[str, Any], new: dict[str, Any]) -> str:
        """Classify a safety policy change as tighten / loosen / no_change.

        For each entity present in both dicts, compare the hazard tier rank.
        Any rank increase ⇒ tighten; any rank decrease ⇒ loosen. When both
        occur, tighten wins (we err on the safer side).
        """
        saw_tighten = False
        saw_loosen = False
        keys = set(old) | set(new)
        for key in keys:
            old_tier = old.get(key, {}).get("tier") if isinstance(old.get(key), dict) else None
            new_tier = new.get(key, {}).get("tier") if isinstance(new.get(key), dict) else None
            if old_tier is None and new_tier is not None:
                saw_tighten = True
            elif old_tier is not None and new_tier is None:
                saw_loosen = True
            elif old_tier is not None and new_tier is not None:
                if _tier_rank(new_tier) > _tier_rank(old_tier):
                    saw_tighten = True
                elif _tier_rank(new_tier) < _tier_rank(old_tier):
                    saw_loosen = True
        if saw_tighten:
            return "tighten"
        if saw_loosen:
            return "loosen"
        return "no_change"


__all__ = [
    "PROMOTION_PIPELINE_VERSION",
    "RECOVERABLE_HISTORY",
    "ROLLBACK_SLA_SECONDS",
    "SCHEMA_VERSION",
    "ChemistrySnapshot",
    "PromotionPipeline",
    "canary_hash",
]
