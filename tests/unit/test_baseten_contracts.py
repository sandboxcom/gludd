"""Regression tests for Baseten's extracted transport contracts."""

from __future__ import annotations

from general_ludd.connectors import baseten_contracts
from general_ludd.connectors.baseten import (
    BasetenConfig,
    BasetenDeployment,
    BasetenHealthResult,
    HttpRequest,
)


def test_baseten_connector_reexports_canonical_contracts() -> None:
    """Connector consumers retain the existing public type identities."""
    assert BasetenConfig is baseten_contracts.BasetenConfig
    assert BasetenDeployment is baseten_contracts.BasetenDeployment
    assert BasetenHealthResult is baseten_contracts.BasetenHealthResult
    assert HttpRequest is baseten_contracts.HttpRequest
