"""Tests for immutable public model deployment metadata."""

from types import SimpleNamespace

import pytest

from general_ludd.models.model_deployment_metadata import (
    ModelDeploymentMetadata,
    model_context_tokens,
    model_license_id,
    safetensors_shape,
)


def test_catalog_helpers_compile_exact_deployment_metadata() -> None:
    """Catalog helpers retain immutable sizing, context, and license facts."""
    info = SimpleNamespace(
        card_data={"license": "apache-2.0"},
        config={"max_position_embeddings": 32_768},
        safetensors=SimpleNamespace(total=7_000_000_000, parameters={"BF16": 1}),
    )
    parameters, bits = safetensors_shape(info)

    metadata = ModelDeploymentMetadata(
        model_id="trusted/code-model",
        revision="a" * 40,
        parameter_count=parameters,
        context_tokens=model_context_tokens(info),
        storage_bytes=14_000_000_000,
        weight_bits=bits,
        license_id=model_license_id(info, ()),
        tags=("code", "text-generation"),
        pipeline_tag="text-generation",
        library_name="transformers",
        downloads=1,
    )

    assert metadata.parameter_count == 7_000_000_000
    assert metadata.weight_bits == 16
    assert metadata.context_tokens == 32_768


def test_catalog_helpers_fail_closed_without_supported_tensor_shape() -> None:
    """Ambiguous catalog tensor metadata must not reach deployment planning."""
    info = SimpleNamespace(
        safetensors=SimpleNamespace(total=7, parameters={"F32": 1})
    )
    with pytest.raises(ValueError, match="supported"):
        safetensors_shape(info)
