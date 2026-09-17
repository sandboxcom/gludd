"""Secret-safe CLI contracts for Azure accelerator credential validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.validate_azure_accelerator_credentials import SUCCESS_MARKER, main

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
SECRET_VALUE = "fixture-value-not-a-real-credential"


def _credential_file(tmp_path: Path) -> Path:
    path = tmp_path / "azure-auth-private.json"
    path.write_text(
        json.dumps(
            {
                "clientId": "33333333-4444-5555-6666-777777777777",
                "clientSecret": SECRET_VALUE,
                "subscriptionId": SUBSCRIPTION_ID,
                "tenantId": "22222222-3333-4444-5555-666666666666",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_validator_reports_only_a_fixed_success_marker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _credential_file(tmp_path)

    result = main(
        ["--auth-file", str(path), "--subscription-id", SUBSCRIPTION_ID]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.strip() == SUCCESS_MARKER
    assert captured.err == ""
    assert SECRET_VALUE not in captured.out
    assert str(path) not in captured.out


def test_validator_failure_never_prints_secret_or_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _credential_file(tmp_path)

    result = main(
        [
            "--auth-file",
            str(path),
            "--subscription-id",
            "44444444-5555-6666-7777-888888888888",
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("AZURE_ACCELERATOR_AUTH_FILE_INVALID ")
    assert SECRET_VALUE not in captured.err
    assert str(path) not in captured.err


def test_validate_only_checks_arguments_without_reading_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "--auth-file",
            "/tmp/intentionally-absent-azure-auth.json",
            "--subscription-id",
            SUBSCRIPTION_ID,
            "--validate-only",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.strip() == (
        "AZURE_ACCELERATOR_AUTH_CHECK_PLAN secret_output=false"
    )
    assert captured.err == ""


@pytest.mark.parametrize(
    "subscription_id",
    ["not-a-uuid", "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"],
)
def test_validate_only_rejects_noncanonical_subscription_ids(
    subscription_id: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "--auth-file",
            "/tmp/intentionally-absent-azure-auth.json",
            "--subscription-id",
            subscription_id,
            "--validate-only",
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert captured.err == (
        "AZURE_ACCELERATOR_AUTH_FILE_INVALID "
        "reason=credential identifiers must be canonical UUID values\n"
    )
