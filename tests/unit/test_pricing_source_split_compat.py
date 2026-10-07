"""Compatibility contract for the pricing source module split."""

from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_COMPONENT_EXPORTS = {
    "base": (
        "PricingSource",
        "PricingSourceAuthenticationError",
        "PricingSourceDataError",
        "UnavailableModelPrices",
    ),
    "model_apis": (
        "AnthropicSource",
        "HuggingFacePricingSource",
        "HuggingFaceSource",
        "LiteLLMJSONSource",
        "OpenAISource",
        "OpenRouterSource",
        "ZAIPricingSource",
        "ZAISource",
    ),
    "cloud_compute": (
        "AWSPricingSource",
        "AWSSource",
        "GCPPricingSource",
        "GCPSource",
        "LambdaLabsPricingSource",
        "LambdaLabsSource",
        "RunPodPricingSource",
        "RunPodSource",
    ),
    "cache": ("CachedSource",),
    "registry": ("all_sources", "staleness_text"),
}


def _signature(value: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(value)
    except (TypeError, ValueError):
        return None


def test_facade_exports_are_identical_to_component_definitions() -> None:
    """Every historic facade import resolves to the extracted object itself."""
    facade = importlib.import_module("general_ludd.pricing_intel.sources")
    for module_name, names in _COMPONENT_EXPORTS.items():
        component = importlib.import_module(
            f"general_ludd.pricing_intel.source_components.{module_name}"
        )
        for name in names:
            facade_value = getattr(facade, name)
            component_value = getattr(component, name)
            assert facade_value is component_value, name
            assert _signature(facade_value) == _signature(component_value), name


def test_components_do_not_import_the_compatibility_facade() -> None:
    """The extraction graph remains one-way: facade to components."""
    root = Path("src/general_ludd/pricing_intel/source_components")
    assert root.is_dir()
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "general_ludd.pricing_intel.sources" not in imports, path


@pytest.mark.parametrize("facade_first", [True, False])
def test_cold_import_orders_preserve_export_identity(facade_first: bool) -> None:
    """Facade and components import cleanly in both orders in a fresh process."""
    facade_name = "general_ludd.pricing_intel.sources"
    component_names = [
        f"general_ludd.pricing_intel.source_components.{name}"
        for name in _COMPONENT_EXPORTS
    ]
    import_order = [facade_name, *component_names]
    if not facade_first:
        import_order.reverse()
    code = "\n".join(
        [
            "import importlib",
            f"exports = {_COMPONENT_EXPORTS!r}",
            f"order = {import_order!r}",
            "for module_name in order:",
            "    importlib.import_module(module_name)",
            f"facade = importlib.import_module({facade_name!r})",
            "for component_name, names in exports.items():",
            "    component = importlib.import_module(",
            "        f'general_ludd.pricing_intel.source_components.{component_name}'",
            "    )",
            "    for name in names:",
            "        assert getattr(facade, name) is getattr(component, name), name",
        ]
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_split_modules_have_line_limit_maintenance_headroom() -> None:
    """The facade and each cohesive implementation remain below 2,000 lines."""
    pricing_root = Path("src/general_ludd/pricing_intel")
    paths = [
        pricing_root / "sources.py",
        *sorted((pricing_root / "source_components").glob("*.py")),
    ]
    counts = {
        str(path): len(path.read_text(encoding="utf-8").splitlines())
        for path in paths
    }
    assert counts
    assert all(lines < 2_000 for lines in counts.values()), counts


def test_registry_preserves_source_order_and_slug_identity() -> None:
    """Catalog first-match behavior keeps the exact historic registry order."""
    facade = importlib.import_module("general_ludd.pricing_intel.sources")
    assert [source.provider_slug() for source in facade.all_sources()] == [
        "openrouter",
        "anthropic",
        "openai",
        "litellm_anthropic",
        "litellm_openai",
        "litellm_fireworks_ai",
        "runpod_live",
        "runpod",
        "lambda_labs",
        "lambda_labs_live",
        "aws",
        "aws_live",
        "gcp",
        "gcp_live",
        "huggingface",
        "huggingface_live",
        "zai",
        "zai_live",
    ]


def test_historic_httpx_monkeypatch_seam_reaches_extracted_class() -> None:
    """Patching the facade's HTTP client still controls provider I/O."""
    facade = importlib.import_module("general_ludd.pricing_intel.sources")
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": []}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.return_value = response

    with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=client):
        assert facade.OpenRouterSource().fetch_model_prices() == []
    client.get.assert_called_once_with("https://openrouter.ai/api/v1/models")


@pytest.mark.parametrize("payload", [{"data": {}}, []])
def test_openrouter_decoder_rejects_non_list_contract(payload: object) -> None:
    """The extracted shared decoder keeps its fail-closed data contract."""
    facade = importlib.import_module("general_ludd.pricing_intel.sources")
    response = MagicMock()
    response.json.return_value = payload

    with pytest.raises(facade.PricingSourceDataError, match="data must be a list"):
        facade._openrouter_models(response)


def test_openrouter_decoder_rejects_invalid_json() -> None:
    """JSON decoding errors remain visible rather than becoming free prices."""
    facade = importlib.import_module("general_ludd.pricing_intel.sources")
    response = MagicMock()
    response.json.side_effect = ValueError("not JSON")

    with pytest.raises(facade.PricingSourceDataError, match="expected JSON"):
        facade._openrouter_models(response)


@pytest.mark.parametrize(
    "entry, message",
    [
        (None, "model entry must be an object"),
        ({"id": "bad-pricing", "pricing": []}, "pricing must be an object"),
        (
            {"id": "not-finite", "pricing": {"prompt": "nan", "completion": 1}},
            "token rates must be finite",
        ),
    ],
)
def test_openrouter_source_rejects_malformed_model_entries(
    entry: object,
    message: str,
) -> None:
    """Extraction retains fail-closed model-level validation branches."""
    facade = importlib.import_module("general_ludd.pricing_intel.sources")
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": [entry]}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.return_value = response

    with (
        patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=client),
        pytest.raises(facade.PricingSourceDataError, match=message),
    ):
        facade.OpenRouterSource().fetch_model_prices()


def test_openrouter_source_ignores_invalid_context_window() -> None:
    """A malformed optional context does not discard otherwise valid pricing."""
    facade = importlib.import_module("general_ludd.pricing_intel.sources")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "data": [
            {
                "id": "bad-context",
                "pricing": {"prompt": 0.001, "completion": 0.002},
                "context_length": "not-an-int",
            }
        ]
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.return_value = response

    with patch("general_ludd.pricing_intel.sources.httpx.Client", return_value=client):
        prices = facade.OpenRouterSource().fetch_model_prices()
    assert len(prices) == 1
    assert prices[0].context_window is None
