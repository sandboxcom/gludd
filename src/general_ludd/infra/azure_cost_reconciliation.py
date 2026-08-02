"""Delayed Azure billed-cost reconciliation and cohort accuracy metrics.

Retail prices are a pre-deploy ceiling input, not proof of the final invoice.
This module keeps the immutable prediction identity needed to query delayed
Cost Management data, reconciles every resource-scoped line item (including
ancillary charges), and reports calibration only after a statistically useful
homogeneous cohort exists.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self, cast


class AzureCostReconciliationError(RuntimeError):
    """Raised when Azure billing data cannot be matched safely."""


class AzureCostReconciliationState(StrEnum):
    """Lifecycle state for one prediction-to-bill comparison."""

    PENDING = "COST_RECONCILIATION_PENDING"
    RECONCILED = "COST_RECONCILED"


class AzureCostLedgerState(StrEnum):
    """Durable delayed-data state persisted across worker restarts."""

    PREDICTED = "PREDICTED"
    USAGE_PENDING = "USAGE_PENDING"
    QUERY_DUE = "QUERY_DUE"
    NO_DATA_RETRY = "NO_DATA_RETRY"
    PARTIAL = "PARTIAL"
    PROVISIONAL = "PROVISIONAL"
    STABLE = "STABLE"
    FINAL = "FINAL"
    ADJUSTED = "ADJUSTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    AUTH_BLOCKED = "AUTH_BLOCKED"


AZURE_COST_LEDGER_STATE_RANKS: Mapping[AzureCostLedgerState, int] = MappingProxyType(
    {
        AzureCostLedgerState.PREDICTED: 0,
        AzureCostLedgerState.USAGE_PENDING: 1,
        AzureCostLedgerState.QUERY_DUE: 2,
        AzureCostLedgerState.NO_DATA_RETRY: 2,
        AzureCostLedgerState.RETRYABLE_ERROR: 2,
        AzureCostLedgerState.AUTH_BLOCKED: 2,
        AzureCostLedgerState.PARTIAL: 3,
        AzureCostLedgerState.NEEDS_REVIEW: 3,
        AzureCostLedgerState.PROVISIONAL: 4,
        AzureCostLedgerState.STABLE: 5,
        AzureCostLedgerState.FINAL: 6,
        AzureCostLedgerState.ADJUSTED: 7,
    }
)


class AzureCostCohortState(StrEnum):
    """Whether a homogeneous cost cohort supports an accuracy claim."""

    UNCALIBRATED = "UNCALIBRATED"
    CALIBRATED = "CALIBRATED"
    RECALIBRATION_REQUIRED = "RECALIBRATION_REQUIRED"


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_positive_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class AzureCostPrediction:
    """Immutable identity and estimate emitted before Azure provisioning."""

    prediction_id: str
    todo_id: str
    subscription_id: str
    resource_group: str
    resource_ids: tuple[str, ...]
    meter_ids: tuple[str, ...]
    region: str
    sku: str
    workload: str
    predicted_cost_usd: float
    conservative_ceiling_usd: float
    usage_started_at: datetime
    usage_ended_at: datetime
    prediction_version: int = 1
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "prediction_id",
            "todo_id",
            "subscription_id",
            "resource_group",
            "region",
            "sku",
            "workload",
        ):
            _require_text(name, getattr(self, name))
        if not self.resource_ids:
            raise ValueError("resource_ids must contain at least one ARM resource ID")
        if not self.meter_ids:
            raise ValueError("meter_ids must contain at least one exact meter ID")
        if isinstance(self.prediction_version, bool) or self.prediction_version <= 0:
            raise ValueError("prediction_version must be a positive integer")
        normalized_resources = tuple(dict.fromkeys(resource_id.strip().lower() for resource_id in self.resource_ids))
        if any(not resource_id.startswith("/subscriptions/") for resource_id in normalized_resources):
            raise ValueError("resource_ids must be absolute Azure ARM resource IDs")
        normalized_meters = tuple(dict.fromkeys(meter_id.strip().lower() for meter_id in self.meter_ids))
        if any(not meter_id for meter_id in normalized_meters):
            raise ValueError("meter_ids must not contain empty values")
        _require_positive_finite("predicted_cost_usd", self.predicted_cost_usd)
        _require_positive_finite("conservative_ceiling_usd", self.conservative_ceiling_usd)
        if self.conservative_ceiling_usd < self.predicted_cost_usd:
            raise ValueError("conservative_ceiling_usd must be at least predicted_cost_usd")
        _require_aware("usage_started_at", self.usage_started_at)
        _require_aware("usage_ended_at", self.usage_ended_at)
        if self.usage_ended_at <= self.usage_started_at:
            raise ValueError("usage_ended_at must be after usage_started_at")
        normalized_tags: dict[str, str] = {}
        for key, value in self.tags.items():
            _require_text("tag key", key)
            _require_text(f"tag {key!r}", value)
            normalized_tags[key] = value
        object.__setattr__(self, "resource_ids", normalized_resources)
        object.__setattr__(self, "meter_ids", normalized_meters)
        object.__setattr__(self, "tags", MappingProxyType(normalized_tags))

    @property
    def cohort_key(self) -> tuple[str, str, str, str]:
        """Provider, region, exact SKU, and workload calibration key."""
        return ("azure", self.region, self.sku, self.workload)


@dataclass(frozen=True)
class AzureBilledCostLineItem:
    """One resource-scoped row returned by Azure Cost Management."""

    resource_id: str
    meter_id: str
    cost_usd: float
    currency: str
    service_name: str
    charge_type: str


@dataclass(frozen=True)
class AzureActualCostObservation:
    """Immutable identity and payload for one billed-cost source row."""

    source: str
    snapshot_id: str
    row_identity: str
    cost_usd: float
    currency: str
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source", "snapshot_id", "row_identity", "currency"):
            _require_text(name, getattr(self, name))
        if isinstance(self.cost_usd, bool) or not math.isfinite(self.cost_usd):
            raise ValueError("cost_usd must be finite")
        normalized_payload: dict[str, object] = {}
        for key, value in self.payload.items():
            _require_text("payload key", key)
            normalized_payload[key] = value
        object.__setattr__(self, "currency", self.currency.upper())
        object.__setattr__(self, "payload", normalized_payload)


class AzureActualCostQueryClient(Protocol):
    """Adapter boundary for the mature Azure Cost Management query API."""

    def query_actual_cost(self, prediction: AzureCostPrediction) -> Sequence[AzureBilledCostLineItem]: ...


class _AzureQueryOperations(Protocol):
    def usage(self, scope: str, parameters: dict[str, object]) -> object: ...


class _AzureCostManagementClient(Protocol):
    query: _AzureQueryOperations


class AzureCostManagementQueryClient:
    """Resource-scoped adapter over Azure's official Cost Management client.

    Query API results are treated as bounded probes while billing data settles.
    The adapter deliberately submits plain model-compatible mappings so the
    public module remains importable when the optional Azure SDK extra is not
    installed. :meth:`from_default_credential` is the production constructor.
    """

    _REQUIRED_COLUMNS = (
        "ResourceId",
        "MeterId",
        "CostUSD",
        "ServiceName",
        "ChargeType",
    )

    def __init__(
        self,
        client: _AzureCostManagementClient,
        *,
        subscription_id: str,
    ) -> None:
        _require_text("subscription_id", subscription_id)
        self._client = client
        self._subscription_id = subscription_id

    @classmethod
    def from_default_credential(
        cls,
        subscription_id: str,
        *,
        credential: object | None = None,
    ) -> Self:
        """Build the official SDK client without making Azure a base dependency."""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.costmanagement import CostManagementClient
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise AzureCostReconciliationError(
                "Azure billed-cost queries require the 'azure' optional dependency"
            ) from exc
        selected_credential = credential or DefaultAzureCredential()
        client = CostManagementClient(credential=selected_credential)
        return cls(
            cast(_AzureCostManagementClient, client),
            subscription_id=subscription_id,
        )

    def query_actual_cost(self, prediction: AzureCostPrediction) -> list[AzureBilledCostLineItem]:
        """Return grouped ActualCost rows for only the prediction's ARM IDs."""
        if prediction.subscription_id != self._subscription_id:
            raise AzureCostReconciliationError("prediction subscription does not match the Cost Management client")
        request: dict[str, object] = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": prediction.usage_started_at.isoformat(),
                "to": prediction.usage_ended_at.isoformat(),
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "CostUSD", "function": "Sum"}},
                "grouping": [
                    {"type": "Dimension", "name": "ResourceId"},
                    {"type": "Dimension", "name": "MeterId"},
                    {"type": "Dimension", "name": "ServiceName"},
                    {"type": "Dimension", "name": "ChargeType"},
                ],
                "filter": {
                    "dimensions": {
                        "name": "ResourceId",
                        "operator": "In",
                        "values": list(prediction.resource_ids),
                    }
                },
            },
        }
        response = self._client.query.usage(
            f"/subscriptions/{self._subscription_id}",
            request,
        )
        return self._parse_response(response)

    @classmethod
    def _parse_response(cls, response: object) -> list[AzureBilledCostLineItem]:
        raw_columns: object = getattr(response, "columns", None)
        raw_rows: object = getattr(response, "rows", None)
        if not isinstance(raw_columns, Sequence) or isinstance(raw_columns, (str, bytes)):
            raise AzureCostReconciliationError("Azure Cost Management response has no columns sequence")
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
            raise AzureCostReconciliationError("Azure Cost Management response has no rows sequence")

        column_names: list[str] = []
        for column in raw_columns:
            raw_name: object = column.get("name") if isinstance(column, Mapping) else getattr(column, "name", None)
            if not isinstance(raw_name, str) or not raw_name:
                raise AzureCostReconciliationError("Azure Cost Management response contains an unnamed column")
            column_names.append(raw_name)
        if len(set(column_names)) != len(column_names) or any(
            required not in column_names for required in cls._REQUIRED_COLUMNS
        ):
            raise AzureCostReconciliationError("Azure Cost Management response columns are missing or duplicated")
        indexes = {name: column_names.index(name) for name in cls._REQUIRED_COLUMNS}

        parsed: list[AzureBilledCostLineItem] = []
        for raw_row in raw_rows:
            if (
                not isinstance(raw_row, Sequence)
                or isinstance(raw_row, (str, bytes))
                or len(raw_row) != len(column_names)
            ):
                raise AzureCostReconciliationError("Azure Cost Management response row does not match its columns")
            resource_id = raw_row[indexes["ResourceId"]]
            meter_id = raw_row[indexes["MeterId"]]
            raw_cost = raw_row[indexes["CostUSD"]]
            service_name = raw_row[indexes["ServiceName"]]
            charge_type = raw_row[indexes["ChargeType"]]
            if (
                not all(isinstance(value, str) for value in (resource_id, meter_id, service_name, charge_type))
                or isinstance(raw_cost, bool)
                or not isinstance(raw_cost, (int, float))
            ):
                raise AzureCostReconciliationError("Azure Cost Management response row has invalid value types")
            parsed.append(
                AzureBilledCostLineItem(
                    resource_id=cast(str, resource_id).strip().lower(),
                    meter_id=cast(str, meter_id).strip().lower(),
                    cost_usd=float(raw_cost),
                    currency="USD",
                    service_name=cast(str, service_name),
                    charge_type=cast(str, charge_type),
                )
            )
        return parsed


@dataclass(frozen=True)
class AzureCostReconciliation:
    """Result of matching one prediction to delayed billed line items."""

    prediction: AzureCostPrediction
    state: AzureCostReconciliationState
    actual_cost_usd: float | None = None
    signed_error_usd: float | None = None
    absolute_percentage_error_pct: float | None = None
    ancillary_cost_usd: float = 0.0
    observed_meter_ids: tuple[str, ...] = ()
    line_items: tuple[AzureBilledCostLineItem, ...] = ()


@dataclass(frozen=True)
class AzureCostCohortMetrics:
    """Accuracy evidence for one homogeneous provider/region/SKU/workload cohort."""

    cohort_key: tuple[str, str, str, str]
    state: AzureCostCohortState
    sample_count: int
    mean_signed_error_usd: float
    mape_pct: float
    p95_absolute_percentage_error_pct: float
    bias_pct: float
    systematic_underprediction: bool
    conservative_multiplier: float


class AzureCostReconciler:
    """Reconcile resource-scoped Azure billing data after its bounded delay."""

    def __init__(
        self,
        query_client: AzureActualCostQueryClient,
        *,
        max_data_latency: timedelta = timedelta(hours=72),
    ) -> None:
        if max_data_latency <= timedelta(0):
            raise ValueError("max_data_latency must be positive")
        self._query_client = query_client
        self._max_data_latency = max_data_latency

    def reconcile(
        self,
        prediction: AzureCostPrediction,
        *,
        as_of: datetime,
    ) -> AzureCostReconciliation:
        """Query and reconcile one immutable prediction, failing closed on drift."""
        _require_aware("as_of", as_of)
        if as_of < prediction.usage_ended_at:
            raise ValueError("as_of must not precede usage_ended_at")
        rows = tuple(self._query_client.query_actual_cost(prediction))
        if not rows:
            age = as_of - prediction.usage_ended_at
            if age <= self._max_data_latency:
                return AzureCostReconciliation(
                    prediction=prediction,
                    state=AzureCostReconciliationState.PENDING,
                )
            hours = self._max_data_latency.total_seconds() / 3600.0
            raise AzureCostReconciliationError(
                f"Azure returned no billed rows after the {hours:g}-hour data-latency window"
            )

        allowed_resources = set(prediction.resource_ids)
        expected_meters = set(prediction.meter_ids)
        actual_cost = 0.0
        ancillary_cost = 0.0
        observed_meters: list[str] = []
        for row in rows:
            resource_id = row.resource_id.strip().lower()
            meter_id = row.meter_id.strip().lower()
            if resource_id not in allowed_resources:
                raise AzureCostReconciliationError("Azure bill row did not match the prediction's exact resource IDs")
            if row.currency.upper() != "USD":
                raise AzureCostReconciliationError(f"Azure bill row currency must be USD, got {row.currency!r}")
            if isinstance(row.cost_usd, bool) or not math.isfinite(row.cost_usd) or row.cost_usd < 0:
                raise AzureCostReconciliationError(
                    f"Azure bill row cost must be finite and non-negative, got {row.cost_usd!r}"
                )
            if not meter_id:
                raise AzureCostReconciliationError("Azure bill row has no meter ID")
            actual_cost += row.cost_usd
            if meter_id not in expected_meters:
                ancillary_cost += row.cost_usd
            if meter_id not in observed_meters:
                observed_meters.append(meter_id)
        if actual_cost <= 0:
            raise AzureCostReconciliationError("Azure billed rows summed to zero; final cost is not yet trustworthy")

        signed_error = actual_cost - prediction.predicted_cost_usd
        absolute_percentage_error = abs(signed_error) / actual_cost * 100.0
        return AzureCostReconciliation(
            prediction=prediction,
            state=AzureCostReconciliationState.RECONCILED,
            actual_cost_usd=actual_cost,
            signed_error_usd=signed_error,
            absolute_percentage_error_pct=absolute_percentage_error,
            ancillary_cost_usd=ancillary_cost,
            observed_meter_ids=tuple(observed_meters),
            line_items=rows,
        )


def _nearest_rank_p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def build_cohort_metrics(
    reconciliations: Sequence[AzureCostReconciliation],
    *,
    min_samples: int = 20,
    max_mape_pct: float = 10.0,
    max_p95_pct: float = 20.0,
) -> AzureCostCohortMetrics:
    """Compute acceptance metrics without mixing unlike Azure workloads."""
    if min_samples <= 0:
        raise ValueError("min_samples must be positive")
    if not reconciliations:
        raise ValueError("at least one reconciled sample is required")
    actuals: list[float] = []
    signed_errors: list[float] = []
    absolute_percentages: list[float] = []
    for item in reconciliations:
        if (
            item.state is not AzureCostReconciliationState.RECONCILED
            or item.actual_cost_usd is None
            or item.signed_error_usd is None
            or item.absolute_percentage_error_pct is None
        ):
            raise ValueError("cohort metrics require fully reconciled samples")
        actuals.append(item.actual_cost_usd)
        signed_errors.append(item.signed_error_usd)
        absolute_percentages.append(item.absolute_percentage_error_pct)

    cohort_keys = {item.prediction.cohort_key for item in reconciliations}
    if len(cohort_keys) != 1:
        raise ValueError("cohort metrics require a homogeneous cohort")
    signed_percentages = [
        signed_error / actual * 100.0 for signed_error, actual in zip(signed_errors, actuals, strict=True)
    ]
    actual_to_predicted = [
        actual / item.prediction.predicted_cost_usd for actual, item in zip(actuals, reconciliations, strict=True)
    ]
    bias_pct = statistics.fmean(signed_percentages)
    systematic_underprediction = bias_pct > 1e-9
    sample_count = len(reconciliations)
    mape = statistics.fmean(absolute_percentages)
    p95 = _nearest_rank_p95(absolute_percentages)
    if sample_count < min_samples:
        state = AzureCostCohortState.UNCALIBRATED
    elif mape <= max_mape_pct and p95 <= max_p95_pct and not systematic_underprediction:
        state = AzureCostCohortState.CALIBRATED
    else:
        state = AzureCostCohortState.RECALIBRATION_REQUIRED

    observed_multiplier = _nearest_rank_p95(actual_to_predicted)
    conservative_multiplier = max(
        1.0,
        observed_multiplier,
        1.25 if state is AzureCostCohortState.UNCALIBRATED else 1.0,
    )
    return AzureCostCohortMetrics(
        cohort_key=next(iter(cohort_keys)),
        state=state,
        sample_count=sample_count,
        mean_signed_error_usd=statistics.fmean(signed_errors),
        mape_pct=mape,
        p95_absolute_percentage_error_pct=p95,
        bias_pct=bias_pct,
        systematic_underprediction=systematic_underprediction,
        conservative_multiplier=conservative_multiplier,
    )


__all__ = [
    "AZURE_COST_LEDGER_STATE_RANKS",
    "AzureActualCostObservation",
    "AzureActualCostQueryClient",
    "AzureBilledCostLineItem",
    "AzureCostCohortMetrics",
    "AzureCostCohortState",
    "AzureCostLedgerState",
    "AzureCostManagementQueryClient",
    "AzureCostPrediction",
    "AzureCostReconciler",
    "AzureCostReconciliation",
    "AzureCostReconciliationError",
    "AzureCostReconciliationState",
    "build_cohort_metrics",
]
