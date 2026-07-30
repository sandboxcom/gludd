"""Regression tests for the A/B child process boundary."""

from __future__ import annotations

import inspect

from general_ludd.abtest._child import main


def test_main_defaults_to_parent_safe_resource_limit_mode() -> None:
    """The public in-process boundary must remain opt-in for hard RLIMITs."""
    parameter = inspect.signature(main).parameters["apply_resource_limits"]
    assert parameter.default is False
