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


def test_contract_normalizer_filters_provider_controlled_deployment_shapes() -> None:
    """Only typed string fields cross the extracted response boundary."""
    payload: object = {
        "items": [
            {
                "id": "model-1",
                "name": "coder",
                "deployments": [
                    {
                        "id": "deployment-1",
                        "status": "ACTIVE",
                        "environment": "production",
                        "created_at": "2026-09-11T00:00:00Z",
                    },
                    None,
                ],
            },
            {"deployments": {}},
        ]
    }

    assert baseten_contracts.normalize_baseten_deployments(payload) == [
        {
            "id": "deployment-1",
            "model_id": "model-1",
            "name": "coder",
            "status": "ACTIVE",
            "environment": "production",
            "created_at": "2026-09-11T00:00:00Z",
        }
    ]
