"""Tests for configured Azure bootstrap credential ownership."""

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.self_improve.azure_containerapp_bootstrap_credentials import (
    AzureCredentialAcquisition,
    release_once,
)


def test_release_once_pairs_one_valid_acquisition_with_one_release() -> None:
    """Overlapping owners must never revoke the same credential twice."""
    releases: list[str] = []
    acquisition = AzureCredentialAcquisition(
        AzureAcceleratorCredentials(
            client_id="99999999-8888-7777-6666-555555555555",
            client_secret="unit-secret",
            subscription_id="11111111-2222-3333-4444-555555555555",
            tenant_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
        lambda: releases.append("released"),
    )
    release = release_once(acquisition.release)

    release()
    release()

    assert releases == ["released"]
