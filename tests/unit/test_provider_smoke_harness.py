"""Contract tests for the credential-safe Azure/RunPod smoke harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "provider_smoke_harness.py"
SPEC = importlib.util.spec_from_file_location("provider_smoke_harness", SCRIPT)
assert SPEC and SPEC.loader
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_azure_dry_run_accepts_subscription_keys_and_billing_scope() -> None:
    result = harness.run_harness(
        "azure",
        {
            "AZURE_SUBSCRIPTION_ID": "sub-123",
            "AZURE_TENANT_ID": "tenant-456",
            "AZURE_CLIENT_ID": "client-789",
            "AZURE_CLIENT_SECRET": "super-secret",
            "AZURE_BILLING_ACCOUNT_ID": "billing-001",
            "AZURE_BILLING_PROFILE_ID": "profile-002",
            "AZURE_INVOICE_SECTION_ID": "invoice-003",
        },
        live=False,
    )
    assert result["ok"] is True
    assert result["configuration"]["subscription_id"] == "sub-123"
    assert result["configuration"]["billing_account_id"] == "billing-001"
    assert "super-secret" not in str(result)


def test_azure_requires_subscription_and_client_secret_for_live() -> None:
    with pytest.raises(harness.HarnessConfigError, match="AZURE_SUBSCRIPTION_ID"):
        harness.run_harness("azure", {"AZURE_CLIENT_SECRET": "secret"}, live=True)


def test_runpod_dry_run_accepts_key_endpoint_and_budget() -> None:
    result = harness.run_harness(
        "runpod",
        {
            "RUNPOD_API_KEY": "rp-secret",
            "RUNPOD_ENDPOINT_ID": "endpoint-123",
            "RUNPOD_BUDGET_USD": "25.00",
            "RUNPOD_GPU_TYPE": "NVIDIA A100",
        },
        live=False,
    )
    assert result["ok"] is True
    assert result["configuration"]["endpoint_id"] == "endpoint-123"
    assert result["configuration"]["budget_usd"] == 25.0
    assert "rp-secret" not in str(result)


def test_runpod_requires_api_key_for_live() -> None:
    with pytest.raises(harness.HarnessConfigError, match="RUNPOD_API_KEY"):
        harness.run_harness("runpod", {}, live=True)


def test_telemetry_is_optional_and_reports_disabled() -> None:
    result = harness.run_harness("runpod", {"RUNPOD_API_KEY": "rp-secret"}, live=False)
    assert result["telemetry"]["enabled"] is False


def test_telemetry_publishes_event_and_log_without_exposing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: object, timeout: int) -> Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(harness.urllib.request, "urlopen", fake_urlopen)
    result = harness.run_harness(
        "runpod",
        {
            "RUNPOD_API_KEY": "rp-secret",
            "GLUDD_INGEST_URL": "http://gludd.test",
            "GLUDD_INGEST_TOKEN": "ingest-secret",
        },
        live=False,
    )

    request = captured["request"]
    assert result["telemetry"] == {
        "enabled": True,
        "records": 2,
        "url": "http://gludd.test",
    }
    assert request.full_url == "http://gludd.test/ingest/webhook"
    assert b"ingest-secret" not in request.data
    assert b"rp-secret" not in request.data
    assert b'"kind": "events"' in request.data
    assert b'"kind": "logs"' in request.data
