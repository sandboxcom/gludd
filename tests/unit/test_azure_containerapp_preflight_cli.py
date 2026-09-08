"""Operator CLI contracts for secret-safe live Container Apps preflight."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from scripts.azure_containerapp_preflight import main

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
TENANT_ID = "22222222-3333-4444-5555-666666666666"
CLIENT_ID = "33333333-4444-5555-6666-777777777777"
SECRET_VALUE = "fixture-value-never-print"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
RESOURCE_GROUP = "gludd-models-eastus"
ENVIRONMENT = "gludd-gpu-environment"
PROFILE_NAME = "gpu-t4"


def _args(
    path: Path,
    *,
    live: str = "1",
    weight_bits: str = "16",
) -> list[str]:
    return [
        "--auth-file",
        str(path),
        "--subscription-id",
        SUBSCRIPTION_ID,
        "--resource-group",
        RESOURCE_GROUP,
        "--environment",
        ENVIRONMENT,
        "--workload-profile-name",
        PROFILE_NAME,
        "--location",
        "eastus",
        "--model-id",
        "Qwen/Qwen2.5-0.5B-Instruct",
        "--model-revision",
        REVISION,
        "--parameter-count",
        "494032768",
        "--weight-bits",
        weight_bits,
        "--kv-cache-mib",
        "2048",
        "--runtime-overhead-mib",
        "3072",
        "--live",
        live,
    ]


def _auth_file(tmp_path: Path) -> Path:
    path = tmp_path / "private-azure-auth.json"
    path.write_text(
        json.dumps(
            {
                "clientId": CLIENT_ID,
                "clientSecret": SECRET_VALUE,
                "subscriptionId": SUBSCRIPTION_ID,
                "tenantId": TENANT_ID,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


@dataclass
class _Token:
    token: str


class _Credential:
    def __init__(self, *, fail_close: bool = False) -> None:
        self.closed = False
        self.fail_close = fail_close

    def get_token(self, *_scopes: str) -> _Token:
        return _Token("opaque-token")

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError(f"credential cleanup {SECRET_VALUE}")


class _Transport:
    def __init__(self, *, fail: bool = False, quota_pending: bool = False) -> None:
        self.closed = False
        self.fail = fail
        self.quota_pending = quota_pending

    def get_json(self, path: str, _bearer_token: str) -> object:
        if self.fail:
            raise RuntimeError(f"provider secret {SECRET_VALUE}")
        if "/workloadProfileStates?" in path:
            if self.quota_pending:
                return {"value": []}
            return {
                "value": [
                    {
                        "name": PROFILE_NAME,
                        "properties": {
                            "currentCount": 0,
                            "maximumCount": 1,
                            "minimumCount": 0,
                        },
                    }
                ]
            }
        if "/usages?" in path:
            return {
                "value": [
                    {
                        "name": {"value": "ConsumptionGpuT4"},
                        "currentValue": 0,
                        "limit": 1,
                        "unit": "Count",
                    }
                ]
            }
        return {
            "id": (
                f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/"
                f"providers/Microsoft.App/managedEnvironments/{ENVIRONMENT}"
            ),
            "name": ENVIRONMENT,
            "type": "Microsoft.App/managedEnvironments",
            "location": "eastus",
            "properties": {
                "provisioningState": "Succeeded",
                "workloadProfiles": [
                    {
                        "name": PROFILE_NAME,
                        "workloadProfileType": "Consumption-GPU-NC8as-T4",
                        "minimumCount": 0,
                        "maximumCount": 1,
                    }
                ],
            },
        }

    def close(self) -> None:
        self.closed = True


def test_live_preflight_emits_phase_events_and_closes_clients(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _auth_file(tmp_path)
    credential = _Credential()
    transport = _Transport()
    factory_kwargs: dict[str, object] = {}

    def credential_factory(**kwargs: object) -> _Credential:
        factory_kwargs.update(kwargs)
        return credential

    result = main(
        _args(path),
        credential_factory=credential_factory,
        transport_factory=lambda: transport,
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "phase=authentication_started" in captured.out
    assert "phase=environment_discovered" in captured.out
    assert "phase=quota_discovered" in captured.out
    assert "phase=workload_profile_state_discovered" in captured.out
    assert "AZURE_CONTAINERAPP_PREFLIGHT_READY profile=T4" in captured.out
    assert "quota_verified=true" in captured.out
    assert "quota_scope=environment" in captured.out
    assert captured.err == ""
    assert factory_kwargs == {
        "tenant_id": TENANT_ID,
        "client_id": CLIENT_ID,
        "client_secret": SECRET_VALUE,
        "authority": "login.microsoftonline.com",
        "disable_instance_discovery": True,
        "retry_total": 0,
    }
    assert credential.closed is True
    assert transport.closed is True
    for forbidden in (SECRET_VALUE, str(path), SUBSCRIPTION_ID, CLIENT_ID, TENANT_ID, REVISION, "Qwen"):
        assert forbidden not in captured.out


def test_plan_mode_right_sizes_without_reading_credentials_or_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    absent = tmp_path / "absent.json"
    called = False

    def forbidden_factory(**_kwargs: object) -> _Credential:
        nonlocal called
        called = True
        return _Credential()

    result = main(
        _args(absent, live="0"),
        credential_factory=forbidden_factory,
        transport_factory=lambda: pytest.fail("transport must not be built"),
    )
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.startswith("AZURE_CONTAINERAPP_PREFLIGHT_PLAN profile=T4 ")
    assert "secret_output=false" in captured.out
    assert captured.err == ""
    assert called is False


def test_bad_credential_file_fails_without_constructing_clients(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _auth_file(tmp_path)
    path.chmod(0o644)

    result = main(
        _args(path),
        credential_factory=lambda **_kwargs: pytest.fail("credential client must not be built"),
        transport_factory=lambda: pytest.fail("transport must not be built"),
    )
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "AZURE_CONTAINERAPP_PREFLIGHT_INVALID" in captured.err
    assert SECRET_VALUE not in captured.err
    assert str(path) not in captured.err


def test_provider_failure_is_redacted_and_clients_still_close(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _auth_file(tmp_path)
    credential = _Credential()
    transport = _Transport(fail=True)

    result = main(
        _args(path),
        credential_factory=lambda **_kwargs: credential,
        transport_factory=lambda: transport,
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "reason=environment_read_failed" in captured.out
    assert "AZURE_CONTAINERAPP_PREFLIGHT_INVALID" in captured.err
    assert SECRET_VALUE not in captured.out + captured.err
    assert credential.closed is True
    assert transport.closed is True


def test_environment_usage_verifies_quota_when_profile_state_is_pending(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _auth_file(tmp_path)

    result = main(
        _args(path),
        credential_factory=lambda **_kwargs: _Credential(),
        transport_factory=lambda: _Transport(quota_pending=True),
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "quota_verified=true" in captured.out
    assert "AZURE_CONTAINERAPP_PREFLIGHT_READY" in captured.out
    assert "AZURE_CONTAINERAPP_PREFLIGHT_INVALID" not in captured.err


def test_cleanup_failure_overrides_success_and_is_secret_free(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _auth_file(tmp_path)
    credential = _Credential(fail_close=True)
    transport = _Transport()

    result = main(
        _args(path),
        credential_factory=lambda **_kwargs: credential,
        transport_factory=lambda: transport,
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "reason=Azure preflight client cleanup failed" in captured.err
    assert SECRET_VALUE not in captured.out + captured.err
    assert credential.closed is True
    assert transport.closed is True


def test_unexpected_client_initialization_failure_is_redacted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _auth_file(tmp_path)

    def fail_factory(**_kwargs: object) -> _Credential:
        raise RuntimeError(f"client initialization {SECRET_VALUE}")

    result = main(
        _args(path),
        credential_factory=fail_factory,
        transport_factory=lambda: pytest.fail("transport must not be built"),
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "reason=Azure preflight client initialization failed" in captured.err
    assert SECRET_VALUE not in captured.out + captured.err


def test_invalid_model_sizing_stops_before_credentials_or_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _auth_file(tmp_path)

    result = main(
        _args(path, weight_bits="3"),
        credential_factory=lambda **_kwargs: pytest.fail(
            "credential client must not be built"
        ),
        transport_factory=lambda: pytest.fail("transport must not be built"),
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "AZURE_CONTAINERAPP_PREFLIGHT_INVALID" in captured.err
    assert SECRET_VALUE not in captured.out + captured.err
