"""TDD tests for S.8 — connector registry getattr class_name validation.

Tests that the _validate_class_name guard mechanically blocks arbitrary
class names via getattr on connector modules, closing the CA-Connectors
(D4) arbitrary-code-execution surface.
"""

from __future__ import annotations

import types
from typing import Any, cast
from unittest.mock import patch

import pytest

from general_ludd.connectors.registry import (
    ConnectorRegistry,
    _validate_class_name,
)

# ---------------------------------------------------------------------------
# 1. Only registered (format-valid) connector classes can be accessed
# ---------------------------------------------------------------------------

class TestValidClassNamesPass:
    def test_vanilla_Source_name_passes(self) -> None:
        _validate_class_name("PrometheusSource")

    def test_CamelCase_multiword_Source_passes(self) -> None:
        _validate_class_name("DatadogLogSource")

    def test_short_Source_name_passes(self) -> None:
        _validate_class_name("TSource")

    def test_numeric_suffix_Source_passes(self) -> None:
        _validate_class_name("Syslog2Source")


# ---------------------------------------------------------------------------
# 2. getattr on unregistered/arbitrary class_name raises ValueError
# ---------------------------------------------------------------------------

class TestArbitraryClassNamesRejected:
    def test_no_Source_suffix_rejected(self) -> None:
        with pytest.raises(ValueError, match="must end with 'Source'"):
            _validate_class_name("SomeClass")

    def test_full_lowercase_rejected(self) -> None:
        with pytest.raises(ValueError, match="must end with 'Source'"):
            _validate_class_name("prometheussource")

    def test_lowercase_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="must start with an uppercase"):
            _validate_class_name("prometheusSource")

    def test_all_uppercase_rejected(self) -> None:
        with pytest.raises(ValueError, match="must end with 'Source'"):
            _validate_class_name("PROMETHEUSSOURCE")

    def test_dot_path_in_class_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must start with an uppercase"):
            _validate_class_name("os.systemSource")

    def test_dash_in_class_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_class_name("Some-Source")

    def test_space_in_class_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_class_name("Some Source")

    def test_parenthesis_in_class_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must end with 'Source'"):
            _validate_class_name("ExecSource()")


# ---------------------------------------------------------------------------
# 3. Dunder / private names rejected
# ---------------------------------------------------------------------------

class TestDunderAndPrivateRejected:
    def test___subclasses___rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("__subclasses__")

    def test___init___rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("__init__")

    def test___builtins___rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("__builtins__")

    def test___class___rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("__class__")

    def test___mro___rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("__mro__")

    def test___bases___rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("__bases__")

    def test_single_underscore_private_rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("_internalSource")

    def test___dict___rejected(self) -> None:
        with pytest.raises(ValueError, match="private/dunder"):
            _validate_class_name("__dict__")


# ---------------------------------------------------------------------------
# 4. Case-insensitive matching is safe
# ---------------------------------------------------------------------------

class TestCaseInsensitiveMatching:
    def test_lowercase_Source_suffix_rejected(self) -> None:
        with pytest.raises(ValueError, match="must end with 'Source'"):
            _validate_class_name("prometheussource")

    def test_mixed_case_but_lower_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="must start with an uppercase"):
            _validate_class_name("pROMETHEUSSource")

    def test_uppercase_start_valid(self) -> None:
        _validate_class_name("PROMETHEUSSource")

    def test_module_config_with_valid_class_name_works(self) -> None:
        # Exercise a real operator-selectable module path so this positive
        # control remains compatible with the production module allowlist.
        _MOD_PATH = "general_ludd.connectors.prometheus"

        mock_mod = types.ModuleType(_MOD_PATH)

        class FakeSource:
            __module__ = _MOD_PATH
            KIND = "test"

            def __init__(self, config: dict[str, Any]) -> None:
                self.name = str(config.get("name") or "fake")

            def health(self) -> dict[str, Any]:
                return {"ok": True}

            def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
                return []

        cast(Any, mock_mod).FakeSource = FakeSource

        with patch("importlib.import_module", return_value=mock_mod):
            reg = ConnectorRegistry.from_config(
                [
                    {
                        "name": "test-source",
                        "kind": "test",
                        "module": "prometheus",
                        "class_name": "FakeSource",
                    }
                ],
                factories={},
            )
            assert "test-source" in reg.names()

    def test_module_config_with_lowercase_class_name_blocked(self) -> None:
        reg = ConnectorRegistry.from_config(
            [
                {
                    "name": "test-source",
                    "kind": "test",
                    "module": "prometheus",
                    "class_name": "prometheussource",
                }
            ],
            factories={},
        )
        errors = reg.errors()
        assert len(errors) >= 1
        assert "must end with 'Source'" in errors[0]["error"]


# ---------------------------------------------------------------------------
# Integration — the fixed getattr path never reaches dunder attrs
# ---------------------------------------------------------------------------

class TestGetattrPathUsesValidator:
    def test__import_dotted_uses_validator(self) -> None:
        reg = ConnectorRegistry.from_config(
            [
                {
                    "name": "bad-dotted",
                    "kind": "test",
                    "class": "general_ludd.connectors.prometheus.__init__",
                }
            ],
            factories={},
        )
        errors = reg.errors()
        assert len(errors) >= 1
        assert "private/dunder" in errors[0]["error"]

    def test__resolve_factory_uses_validator_for_class_name(self) -> None:
        reg = ConnectorRegistry.from_config(
            [
                {
                    "name": "bad-classname",
                    "kind": "test",
                    "module": "prometheus",
                    "class_name": "__subclasses__",
                }
            ],
            factories={},
        )
        errors = reg.errors()
        assert len(errors) >= 1
        assert "private/dunder" in errors[0]["error"]
