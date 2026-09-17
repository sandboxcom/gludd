"""Regression tests for connector helper-module discovery boundaries."""

import pytest

from general_ludd.connectors.registry import ConnectorRegistry

_HELPER_MODULES = (
    "baseten_contracts",
    "macos_security_support",
    "windows_defender_support",
)


@pytest.mark.parametrize("module_name", _HELPER_MODULES)
def test_helper_module_is_not_operator_selectable(module_name: str) -> None:
    """Extracted contracts and command helpers are not connector plugins."""
    module_path = f"general_ludd.connectors.{module_name}"

    assert module_path not in ConnectorRegistry.source_module_paths()


@pytest.mark.parametrize("module_name", _HELPER_MODULES)
def test_helper_module_selector_fails_closed(module_name: str) -> None:
    """Operator config cannot import a helper as a live telemetry source."""
    registry = ConnectorRegistry.from_config(
        [{"name": module_name, "kind": "logs", "module": module_name}]
    )

    assert registry.list_sources() == []
    errors = registry.errors()
    assert [error["name"] for error in errors] == [module_name]
    assert "not in the connector allowlist" in errors[0]["error"]
