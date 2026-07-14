"""Structural tests for connectors/signoz.py — SigNozSource."""

from __future__ import annotations

from general_ludd.connectors.signoz import _DEFAULT_TIMEOUT, SigNozSource


class TestSigNozModule:
    def test_source_importable(self) -> None:
        assert SigNozSource is not None

    def test_default_timeout_positive(self) -> None:
        assert _DEFAULT_TIMEOUT > 0
        assert isinstance(_DEFAULT_TIMEOUT, float)

    def test_kind_is_traces(self) -> None:
        assert SigNozSource.KIND == "traces"
