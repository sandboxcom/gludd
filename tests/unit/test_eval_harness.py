"""Tests for G2 offline eval harness."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.eval.harness import EvalHarness
from general_ludd.eval.model import ModelEvaluator
from general_ludd.eval.schema import EvalCase, EvalResult
from general_ludd.eval.scorers import (
    check_assertions,
    composite_eval_score,
    compute_patch_similarity,
)
from general_ludd.models.gateway import ModelGateway, ModelResponse

# ── schema tests ──────────────────────────────────────────────────────────


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


def test_eval_case_assertions_default():
    """EvalCase.assertions defaults to empty dict."""
    case = EvalCase(
        id="c1",
        description="test",
        input_files={},
        expected_patch="",
    )
    assert case.assertions == {}


def test_eval_case_with_assertions():
    """EvalCase stores assertions dict."""
    case = EvalCase(
        id="c1",
        description="test",
        input_files={},
        expected_patch="",
        assertions={"patch_contains": "def foo", "filename": "main.py"},
    )
    assert case.assertions["patch_contains"] == "def foo"
    assert case.assertions["filename"] == "main.py"


# ── harness tests ─────────────────────────────────────────────────────────


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


# ── ModelEvaluator tests ──────────────────────────────────────────────────


def test_model_evaluator_dry_run_returns_prompt():
    """dry_run=True returns the constructed prompt without calling model."""
    gateway = ModelGateway()
    evaluator = ModelEvaluator(gateway, profile_id="test-profile", dry_run=True)
    case = EvalCase(
        id="e1",
        description="Add null check",
        input_files={"app.py": "def go():\n    return None\n"},
        expected_patch="",
    )
    result = evaluator.generate_patch(case)
    assert isinstance(result, str)
    assert "Add null check" in result
    assert "app.py" in result
    assert "def go():" in result
    assert "unified diff patch" in result.lower()


def test_model_evaluator_calls_gateway():
    """generate_patch calls ModelGateway.call_model and returns content."""
    gateway = MagicMock(spec=ModelGateway)
    response = MagicMock(spec=ModelResponse)
    response.content = "+ return x.bar() if x else None\n"
    gateway.call_model.return_value = response

    evaluator = ModelEvaluator(gateway, profile_id="sonnet")
    case = EvalCase(
        id="e1",
        description="Fix NPE",
        input_files={"main.py": "def foo(x): return x.bar()\n"},
        expected_patch="",
    )
    result = evaluator.generate_patch(case)

    gateway.call_model.assert_called_once()
    call_args = gateway.call_model.call_args
    assert call_args[0][0] == "sonnet"
    assert len(call_args[0][1]) == 1
    assert call_args[0][1][0]["role"] == "user"
    assert "Fix NPE" in call_args[0][1][0]["content"]
    assert result == "+ return x.bar() if x else None\n"


def test_model_evaluator_respects_profile_id():
    """ModelEvaluator uses the configured profile_id."""
    gateway = MagicMock(spec=ModelGateway)
    response = MagicMock(spec=ModelResponse)
    response.content = "patch"
    gateway.call_model.return_value = response

    evaluator = ModelEvaluator(gateway, profile_id="opus")
    case = EvalCase(
        id="e1", description="test", input_files={}, expected_patch=""
    )
    evaluator.generate_patch(case)
    assert gateway.call_model.call_args[0][0] == "opus"


# ── compute_patch_similarity tests ────────────────────────────────────────


def test_similarity_identical_patches():
    assert compute_patch_similarity("abc", "abc") == 1.0


def test_similarity_completely_different():
    assert compute_patch_similarity("abc", "xyz") < 0.5


def test_similarity_both_empty():
    assert compute_patch_similarity("", "") == 1.0


def test_similarity_one_empty():
    assert compute_patch_similarity("abc", "") == 0.0


def test_similarity_partial_overlap():
    score = compute_patch_similarity("hello world", "hello earth")
    assert 0.0 < score < 1.0


# ── check_assertions tests ────────────────────────────────────────────────


def test_assertions_patch_contains():
    results = check_assertions(
        {"patch_contains": "return x + 1"},
        "def add(x):\n    return x + 1\n",
    )
    assert results["patch_contains"] is True


def test_assertions_patch_contains_missing():
    results = check_assertions(
        {"patch_contains": "import os"},
        "def add(x):\n    return x + 1\n",
    )
    assert results["patch_contains"] is False


def test_assertions_filename():
    results = check_assertions(
        {"filename": "main.py"},
        "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-foo\n+bar\n",
    )
    assert results["filename"] is True


def test_assertions_filename_missing():
    results = check_assertions(
        {"filename": "lib.py"},
        "--- a/main.py\n+++ b/main.py\n",
    )
    assert results["filename"] is False


def test_assertions_line_count_min_meets_threshold():
    results = check_assertions(
        {"line_count_min": "3"},
        "line1\nline2\nline3\n",
    )
    assert results["line_count_min"] is True


def test_assertions_line_count_min_below_threshold():
    results = check_assertions(
        {"line_count_min": "10"},
        "a\nb\nc\n",
    )
    assert results["line_count_min"] is False


def test_assertions_line_count_min_invalid_value():
    results = check_assertions(
        {"line_count_min": "not_a_number"},
        "a\nb\n",
    )
    assert results["line_count_min"] is False


def test_assertions_multiple_keys():
    results = check_assertions(
        {"patch_contains": "return x", "filename": "main.py"},
        "--- a/main.py\n+++ b/main.py\ndef foo(x):\n    return x\n",
    )
    assert results == {"patch_contains": True, "filename": True}


def test_assertions_empty_dict():
    assert check_assertions({}, "any patch") == {}


def test_assertions_unknown_key_defaults_to_contains():
    results = check_assertions(
        {"custom_check": "hello"},
        "hello world\n",
    )
    assert results["custom_check"] is True


# ── composite_eval_score tests ────────────────────────────────────────────


def test_composite_score_perfect_match():
    patch = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    case = EvalCase(
        id="c1",
        description="test",
        input_files={},
        expected_patch=patch,
    )
    result = composite_eval_score(case, patch, tokens_used=100, duration_ms=500)
    assert result.passed is True
    assert result.score > 0.9
    assert result.actual_patch == patch
    assert result.tokens_used == 100
    assert result.duration_ms == 500


def test_composite_score_total_mismatch():
    case = EvalCase(
        id="c1",
        description="test",
        input_files={},
        expected_patch="--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n",
    )
    result = composite_eval_score(case, "unrelated output", tokens_used=0, duration_ms=0)
    assert result.passed is False
    assert result.score < 0.5
    assert "low_similarity" in " ".join(result.errors)


def test_composite_score_with_assertions():
    patch = "--- a/main.py\n+++ b/main.py\ndef add(x):\n    return x + 1\n"
    case = EvalCase(
        id="c1",
        description="test",
        input_files={},
        expected_patch=patch,
        assertions={"patch_contains": "return x + 1", "filename": "main.py"},
    )
    result = composite_eval_score(case, patch, tokens_used=50, duration_ms=200)
    assert result.passed is True
    assert result.score == 1.0


def test_composite_score_failing_assertion_adds_error():
    case = EvalCase(
        id="c2",
        description="test",
        input_files={},
        expected_patch="patch",
        assertions={"patch_contains": "NO_SUCH_STRING"},
    )
    result = composite_eval_score(case, "patch content here", tokens_used=0, duration_ms=0)
    assert "assertion_failed:patch_contains" in result.errors


def test_composite_score_no_assertions_returns_result():
    case = EvalCase(
        id="c3",
        description="test",
        input_files={},
        expected_patch="same\ntext\n",
    )
    result = composite_eval_score(case, "same\ntext\n", tokens_used=10, duration_ms=100)
    assert isinstance(result, EvalResult)
    assert result.score == 1.0
    assert result.errors == []


def test_composite_score_empty_assertions_uses_default():
    case = EvalCase(
        id="c4",
        description="test",
        input_files={},
        expected_patch="content",
    )
    result = composite_eval_score(case, "content", tokens_used=0, duration_ms=0)
    assert result.score >= 0.0
