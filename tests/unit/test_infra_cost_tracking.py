"""TDD tests for InfraCostTracker — cloud infrastructure cost tracking.

Covers:
  - Rate lookup: built-in rates for AWS, GCP, Azure, RunPod, Vast.ai
  - Rate lookup: PricingCatalog primary, built-in fallback
  - Cost accumulation: per-provider, per-resource-type, per-project
  - record_by_duration convenience method
  - record_terraform for Terraform-provisioned resources
  - Record validation (negative costs rejected)
  - Snapshot serialization
  - Thread safety
  - /api/costs endpoint: combined model API + infra breakdown
  - /api/costs endpoint: fallback when no trackers configured
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.infra.cost_tracker import (
    CloudProvider,
    InfraCostRecord,
    InfraCostTracker,
    ResourceType,
)
from general_ludd.infra.pricing import INFRA_PRICING
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
)
from general_ludd.routers.spend import register as register_spend

# ── Helpers ──────────────────────────────────────────────────────────────


class _FakeCatalog:
    """Deterministic PricingCatalog stand-in for testing rate lookup."""

    def __init__(
        self,
        prices: Sequence[ComputePrice | None] | None = None,
        boom: bool = False,
    ) -> None:
        self._prices = list(prices) if prices is not None else []
        self._boom = boom
        self.calls: list[tuple[str, str, bool]] = []

    def compute_price(
        self,
        provider: str,
        sku: str,
        spot: bool = False,
        refresh: bool = False,
    ) -> ComputePrice | None:
        self.calls.append((provider, sku, spot))
        if self._boom:
            raise RuntimeError("catalog network down")
        if not self._prices:
            return None
        return self._prices.pop(0)


def _cp(
    provider: str,
    sku: str,
    usd_per_unit: float,
    granularity: BillingGranularity = BillingGranularity.per_second,
    spot: bool = False,
) -> ComputePrice:
    return ComputePrice(
        provider=provider,
        sku=sku,
        usd_per_unit=usd_per_unit,
        granularity=granularity,
        spot=spot,
        terms=BillingTerms.prepaid_balance,
        source="fake-test-source",
    )


# ── Rate lookup tests ────────────────────────────────────────────────────


class TestHourlyRateLookup:
    def test_aws_gpu_rate_from_builtin(self) -> None:
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("aws", "p4d.24xlarge")
        assert rate == pytest.approx(32.77)

    def test_gcp_gpu_rate_from_builtin(self) -> None:
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("gcp", "a2-highgpu-1g")
        assert rate == pytest.approx(3.673)

    def test_azure_gpu_rate_from_builtin(self) -> None:
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("azure", "Standard_NC6s_v3")
        assert rate == pytest.approx(3.06)

    def test_runpod_rate_from_builtin(self) -> None:
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("runpod", "H100-SXM5-80GB-1x")
        assert rate == pytest.approx(4.69)

    def test_vast_ai_rate_from_builtin(self) -> None:
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("vast_ai", "A100-SXM4-80GB-1x")
        assert rate == pytest.approx(1.80)

    def test_unknown_provider_sku_falls_back_to_gpu_second(self) -> None:
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("unknown", "nonexistent")
        expected = INFRA_PRICING["gpu_second"] * 3600.0
        assert rate == pytest.approx(expected)

    def test_catalog_overrides_builtin(self) -> None:
        cat = _FakeCatalog(prices=[_cp("aws", "p4d.24xlarge", 0.001)])
        tracker = InfraCostTracker(catalog=cat)
        rate = tracker.hourly_rate_usd("aws", "p4d.24xlarge")
        assert rate == pytest.approx(0.001 * 3600)
        assert len(cat.calls) == 1

    def test_catalog_raising_falls_back_to_builtin(self) -> None:
        tracker = InfraCostTracker(catalog=_FakeCatalog(boom=True))
        rate = tracker.hourly_rate_usd("aws", "p4d.24xlarge")
        assert rate == pytest.approx(32.77)

    def test_catalog_miss_falls_back_to_builtin(self) -> None:
        tracker = InfraCostTracker(catalog=_FakeCatalog(prices=[None]))
        rate = tracker.hourly_rate_usd("aws", "p4d.24xlarge")
        assert rate == pytest.approx(32.77)


class TestCostForDuration:
    def test_one_hour_at_known_rate(self) -> None:
        tracker = InfraCostTracker()
        cost = tracker.cost_for_duration("aws", "p3.2xlarge", 2.5)
        assert cost == pytest.approx(3.06 * 2.5)

    def test_zero_duration_is_zero_cost(self) -> None:
        tracker = InfraCostTracker()
        assert tracker.cost_for_duration("aws", "p3.2xlarge", 0.0) == 0.0


# ── Cost accumulation tests ──────────────────────────────────────────────


class TestRecord:
    def test_record_single_cost(self) -> None:
        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-12345", 1.50)
        assert tracker.total_cost() == pytest.approx(1.50)

    def test_record_multiple_costs_accumulate(self) -> None:
        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 1.0)
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-2", 2.0)
        tracker.record("gcp", ResourceType.CPU_INSTANCE, "ci-1", 0.5)
        assert tracker.total_cost() == pytest.approx(3.5)
        assert len(tracker.records()) == 3

    def test_record_negative_cost_raises(self) -> None:
        tracker = InfraCostTracker()
        with pytest.raises(ValueError, match="cost_usd"):
            tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", -1.0)

    def test_record_nan_cost_raises(self) -> None:
        tracker = InfraCostTracker()
        with pytest.raises(ValueError, match="cost_usd"):
            tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", float("nan"))

    def test_record_inf_cost_raises(self) -> None:
        tracker = InfraCostTracker()
        with pytest.raises(ValueError, match="cost_usd"):
            tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", float("inf"))

    def test_record_returns_infra_cost_record(self) -> None:
        tracker = InfraCostTracker()
        rec = tracker.record(
            "aws",
            ResourceType.GPU_INSTANCE,
            "i-abc",
            2.50,
            sku="p4d.24xlarge",
            gpu_type="A100",
            gpu_count=8,
            region="us-east-1",
            project_id="proj-1",
            spot=False,
            notes="oneday test cluster",
        )
        assert isinstance(rec, InfraCostRecord)
        assert rec.provider == "aws"
        assert rec.resource_id == "i-abc"
        assert rec.cost_usd == pytest.approx(2.50)
        assert rec.sku == "p4d.24xlarge"
        assert rec.gpu_type == "A100"
        assert rec.gpu_count == 8
        assert rec.region == "us-east-1"
        assert rec.project_id == "proj-1"


class TestRecordByDuration:
    def test_one_hour_gpu_usage_recorded_correctly(self) -> None:
        tracker = InfraCostTracker()
        rec = tracker.record_by_duration(
            "aws", "p4d.24xlarge", 1.0,
            resource_type=ResourceType.GPU_INSTANCE,
            gpu_type="A100",
            gpu_count=8,
            region="us-east-1",
            project_id="proj-1",
        )
        assert rec.cost_usd == pytest.approx(32.77)
        assert rec.provider == "aws"
        assert rec.sku == "p4d.24xlarge"
        assert tracker.total_cost() == pytest.approx(32.77)

    def test_auto_generated_resource_id(self) -> None:
        tracker = InfraCostTracker()
        rec = tracker.record_by_duration("aws", "p3.2xlarge", 0.5)
        assert rec.resource_id is not None
        assert rec.resource_id.startswith("aws-p3.2xlarge-")

    def test_spot_routing_to_catalog(self) -> None:
        cat = _FakeCatalog(
            prices=[_cp("aws", "p4d.24xlarge-spot", 0.001, spot=True)]
        )
        tracker = InfraCostTracker(catalog=cat)
        rec = tracker.record_by_duration("aws", "p4d.24xlarge", 1.0, spot=True)
        assert rec.spot is True
        assert cat.calls[0][2] is True


class TestRecordTerraform:
    def test_records_terraform_resource(self) -> None:
        tracker = InfraCostTracker()
        rec = tracker.record_terraform(
            "aws_instance.gpu_node",
            120.50,
            project_id="proj-tf",
        )
        assert rec.resource_type == ResourceType.TERRAFORM_RESOURCE
        assert rec.resource_id == "aws_instance.gpu_node"
        assert rec.cost_usd == pytest.approx(120.50)
        assert rec.project_id == "proj-tf"


# ── Breakdown query tests ────────────────────────────────────────────────


class TestBreakdownQueries:
    def test_cost_by_provider(self) -> None:
        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 10.0)
        tracker.record("aws", ResourceType.CPU_INSTANCE, "ci-1", 5.0)
        tracker.record("gcp", ResourceType.GPU_INSTANCE, "gi-1", 7.0)
        tracker.record("azure", ResourceType.CPU_INSTANCE, "azi-1", 3.0)

        by_prov = tracker.cost_by_provider()
        assert by_prov["aws"] == pytest.approx(15.0)
        assert by_prov["gcp"] == pytest.approx(7.0)
        assert by_prov["azure"] == pytest.approx(3.0)

    def test_cost_by_resource_type(self) -> None:
        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 10.0)
        tracker.record("gcp", ResourceType.GPU_INSTANCE, "gi-1", 5.0)
        tracker.record("aws", ResourceType.CPU_INSTANCE, "ci-1", 2.0)
        tracker.record("aws", ResourceType.STORAGE, "vol-1", 1.0)

        by_type = tracker.cost_by_resource_type()
        assert by_type[ResourceType.GPU_INSTANCE] == pytest.approx(15.0)
        assert by_type[ResourceType.CPU_INSTANCE] == pytest.approx(2.0)
        assert by_type[ResourceType.STORAGE] == pytest.approx(1.0)

    def test_cost_by_project(self) -> None:
        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 10.0, project_id="p1")
        tracker.record("gcp", ResourceType.GPU_INSTANCE, "gi-1", 5.0, project_id="p1")
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-2", 3.0, project_id="p2")

        by_proj = tracker.cost_by_project()
        assert by_proj["p1"] == pytest.approx(15.0)
        assert by_proj["p2"] == pytest.approx(3.0)

    def test_provider_breakdown_per_resource_type(self) -> None:
        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 10.0)
        tracker.record("aws", ResourceType.CPU_INSTANCE, "ci-1", 5.0)
        tracker.record("aws", ResourceType.STORAGE, "vol-1", 2.0)
        tracker.record("gcp", ResourceType.GPU_INSTANCE, "gi-1", 7.0)

        aws_bd = tracker.provider_breakdown("aws")
        assert aws_bd[ResourceType.GPU_INSTANCE] == pytest.approx(10.0)
        assert aws_bd[ResourceType.CPU_INSTANCE] == pytest.approx(5.0)
        assert aws_bd[ResourceType.STORAGE] == pytest.approx(2.0)

        gcp_bd = tracker.provider_breakdown("gcp")
        assert gcp_bd[ResourceType.GPU_INSTANCE] == pytest.approx(7.0)

    def test_empty_tracker_returns_zeros(self) -> None:
        tracker = InfraCostTracker()
        assert tracker.total_cost() == 0.0
        assert tracker.cost_by_provider() == {}
        assert tracker.cost_by_resource_type() == {}
        assert tracker.cost_by_project() == {}
        assert tracker.records() == []
        assert tracker.provider_breakdown("aws") == {}


# ── Snapshot tests ───────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_reflects_current_state(self) -> None:
        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 10.0, project_id="p1")
        tracker.record("gcp", ResourceType.GPU_INSTANCE, "gi-1", 5.0)

        snap = tracker.snapshot()
        assert snap["total_cost"] == pytest.approx(15.0)
        assert snap["by_provider"]["aws"] == pytest.approx(10.0)
        assert snap["by_provider"]["gcp"] == pytest.approx(5.0)
        assert snap["by_resource_type"][ResourceType.GPU_INSTANCE] == pytest.approx(15.0)
        assert snap["by_project"]["p1"] == pytest.approx(10.0)
        assert snap["record_count"] == 2

    def test_snapshot_is_serializable(self) -> None:
        import json

        tracker = InfraCostTracker()
        tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 5.0)
        snap = tracker.snapshot()
        encoded = json.dumps(snap)
        decoded = json.loads(encoded)
        assert decoded["total_cost"] == 5.0


# ── Thread safety tests ──────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_records_accumulate_correctly(self) -> None:
        tracker = InfraCostTracker()
        n_threads = 10
        n_records_per = 100
        errors: list[Exception] = []

        def worker(thread_idx: int) -> None:
            try:
                for i in range(n_records_per):
                    tracker.record(
                        f"provider-{thread_idx}",
                        ResourceType.GPU_INSTANCE,
                        f"i-{thread_idx}-{i}",
                        0.01,
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors in concurrent record: {errors}"
        expected_total = n_threads * n_records_per * 0.01
        assert tracker.total_cost() == pytest.approx(expected_total)
        assert len(tracker.records()) == n_threads * n_records_per


# ── /api/costs endpoint tests ────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    register_spend(app, {})
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestApiCostsEndpoint:
    def test_no_trackers_configured_returns_zeros(self, client: TestClient) -> None:
        resp = client.get("/api/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_api"] == pytest.approx(0.0)
        assert data["infrastructure"] == pytest.approx(0.0)
        assert data["total"] == pytest.approx(0.0)
        assert data["breakdown_by_provider"] == {}
        assert data["breakdown_by_resource_type"] == {}
        assert data["breakdown_by_project"] == {}
        assert data["record_count"] == 0

    def test_with_spend_limiter_only_reports_api_cost(
        self, app: FastAPI, client: TestClient
    ) -> None:
        clock = lambda: 1000.0  # noqa: E731
        limiter = SpendLimiter(limit_usd=50.0, window_seconds=3600.0, clock=clock)
        limiter.record(12.50, kind="token", at=1000.0)
        app.state._spend_limiter = limiter

        resp = client.get("/api/costs")
        data = resp.json()
        assert data["model_api"] == pytest.approx(12.50)
        assert data["infrastructure"] == pytest.approx(0.0)
        assert data["total"] == pytest.approx(12.50)

    def test_with_infra_tracker_v2_reports_combined_breakdown(
        self, app: FastAPI, client: TestClient
    ) -> None:
        clock = lambda: 1000.0  # noqa: E731
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        limiter.record(20.0, kind="token", at=1000.0)
        app.state._spend_limiter = limiter

        cost_tracker = InfraCostTracker()
        cost_tracker.record("aws", ResourceType.GPU_INSTANCE, "i-1", 15.0, project_id="p1")
        cost_tracker.record("gcp", ResourceType.CPU_INSTANCE, "ci-1", 5.0, project_id="p1")
        cost_tracker.record("runpod", ResourceType.GPU_INSTANCE, "r-1", 3.0)
        app.state._infra_cost_tracker = cost_tracker

        resp = client.get("/api/costs")
        data = resp.json()

        assert data["model_api"] == pytest.approx(20.0)
        assert data["infrastructure"] == pytest.approx(23.0)
        assert data["total"] == pytest.approx(43.0)
        assert data["breakdown_by_provider"]["aws"] == pytest.approx(15.0)
        assert data["breakdown_by_provider"]["gcp"] == pytest.approx(5.0)
        assert data["breakdown_by_provider"]["runpod"] == pytest.approx(3.0)
        assert data["breakdown_by_resource_type"]["gpu_instance"] == pytest.approx(18.0)
        assert data["breakdown_by_resource_type"]["cpu_instance"] == pytest.approx(5.0)
        assert data["breakdown_by_project"]["p1"] == pytest.approx(20.0)
        assert data["record_count"] == 3

    def test_falls_back_to_infra_tracker_v1_when_v2_not_present(
        self, app: FastAPI, client: TestClient
    ) -> None:
        clock = lambda: 1000.0  # noqa: E731
        limiter = SpendLimiter(limit_usd=50.0, window_seconds=3600.0, clock=clock)
        limiter.record(5.0, kind="token", at=1000.0)
        app.state._spend_limiter = limiter

        from general_ludd.infra.pricing import InfraTracker

        v1 = InfraTracker()
        v1.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 3600)
        app.state._infra_tracker = v1

        resp = client.get("/api/costs")
        data = resp.json()
        assert data["model_api"] == pytest.approx(5.0)
        assert data["infrastructure"] > 0.0
        assert data["total"] > 5.0
        assert "runpod" in data["breakdown_by_provider"]


# ── CloudProvider and ResourceType enum tests ─────────────────────────────


class TestCloudProviderEnum:
    def test_all_providers_defined(self) -> None:
        assert CloudProvider.AWS == "aws"
        assert CloudProvider.GCP == "gcp"
        assert CloudProvider.AZURE == "azure"
        assert CloudProvider.RUNPOD == "runpod"
        assert CloudProvider.VAST_AI == "vast_ai"
        assert CloudProvider.LAMBDA_LABS == "lambda_labs"
        assert CloudProvider.TERRAFORM == "terraform"


class TestResourceTypeEnum:
    def test_all_types_defined(self) -> None:
        assert ResourceType.GPU_INSTANCE == "gpu_instance"
        assert ResourceType.CPU_INSTANCE == "cpu_instance"
        assert ResourceType.STORAGE == "storage"
        assert ResourceType.NETWORK == "network"
        assert ResourceType.KUBERNETES == "kubernetes"
        assert ResourceType.MANAGED_SERVICE == "managed_service"
        assert ResourceType.TERRAFORM_RESOURCE == "terraform_resource"


# ── Built-in rate table completeness ──────────────────────────────────────


class TestBuiltinRateCoverage:
    """Verify the built-in rate table covers all major cloud providers."""

    def test_aws_cpu_instances_have_rates(self) -> None:
        tracker = InfraCostTracker()
        assert tracker.hourly_rate_usd("aws", "m7i.xlarge") > 0
        assert tracker.hourly_rate_usd("aws", "c7i.large") > 0
        assert tracker.hourly_rate_usd("aws", "r7i.large") > 0

    def test_gcp_cpu_instances_have_rates(self) -> None:
        tracker = InfraCostTracker()
        assert tracker.hourly_rate_usd("gcp", "n2-standard-4") > 0
        assert tracker.hourly_rate_usd("gcp", "c2-standard-4") > 0

    def test_azure_cpu_instances_have_rates(self) -> None:
        tracker = InfraCostTracker()
        assert tracker.hourly_rate_usd("azure", "Standard_D4s_v5") > 0
        assert tracker.hourly_rate_usd("azure", "Standard_F4s_v2") > 0

    def test_terraform_resources_have_rates(self) -> None:
        tracker = InfraCostTracker()
        assert tracker.hourly_rate_usd("terraform", "aws_eks_cluster") > 0
        assert tracker.hourly_rate_usd("terraform", "gcp_gke_cluster") > 0
        assert tracker.hourly_rate_usd("terraform", "azure_aks_cluster") > 0

    def test_all_rate_lookup_positive(self) -> None:
        """All built-in rates must be positive dollars."""
        from general_ludd.infra.cost_tracker import _INFRA_RATES

        for key, rate in _INFRA_RATES.items():
            assert rate > 0, (
                f"Rate for {key} is {rate} — must be positive"
            )
            assert rate < 500, (
                f"Rate for {key} is {rate} — suspiciously high, validate source"
            )


# ── Cost tracking realism tests ──────────────────────────────────────────


class TestRealWorldScenarios:
    def test_aws_p4d_8_gpu_training_run(self) -> None:
        tracker = InfraCostTracker()
        rec = tracker.record_by_duration(
            "aws", "p4d.24xlarge", 24.0,
            resource_type=ResourceType.GPU_INSTANCE,
            gpu_type="A100",
            gpu_count=8,
            region="us-east-1",
            project_id="training-run-42",
        )
        assert rec.cost_usd == pytest.approx(32.77 * 24.0)
        assert tracker.total_cost() == pytest.approx(32.77 * 24.0)
        assert tracker.cost_by_project()["training-run-42"] == pytest.approx(32.77 * 24.0)

    def test_multi_cloud_multi_resource_accumulation(self) -> None:
        tracker = InfraCostTracker()
        tracker.record_by_duration("aws", "p5.48xlarge", 10.0, project_id="ml-run")
        tracker.record_by_duration("gcp", "a3-highgpu-8g", 5.0, project_id="ml-run")
        tracker.record_by_duration("runpod", "H100-SXM5-80GB-1x", 20.0, project_id="ml-run")
        tracker.record_terraform("aws_instance.gpu_node", 150.0, project_id="ml-run")
        tracker.record("azure", ResourceType.STORAGE, "disk-1", 25.0, project_id="ml-run")

        total = tracker.total_cost()
        assert total > 0
        by_proj = tracker.cost_by_project()
        assert by_proj["ml-run"] == pytest.approx(total)
        by_prov = tracker.cost_by_provider()
        assert "aws" in by_prov
        assert "gcp" in by_prov
        assert "runpod" in by_prov
        assert "azure" in by_prov
        assert "terraform" in by_prov
