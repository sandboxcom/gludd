"""Structural guard for the oversized-test refactor."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e import _game_building_deepseek_definitions as definitions
from tests.e2e import test_game_building_deepseek as legacy_game_suite

ROOT = Path(__file__).resolve().parents[2]
SPLIT_TEST_FAMILY_PATHS = (
    "tests/e2e/_game_building_deepseek_definitions.py",
    "tests/e2e/_game_building_deepseek_runtime.py",
    "tests/e2e/_game_building_deepseek_verification.py",
    "tests/e2e/test_connectors_batch5_platform_workflows.py",
    "tests/e2e/test_game_building_deepseek.py",
    "tests/e2e/test_connectors_batch5_workflows.py",
    "tests/unit/test_automatic_disk_cleanup.py",
    "tests/unit/test_automatic_disk_cleanup_resources.py",
    "tests/unit/test_behavioral_enforcement.py",
    "tests/unit/test_behavioral_enforcement_runtime.py",
    "tests/unit/test_ci_named_shard_files.py",
    "tests/unit/test_ci_named_shard_safety.py",
    "tests/unit/test_probabilistic_deep.py",
    "tests/unit/test_probabilistic_v2_internals.py",
    "tests/unit/test_routers_endpoints.py",
    "tests/unit/test_routers_workflow_endpoints.py",
    "tests/unit/test_self_improve_codex_comparison.py",
    "tests/unit/test_self_improve_codex_span_protocol.py",
    "tests/unit/test_self_improve_codex_runner.py",
    "tests/unit/test_self_improve_codex_runner_live.py",
)


@pytest.mark.parametrize("relative_path", SPLIT_TEST_FAMILY_PATHS)
def test_split_test_module_stays_below_repository_line_limit(relative_path: str) -> None:
    """Keep every resulting module in the nine split families below the hard limit."""
    path = ROOT / relative_path
    line_count = len(path.read_text(encoding="utf-8").splitlines())

    assert line_count < 2_500, f"{relative_path}: {line_count} lines"


def test_game_building_split_preserves_legacy_fixture_exports() -> None:
    """Keep imports used by the per-game fixture modules collection-safe."""
    assert legacy_game_suite._SKIP_REASON == definitions._SKIP_REASON
    assert legacy_game_suite._get_deepseek_key is definitions._get_deepseek_key
