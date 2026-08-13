"""Focused Azure onboarding adapter coverage."""

from __future__ import annotations

from unittest.mock import patch

from general_ludd.onboard.azure import AzureOnboardProvider


def test_azure_provider_instructions_are_accelerator_least_privilege() -> None:
    provider = AzureOnboardProvider(subscription_id="sub-test")

    instructions = provider.create_role_instructions()

    assert "General Ludd Accelerator Deployer" in instructions
    assert "sub-test" in instructions
    assert "operator_principal_id" in instructions


def test_azure_provider_validation_returns_clean_failure() -> None:
    provider = AzureOnboardProvider(subscription_id="sub-test")

    with patch(
        "general_ludd.onboard.azure.validate_token_and_role",
        side_effect=RuntimeError("Azure SDK is unavailable"),
    ):
        ok, details = provider.validate_token_and_role(
            token="",
            role_arn="principal-test",
            region="eastus",
        )

    assert ok is False
    assert details == {"detail": "RuntimeError: Azure SDK is unavailable"}
