"""Tests for G2 offline eval harness."""

from __future__ import annotations

from general_ludd.eval.harness import EvalHarness
from general_ludd.eval.schema import EvalCase, EvalResult


def test_eval_case_and_result_dataclasses():
    """EvalCase and EvalResult can be instantiated with required fields."""
    patch_text = (
        "--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n"
        " def foo(x):\n-    return x.bar()\n+    return x.bar() if x else None\n"
    )
    case = EvalCase(
        id="test_001",
        description="Fix a null pointer dereference",
        input_files={"main.py": "def foo(x):\n    return x.bar()\n"},
        expected_patch=patch_text,
        task_type="bug_fix",
    )
    result = EvalResult(
        case_id="test_001",
        passed=True,
        actual_patch=patch_text,
        score=0.95,
        tokens_used=1240,
        duration_ms=3421,
    )

    assert case.id == "test_001"
    assert case.input_files == {"main.py": "def foo(x):\n    return x.bar()\n"}
    assert result.case_id == "test_001"
    assert result.passed is True
    assert result.score == 0.95


def test_eval_harness_benchmark_stub():
    """EvalHarness.run_benchmark returns a list of EvalResult."""
    harness = EvalHarness(model="sonnet")
    cases = [
        EvalCase(
            id="case_1",
            description="Add type hints",
            input_files={"lib.py": "def add(a, b):\n    return a + b\n"},
            expected_patch="",
            task_type="feature",
        ),
    ]
    results = harness.run_benchmark(cases)

    assert isinstance(results, list)
    assert len(results) == len(cases)
    for r in results:
        assert isinstance(r, EvalResult)
