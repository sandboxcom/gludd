"""Architecture contract for provider-neutral Azure availability evidence."""

from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_infra_owns_provider_neutral_azure_availability_contracts() -> None:
    contracts = importlib.import_module(
        "general_ludd.infra.azure_operational_availability"
    )
    scope = contracts.AzureAvailabilityScope(
        location="eastus",
        resource_sku="Standard_NC24ads_A100_v4",
        runtime_version_digest="sha256:" + "a" * 64,
        topology_digest="b" * 64,
    )

    assessment = contracts.AzureAvailabilityIndex(assessments=()).assess(scope)

    assert assessment.scope_digest == scope.scope_digest
    assert assessment.observed_outcomes == 0
    assert assessment.availability_score == 0.5
    assert assessment.feasible is True

    contract_source = Path(contracts.__file__).read_text(encoding="utf-8")
    strategy_source = (
        ROOT / "src/general_ludd/infra/azure_gpu_vm_strategy.py"
    ).read_text(encoding="utf-8")
    assert "general_ludd.self_improve" not in contract_source
    assert "general_ludd.self_improve" not in strategy_source


def test_legacy_self_improve_import_is_a_compatibility_facade() -> None:
    contracts = importlib.import_module(
        "general_ludd.infra.azure_operational_availability"
    )
    legacy = importlib.import_module(
        "general_ludd.self_improve.azure_operational_availability"
    )

    for name in (
        "AzureAvailabilityAssessment",
        "AzureAvailabilityIndex",
        "AzureAvailabilityScope",
    ):
        assert getattr(legacy, name) is getattr(contracts, name)
