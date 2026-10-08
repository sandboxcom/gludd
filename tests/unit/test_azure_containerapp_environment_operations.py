"""Focused module-boundary tests for shared environment lifecycle operations."""

from general_ludd.infra.azure_containerapp_environment_operations import (
    discard_environment_trace,
)


def test_operations_module_exposes_the_default_trace_sink() -> None:
    """Keep the shared no-op trace boundary independently importable."""
    assert callable(discard_environment_trace)
