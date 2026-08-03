"""Shared fixtures and helpers for per-game e2e tests.

Imports from the canonical test_game_building_deepseek.py module
so each per-game file only contains its own test methods.
"""

from __future__ import annotations

import pytest

from tests.e2e.test_game_building_deepseek import (
    _SKIP_REASON,
    _build_deepseek_gateway,
    _get_deepseek_key,
)


@pytest.fixture(scope="class")
def gateway():
    """Return a deepseek gateway instance (class-scoped for reuse).

    Skips the entire test class if no deepseek API key is configured.
    """
    key = _get_deepseek_key()
    if not key:
        pytest.skip(_SKIP_REASON)
    return _build_deepseek_gateway()
