"""Tests for the plan-time technical-debt evaluator.

TDD coverage of the policy override, fail-soft fallbacks, and truncation of
:class:`general_ludd.planning.debt_evaluator.DebtEvaluator`.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.debt_evaluator import (
    DebtEvaluator,
    DebtFindings,
    make_debt_evaluate_fn,
)


def _plan_with_test() -> PlanArtifact:
    """A plan whose impl file already has a sibling test in scope."""
    return PlanArtifact(
        todo_id="t1",
        title="harden config parser",
        description="tighten config parsing",
        target_files=["src/app/config.py", "tests/app/test_config.py"],
        contracts=["parse_config(path) returns dict"],
    )


def _plan_missing_test() -> PlanArtifact:
    """A plan whose impl file has NO sibling test — exercises fallback H1."""
    return PlanArtifact(
        todo_id="t2",
        title="add widget",
        target_files=["src/app/widget.py"],
        contracts=[],
    )


# ---------------------------------------------------------------------------
# Policy: fold_in
# ---------------------------------------------------------------------------


def test_in_scope_small_no_new_capability_is_fold_in() -> None:
    plan = _plan_with_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        # Model asserts "defer" — policy must OVERRIDE this to fold_in.
        return [
            {
                "gap": "parse_config drops the error path silently",
                "kind": "sharp_edge",
                "why_it_matters": "callers can't tell a parse failed",
                "recommendation": "defer",
                "effort": "small",
                "touched_files": ["src/app/config.py"],
            }
        ]

    result = DebtEvaluator(fn).evaluate(plan, goal="harden config parsing")
    assert isinstance(result, DebtFindings)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.recommendation == "fold_in"
    assert "in-scope" in f.feature_creep_rationale
    # Non-policy fields survive coercion untouched.
    assert f.kind == "sharp_edge"
    assert f.touched_files == ["src/app/config.py"]


def test_sibling_test_of_target_counts_as_in_scope() -> None:
    plan = _plan_with_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        # touched file is the sibling test of src/app/config.py -> in scope.
        return [
            {
                "gap": "parse_config missing coverage for blank path",
                "kind": "sharp_edge",
                "effort": "small",
                "recommendation": "defer",
                "touched_files": ["tests/app/test_config.py"],
            }
        ]

    result = DebtEvaluator(fn).evaluate(plan, goal="x")
    assert result.findings[0].recommendation == "fold_in"


# ---------------------------------------------------------------------------
# Policy: defer overrides the model's fold_in, on each disqualifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("finding", "reason"),
    [
        (
            {
                "gap": "config also needs a brand new module",
                "kind": "sharp_edge",
                "effort": "small",
                "recommendation": "fold_in",
                "touched_files": ["src/app/newmod.py"],  # NOT in target_files
            },
            "new file",
        ),
        (
            {
                "gap": "parse_config error path is rough",
                "kind": "sharp_edge",
                "effort": "medium",  # not small
                "recommendation": "fold_in",
                "touched_files": ["src/app/config.py"],
            },
            "effort medium",
        ),
        (
            {
                "gap": "parse_config error path is rough",
                "kind": "missing_feature",  # new capability by definition
                "effort": "small",
                "recommendation": "fold_in",
                "touched_files": ["src/app/config.py"],
            },
            "missing_feature",
        ),
    ],
)
def test_policy_overrides_model_to_defer(finding: dict, reason: str) -> None:
    plan = _plan_with_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        return [finding]

    result = DebtEvaluator(fn).evaluate(plan, goal="x")
    assert result.findings[0].recommendation == "defer", reason
    assert "feature creep" in result.findings[0].feature_creep_rationale


def test_empty_touched_files_cannot_fold_in() -> None:
    # A finding naming ZERO files is not "a gap inside files already being
    # changed" -> it must never be fold_in, even if small + sharp_edge.
    plan = _plan_with_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        return [
            {
                "gap": "parse_config error path is rough",
                "kind": "sharp_edge",
                "effort": "small",
                "recommendation": "fold_in",
                # touched_files omitted -> defaults to []
            }
        ]

    result = DebtEvaluator(fn).evaluate(plan, goal="x")
    assert result.findings[0].recommendation == "defer"


def test_goal_text_satisfies_no_new_capability() -> None:
    # A feature-signal phrase present in the GOAL is not "new" -> the finding
    # can fold_in (given it is in-scope + small). Guards the goal/contracts
    # reference wiring in _introduces_new_capability.
    plan = _plan_with_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        return [
            {
                "gap": "implement the null guard in parse_config",
                "kind": "sharp_edge",
                "effort": "small",
                "recommendation": "defer",
                "touched_files": ["src/app/config.py"],
            }
        ]

    ev = DebtEvaluator(fn)
    # Goal MENTIONS "implement" -> signal not new -> fold_in.
    assert ev.evaluate(plan, goal="implement the parser").findings[0].recommendation == (
        "fold_in"
    )
    # Goal WITHOUT the signal -> "implement" reads as new capability -> defer.
    assert ev.evaluate(plan, goal="tidy up").findings[0].recommendation == "defer"


def test_feature_signal_phrase_forces_defer() -> None:
    plan = _plan_with_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        return [
            {
                "gap": "implement a retry queue for parse_config",
                "kind": "sharp_edge",
                "effort": "small",
                "recommendation": "fold_in",
                "touched_files": ["src/app/config.py"],
            }
        ]

    result = DebtEvaluator(fn).evaluate(plan, goal="x")
    assert result.findings[0].recommendation == "defer"


# ---------------------------------------------------------------------------
# Fail-soft fallbacks
# ---------------------------------------------------------------------------


def test_evaluate_fn_raising_falls_back_deterministically() -> None:
    plan = _plan_missing_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        raise RuntimeError("gateway down")

    result = DebtEvaluator(fn).evaluate(plan, goal="x")
    assert isinstance(result, DebtFindings)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.gap == "no test for src/app/widget.py"
    assert f.recommendation == "fold_in"
    assert f.effort == "small"


@pytest.mark.parametrize(
    "bad",
    [
        "not a list",  # non-list return
        {"findings": []},  # dict, not a list
        [{"gap": "x", "kind": "bogus_kind", "effort": "small"}],  # bad enum
        [{"kind": "sharp_edge"}],  # missing required 'gap'
        [123, "nope"],  # list of non-dicts
    ],
)
def test_malformed_return_falls_back(bad: Any) -> None:
    plan = _plan_missing_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> Any:
        return bad

    result = DebtEvaluator(fn).evaluate(plan, goal="x")
    assert isinstance(result, DebtFindings)
    # Deterministic fallback fired (the missing-test heuristic).
    assert any(f.gap == "no test for src/app/widget.py" for f in result.findings)


def test_empty_list_falls_back() -> None:
    # The wired fn returns [] on any gateway error; evaluate() must treat an
    # empty result as a failure signal and use the deterministic fallback.
    plan = _plan_missing_test()
    result = DebtEvaluator(lambda _p, _g, _c: []).evaluate(plan, goal="x")
    assert any(f.gap == "no test for src/app/widget.py" for f in result.findings)


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_max_findings_truncation() -> None:
    plan = _plan_with_test()

    def fn(_p: PlanArtifact, _g: str, _c: str) -> list[dict]:
        return [
            {
                "gap": f"edge {i}",
                "kind": "sharp_edge",
                "effort": "small",
                "recommendation": "defer",
                "touched_files": ["src/app/config.py"],
            }
            for i in range(5)
        ]

    result = DebtEvaluator(fn, max_findings=2).evaluate(plan, goal="x")
    assert len(result.findings) == 2


def test_fallback_truncates_to_max_findings() -> None:
    # Three impl files with no sibling tests -> three H1 findings, capped at 2.
    plan = PlanArtifact(
        todo_id="t4",
        title="multi",
        target_files=["src/a.py", "src/b.py", "src/c.py"],
    )
    result = DebtEvaluator(evaluate_fn=None, max_findings=2).evaluate(plan, goal="x")
    assert len(result.findings) == 2


# ---------------------------------------------------------------------------
# Fallback-only (no injected fn)
# ---------------------------------------------------------------------------


def test_fallback_only_flags_impl_without_sibling_test() -> None:
    plan = _plan_missing_test()
    result = DebtEvaluator(evaluate_fn=None).evaluate(plan, goal="x")
    assert isinstance(result, DebtFindings)
    gaps = {f.gap: f for f in result.findings}
    assert "no test for src/app/widget.py" in gaps
    f = gaps["no test for src/app/widget.py"]
    assert f.kind == "sharp_edge"
    assert f.recommendation == "fold_in"
    assert f.effort == "small"


def test_fallback_flags_resilience_contract_as_defer() -> None:
    plan = PlanArtifact(
        todo_id="t3",
        title="fetch",
        target_files=["src/app/fetch.py", "tests/app/test_fetch.py"],
        contracts=["fetch() retries on timeout and raises on failure"],
    )
    result = DebtEvaluator(evaluate_fn=None).evaluate(plan, goal="x")
    resilience = [f for f in result.findings if "resilience" in f.gap]
    assert len(resilience) == 1
    assert resilience[0].recommendation == "defer"
    assert resilience[0].kind == "sharp_edge"


# ---------------------------------------------------------------------------
# make_debt_evaluate_fn wiring (offline, fake gateway)
# ---------------------------------------------------------------------------


def test_make_debt_evaluate_fn_parses_findings_envelope() -> None:
    class FakeResp:
        content = (
            '{"findings": [{"gap": "g", "kind": "sharp_edge", '
            '"effort": "small", "touched_files": ["src/app/config.py"]}]}'
        )

    class FakeGateway:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def call_model(self, profile_id: str, **kwargs: Any) -> FakeResp:
            self.calls.append({"profile_id": profile_id, **kwargs})
            return FakeResp()

    gw = FakeGateway()
    fn = make_debt_evaluate_fn(gw, profile_id="compactor")
    out = fn(_plan_with_test(), "goal", "")
    assert out == [
        {
            "gap": "g",
            "kind": "sharp_edge",
            "effort": "small",
            "touched_files": ["src/app/config.py"],
        }
    ]
    # Correct gateway contract: work_type + guided_json schema forwarded.
    assert gw.calls[0]["work_type"] == "debt_eval"
    assert "guided_json" in gw.calls[0]
    assert gw.calls[0]["requested_max_output_tokens"] == 768


def test_make_debt_evaluate_fn_parses_bare_list() -> None:
    # The model may return a top-level JSON array instead of the envelope.
    class FakeResp:
        content = '[{"gap": "g", "kind": "sharp_edge", "effort": "small"}]'

    class FakeGateway:
        def call_model(self, _profile_id: str, **_kwargs: Any) -> FakeResp:
            return FakeResp()

    fn = make_debt_evaluate_fn(FakeGateway())
    assert fn(_plan_with_test(), "goal", "") == [
        {"gap": "g", "kind": "sharp_edge", "effort": "small"}
    ]


def test_make_debt_evaluate_fn_returns_empty_on_gateway_error() -> None:
    class BoomGateway:
        def call_model(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("boom")

    fn = make_debt_evaluate_fn(BoomGateway())
    assert fn(_plan_with_test(), "goal", "") == []
