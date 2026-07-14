"""Structural tests for connectors/baseten.py — BasetenClient."""

from __future__ import annotations

from general_ludd.connectors.baseten import (
    _DEFAULT_API_KEY_ENV,
    _DEFAULT_BASE_URL,
    _DEFAULT_MANAGEMENT_URL,
    KIND,
    BasetenClient,
)


class TestBasetenModule:
    def test_client_importable(self) -> None:
        assert BasetenClient is not None

    def test_kind_is_pipeline(self) -> None:
        assert KIND == "pipeline"

    def test_default_base_url(self) -> None:
        assert "baseten" in _DEFAULT_BASE_URL

    def test_default_management_url(self) -> None:
        assert "baseten" in _DEFAULT_MANAGEMENT_URL

    def test_default_api_key_env_var_name(self) -> None:
        assert _DEFAULT_API_KEY_ENV == "BASETEN_API_KEY"

    def test_class_kind_matches_module_kind(self) -> None:
        assert BasetenClient.KIND == KIND
