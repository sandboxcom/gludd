"""Lease-scoped OpenBao credentials for autonomous Azure provisioning."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.azure.accelerator_credential_source import (
    AzureCredentialLeaseState,
    AzureOpenBaoCredentialLeaseError,
    OpenBaoAzureCredentialLeaseSource,
)

SUBSCRIPTION_ID = "11111111-2222-4333-8444-555555555555"
TENANT_ID = "22222222-3333-4444-8555-666666666666"
CLIENT_ID = "33333333-4444-4555-8666-777777777777"
SECRET = "dynamic-openbao-secret-never-render"
LEASE_ID = "azure/creds/gludd-accelerator/lease-abc123"


class _Sys:
    def __init__(self) -> None:
        self.revoked: list[str] = []
        self.fail = False

    def revoke_lease(self, lease_id: str) -> None:
        self.revoked.append(lease_id)
        if self.fail:
            raise RuntimeError(f"private revoke detail {SECRET}")


class _Client:
    def __init__(self, response: object) -> None:
        self.response = response
        self.reads: list[str] = []
        self.sys = _Sys()

    def read(self, path: str) -> object:
        self.reads.append(path)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _response(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "lease_id": LEASE_ID,
        "lease_duration": 900,
        "renewable": True,
        "data": {"client_id": CLIENT_ID, "client_secret": SECRET},
    }
    result.update(overrides)
    return result


def _source(
    client: _Client,
    *,
    traces: list[object] | None = None,
    max_lease_seconds: int = 900,
) -> OpenBaoAzureCredentialLeaseSource:
    return OpenBaoAzureCredentialLeaseSource(
        client=client,
        subscription_id=SUBSCRIPTION_ID,
        tenant_id=TENANT_ID,
        mount="azure",
        role_name="gludd-accelerator",
        max_lease_seconds=max_lease_seconds,
        trace_sink=(traces.append if traces is not None else lambda _event: None),
    )


def test_acquire_uses_public_hvac_read_and_release_revokes_only_exact_lease() -> None:
    client = _Client(_response())
    traces: list[Any] = []
    source = _source(client, traces=traces)

    lease = source.acquire()

    assert client.reads == ["azure/creds/gludd-accelerator"]
    assert lease.credentials.client_id == CLIENT_ID
    assert lease.credentials.client_secret == SECRET
    assert lease.credentials.subscription_id == SUBSCRIPTION_ID
    assert lease.credentials.tenant_id == TENANT_ID
    assert lease.lease_duration_seconds == 900
    assert lease.renewable is True
    assert source.active_lease_count == 1
    assert SECRET not in repr(lease)
    assert LEASE_ID not in repr(lease)

    source.release(lease)
    source.release(lease)

    assert client.sys.revoked == [LEASE_ID]
    assert source.active_lease_count == 0
    assert [event.state for event in traces] == [
        AzureCredentialLeaseState.REQUESTED,
        AzureCredentialLeaseState.ACQUIRED,
        AzureCredentialLeaseState.REVOKE_STARTED,
        AzureCredentialLeaseState.REVOKED,
    ]
    assert SECRET not in repr(traces)
    assert LEASE_ID not in repr(traces)


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        _response(lease_id="azure/creds/other-role/lease-abc123"),
        _response(lease_duration=0),
        _response(lease_duration=True),
        _response(renewable="yes"),
        _response(data={"client_id": "not-a-uuid", "client_secret": SECRET}),
        _response(data={"client_id": CLIENT_ID}),
        _response(data={"client_id": CLIENT_ID, "client_secret": "bad\nsecret"}),
    ],
)
def test_malformed_or_foreign_lease_responses_fail_closed(response: object) -> None:
    client = _Client(response)
    source = _source(client)

    with pytest.raises(AzureOpenBaoCredentialLeaseError) as captured:
        source.acquire()

    assert source.active_lease_count == 0
    assert SECRET not in repr(captured.value)
    assert LEASE_ID not in repr(captured.value)


def test_overlong_lease_is_immediately_revoked_and_never_returned() -> None:
    client = _Client(_response(lease_duration=901))
    source = _source(client, max_lease_seconds=900)

    with pytest.raises(AzureOpenBaoCredentialLeaseError, match="acquire"):
        source.acquire()

    assert client.sys.revoked == [LEASE_ID]
    assert source.active_lease_count == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mount", "sys"),
        ("mount", "azure/../other"),
        ("role_name", "other/role"),
        ("role_name", "--role"),
        ("max_lease_seconds", 0),
        ("max_lease_seconds", True),
        ("trace_sink", object()),
    ],
)
def test_constructor_rejects_widened_or_ambiguous_boundaries(
    field: str,
    value: object,
) -> None:
    arguments: dict[str, object] = {
        "client": _Client(_response()),
        "subscription_id": SUBSCRIPTION_ID,
        "tenant_id": TENANT_ID,
        "mount": "azure",
        "role_name": "gludd-accelerator",
        "max_lease_seconds": 900,
        "trace_sink": lambda _event: None,
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        OpenBaoAzureCredentialLeaseSource(**arguments)


def test_read_and_exact_revoke_failures_are_censored_and_retriable() -> None:
    failed_read = _Client(RuntimeError(f"private OpenBao detail {SECRET}"))
    with pytest.raises(AzureOpenBaoCredentialLeaseError) as read_error:
        _source(failed_read).acquire()
    assert read_error.value.operation == "acquire"
    assert SECRET not in repr(read_error.value)

    client = _Client(_response())
    source = _source(client)
    lease = source.acquire()
    client.sys.fail = True
    with pytest.raises(AzureOpenBaoCredentialLeaseError) as revoke_error:
        source.release(lease)
    assert revoke_error.value.operation == "revoke"
    assert source.active_lease_count == 1
    assert SECRET not in repr(revoke_error.value)

    client.sys.fail = False
    source.release(lease)
    assert client.sys.revoked == [LEASE_ID, LEASE_ID]
    assert source.active_lease_count == 0


def test_revoke_trace_failure_never_prevents_credential_revocation() -> None:
    client = _Client(_response())

    def trace(event: Any) -> None:
        if event.state is AzureCredentialLeaseState.REVOKE_STARTED:
            raise RuntimeError(f"private trace detail {SECRET}")

    source = OpenBaoAzureCredentialLeaseSource(
        client=client,
        subscription_id=SUBSCRIPTION_ID,
        tenant_id=TENANT_ID,
        max_lease_seconds=900,
        trace_sink=trace,
    )
    lease = source.acquire()

    with pytest.raises(AzureOpenBaoCredentialLeaseError) as captured:
        source.release(lease)

    assert captured.value.operation == "trace"
    assert client.sys.revoked == [LEASE_ID]
    assert source.active_lease_count == 0
    assert SECRET not in repr(captured.value)


def test_source_exposes_no_renew_prefix_force_or_plugin_configuration_surface() -> None:
    public = {
        name
        for name in dir(OpenBaoAzureCredentialLeaseSource)
        if not name.startswith("_")
    }

    assert public == {"acquire", "active_lease_count", "release"}
