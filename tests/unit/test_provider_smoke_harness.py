"""Contracts for the non-provisioning Azure accelerator smoke harness."""

from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "provider_smoke_harness.py"
SPEC = importlib.util.spec_from_file_location("provider_smoke_harness", SCRIPT)
assert SPEC and SPEC.loader
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_azure_dry_run_is_safe_without_credentials() -> None:
    result = harness.run_harness("azure", {}, live=False)
    assert result["ok"] is True
    assert result["mode"] == "dry-run"
    assert result["configuration"]["credentials_complete"] is False
    assert result["accelerator"]["vm_size"] == "Standard_NC24ads_A100_v4"
    assert result["live_operations"] == []


def test_azure_dry_run_maps_h100_and_redacts_client_secret() -> None:
    result = harness.run_harness(
        "azure",
        {
            "AZURE_SUBSCRIPTION_ID": "sub-123",
            "AZURE_TENANT_ID": "tenant-456",
            "AZURE_CLIENT_ID": "client-789",
            "AZURE_CLIENT_SECRET": "super-secret",
            "AZURE_GPU_TYPE": "h100",
            "AZURE_GPU_COUNT": "2",
            "AZURE_LOCATION": "eastus",
        },
        live=False,
    )
    assert result["ok"] is True
    assert result["accelerator"]["vm_size"] == "Standard_NC80adis_H100_v5"
    assert result["configuration"]["credentials_complete"] is True
    assert "super-secret" not in str(result)


def test_azure_live_requires_subscription_id() -> None:
    with pytest.raises(harness.HarnessConfigError, match="AZURE_SUBSCRIPTION_ID"):
        harness.run_harness("azure", {}, live=True)


def test_azure_live_accepts_managed_identity_without_client_secret() -> None:
    with patch.object(
        harness,
        "_live_preflight",
        return_value={"ready": True},
    ):
        result = harness.run_harness(
            "azure",
            {
                "AZURE_SUBSCRIPTION_ID": "sub-123",
                "AZURE_CLIENT_ID": "managed-identity-client",
            },
            live=True,
        )

    assert result["ok"] is True
    assert result["configuration"]["auth_mode"] == "managed-identity"
    assert result["configuration"]["client_secret_configured"] is False


def test_harness_rejects_paid_or_unknown_provider_modes() -> None:
    with pytest.raises(harness.HarnessConfigError, match="provider must be azure"):
        harness.run_harness("runpod", {}, live=False)


def test_harness_rejects_invalid_gpu_count_before_any_cloud_call() -> None:
    with pytest.raises(harness.HarnessConfigError, match="AZURE_GPU_COUNT"):
        harness.run_harness(
            "azure",
            {"AZURE_GPU_COUNT": "not-an-int"},
            live=False,
        )


def test_harness_rejects_nonpositive_gpu_count_and_unknown_gpu() -> None:
    with pytest.raises(harness.HarnessConfigError, match="greater than zero"):
        harness.run_harness(
            "azure",
            {"AZURE_GPU_COUNT": "0"},
            live=False,
        )
    with pytest.raises(harness.HarnessConfigError, match="unsupported AZURE_GPU_TYPE"):
        harness.run_harness(
            "azure",
            {"AZURE_GPU_TYPE": "fictional-gpu"},
            live=False,
        )


def test_harness_rejects_incomplete_service_principal_and_shape() -> None:
    with pytest.raises(harness.HarnessConfigError, match="requires"):
        harness.run_harness(
            "azure",
            {"AZURE_CLIENT_SECRET": "configured-without-client"},
            live=False,
        )
    with pytest.raises(harness.HarnessConfigError, match="supported GPU counts"):
        harness.run_harness(
            "azure",
            {"AZURE_GPU_TYPE": "h100", "AZURE_GPU_COUNT": "4"},
            live=False,
        )


def test_telemetry_posts_redacted_event_and_log() -> None:
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b""
    with patch.object(
        harness.urllib.request,
        "urlopen",
        return_value=response,
    ) as urlopen:
        result = harness.run_harness(
            "azure",
            {
                "GLUDD_INGEST_URL": "https://gludd.example",
                "GLUDD_INGEST_TOKEN": "ingest-secret",
            },
            live=False,
        )

    assert result["telemetry"] == {
        "enabled": True,
        "records": 2,
        "url": "https://gludd.example",
    }
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://gludd.example/ingest/webhook"
    assert b"ingest-secret" not in request.data
    assert request.get_header("Authorization") == "Bearer ingest-secret"


def test_telemetry_delivery_failure_is_explicit() -> None:
    with (
        patch.object(
            harness.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ),
        pytest.raises(harness.HarnessConfigError, match="telemetry delivery failed"),
    ):
        harness.run_harness(
            "azure",
            {
                "GLUDD_INGEST_URL": "https://gludd.example",
                "GLUDD_INGEST_TOKEN": "ingest-secret",
            },
            live=False,
        )


def test_main_exit_codes_reflect_success_configuration_error_and_blocker() -> None:
    with patch.object(
        harness,
        "run_harness",
        return_value={"ok": True},
    ):
        assert harness.main(["azure"]) == 0
    with patch.object(
        harness,
        "run_harness",
        side_effect=harness.HarnessConfigError("bad config"),
    ):
        assert harness.main(["azure"]) == 2
    with patch.object(
        harness,
        "run_harness",
        return_value={"ok": False},
    ):
        assert harness.main(["azure"]) == 3
