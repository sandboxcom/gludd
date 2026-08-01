"""Azure billed-cost reconciliation and cohort-calibration contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.infra.azure_cost_reconciliation import (
    AzureBilledCostLineItem,
    AzureCostCohortState,
    AzureCostManagementQueryClient,
    AzureCostPrediction,
    AzureCostReconciler,
    AzureCostReconciliationError,
    AzureCostReconciliationState,
    build_cohort_metrics,
)

_START = datetime(2026, 8, 1, 12, tzinfo=UTC)
_END = _START + timedelta(minutes=15)
_RESOURCE = (
    "/subscriptions/sub-1/resourceGroups/rg-1/providers/"
    "Microsoft.App/containerApps/app-1"
)


def _prediction(**overrides: object) -> AzureCostPrediction:
    values: dict[str, object] = {
        "prediction_id": "pred-1",
        "todo_id": "TODO-1",
        "subscription_id": "sub-1",
        "resource_group": "rg-1",
        "resource_ids": (_RESOURCE,),
        "meter_ids": ("gpu-meter", "cpu-meter", "memory-meter"),
        "region": "eastus",
        "sku": "Consumption-GPU-NC8as-T4",
        "workload": "fps-e2e",
        "predicted_cost_usd": 1.50,
        "conservative_ceiling_usd": 2.00,
        "usage_started_at": _START,
        "usage_ended_at": _END,
        "tags": {"gludd-run-id": "run-1", "gludd-todo-id": "TODO-1"},
    }
    values.update(overrides)
    return AzureCostPrediction(**values)  # type: ignore[arg-type]


class _QueryClient:
    def __init__(self, rows: list[AzureBilledCostLineItem]) -> None:
        self.rows = rows
        self.predictions: list[AzureCostPrediction] = []

    def query_actual_cost(
        self, prediction: AzureCostPrediction
    ) -> list[AzureBilledCostLineItem]:
        self.predictions.append(prediction)
        return list(self.rows)


class _Column:
    def __init__(self, name: str) -> None:
        self.name = name


class _QueryResponse:
    def __init__(self, columns: list[str], rows: list[list[object]]) -> None:
        self.columns = [_Column(name) for name in columns]
        self.rows = rows


class _UsageOperations:
    def __init__(self, response: _QueryResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def usage(self, scope: str, parameters: dict[str, object]) -> _QueryResponse:
        self.calls.append((scope, parameters))
        return self.response


class _CostManagementSDKClient:
    def __init__(self, response: _QueryResponse) -> None:
        self.query = _UsageOperations(response)


def _line(
    cost: float,
    *,
    resource_id: str = _RESOURCE,
    meter_id: str = "gpu-meter",
    currency: str = "USD",
) -> AzureBilledCostLineItem:
    return AzureBilledCostLineItem(
        resource_id=resource_id,
        meter_id=meter_id,
        cost_usd=cost,
        currency=currency,
        service_name="Azure Container Apps",
        charge_type="Usage",
    )


class TestPredictionIdentity:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("prediction_id", ""),
            ("subscription_id", ""),
            ("resource_group", ""),
            ("resource_ids", ()),
            ("meter_ids", ()),
            ("predicted_cost_usd", 0.0),
            ("predicted_cost_usd", float("nan")),
            ("conservative_ceiling_usd", 1.49),
        ],
    )
    def test_rejects_incomplete_or_unsafe_prediction_identity(
        self, field: str, value: object
    ) -> None:
        with pytest.raises(ValueError):
            _prediction(**{field: value})

    def test_rejects_naive_or_reversed_usage_window(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _prediction(usage_started_at=_START.replace(tzinfo=None))
        with pytest.raises(ValueError, match="after"):
            _prediction(usage_ended_at=_START - timedelta(seconds=1))

    def test_normalizes_identity_for_case_insensitive_arm_matching(self) -> None:
        prediction = _prediction(resource_ids=(_RESOURCE.upper(), _RESOURCE))
        assert prediction.resource_ids == (_RESOURCE.lower(),)
        assert prediction.cohort_key == (
            "azure",
            "eastus",
            "Consumption-GPU-NC8as-T4",
            "fps-e2e",
        )


class TestDelayedReconciliation:
    def test_missing_bill_inside_72_hour_window_remains_pending(self) -> None:
        client = _QueryClient([])
        result = AzureCostReconciler(client).reconcile(
            _prediction(), as_of=_END + timedelta(hours=24)
        )
        assert result.state is AzureCostReconciliationState.PENDING
        assert result.actual_cost_usd is None
        assert result.signed_error_usd is None
        assert client.predictions == [_prediction()]

    def test_missing_bill_after_latency_window_fails_closed(self) -> None:
        with pytest.raises(AzureCostReconciliationError, match="72"):
            AzureCostReconciler(_QueryClient([])).reconcile(
                _prediction(), as_of=_END + timedelta(hours=73)
            )


class TestCostManagementQueryAdapter:
    def test_builds_resource_scoped_actual_cost_query_and_parses_reordered_columns(
        self,
    ) -> None:
        response = _QueryResponse(
            ["ChargeType", "MeterId", "CostUSD", "ResourceId", "ServiceName"],
            [["Usage", "GPU-METER", 1.25, _RESOURCE.upper(), "Azure Container Apps"]],
        )
        sdk = _CostManagementSDKClient(response)
        adapter = AzureCostManagementQueryClient(sdk, subscription_id="sub-1")

        rows = adapter.query_actual_cost(_prediction())

        assert rows == [
            AzureBilledCostLineItem(
                resource_id=_RESOURCE.lower(),
                meter_id="gpu-meter",
                cost_usd=1.25,
                currency="USD",
                service_name="Azure Container Apps",
                charge_type="Usage",
            )
        ]
        assert len(sdk.query.calls) == 1
        scope, request = sdk.query.calls[0]
        assert scope == "/subscriptions/sub-1"
        assert request["type"] == "ActualCost"
        assert request["timeframe"] == "Custom"
        dataset = request["dataset"]
        assert isinstance(dataset, dict)
        assert dataset["aggregation"] == {
            "totalCost": {"name": "CostUSD", "function": "Sum"}
        }
        assert dataset["filter"] == {
            "dimensions": {
                "name": "ResourceId",
                "operator": "In",
                "values": [_RESOURCE.lower()],
            }
        }
        assert dataset["grouping"] == [
            {"type": "Dimension", "name": "ResourceId"},
            {"type": "Dimension", "name": "MeterId"},
            {"type": "Dimension", "name": "ServiceName"},
            {"type": "Dimension", "name": "ChargeType"},
        ]

    def test_subscription_mismatch_fails_before_query(self) -> None:
        sdk = _CostManagementSDKClient(_QueryResponse([], []))
        adapter = AzureCostManagementQueryClient(sdk, subscription_id="sub-2")
        with pytest.raises(AzureCostReconciliationError, match="subscription"):
            adapter.query_actual_cost(_prediction())
        assert sdk.query.calls == []

    @pytest.mark.parametrize(
        "columns",
        [
            ["ResourceId", "MeterId", "ServiceName", "ChargeType"],
            [
                "ResourceId",
                "ResourceId",
                "MeterId",
                "CostUSD",
                "ServiceName",
                "ChargeType",
            ],
        ],
    )
    def test_missing_or_duplicate_response_columns_fail_closed(
        self, columns: list[str]
    ) -> None:
        sdk = _CostManagementSDKClient(_QueryResponse(columns, []))
        adapter = AzureCostManagementQueryClient(sdk, subscription_id="sub-1")
        with pytest.raises(AzureCostReconciliationError, match="columns"):
            adapter.query_actual_cost(_prediction())

    def test_malformed_row_width_fails_closed(self) -> None:
        sdk = _CostManagementSDKClient(
            _QueryResponse(
                ["ResourceId", "MeterId", "CostUSD", "ServiceName", "ChargeType"],
                [[_RESOURCE, "gpu-meter"]],
            )
        )
        adapter = AzureCostManagementQueryClient(sdk, subscription_id="sub-1")
        with pytest.raises(AzureCostReconciliationError, match="row"):
            adapter.query_actual_cost(_prediction())


class TestReconciledRows:

    def test_reconciles_compute_and_ancillary_line_items(self) -> None:
        rows = [
            _line(1.25, meter_id="gpu-meter"),
            _line(0.10, meter_id="logs-meter"),
            _line(0.05, meter_id="network-meter"),
        ]
        result = AzureCostReconciler(_QueryClient(rows)).reconcile(
            _prediction(), as_of=_END + timedelta(hours=12)
        )
        assert result.state is AzureCostReconciliationState.RECONCILED
        assert result.actual_cost_usd == pytest.approx(1.40)
        assert result.signed_error_usd == pytest.approx(-0.10)
        assert result.absolute_percentage_error_pct == pytest.approx(100 * 0.10 / 1.40)
        assert result.ancillary_cost_usd == pytest.approx(0.15)
        assert result.observed_meter_ids == (
            "gpu-meter",
            "logs-meter",
            "network-meter",
        )

    @pytest.mark.parametrize(
        "rows",
        [
            [_line(1.0, resource_id=_RESOURCE + "-other")],
            [_line(1.0, currency="EUR")],
            [_line(-1.0)],
            [_line(float("nan"))],
        ],
    )
    def test_unrelated_or_malformed_bill_rows_fail_closed(
        self, rows: list[AzureBilledCostLineItem]
    ) -> None:
        with pytest.raises(AzureCostReconciliationError):
            AzureCostReconciler(_QueryClient(rows)).reconcile(
                _prediction(), as_of=_END + timedelta(hours=12)
            )

    def test_rejects_naive_reconciliation_clock(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            AzureCostReconciler(_QueryClient([])).reconcile(
                _prediction(), as_of=_END.replace(tzinfo=None)
            )


def _reconciled_sample(
    sample: int,
    *,
    predicted: float,
    actual: float,
    sku: str = "Consumption-GPU-NC8as-T4",
) -> object:
    prediction = _prediction(
        prediction_id=f"pred-{sample}",
        predicted_cost_usd=predicted,
        conservative_ceiling_usd=max(predicted * 1.5, actual),
        sku=sku,
    )
    return AzureCostReconciler(_QueryClient([_line(actual)])).reconcile(
        prediction, as_of=_END + timedelta(hours=12)
    )


class TestCohortMetrics:
    def test_under_20_samples_is_uncalibrated_and_conservative(self) -> None:
        samples = [
            _reconciled_sample(i, predicted=1.0, actual=1.05)
            for i in range(19)
        ]
        metrics = build_cohort_metrics(samples)  # type: ignore[arg-type]
        assert metrics.state is AzureCostCohortState.UNCALIBRATED
        assert metrics.sample_count == 19
        assert metrics.mape_pct == pytest.approx(100 * 0.05 / 1.05)
        assert metrics.conservative_multiplier >= 1.05

    def test_20_accurate_non_underpredicted_samples_are_calibrated(self) -> None:
        samples = [
            _reconciled_sample(i, predicted=1.02, actual=1.0)
            for i in range(20)
        ]
        metrics = build_cohort_metrics(samples)  # type: ignore[arg-type]
        assert metrics.state is AzureCostCohortState.CALIBRATED
        assert metrics.mape_pct == pytest.approx(2.0)
        assert metrics.p95_absolute_percentage_error_pct == pytest.approx(2.0)
        assert metrics.bias_pct == pytest.approx(-2.0)
        assert metrics.systematic_underprediction is False

    def test_systematic_underprediction_requires_recalibration(self) -> None:
        samples = [
            _reconciled_sample(i, predicted=0.95, actual=1.0)
            for i in range(20)
        ]
        metrics = build_cohort_metrics(samples)  # type: ignore[arg-type]
        assert metrics.mape_pct == pytest.approx(5.0)
        assert metrics.state is AzureCostCohortState.RECALIBRATION_REQUIRED
        assert metrics.systematic_underprediction is True
        assert metrics.conservative_multiplier >= (1 / 0.95) - 1e-12

    def test_p95_over_20_percent_requires_recalibration(self) -> None:
        samples = [
            _reconciled_sample(i, predicted=1.0, actual=1.0)
            for i in range(18)
        ] + [
            _reconciled_sample(18, predicted=1.0, actual=2.0),
            _reconciled_sample(19, predicted=1.0, actual=2.0),
        ]
        metrics = build_cohort_metrics(samples)  # type: ignore[arg-type]
        assert metrics.p95_absolute_percentage_error_pct == pytest.approx(50.0)
        assert metrics.state is AzureCostCohortState.RECALIBRATION_REQUIRED

    def test_mixed_cohorts_and_pending_samples_are_rejected(self) -> None:
        mixed = [
            _reconciled_sample(1, predicted=1.0, actual=1.0),
            _reconciled_sample(
                2,
                predicted=1.0,
                actual=1.0,
                sku="Consumption-GPU-NC24-A100",
            ),
        ]
        with pytest.raises(ValueError, match="homogeneous"):
            build_cohort_metrics(mixed)  # type: ignore[arg-type]

        pending = AzureCostReconciler(_QueryClient([])).reconcile(
            replace(_prediction(), prediction_id="pending"),
            as_of=_END + timedelta(hours=1),
        )
        with pytest.raises(ValueError, match="reconciled"):
            build_cohort_metrics([pending])
