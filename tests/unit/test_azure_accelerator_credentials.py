"""Security contracts for Azure CLI ``--json-auth`` credential files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentialError,
    build_azure_accelerator_credentials,
    load_azure_accelerator_credentials,
)

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
TENANT_ID = "22222222-3333-4444-5555-666666666666"
CLIENT_ID = "33333333-4444-5555-6666-777777777777"
SECRET_VALUE = "fixture-value-not-a-real-credential"


def test_validated_value_factory_reuses_the_file_loader_security_contract() -> None:
    credential = build_azure_accelerator_credentials(
        client_id=CLIENT_ID,
        client_secret=SECRET_VALUE,
        subscription_id=SUBSCRIPTION_ID,
        tenant_id=TENANT_ID,
        expected_subscription_id=SUBSCRIPTION_ID,
    )

    assert credential.client_id == CLIENT_ID
    assert credential.subscription_id == SUBSCRIPTION_ID
    assert credential.tenant_id == TENANT_ID
    assert credential.client_secret == SECRET_VALUE
    assert SECRET_VALUE not in repr(credential)


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "not-a-uuid"},
        {"tenant_id": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"},
        {"client_secret": "line\nbreak"},
        {"subscription_id": "44444444-5555-6666-7777-888888888888"},
    ],
)
def test_validated_value_factory_rejects_untrusted_dynamic_values(
    overrides: dict[str, str],
) -> None:
    values = {
        "client_id": CLIENT_ID,
        "client_secret": SECRET_VALUE,
        "subscription_id": SUBSCRIPTION_ID,
        "tenant_id": TENANT_ID,
    }
    values.update(overrides)

    with pytest.raises(AzureAcceleratorCredentialError) as captured:
        build_azure_accelerator_credentials(
            **values,
            expected_subscription_id=SUBSCRIPTION_ID,
        )

    assert SECRET_VALUE not in repr(captured.value)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "clientId": CLIENT_ID,
        "clientSecret": SECRET_VALUE,
        "subscriptionId": SUBSCRIPTION_ID,
        "tenantId": TENANT_ID,
        "activeDirectoryEndpointUrl": "https://login.microsoftonline.com",
        "resourceManagerEndpointUrl": "https://management.azure.com/",
    }
    payload.update(overrides)
    return payload


def _write(path: Path, payload: dict[str, object] | str) -> Path:
    rendered = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(rendered, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_valid_private_json_loads_without_exposing_secret(tmp_path: Path) -> None:
    path = _write(tmp_path / "azure-auth.json", _payload())

    credential = load_azure_accelerator_credentials(
        path,
        expected_subscription_id=SUBSCRIPTION_ID,
    )

    assert credential.client_id == CLIENT_ID
    assert credential.tenant_id == TENANT_ID
    assert credential.subscription_id == SUBSCRIPTION_ID
    assert credential.client_secret == SECRET_VALUE
    assert SECRET_VALUE not in repr(credential)
    assert credential.arm_environment() == {
        "ARM_CLIENT_ID": CLIENT_ID,
        "ARM_CLIENT_SECRET": SECRET_VALUE,
        "ARM_TENANT_ID": TENANT_ID,
        "ARM_SUBSCRIPTION_ID": SUBSCRIPTION_ID,
        "AZURE_CLIENT_ID": CLIENT_ID,
        "AZURE_CLIENT_SECRET": SECRET_VALUE,
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION_ID,
    }


@pytest.mark.parametrize("mode", [0o604, 0o640, 0o660, 0o666])
def test_group_or_other_access_is_rejected(tmp_path: Path, mode: int) -> None:
    path = _write(tmp_path / "azure-auth.json", _payload())
    path.chmod(mode)

    with pytest.raises(AzureAcceleratorCredentialError, match="mode 0600") as captured:
        load_azure_accelerator_credentials(path)

    assert SECRET_VALUE not in repr(captured.value)


def test_symlink_and_non_regular_files_are_rejected(tmp_path: Path) -> None:
    target = _write(tmp_path / "target.json", _payload())
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(AzureAcceleratorCredentialError, match="regular file"):
        load_azure_accelerator_credentials(link)
    with pytest.raises(AzureAcceleratorCredentialError, match="regular file"):
        load_azure_accelerator_credentials(tmp_path)


def test_wrong_owner_is_rejected_without_reading_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write(tmp_path / "azure-auth.json", _payload())
    real_fstat = os.fstat

    def wrong_owner(fd: int) -> os.stat_result:
        current = real_fstat(fd)
        values = list(current)
        values[4] = current.st_uid + 1
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", wrong_owner)

    with pytest.raises(AzureAcceleratorCredentialError, match="current user"):
        load_azure_accelerator_credentials(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "valid JSON"),
        ("[]", "JSON object"),
        (
            '{"clientId":"one","clientId":"two","clientSecret":"x",'
            f'"subscriptionId":"{SUBSCRIPTION_ID}","tenantId":"{TENANT_ID}"}}',
            "duplicate field",
        ),
    ],
)
def test_malformed_or_ambiguous_json_is_rejected(
    tmp_path: Path,
    payload: str,
    message: str,
) -> None:
    path = _write(tmp_path / "azure-auth.json", payload)

    with pytest.raises(AzureAcceleratorCredentialError, match=message):
        load_azure_accelerator_credentials(path)


@pytest.mark.parametrize("field", ["clientId", "clientSecret", "subscriptionId", "tenantId"])
def test_required_fields_must_be_present_nonempty_strings(tmp_path: Path, field: str) -> None:
    for replacement in (None, "", "   ", 7):
        payload = _payload()
        if replacement is None:
            payload.pop(field)
        else:
            payload[field] = replacement
        path = _write(tmp_path / f"{field}-{replacement!s}.json", payload)

        with pytest.raises(AzureAcceleratorCredentialError, match="required fields"):
            load_azure_accelerator_credentials(path)


@pytest.mark.parametrize("field", ["clientId", "subscriptionId", "tenantId"])
def test_identifier_fields_must_be_canonical_uuids(tmp_path: Path, field: str) -> None:
    path = _write(tmp_path / "azure-auth.json", _payload(**{field: "not-a-uuid"}))

    with pytest.raises(AzureAcceleratorCredentialError, match="canonical UUID"):
        load_azure_accelerator_credentials(path)


def test_expected_subscription_mismatch_fails_closed(tmp_path: Path) -> None:
    path = _write(tmp_path / "azure-auth.json", _payload())

    with pytest.raises(AzureAcceleratorCredentialError, match="subscription mismatch"):
        load_azure_accelerator_credentials(
            path,
            expected_subscription_id="44444444-5555-6666-7777-888888888888",
        )


@pytest.mark.parametrize(
    "secret",
    ["line\nbreak", "nul\0byte", pytest.param("x" * 4097, id="oversized")],
)
def test_secret_control_characters_and_unbounded_values_are_rejected(
    tmp_path: Path,
    secret: str,
) -> None:
    path = _write(tmp_path / "azure-auth.json", _payload(clientSecret=secret))

    with pytest.raises(AzureAcceleratorCredentialError, match="client secret") as captured:
        load_azure_accelerator_credentials(path)

    assert secret not in repr(captured.value)


def test_untrusted_cloud_endpoints_are_rejected(tmp_path: Path) -> None:
    for field in ("activeDirectoryEndpointUrl", "resourceManagerEndpointUrl"):
        path = _write(
            tmp_path / f"{field}.json",
            _payload(**{field: "https://attacker.invalid/azure"}),
        )

        with pytest.raises(AzureAcceleratorCredentialError, match="Azure public cloud"):
            load_azure_accelerator_credentials(path)


def test_oversized_file_is_rejected_before_json_decode(tmp_path: Path) -> None:
    path = _write(tmp_path / "azure-auth.json", "{" + "x" * 20_000)

    with pytest.raises(AzureAcceleratorCredentialError, match="too large"):
        load_azure_accelerator_credentials(path)


def test_file_growth_after_open_is_still_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write(tmp_path / "azure-auth.json", "{" + "x" * 20_000)
    real_fstat = os.fstat

    def stale_small_size(fd: int) -> os.stat_result:
        current = real_fstat(fd)
        values = list(current)
        values[6] = 1
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", stale_small_size)

    with pytest.raises(AzureAcceleratorCredentialError, match="too large"):
        load_azure_accelerator_credentials(path)


def test_errors_never_include_path_or_secret(tmp_path: Path) -> None:
    path = _write(tmp_path / "private-name-with-context.json", _payload(clientSecret="bad\nsecret"))

    with pytest.raises(AzureAcceleratorCredentialError) as captured:
        load_azure_accelerator_credentials(path)

    rendered = repr(captured.value)
    assert str(path) not in rendered
    assert "bad\\nsecret" not in rendered
