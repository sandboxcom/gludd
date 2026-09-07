"""Hermetic lifecycle tests for the narrow Azure resource-group bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.azure.accelerator_role import EXPECTED_ACTIONS
from general_ludd.azure.resource_group_bootstrap import (
    AzureResourceGroupBootstrapError,
    AzureResourceGroupBootstrapPolicy,
    AzureResourceGroupBootstrapState,
    ensure_azure_resource_group,
)

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT_ID = "99999999-8888-7777-6666-555555555555"
OWNER_DIGEST = "a" * 64
_USE_CREATED = object()


class _SDKError(Exception):
    def __init__(self, status_code: int, message: str = "provider-secret") -> None:
        super().__init__(message)
        self.status_code = status_code


class _ResponseSDKError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider-secret")
        self.response = SimpleNamespace(status_code=status_code)


@dataclass
class _Closable:
    closed: int = 0
    fail_close: bool = False

    def close(self) -> None:
        self.closed += 1
        if self.fail_close:
            raise RuntimeError("provider-secret")


class _Groups:
    def __init__(
        self,
        existing: object = None,
        *,
        get_error: Exception | None = None,
        post_create_existing: object = _USE_CREATED,
    ) -> None:
        self.existing = existing
        self.get_error = get_error
        self.post_create_existing = post_create_existing
        self.get_calls: list[str] = []
        self.create_calls: list[tuple[str, dict[str, object]]] = []

    def get(self, resource_group: str) -> object:
        self.get_calls.append(resource_group)
        if self.get_error is not None:
            error = self.get_error
            self.get_error = None
            raise error
        return self.existing

    def create_or_update(
        self,
        resource_group: str,
        parameters: dict[str, object],
    ) -> object:
        self.create_calls.append((resource_group, parameters))
        created = SimpleNamespace(
            name=resource_group,
            location=parameters["location"],
            tags=parameters["tags"],
        )
        self.existing = (
            created
            if self.post_create_existing is _USE_CREATED
            else self.post_create_existing
        )
        return created


class _Client(_Closable):
    def __init__(self, groups: _Groups, *, fail_close: bool = False) -> None:
        super().__init__(fail_close=fail_close)
        self.resource_groups = groups


def _credentials(subscription_id: str = SUBSCRIPTION_ID) -> AzureAcceleratorCredentials:
    return AzureAcceleratorCredentials(
        client_id=CLIENT_ID,
        client_secret="unit-secret",
        subscription_id=subscription_id,
        tenant_id=TENANT_ID,
    )


def _policy(**overrides: str) -> AzureResourceGroupBootstrapPolicy:
    values = {
        "subscription_id": SUBSCRIPTION_ID,
        "resource_group": "gludd-models-eastus",
        "location": "eastus",
        "owner_digest": OWNER_DIGEST,
    }
    values.update(overrides)
    return AzureResourceGroupBootstrapPolicy(**values)


def test_existing_exact_scope_role_contains_only_non_destructive_group_bootstrap() -> None:
    assert {
        "Microsoft.Resources/subscriptions/resourceGroups/read",
        "Microsoft.Resources/subscriptions/resourceGroups/write",
    } < EXPECTED_ACTIONS
    assert "Microsoft.Resources/subscriptions/resourceGroups/delete" not in EXPECTED_ACTIONS
    assert not any("authorization" in action.casefold() for action in EXPECTED_ACTIONS)


def test_absent_group_is_created_once_with_exact_owner_tags_and_clients_close() -> None:
    groups = _Groups(get_error=_SDKError(404))
    client = _Client(groups)
    credential = _Closable()
    events: list[object] = []
    policy = _policy()

    result = ensure_azure_resource_group(
        policy,
        _credentials(),
        credential_builder=lambda _credentials: credential,
        client_builder=lambda _credential, _subscription: client,
        trace_sink=events.append,
    )

    assert result.state is AzureResourceGroupBootstrapState.CREATED
    assert groups.get_calls == [policy.resource_group, policy.resource_group]
    assert groups.create_calls == [
        (
            policy.resource_group,
            {"location": policy.location, "tags": dict(policy.tags)},
        )
    ]
    assert client.closed == credential.closed == 1
    assert [event.state for event in events] == [
        AzureResourceGroupBootstrapState.CHECK_STARTED,
        AzureResourceGroupBootstrapState.ABSENT,
        AzureResourceGroupBootstrapState.CREATE_STARTED,
        AzureResourceGroupBootstrapState.CREATED,
    ]


def test_exact_owned_group_is_reused_without_any_write() -> None:
    policy = _policy()
    groups = _Groups(
        SimpleNamespace(
            name=policy.resource_group,
            location="East US",
            tags=dict(policy.tags),
        )
    )
    client = _Client(groups)
    credential = _Closable()

    result = ensure_azure_resource_group(
        policy,
        _credentials(),
        credential_builder=lambda _credentials: credential,
        client_builder=lambda _credential, _subscription: client,
    )

    assert result.state is AzureResourceGroupBootstrapState.REUSED
    assert groups.create_calls == []
    assert client.closed == credential.closed == 1


def test_mapping_shaped_owned_group_is_reused_without_any_write() -> None:
    policy = _policy()
    groups = _Groups(
        {
            "name": policy.resource_group,
            "location": "East US",
            "tags": dict(policy.tags),
        }
    )

    result = ensure_azure_resource_group(
        policy,
        _credentials(),
        credential_builder=lambda _credentials: _Closable(),
        client_builder=lambda _credential, _subscription: _Client(groups),
    )

    assert result.state is AzureResourceGroupBootstrapState.REUSED
    assert groups.create_calls == []


def test_post_create_readback_must_still_match_exact_ownership() -> None:
    groups = _Groups(
        get_error=_SDKError(404),
        post_create_existing=SimpleNamespace(
            name="gludd-models-eastus",
            location="eastus",
            tags={"gludd-managed-by": "foreign"},
        ),
    )

    with pytest.raises(AzureResourceGroupBootstrapError) as captured:
        ensure_azure_resource_group(
            _policy(),
            _credentials(),
            credential_builder=lambda _credentials: _Closable(),
            client_builder=lambda _credential, _subscription: _Client(groups),
        )

    assert captured.value.failure_class == "ownership"
    assert groups.get_calls == ["gludd-models-eastus", "gludd-models-eastus"]
    assert len(groups.create_calls) == 1


@pytest.mark.parametrize(
    "existing",
    [
        SimpleNamespace(name="foreign", location="eastus", tags={}),
        SimpleNamespace(
            name="gludd-models-eastus",
            location="westus",
            tags={
                "gludd-managed-by": "general-ludd",
                "gludd-purpose": "accelerator-boundary",
                "gludd-owner-digest": OWNER_DIGEST,
            },
        ),
        SimpleNamespace(
            name="gludd-models-eastus",
            location="eastus",
            tags={"gludd-managed-by": "somebody-else"},
        ),
    ],
)
def test_foreign_or_ambiguous_existing_group_is_never_adopted(existing: object) -> None:
    groups = _Groups(existing)

    with pytest.raises(AzureResourceGroupBootstrapError) as captured:
        ensure_azure_resource_group(
            _policy(),
            _credentials(),
            credential_builder=lambda _credentials: _Closable(),
            client_builder=lambda _credential, _subscription: _Client(groups),
        )

    assert captured.value.failure_class == "ownership"
    assert groups.create_calls == []
    assert "provider" not in str(captured.value).casefold()


@pytest.mark.parametrize(
    ("status_code", "failure_class"),
    [(401, "authentication"), (403, "authorization"), (409, "conflict"), (429, "quota")],
)
def test_sdk_failures_are_classified_without_provider_content(
    status_code: int,
    failure_class: str,
) -> None:
    secret = "this-provider-message-must-never-escape"
    groups = _Groups(get_error=_SDKError(status_code, secret))
    events: list[object] = []

    with pytest.raises(AzureResourceGroupBootstrapError) as captured:
        ensure_azure_resource_group(
            _policy(),
            _credentials(),
            credential_builder=lambda _credentials: _Closable(),
            client_builder=lambda _credential, _subscription: _Client(groups),
            trace_sink=events.append,
        )

    assert captured.value.failure_class == failure_class
    assert secret not in str(captured.value)
    assert secret not in repr(events)
    assert events[-1].state is AzureResourceGroupBootstrapState.FAILED


def test_sdk_response_status_is_classified_without_provider_content() -> None:
    with pytest.raises(AzureResourceGroupBootstrapError) as captured:
        ensure_azure_resource_group(
            _policy(),
            _credentials(),
            credential_builder=lambda _credentials: _Closable(),
            client_builder=lambda _credential, _subscription: _Client(
                _Groups(get_error=_ResponseSDKError(403))
            ),
        )

    assert captured.value.failure_class == "authorization"
    assert "provider-secret" not in str(captured.value)


def test_subscription_mismatch_and_invalid_policy_fail_before_sdk_construction() -> None:
    built: list[str] = []

    with pytest.raises(ValueError, match="subscription"):
        ensure_azure_resource_group(
            _policy(),
            _credentials("22222222-2222-2222-2222-222222222222"),
            credential_builder=lambda _credentials: built.append("credential"),
            client_builder=lambda _credential, _subscription: built.append("client"),
        )
    with pytest.raises(ValueError, match="owner_digest"):
        _policy(owner_digest="not-a-digest")
    with pytest.raises(ValueError, match="location"):
        _policy(location="East US")
    assert built == []


def test_invalid_public_boundaries_fail_before_sdk_construction() -> None:
    built: list[str] = []

    with pytest.raises(ValueError, match="policy"):
        ensure_azure_resource_group(
            cast(Any, object()),
            _credentials(),
            credential_builder=lambda _credentials: built.append("credential"),
        )
    with pytest.raises(ValueError, match="credentials"):
        ensure_azure_resource_group(
            _policy(),
            cast(Any, object()),
            credential_builder=lambda _credentials: built.append("credential"),
        )
    with pytest.raises(ValueError, match="trace_sink"):
        ensure_azure_resource_group(
            _policy(),
            _credentials(),
            credential_builder=lambda _credentials: built.append("credential"),
            trace_sink=cast(Any, object()),
        )

    assert built == []


def test_cleanup_failure_fails_closed_after_successful_create() -> None:
    groups = _Groups(get_error=_SDKError(404))
    client = _Client(groups, fail_close=True)
    credential = _Closable()

    with pytest.raises(AzureResourceGroupBootstrapError) as captured:
        ensure_azure_resource_group(
            _policy(),
            _credentials(),
            credential_builder=lambda _credentials: credential,
            client_builder=lambda _credential, _subscription: client,
        )

    assert captured.value.failure_class == "cleanup"
    assert len(groups.create_calls) == 1
    assert client.closed == credential.closed == 1
