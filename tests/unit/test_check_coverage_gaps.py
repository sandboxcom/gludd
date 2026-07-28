"""Regression tests for the static coverage-gap audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_coverage_gaps.py"


@pytest.fixture
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_coverage_gaps_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("source", "expected_test"),
    [
        ("src/general_ludd/agents/skill_context.py", "tests/unit/test_skill_context.py"),
        ("src/general_ludd/ansible/skill_lens.py", "tests/unit/test_skill_lens.py"),
        (
            "src/general_ludd/memory/tempr_retriever.py",
            "tests/unit/test_tempr_retriever.py",
        ),
    ],
)
def test_nested_modules_include_leaf_named_test_candidate(
    checker: ModuleType,
    source: str,
    expected_test: str,
) -> None:
    candidates = checker._candidate_test_paths(checker.PROJECT_ROOT / source)

    assert checker.PROJECT_ROOT / expected_test in candidates


@pytest.mark.parametrize(
    "source",
    [
        "src/general_ludd/agents/skill_context.py",
        "src/general_ludd/ansible/skill_lens.py",
        "src/general_ludd/memory/hindsight_adapter.py",
        "src/general_ludd/memory/memory_bank.py",
        "src/general_ludd/memory/observation_consolidator.py",
        "src/general_ludd/memory/tempr_retriever.py",
    ],
)
def test_existing_leaf_named_tests_are_recognized(
    checker: ModuleType,
    source: str,
) -> None:
    result = checker._check_module(checker.PROJECT_ROOT / source)

    assert result["status"] == "OK", result
