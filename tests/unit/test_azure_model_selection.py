"""Compatibility tests for the public Azure model-selection facade."""

import pytest

from general_ludd.self_improve.azure_model_selection import (
    azure_model_deployment_identity_digest,
)


def test_public_selector_facade_rejects_mutable_model_revision() -> None:
    """The established facade preserves immutable deployment validation."""
    with pytest.raises(ValueError, match="immutable commit"):
        azure_model_deployment_identity_digest(
            model_id="trusted/code-model",
            model_revision="main",
            weight_bits=16,
            container_image="registry.example/vllm@sha256:" + "9" * 64,
            workload_profile_type="Consumption-GPU-NC8as-T4",
        )
