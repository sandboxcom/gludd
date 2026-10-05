"""Pure, collection-local idle-retention planning for Azure Container Apps."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

_DIGEST = re.compile(r"[0-9a-f]{64}")


class AzureRetentionPreset(StrEnum):
    """Operator-visible cost and latency posture."""

    ALWAYS_DESTROY = "always_destroy"
    ZERO_COST_ONLY = "zero_cost_only"
    BALANCED = "balanced"
    LATENCY_FIRST = "latency_first"


class AzureRetentionLayerKind(StrEnum):
    """Container Apps layers whose idle lifecycle is independent."""

    MANAGED_ENVIRONMENT = "managed_environment"
    CONTAINER_APP = "container_app"


class AzureRetentionDisposition(StrEnum):
    """Disposition for one idle layer."""

    RETAIN = "retain"
    DESTROY = "destroy"


class AzureRetentionReason(StrEnum):
    """Content-free reason suitable for durable facts and events."""

    SELECTED = "selected"
    PRESET_DESTROY = "preset_destroy"
    PRESET_EXCLUDES_COST = "preset_excludes_cost"
    PRICE_EVIDENCE_MISSING = "price_evidence_missing"
    PRICE_EVIDENCE_STALE = "price_evidence_stale"
    LATENCY_EVIDENCE_STALE = "latency_evidence_stale"
    EVIDENCE_FROM_FUTURE = "evidence_from_future"
    IDLE_COMPUTE_FORBIDDEN = "idle_compute_forbidden"
    IDLE_ACTIVATION_POSSIBLE = "idle_activation_possible"
    DEMAND_OUTSIDE_WINDOW = "demand_outside_window"
    BUDGET_TRADEOFF = "budget_tradeoff"


def _aware_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _bounded_int(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _positive_finite(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and > 0")


@dataclass(frozen=True, slots=True)
class AzureProvisioningLatencyEvidence:
    """Measured provisioning latency used as a retention benefit."""

    p50_seconds: float
    p95_seconds: float
    sample_count: int
    observed_at: datetime

    def __post_init__(self) -> None:
        _positive_finite("p50_seconds", self.p50_seconds)
        _positive_finite("p95_seconds", self.p95_seconds)
        if self.p95_seconds < self.p50_seconds:
            raise ValueError("p95_seconds must be >= p50_seconds")
        _bounded_int("sample_count", self.sample_count, minimum=1)
        object.__setattr__(self, "observed_at", _aware_utc("observed_at", self.observed_at))


@dataclass(frozen=True, slots=True)
class AzureIdleRetentionPolicy:
    """Explicit operator ceilings for one idle-retention decision."""

    preset: AzureRetentionPreset
    max_idle_hourly_cost_microusd: int
    max_idle_monthly_cost_microusd: int
    max_retention_cost_microusd: int
    max_retention_seconds: int
    max_price_age_seconds: int
    max_latency_age_seconds: int
    max_cost_per_saved_hour_microusd: int

    def __post_init__(self) -> None:
        if not isinstance(self.preset, AzureRetentionPreset):
            raise ValueError("preset must be an AzureRetentionPreset")
        for name in (
            "max_idle_hourly_cost_microusd",
            "max_idle_monthly_cost_microusd",
            "max_retention_cost_microusd",
            "max_cost_per_saved_hour_microusd",
        ):
            _bounded_int(name, getattr(self, name), minimum=0)
        for name in (
            "max_retention_seconds",
            "max_price_age_seconds",
            "max_latency_age_seconds",
        ):
            _bounded_int(name, getattr(self, name), minimum=1)


@dataclass(frozen=True, slots=True)
class AzureRetentionLayerDecision:
    """One deterministic layer decision."""

    kind: AzureRetentionLayerKind
    disposition: AzureRetentionDisposition
    reason: AzureRetentionReason


@dataclass(frozen=True, slots=True)
class AzureIdleRetentionPlan:
    """Bounded Container Apps retained-state frontier."""

    decisions: tuple[AzureRetentionLayerDecision, ...]
    retained_layers: tuple[AzureRetentionLayerKind, ...]
    destroyed_layers: tuple[AzureRetentionLayerKind, ...]
    retention_seconds: int
    reconcile_at: datetime
    hourly_cost_microusd: int
    monthly_cost_microusd: int
    projected_cost_microusd: int
    p95_seconds_saved: float
    scope_digest: str
    plan_digest: str


def _exclusion_reason(
    *,
    kind: AzureRetentionLayerKind,
    policy: AzureIdleRetentionPolicy,
    now: datetime,
    latency: AzureProvisioningLatencyEvidence,
    horizon_seconds: int,
    zero_cost_evidenced: bool,
    min_replicas: int,
    activation_blocked_when_idle: bool,
) -> AzureRetentionReason | None:
    if policy.preset is AzureRetentionPreset.ALWAYS_DESTROY:
        return AzureRetentionReason.PRESET_DESTROY
    if horizon_seconds == 0:
        return AzureRetentionReason.DEMAND_OUTSIDE_WINDOW
    if kind is AzureRetentionLayerKind.CONTAINER_APP and min_replicas:
        return AzureRetentionReason.IDLE_COMPUTE_FORBIDDEN
    if kind is AzureRetentionLayerKind.CONTAINER_APP and not activation_blocked_when_idle:
        return AzureRetentionReason.IDLE_ACTIVATION_POSSIBLE
    if not zero_cost_evidenced:
        return AzureRetentionReason.PRICE_EVIDENCE_MISSING
    latency_age = (now - latency.observed_at).total_seconds()
    if latency_age < 0:
        return AzureRetentionReason.EVIDENCE_FROM_FUTURE
    if latency_age > policy.max_latency_age_seconds:
        return AzureRetentionReason.LATENCY_EVIDENCE_STALE
    return None


def _plan_digest(
    *,
    policy: AzureIdleRetentionPolicy,
    scope_digest: str,
    now: datetime,
    horizon_seconds: int,
    decisions: tuple[AzureRetentionLayerDecision, ...],
) -> str:
    payload = {
        "decisions": [
            (decision.kind.value, decision.disposition.value, decision.reason.value)
            for decision in decisions
        ],
        "horizon_seconds": horizon_seconds,
        "now": now.isoformat(),
        "scope_digest": scope_digest,
        "policy": {
            "preset": policy.preset.value,
            "hourly": policy.max_idle_hourly_cost_microusd,
            "monthly": policy.max_idle_monthly_cost_microusd,
            "total": policy.max_retention_cost_microusd,
            "retention": policy.max_retention_seconds,
            "price_age": policy.max_price_age_seconds,
            "latency_age": policy.max_latency_age_seconds,
            "value": policy.max_cost_per_saved_hour_microusd,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def plan_container_apps_idle_retention(
    *,
    policy: AzureIdleRetentionPolicy,
    scope_digest: str,
    now: datetime,
    environment_latency: AzureProvisioningLatencyEvidence,
    app_latency: AzureProvisioningLatencyEvidence,
    min_replicas: int,
    activation_blocked_when_idle: bool,
    has_dedicated_profiles: bool,
    has_private_endpoint: bool,
    has_planned_maintenance: bool,
    has_paid_logging: bool,
    runnable_todo_count: int,
    expected_next_demand_seconds: int | None,
) -> AzureIdleRetentionPlan:
    """Select the safe reusable layers without importing Gludd core."""
    if _DIGEST.fullmatch(scope_digest) is None:
        raise ValueError("scope_digest must be a lowercase SHA-256 digest")
    current = _aware_utc("now", now)
    _bounded_int("runnable_todo_count", runnable_todo_count, minimum=0)
    if runnable_todo_count:
        raise ValueError("idle retention requires an empty durable todo")
    if expected_next_demand_seconds is not None:
        _bounded_int("expected_next_demand_seconds", expected_next_demand_seconds, minimum=1)
    _bounded_int("min_replicas", min_replicas, minimum=0)
    flags = (
        activation_blocked_when_idle,
        has_dedicated_profiles,
        has_private_endpoint,
        has_planned_maintenance,
        has_paid_logging,
    )
    if not all(isinstance(flag, bool) for flag in flags):
        raise ValueError("Container Apps retention flags must be boolean")
    horizon = policy.max_retention_seconds
    if expected_next_demand_seconds is not None:
        horizon = (
            expected_next_demand_seconds
            if expected_next_demand_seconds <= policy.max_retention_seconds
            else 0
        )
    kinds = (
        AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
        AzureRetentionLayerKind.CONTAINER_APP,
    )
    latencies = (environment_latency, app_latency)
    zero_cost = (not any(flags[1:]), min_replicas == 0)
    exclusions = {
        kind: _exclusion_reason(
            kind=kind,
            policy=policy,
            now=current,
            latency=latency,
            horizon_seconds=horizon,
            zero_cost_evidenced=evidenced,
            min_replicas=min_replicas,
            activation_blocked_when_idle=activation_blocked_when_idle,
        )
        for kind, latency, evidenced in zip(kinds, latencies, zero_cost, strict=True)
    }
    retained: tuple[AzureRetentionLayerKind, ...] = tuple(
        kind
        for kind in kinds
        if exclusions[kind] is None
        and (
            kind is AzureRetentionLayerKind.MANAGED_ENVIRONMENT
            or exclusions[AzureRetentionLayerKind.MANAGED_ENVIRONMENT] is None
        )
    )
    decisions = tuple(
        AzureRetentionLayerDecision(
            kind=kind,
            disposition=(
                AzureRetentionDisposition.RETAIN
                if kind in retained
                else AzureRetentionDisposition.DESTROY
            ),
            reason=(
                AzureRetentionReason.SELECTED
                if kind in retained
                else exclusions[kind] or AzureRetentionReason.BUDGET_TRADEOFF
            ),
        )
        for kind in kinds
    )
    retention_seconds = horizon if retained else 0
    saved = sum(
        latency.p95_seconds
        for kind, latency in zip(kinds, latencies, strict=True)
        if kind in retained
    )
    return AzureIdleRetentionPlan(
        decisions=decisions,
        retained_layers=retained,
        destroyed_layers=tuple(kind for kind in kinds if kind not in retained),
        retention_seconds=retention_seconds,
        reconcile_at=current + timedelta(seconds=retention_seconds),
        hourly_cost_microusd=0,
        monthly_cost_microusd=0,
        projected_cost_microusd=0,
        p95_seconds_saved=saved,
        scope_digest=scope_digest,
        plan_digest=_plan_digest(
            policy=policy,
            scope_digest=scope_digest,
            now=current,
            horizon_seconds=retention_seconds,
            decisions=decisions,
        ),
    )


__all__ = (
    "AzureIdleRetentionPlan",
    "AzureIdleRetentionPolicy",
    "AzureProvisioningLatencyEvidence",
    "AzureRetentionPreset",
    "plan_container_apps_idle_retention",
)
