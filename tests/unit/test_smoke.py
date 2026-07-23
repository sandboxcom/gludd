"""Compatibility tests for the general_ludd.smoke module."""

from __future__ import annotations

import general_ludd.smoke as smoke


def test_smoke_module_exports_runner() -> None:
    assert callable(smoke.run_smoke)
