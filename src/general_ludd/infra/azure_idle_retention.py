"""Fail-closed, evidence-priced retention of idle Azure resource layers.

The planner separates active compute from reusable control-plane and cache
layers. It never guesses an Azure price: each candidate needs current cost and
measured latency evidence, and an empty durable todo forbids retained replicas.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from general_ludd.infra.azure_retail_pricing import AzureRetailMeter

_MONTHLY_HOURS = 730
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class AzureRetentionPreset(StrEnum):
    """Operator-visible cost/latency posture."""

    ALWAYS_DESTROY = "always_destroy"
    ZERO_COST_ONLY = "zero_cost_only"
    BALANCED = "balanced"
    LATENCY_FIRST = "latency_first"


class AzureRetentionLayerKind(StrEnum):
    """Bounded reusable layers considered by the optimizer."""

    RESOURCE_GROUP = "resource_group"
    MANAGED_ENVIRONMENT = "managed_environment"
    CONTAINER_APP = "container_app"
    CONTAINER_REGISTRY = "container_registry"
    MODEL_CACHE = "model_cache"
    DEDICATED_PROFILE = "dedicated_profile"
    PRIVATE_ENDPOINT = "private_endpoint"
    LOG_ANALYTICS = "log_analytics"


_LAYER_ORDER = {kind: index for index, kind in enumerate(AzureRetentionLayerKind)}


class AzureRetentionEvidenceSource(StrEnum):
    """Reviewed sources allowed to justify an idle price."""

    AZURE_RETAIL_PRICES = "azure_retail_prices"
    AZURE_BILLING_CONTRACT = "azure_billing_contract"
    AZURE_COST_MANAGEMENT = "azure_cost_management"


class AzureRetentionDisposition(StrEnum):
    """One resource-layer outcome."""

    RETAIN = "retain"
    DESTROY = "destroy"


class AzureRetentionReason(StrEnum):
    """Content-free reason safe for events and durable evidence."""

    SELECTED = "selected"
    PRESET_DESTROY = "preset_destroy"
    PRESET_EXCLUDES_COST = "preset_excludes_cost"
    PRICE_EVIDENCE_MISSING = "price_evidence_missing"
    PRICE_EVIDENCE_STALE = "price_evidence_stale"
    LATENCY_EVIDENCE_MISSING = "latency_evidence_missing"
    LATENCY_EVIDENCE_STALE = "latency_evidence_stale"
    EVIDENCE_FROM_FUTURE = "evidence_from_future"
    IDLE_COMPUTE_FORBIDDEN = "idle_compute_forbidden"
    IDLE_ACTIVATION_POSSIBLE = "idle_activation_possible"
    DEMAND_OUTSIDE_WINDOW = "demand_outside_window"
    VALUE_CEILING_EXCEEDED = "value_ceiling_exceeded"
    BUDGET_TRADEOFF = "budget_tradeoff"


class AzureRetentionTraceEvent(StrEnum):
    """Observable pure-planner transitions."""

    EVALUATION_STARTED = "evaluation_started"
    PLAN_SELECTED = "plan_selected"


def _aware_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    offset = value.utcoffset()
    if offset is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _bounded_int(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _positive_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and > 0")
    if not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")


@dataclass(frozen=True, slots=True)
class AzureIdleCostEvidence:
    """A current exact idle hourly price, including an evidenced zero."""

    hourly_cost_microusd: int
    observed_at: datetime
    source: AzureRetentionEvidenceSource
    meter_ids: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        """Validate money, timestamp, source, and optional Azure meter IDs."""
        _bounded_int("hourly_cost_microusd", self.hourly_cost_microusd, minimum=0)
        object.__setattr__(
            self,
            "observed_at",
            _aware_utc("observed_at", self.observed_at),
        )
        if not isinstance(self.source, AzureRetentionEvidenceSource):
            raise ValueError("source must be an AzureRetentionEvidenceSource")
        if not isinstance(self.meter_ids, tuple):
            raise ValueError("meter_ids must be an immutable tuple")
        canonical: list[str] = []
        for meter_id in self.meter_ids:
            try:
                parsed = str(uuid.UUID(meter_id))
            except (AttributeError, ValueError):
                raise ValueError("meter_ids must contain canonical UUIDs") from None
            if parsed != meter_id:
                raise ValueError("meter_ids must contain canonical UUIDs")
            canonical.append(parsed)
        if len(set(canonical)) != len(canonical):
            raise ValueError("meter_ids must not contain duplicates")


@dataclass(frozen=True, slots=True)
class AzureProvisioningLatencyEvidence:
    """Measured provisioning latency used as the retention benefit."""

    p50_seconds: float
    p95_seconds: float
    sample_count: int
    observed_at: datetime

    def __post_init__(self) -> None:
        """Reject invalid, reversed, or unbounded observations."""
        _positive_finite("p50_seconds", self.p50_seconds)
        _positive_finite("p95_seconds", self.p95_seconds)
        if self.p95_seconds < self.p50_seconds:
            raise ValueError("p95_seconds must be >= p50_seconds")
        _bounded_int("sample_count", self.sample_count, minimum=1)
        object.__setattr__(
            self,
            "observed_at",
            _aware_utc("observed_at", self.observed_at),
        )


@dataclass(frozen=True, slots=True)
class AzureRetentionLayer:
    """One independently priced and destroyable Azure layer."""

    kind: AzureRetentionLayerKind
    idle_cost: AzureIdleCostEvidence | None
    provisioning_latency: AzureProvisioningLatencyEvidence | None
    dependencies: tuple[AzureRetentionLayerKind, ...] = ()
    idle_compute_replicas: int = 0
    activation_blocked_when_idle: bool = True

    def __post_init__(self) -> None:
        """Canonicalize dependencies and validate no-compute evidence."""
        if not isinstance(self.kind, AzureRetentionLayerKind):
            raise ValueError("kind must be an AzureRetentionLayerKind")
        if self.idle_cost is not None and not isinstance(
            self.idle_cost, AzureIdleCostEvidence
        ):
            raise ValueError("idle_cost must be AzureIdleCostEvidence or None")
        if self.provisioning_latency is not None and not isinstance(
            self.provisioning_latency, AzureProvisioningLatencyEvidence
        ):
            raise ValueError(
                "provisioning_latency must be AzureProvisioningLatencyEvidence or None"
            )
        if not isinstance(self.dependencies, tuple) or not all(
            isinstance(item, AzureRetentionLayerKind) for item in self.dependencies
        ):
            raise ValueError("dependencies must be an immutable layer-kind tuple")
        if self.kind in self.dependencies:
            raise ValueError("layer cannot depend on itself")
        canonical = tuple(
            sorted(set(self.dependencies), key=_LAYER_ORDER.__getitem__)
        )
        if canonical != self.dependencies:
            raise ValueError("dependencies must be unique and canonical")
        _bounded_int(
            "idle_compute_replicas",
            self.idle_compute_replicas,
            minimum=0,
        )
        if not isinstance(self.activation_blocked_when_idle, bool):
            raise ValueError("activation_blocked_when_idle must be boolean")


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
        """Require finite integer bounds and a reviewed preset."""
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
    """Auditable disposition for one bounded layer kind."""

    kind: AzureRetentionLayerKind
    disposition: AzureRetentionDisposition
    reason: AzureRetentionReason


@dataclass(frozen=True, slots=True)
class AzureRetentionTrace:
    """Resource-name-free planner telemetry."""

    event: AzureRetentionTraceEvent
    plan_digest: str
    candidate_count: int
    retained_count: int
    hourly_cost_microusd: int
    p95_seconds_saved: float


@dataclass(frozen=True, slots=True)
class AzureIdleRetentionPlan:
    """Deterministic layer choices and their bounded reevaluation time."""

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

    def decision_for(
        self,
        kind: AzureRetentionLayerKind,
    ) -> AzureRetentionLayerDecision:
        """Return the unique decision for a kind."""
        for decision in self.decisions:
            if decision.kind is kind:
                return decision
        raise KeyError(kind)


_TraceSink = Callable[[AzureRetentionTrace], None]


def _emit(sink: _TraceSink, trace: AzureRetentionTrace) -> None:
    try:
        sink(trace)
    except Exception:
        raise RuntimeError("Azure retention trace failed") from None


def _noop_trace(_trace: AzureRetentionTrace) -> None:
    return None


def _projected_cost(hourly_microusd: int, seconds: int) -> int:
    return math.ceil(hourly_microusd * seconds / 3_600)


def idle_cost_evidence_from_retail_meters(
    meters: tuple[AzureRetailMeter, ...],
) -> AzureIdleCostEvidence:
    """Normalize exact existing Retail Prices meters to one hourly cost."""
    if not isinstance(meters, tuple) or not meters or not all(
        isinstance(meter, AzureRetailMeter) for meter in meters
    ):
        raise ValueError("retail meters must be a non-empty immutable tuple")
    if len({meter.region for meter in meters}) != 1:
        raise ValueError("retail meters must use one exact region")
    hourly_usd = 0.0
    fetched_at: list[datetime] = []
    for meter in meters:
        price = meter.retail_price
        if isinstance(price, bool) or not math.isfinite(price) or price < 0:
            raise ValueError("retail meter price must be finite and non-negative")
        if meter.unit_of_measure == "1 Hour":
            hourly_usd += price
        elif meter.unit_of_measure == "1/Month":
            hourly_usd += price / _MONTHLY_HOURS
        else:
            raise ValueError("retail meter unit is unsupported for idle pricing")
        fetched_at.append(_aware_utc("retail meter fetched_at", meter.fetched_at))
    return AzureIdleCostEvidence(
        hourly_cost_microusd=math.ceil(hourly_usd * 1_000_000),
        observed_at=min(fetched_at),
        source=AzureRetentionEvidenceSource.AZURE_RETAIL_PRICES,
        meter_ids=tuple(meter.meter_id for meter in meters),
    )


def _evidence_reason(
    layer: AzureRetentionLayer,
    policy: AzureIdleRetentionPolicy,
    now: datetime,
    horizon_seconds: int,
) -> AzureRetentionReason | None:
    if policy.preset is AzureRetentionPreset.ALWAYS_DESTROY:
        return AzureRetentionReason.PRESET_DESTROY
    if horizon_seconds == 0:
        return AzureRetentionReason.DEMAND_OUTSIDE_WINDOW
    if layer.idle_compute_replicas:
        return AzureRetentionReason.IDLE_COMPUTE_FORBIDDEN
    if not layer.activation_blocked_when_idle:
        return AzureRetentionReason.IDLE_ACTIVATION_POSSIBLE
    if layer.idle_cost is None:
        return AzureRetentionReason.PRICE_EVIDENCE_MISSING
    if layer.provisioning_latency is None:
        return AzureRetentionReason.LATENCY_EVIDENCE_MISSING
    price_age = (now - layer.idle_cost.observed_at).total_seconds()
    latency_age = (now - layer.provisioning_latency.observed_at).total_seconds()
    if price_age < 0 or latency_age < 0:
        return AzureRetentionReason.EVIDENCE_FROM_FUTURE
    if price_age > policy.max_price_age_seconds:
        return AzureRetentionReason.PRICE_EVIDENCE_STALE
    if latency_age > policy.max_latency_age_seconds:
        return AzureRetentionReason.LATENCY_EVIDENCE_STALE
    if (
        policy.preset is AzureRetentionPreset.ZERO_COST_ONLY
        and layer.idle_cost.hourly_cost_microusd > 0
    ):
        return AzureRetentionReason.PRESET_EXCLUDES_COST
    if policy.preset is AzureRetentionPreset.BALANCED:
        projected = _projected_cost(
            layer.idle_cost.hourly_cost_microusd,
            horizon_seconds,
        )
        value_rate = math.ceil(
            projected * 3_600 / layer.provisioning_latency.p95_seconds
        )
        if value_rate > policy.max_cost_per_saved_hour_microusd:
            return AzureRetentionReason.VALUE_CEILING_EXCEEDED
    return None


def _validate_graph(layers: tuple[AzureRetentionLayer, ...]) -> None:
    if not isinstance(layers, tuple) or not all(
        isinstance(layer, AzureRetentionLayer) for layer in layers
    ):
        raise ValueError("layers must be an immutable AzureRetentionLayer tuple")
    if len(layers) > len(AzureRetentionLayerKind):
        raise ValueError("layers exceed the bounded layer catalog")
    by_kind = {layer.kind: layer for layer in layers}
    if len(by_kind) != len(layers):
        raise ValueError("layer kinds must be unique")
    for layer in layers:
        if any(dependency not in by_kind for dependency in layer.dependencies):
            raise ValueError("every dependency must be present")

    visiting: set[AzureRetentionLayerKind] = set()
    visited: set[AzureRetentionLayerKind] = set()

    def visit(kind: AzureRetentionLayerKind) -> None:
        if kind in visiting:
            raise ValueError("layer dependency cycle")
        if kind in visited:
            return
        visiting.add(kind)
        for dependency in by_kind[kind].dependencies:
            visit(dependency)
        visiting.remove(kind)
        visited.add(kind)

    for kind in by_kind:
        visit(kind)


def _subset_metrics(
    selected: tuple[AzureRetentionLayer, ...],
    horizon_seconds: int,
) -> tuple[int, int, int, float]:
    hourly = sum(
        layer.idle_cost.hourly_cost_microusd
        for layer in selected
        if layer.idle_cost
    )
    monthly = hourly * _MONTHLY_HOURS
    projected = _projected_cost(hourly, horizon_seconds)
    saved = sum(
        layer.provisioning_latency.p95_seconds
        for layer in selected
        if layer.provisioning_latency
    )
    return hourly, monthly, projected, saved


def _best_subset(
    eligible: tuple[AzureRetentionLayer, ...],
    policy: AzureIdleRetentionPolicy,
    horizon_seconds: int,
) -> tuple[AzureRetentionLayer, ...]:
    best: tuple[AzureRetentionLayer, ...] = ()
    best_score: tuple[float, int, int, tuple[int, ...]] = (0.0, 0, 0, ())
    for mask in range(1 << len(eligible)):
        selected = tuple(
            layer for index, layer in enumerate(eligible) if mask & (1 << index)
        )
        selected_kinds = {layer.kind for layer in selected}
        if any(
            dependency not in selected_kinds
            for layer in selected
            for dependency in layer.dependencies
        ):
            continue
        hourly, monthly, projected, saved = _subset_metrics(
            selected,
            horizon_seconds,
        )
        if (
            hourly > policy.max_idle_hourly_cost_microusd
            or monthly > policy.max_idle_monthly_cost_microusd
            or projected > policy.max_retention_cost_microusd
        ):
            continue
        signature = tuple(-_LAYER_ORDER[layer.kind] for layer in selected)
        score = (saved, -projected, -hourly, signature)
        if score > best_score:
            best, best_score = selected, score
    return best


def _digest(
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
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validated_inputs(
    layers: tuple[AzureRetentionLayer, ...],
    policy: AzureIdleRetentionPolicy,
    scope_digest: str,
    now: datetime,
    runnable_todo_count: int,
    expected_next_demand_seconds: int | None,
    trace_sink: _TraceSink | None,
) -> tuple[datetime, _TraceSink]:
    _validate_graph(layers)
    if not isinstance(policy, AzureIdleRetentionPolicy):
        raise ValueError("policy must be AzureIdleRetentionPolicy")
    if _DIGEST_PATTERN.fullmatch(scope_digest) is None:
        raise ValueError("scope_digest must be a lowercase SHA-256 digest")
    current = _aware_utc("now", now)
    _bounded_int("runnable_todo_count", runnable_todo_count, minimum=0)
    if runnable_todo_count:
        raise ValueError("idle retention requires an empty durable todo")
    if expected_next_demand_seconds is not None:
        _bounded_int(
            "expected_next_demand_seconds",
            expected_next_demand_seconds,
            minimum=1,
        )
    if trace_sink is not None and not callable(trace_sink):
        raise ValueError("trace_sink must be callable or None")
    return current, trace_sink or _noop_trace


def plan_azure_idle_retention(
    layers: tuple[AzureRetentionLayer, ...],
    *,
    policy: AzureIdleRetentionPolicy,
    scope_digest: str,
    now: datetime,
    runnable_todo_count: int,
    expected_next_demand_seconds: int | None,
    trace_sink: _TraceSink | None = None,
) -> AzureIdleRetentionPlan:
    """Select the highest measured latency benefit under every cost ceiling."""
    current, sink = _validated_inputs(
        layers,
        policy,
        scope_digest,
        now,
        runnable_todo_count,
        expected_next_demand_seconds,
        trace_sink,
    )
    ordered = tuple(sorted(layers, key=lambda layer: _LAYER_ORDER[layer.kind]))
    horizon = policy.max_retention_seconds
    if expected_next_demand_seconds is not None:
        horizon = (
            expected_next_demand_seconds
            if expected_next_demand_seconds <= policy.max_retention_seconds
            else 0
        )
    _emit(
        sink,
        AzureRetentionTrace(
            AzureRetentionTraceEvent.EVALUATION_STARTED,
            "0" * 64,
            len(ordered),
            0,
            0,
            0.0,
        ),
    )
    exclusions = {
        layer.kind: _evidence_reason(layer, policy, current, horizon)
        for layer in ordered
    }
    eligible = tuple(
        layer for layer in ordered if exclusions[layer.kind] is None
    )
    selected = _best_subset(eligible, policy, horizon)
    selected_kinds = {layer.kind for layer in selected}
    decisions = tuple(
        AzureRetentionLayerDecision(
            layer.kind,
            (
                AzureRetentionDisposition.RETAIN
                if layer.kind in selected_kinds
                else AzureRetentionDisposition.DESTROY
            ),
            (
                AzureRetentionReason.SELECTED
                if layer.kind in selected_kinds
                else exclusions[layer.kind]
                or AzureRetentionReason.BUDGET_TRADEOFF
            ),
        )
        for layer in ordered
    )
    hourly, monthly, projected, saved = _subset_metrics(selected, horizon)
    retention_seconds = horizon if selected else 0
    digest = _digest(
        policy,
        scope_digest,
        current,
        retention_seconds,
        decisions,
    )
    plan = AzureIdleRetentionPlan(
        decisions=decisions,
        retained_layers=tuple(layer.kind for layer in selected),
        destroyed_layers=tuple(
            layer.kind for layer in ordered if layer.kind not in selected_kinds
        ),
        retention_seconds=retention_seconds,
        reconcile_at=current + timedelta(seconds=retention_seconds),
        hourly_cost_microusd=hourly,
        monthly_cost_microusd=monthly,
        projected_cost_microusd=projected,
        p95_seconds_saved=saved,
        scope_digest=scope_digest,
        plan_digest=digest,
    )
    _emit(
        sink,
        AzureRetentionTrace(
            AzureRetentionTraceEvent.PLAN_SELECTED,
            digest,
            len(ordered),
            len(selected),
            hourly,
            saved,
        ),
    )
    return plan


def container_apps_consumption_layers(
    *,
    observed_at: datetime,
    environment_latency: AzureProvisioningLatencyEvidence,
    app_latency: AzureProvisioningLatencyEvidence,
    min_replicas: int,
    activation_blocked_when_idle: bool,
    has_dedicated_profiles: bool,
    has_private_endpoint: bool,
    has_planned_maintenance: bool,
    has_paid_logging: bool,
) -> tuple[AzureRetentionLayer, AzureRetentionLayer]:
    """Build zero-price claims only for Azure's documented free idle shape."""
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
    zero = AzureIdleCostEvidence(
        hourly_cost_microusd=0,
        observed_at=observed_at,
        source=AzureRetentionEvidenceSource.AZURE_BILLING_CONTRACT,
    )
    environment_has_paid_feature = any(flags[1:])
    environment = AzureRetentionLayer(
        kind=AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
        idle_cost=None if environment_has_paid_feature else zero,
        provisioning_latency=environment_latency,
    )
    app = AzureRetentionLayer(
        kind=AzureRetentionLayerKind.CONTAINER_APP,
        idle_cost=zero if min_replicas == 0 else None,
        provisioning_latency=app_latency,
        dependencies=(AzureRetentionLayerKind.MANAGED_ENVIRONMENT,),
        idle_compute_replicas=min_replicas,
        activation_blocked_when_idle=activation_blocked_when_idle,
    )
    return environment, app


__all__ = (
    "AzureIdleCostEvidence",
    "AzureIdleRetentionPlan",
    "AzureIdleRetentionPolicy",
    "AzureProvisioningLatencyEvidence",
    "AzureRetentionDisposition",
    "AzureRetentionEvidenceSource",
    "AzureRetentionLayer",
    "AzureRetentionLayerDecision",
    "AzureRetentionLayerKind",
    "AzureRetentionPreset",
    "AzureRetentionReason",
    "AzureRetentionTrace",
    "AzureRetentionTraceEvent",
    "container_apps_consumption_layers",
    "idle_cost_evidence_from_retail_meters",
    "plan_azure_idle_retention",
)
