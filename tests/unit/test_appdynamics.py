"""Structural tests for connectors/appdynamics.py — AppDynamicsSource."""

from __future__ import annotations

from general_ludd.connectors.appdynamics import (
    DEFAULT_TIMEOUT,
    KIND_METRICS,
    KIND_TRACES,
    VALID_KINDS,
    AppDynamicsSource,
)


class TestAppDynamicsModule:
    def test_source_importable(self) -> None:
        assert AppDynamicsSource is not None

    def test_kind_metrics_constant(self) -> None:
        assert KIND_METRICS == "metrics"

    def test_kind_traces_constant(self) -> None:
        assert KIND_TRACES == "traces"

    def test_valid_kinds_includes_both(self) -> None:
        assert "metrics" in VALID_KINDS
        assert "traces" in VALID_KINDS

    def test_default_timeout_positive(self) -> None:
        assert DEFAULT_TIMEOUT > 0
