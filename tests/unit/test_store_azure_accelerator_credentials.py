"""Secret-safe CLI coverage for durable Azure credential ingestion."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from scripts.store_azure_accelerator_credentials import main

import general_ludd.azure.accelerator_credential_store as store_module

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
SECRET = "one-time-secret-value"


def _payload() -> bytes:
    return json.dumps(
        {
            "clientId": "01234567-89ab-cdef-0123-456789abcdef",
            "clientSecret": SECRET,
            "subscriptionId": SUBSCRIPTION_ID,
            "tenantId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        }
    ).encode()


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    stream = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stream)


def test_cli_installs_stdin_in_durable_versioned_store_without_secret_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "credentials"
    target = root / "azure-accelerator-auth.json"
    monkeypatch.setattr(store_module, "_reject_unsafe_root", lambda _root: None)
    _stdin(monkeypatch, _payload())

    result = main(
        [
            "--auth-file",
            str(target),
            "--subscription-id",
            SUBSCRIPTION_ID,
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert captured.err == ""
    assert captured.out.startswith("AZURE_ACCELERATOR_AUTH_STORED ")
    assert "immutable_generation=true" in captured.out
    assert "secret_output=false" in captured.out
    assert SECRET not in captured.out
    assert str(target) not in captured.out
    assert target.exists()
    assert len(tuple((root / "generations").glob("*.json"))) == 1


def test_cli_invalid_input_never_replaces_existing_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "credentials"
    target = root / "azure-accelerator-auth.json"
    monkeypatch.setattr(store_module, "_reject_unsafe_root", lambda _root: None)
    _stdin(monkeypatch, _payload())
    assert main(["--auth-file", str(target), "--subscription-id", SUBSCRIPTION_ID]) == 0
    capsys.readouterr()
    before = tuple((root / "generations").glob("*.json"))
    _stdin(monkeypatch, b'{"clientSecret":"' + SECRET.encode() + b'"}')

    result = main(
        ["--auth-file", str(target), "--subscription-id", SUBSCRIPTION_ID]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert target.read_bytes() == _payload()
    assert tuple((root / "generations").glob("*.json")) == before
    assert SECRET not in captured.out + captured.err
    assert str(target) not in captured.out + captured.err


def test_cli_validate_only_checks_destination_without_reading_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "credentials" / "azure-accelerator-auth.json"
    monkeypatch.setattr(store_module, "_reject_unsafe_root", lambda _root: None)
    _stdin(monkeypatch, b"must-not-be-read")

    result = main(
        [
            "--auth-file",
            str(target),
            "--subscription-id",
            SUBSCRIPTION_ID,
            "--validate-only",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.strip() == (
        "AZURE_ACCELERATOR_AUTH_STORE_PLAN durable=true "
        "immutable_generation=true automatic_deletion=false secret_output=false"
    )
    assert captured.err == ""
    assert not target.parent.exists()


def test_cli_refuses_oversized_stdin_without_echoing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "credentials" / "azure-accelerator-auth.json"
    monkeypatch.setattr(store_module, "_reject_unsafe_root", lambda _root: None)
    _stdin(monkeypatch, SECRET.encode() * 2000)

    result = main(
        ["--auth-file", str(target), "--subscription-id", SUBSCRIPTION_ID]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert SECRET not in captured.out + captured.err
    assert not target.parent.exists()
