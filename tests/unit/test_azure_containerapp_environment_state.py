"""Strict state-adoption helpers for one owned Azure environment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
)
from general_ludd.infra.azure_containerapp_environment_state import (
    environment_import_id,
    state_tracks_environment,
)
from general_ludd.infra.azure_containerapp_make_types import (
    AzureContainerAppMakeRuntimeError,
)

SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"


def _policy() -> AzureEnvironmentLifecyclePolicy:
    return AzureEnvironmentLifecyclePolicy(
        subscription_id=SUBSCRIPTION,
        resource_group="gludd-models-eastus",
        environment_name="gludd-gpu-environment",
        location="eastus",
        profiles=(
            AzureEnvironmentProfile(
                profile_name="gpu-a100",
                workload_profile_type="Consumption-GPU-NC24-A100",
            ),
        ),
        owner_digest="a" * 64,
        plan_digest="b" * 64,
        expires_at_utc="2026-09-13T22:00:00Z",
    )


def _state(policy: AzureEnvironmentLifecyclePolicy, resource_id: str) -> object:
    return {
        "version": 4,
        "resources": [
            {
                "module": "module.environment",
                "mode": "managed",
                "type": "azapi_resource",
                "name": "managed_environment",
                "instances": [{"attributes": {"id": resource_id}}],
            }
        ],
    }


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def test_environment_import_id_uses_the_pinned_azapi_version() -> None:
    policy = _policy()

    assert environment_import_id(policy) == (
        f"{policy.environment_id}?api-version=2025-07-01"
    )


@pytest.mark.parametrize("stored_with_api_version", (False, True))
def test_state_accepts_only_the_exact_owned_environment_identity(
    tmp_path: Path,
    stored_with_api_version: bool,
) -> None:
    policy = _policy()
    state_path = tmp_path / "terraform.tfstate"
    resource_id = environment_import_id(policy) if stored_with_api_version else policy.environment_id
    _write(state_path, _state(policy, resource_id))

    assert state_tracks_environment(state_path, policy) is True


@pytest.mark.parametrize(
    "payload",
    (
        {"version": 4, "resources": "not-a-list"},
        {"version": 4, "resources": []},
        {"version": 4, "resources": [{"module": "foreign"}]},
    ),
)
def test_state_distinguishes_empty_state_from_ambiguous_or_foreign_state(
    tmp_path: Path,
    payload: object,
) -> None:
    policy = _policy()
    state_path = tmp_path / "terraform.tfstate"
    _write(state_path, payload)

    if cast(dict[str, Any], payload).get("resources") == []:
        assert state_tracks_environment(state_path, policy) is False
    else:
        with pytest.raises(AzureContainerAppMakeRuntimeError, match="state-ownership"):
            state_tracks_environment(state_path, policy)


def test_state_rejects_symlink_and_wrong_api_version(tmp_path: Path) -> None:
    policy = _policy()
    target = tmp_path / "foreign-state"
    _write(target, _state(policy, environment_import_id(policy)))
    state_path = tmp_path / "terraform.tfstate"
    state_path.symlink_to(target)

    with pytest.raises(AzureContainerAppMakeRuntimeError, match="state-ownership"):
        state_tracks_environment(state_path, policy)

    state_path.unlink()
    _write(
        state_path,
        _state(policy, f"{policy.environment_id}?api-version=2024-03-01"),
    )
    with pytest.raises(AzureContainerAppMakeRuntimeError, match="state-ownership"):
        state_tracks_environment(state_path, policy)

