"""Structural tests for infra/cost_tracker.py — InfraCostTracker."""

from __future__ import annotations

import pytest

from general_ludd.infra.cost_tracker import (
    _INFRA_RATES,
    InfraCostRecord,
    InfraCostTracker,
    _rate_key,
)


class TestInfraCostRecord:
    def test_creation_minimal(self):
        rec = InfraCostRecord(
            provider="aws",
            resource_type="gpu_instance",
            resource_id="i-123",
            cost_usd=5.0,
        )
        assert rec.provider == "aws"
        assert rec.resource_type == "gpu_instance"
        assert rec.resource_id == "i-123"
        assert rec.cost_usd == 5.0

    def test_defaults_none(self):
        rec = InfraCostRecord(
            provider="aws",
            resource_type="gpu_instance",
            resource_id="i-123",
            cost_usd=5.0,
        )
        assert rec.sku is None
        assert rec.gpu_type is None
        assert rec.gpu_count is None
        assert rec.region is None
        assert rec.project_id is None
        assert rec.spot is False
        assert rec.notes == ""


class TestRateKey:
    def test_formats_correctly(self):
        assert _rate_key("aws", "p4d.24xlarge") == "aws/p4d.24xlarge"

    def test_with_underscores(self):
        assert _rate_key("vast_ai", "RTX-4090-1x") == "vast_ai/RTX-4090-1x"


class TestInfraRates:
    def test_known_rate_present(self):
        assert "aws/p4d.24xlarge" in _INFRA_RATES
        assert _INFRA_RATES["aws/p4d.24xlarge"] == 32.77

    def test_azure_rate_present(self):
        assert "azure/Standard_NC6s_v3" in _INFRA_RATES

    def test_vast_ai_rate_present(self):
        assert "vast_ai/RTX-4090-1x" in _INFRA_RATES

    def test_all_rates_positive(self):
        for key, rate in _INFRA_RATES.items():
            assert rate > 0, f"{key} rate must be positive, got {rate}"


class TestInfraCostTrackerInit:
    def test_default_total_zero(self):
        tracker = InfraCostTracker()
        assert tracker.total_cost() == 0.0

    def test_empty_costs_by_provider(self):
        tracker = InfraCostTracker()
        assert tracker.cost_by_provider() == {}

    def test_empty_costs_by_project(self):
        tracker = InfraCostTracker()
        assert tracker.cost_by_project() == {}

    def test_empty_costs_by_resource_type(self):
        tracker = InfraCostTracker()
        assert tracker.cost_by_resource_type() == {}

    def test_empty_records(self):
        tracker = InfraCostTracker()
        assert tracker.records() == []


class TestInfraCostTrackerRecord:
    def test_records_and_returns_record(self):
        tracker = InfraCostTracker()
        rec = tracker.record(
            provider="aws",
            resource_type="gpu_instance",
            resource_id="i-123",
            cost_usd=5.0,
        )
        assert rec.provider == "aws"
        assert rec.cost_usd == 5.0

    def test_accumulate_total(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0)
        tracker.record("aws", "gpu_instance", "i-2", 5.0)
        assert tracker.total_cost() == 15.0

    def test_accumulate_by_provider(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0)
        tracker.record("gcp", "gpu_instance", "i-2", 5.0)
        by_prov = tracker.cost_by_provider()
        assert by_prov == {"aws": 10.0, "gcp": 5.0}

    def test_accumulate_by_resource_type(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0)
        tracker.record("aws", "storage", "vol-1", 3.0)
        by_type = tracker.cost_by_resource_type()
        assert by_type == {"gpu_instance": 10.0, "storage": 3.0}

    def test_accumulate_by_project(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0, project_id="proj-a")
        tracker.record("aws", "gpu_instance", "i-2", 5.0, project_id="proj-b")
        tracker.record("aws", "gpu_instance", "i-3", 3.0)
        by_proj = tracker.cost_by_project()
        assert by_proj == {"proj-a": 10.0, "proj-b": 5.0}

    def test_negative_cost_raises(self):
        tracker = InfraCostTracker()
        with pytest.raises(ValueError, match="cost_usd"):
            tracker.record("aws", "gpu", "i-1", -1.0)

    def test_inf_cost_raises(self):
        tracker = InfraCostTracker()
        with pytest.raises(ValueError, match="cost_usd"):
            tracker.record("aws", "gpu", "i-1", float("inf"))

    def test_nan_cost_raises(self):
        tracker = InfraCostTracker()
        with pytest.raises(ValueError, match="cost_usd"):
            tracker.record("aws", "gpu", "i-1", float("nan"))

    def test_records_list_grows(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu", "i-1", 1.0)
        tracker.record("aws", "gpu", "i-2", 2.0)
        assert len(tracker.records()) == 2


class TestInfraCostTrackerSnapshot:
    def test_snapshot_shape(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0, project_id="p1")
        snap = tracker.snapshot()
        assert "total_cost" in snap
        assert "by_provider" in snap
        assert "by_resource_type" in snap
        assert "by_project" in snap
        assert "record_count" in snap

    def test_snapshot_values(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0)
        tracker.record("gcp", "storage", "vol-1", 5.0)
        snap = tracker.snapshot()
        assert snap["total_cost"] == 15.0
        assert snap["record_count"] == 2
        assert snap["by_provider"]["aws"] == 10.0
        assert snap["by_provider"]["gcp"] == 5.0


class TestInfraCostTrackerProviderBreakdown:
    def test_single_provider_breakdown(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0)
        tracker.record("aws", "storage", "vol-1", 3.0)
        tracker.record("gcp", "gpu_instance", "i-2", 5.0)
        breakdown = tracker.provider_breakdown("aws")
        assert breakdown == {"gpu_instance": 10.0, "storage": 3.0}

    def test_empty_breakdown_for_unknown_provider(self):
        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0)
        assert tracker.provider_breakdown("gcp") == {}


class TestInfraCostTrackerHourlyRate:
    def test_known_fallback_rate(self):
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("aws", "p4d.24xlarge")
        assert rate == 32.77

    def test_known_gcp_rate(self):
        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("gcp", "a2-highgpu-1g")
        assert rate == 3.673


class TestInfraCostTrackerCostForDuration:
    def test_one_hour(self):
        tracker = InfraCostTracker()
        cost = tracker.cost_for_duration("aws", "p4d.24xlarge", 1.0)
        assert cost == 32.77

    def test_half_hour(self):
        tracker = InfraCostTracker()
        cost = tracker.cost_for_duration("aws", "p4d.24xlarge", 0.5)
        assert cost == 16.385


class TestInfraCostTrackerRecordByDuration:
    def test_auto_generates_resource_id(self):
        tracker = InfraCostTracker()
        rec = tracker.record_by_duration(
            provider="aws",
            sku="p4d.24xlarge",
            duration_hours=1.0,
            resource_type="gpu_instance",
        )
        assert rec.resource_id.startswith("aws-p4d.24xlarge-")
        assert rec.cost_usd == 32.77

    def test_uses_provided_resource_id(self):
        tracker = InfraCostTracker()
        rec = tracker.record_by_duration(
            provider="aws",
            sku="p4d.24xlarge",
            duration_hours=1.0,
            resource_type="gpu_instance",
            resource_id="my-custom-id",
        )
        assert rec.resource_id == "my-custom-id"


class TestInfraCostTrackerRecordTerraform:
    def test_records_terraform_cost(self):
        tracker = InfraCostTracker()
        rec = tracker.record_terraform(
            resource_address="aws_instance.gpu_node",
            monthly_cost_usd=100.0,
            project_id="tf-proj",
        )
        assert rec.provider == "terraform"
        assert rec.resource_type == "terraform_resource"
        assert rec.resource_id == "aws_instance.gpu_node"
        assert rec.cost_usd == 100.0
        assert rec.project_id == "tf-proj"


class TestInfraCostTrackerCatalogRate:
    def test_returns_none_when_no_catalog(self):
        tracker = InfraCostTracker()
        assert tracker._catalog_rate("aws", "any-sku", spot=False) is None
