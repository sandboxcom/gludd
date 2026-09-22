"""Fail-closed tests for the split FreeLLMAPI schema parser."""

from __future__ import annotations

import pytest

from general_ludd.models.freellmapi_catalog_schema import parse_catalog
from general_ludd.models.freellmapi_catalog_types import CatalogSchemaError


def _catalog_payload() -> dict[str, object]:
    return {
        "version": "2026.06.07",
        "generatedAt": "2026-06-07T00:00:00Z",
        "tier": "live",
        "models": [
            {
                "platform": "example",
                "modelId": "model-1",
                "displayName": "Model One",
                "intelligenceRank": 1,
                "speedRank": 2,
                "sizeLabel": "small",
                "limits": {"rpm": 10, "rpd": None, "tpm": 1_000, "tpd": None},
                "monthlyTokenBudget": None,
                "contextWindow": 4_096,
                "enabled": True,
                "supportsVision": False,
                "supportsTools": True,
            },
        ],
        "quirks": [
            {
                "slug": "example",
                "title": "Example",
                "body": "Advisory only",
                "severity": "info",
                "targets": [{"platform": "example", "modelGlob": None}],
            },
        ],
    }


def test_parse_catalog_preserves_bounded_advisory_rows() -> None:
    catalog = parse_catalog(_catalog_payload(), payload_sha256="a" * 64)

    assert catalog.version == "2026.06.07"
    assert catalog.models[0].identity == ("example", "model-1")
    assert catalog.quirks[0].targets[0].platform == "example"
    assert catalog.payload_sha256 == "a" * 64


def test_parse_catalog_rejects_duplicate_model_identity() -> None:
    payload = _catalog_payload()
    models = payload["models"]
    assert isinstance(models, list)
    models.append(dict(models[0]))

    with pytest.raises(CatalogSchemaError):
        parse_catalog(payload, payload_sha256="b" * 64)
