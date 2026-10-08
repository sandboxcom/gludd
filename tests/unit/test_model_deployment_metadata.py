"""Tests for immutable public model deployment metadata."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from general_ludd.models.model_deployment_metadata import (
    ModelDeploymentMetadata,
    model_context_tokens,
    model_license_id,
    safetensors_shape,
)

VALID_METADATA: dict[str, object] = {
    "model_id": "trusted/code-model",
    "revision": "a" * 40,
    "parameter_count": 7_000_000_000,
    "context_tokens": 32_768,
    "storage_bytes": 14_000_000_000,
    "weight_bits": 16,
    "license_id": "apache-2.0",
    "tags": ("code", "text-generation"),
    "pipeline_tag": "text-generation",
    "library_name": "transformers",
    "downloads": 1,
}


def _metadata(**overrides: object) -> ModelDeploymentMetadata:
    values = {**VALID_METADATA, **overrides}
    return ModelDeploymentMetadata(**cast(Any, values))


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


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"model_id": "not-canonical"}, "model_id"),
        ({"revision": "mutable"}, "immutable revision"),
        ({"parameter_count": True}, "parameter_count"),
        ({"context_tokens": 0}, "context_tokens"),
        ({"storage_bytes": 10_000_000_000_001}, "storage_bytes"),
        ({"downloads": -1}, "downloads"),
        ({"weight_bits": 32}, "weight_bits"),
        ({"license_id": "Apache-2.0"}, "license_id"),
        ({"tags": ("code", "code")}, "tags"),
    ),
)
def test_deployment_metadata_rejects_ambiguous_or_unbounded_values(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _metadata(**overrides)


def test_catalog_helpers_cover_context_license_and_int8_variants() -> None:
    info = SimpleNamespace(
        card_data=SimpleNamespace(license="MIT"),
        config={
            "max_position_embeddings": 4_096,
            "model_max_length": 8_192,
            "seq_length": True,
        },
        safetensors=SimpleNamespace(total=2_000_000_000, parameters={"I8": 1}),
    )

    assert model_context_tokens(info) == 8_192
    assert model_license_id(info, ()) == "mit"
    assert model_license_id(info, ("license:Apache-2.0",)) == "apache-2.0"
    assert safetensors_shape(info) == (2_000_000_000, 8)

    with pytest.raises(ValueError, match="context metadata"):
        model_context_tokens(SimpleNamespace(config=None))
    with pytest.raises(ValueError, match="context metadata"):
        model_context_tokens(SimpleNamespace(config={"seq_length": False}))
    with pytest.raises(ValueError, match="declared license"):
        model_license_id(SimpleNamespace(card_data=None), ())
