"""Behavioral tests for self-improvement evaluation and acceptance policy."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import general_ludd.self_improve.evaluator as evaluator_module
from general_ludd.self_improve.evaluator import (
    SELF_IMPROVE_TARGETS,
    RunMetrics,
    SelfImproveEvaluator,
    SelfImproveResult,
    print_report,
    run_all_self_improve,
    run_self_improve,
)


class CallModelGateway:
    def __init__(self, content: str = "```python\nvalue = 2\n```") -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []
        self.prompts: list[str] = []

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        estimated_cost: float,
        budget_remaining: float,
    ) -> SimpleNamespace:
        self.prompts.append(messages[0]["content"])
        self.calls.append(
            {
                "profile_id": profile_id,
                "messages": messages,
                "estimated_cost": estimated_cost,
                "budget_remaining": budget_remaining,
            }
        )
        return SimpleNamespace(content=self.content)


class CompleteGateway:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


def _evaluator(
    tmp_path: Path,
    *,
    gateway: object | None = None,
    thresholds: dict[str, float] | None = None,
    max_attempts: int = 2,
) -> SelfImproveEvaluator:
    (tmp_path / "component.py").write_text("value = 1\n")
    (tmp_path / "test_component.py").write_text("def test_value():\n    assert True\n")
    return SelfImproveEvaluator(
        gateway=gateway,
        test_file="test_component.py",
        component_file="component.py",
        provider="local",
        improvement_thresholds=thresholds,
        max_attempts=max_attempts,
        budget_usd=2.0,
        repo_root=str(tmp_path),
        model_profile_id="test-profile",
    )


def test_metrics_and_result_deltas_are_derived() -> None:
    empty = RunMetrics()
    baseline = RunMetrics(test_count=4, test_pass=3, median_wall_ms=20.0, compute_cost_usd=0.1)
    improved = RunMetrics(test_count=4, test_pass=4, median_wall_ms=12.0, compute_cost_usd=0.2)
    result = SelfImproveResult(
        component="component",
        provider="local",
        baseline=baseline,
        improved_metrics=improved,
        improvement_attempted=True,
        improvement_accepted=True,
    )

    assert empty.pass_rate == 0.0
    assert baseline.pass_rate == 0.75
    assert improved.total_cost_usd == 0.2
    assert result.completeness_delta == 0.25
    assert result.timing_delta_ms == -8.0
    assert result.cost_delta_usd == pytest.approx(0.1)
    assert result.improved is True


def test_result_requires_attempt_and_acceptance() -> None:
    result = SelfImproveResult(component="x", provider="local")
    result.improvement_accepted = True
    assert result.improved is False
    result.improvement_attempted = True
    assert result.improved is True


def test_numeric_helpers_ignore_non_numeric_durations(tmp_path: Path) -> None:
    evaluator = _evaluator(tmp_path)

    assert evaluator._extract_timings(
        {"tests": [{"duration": 0.3}, {"duration": 1}, {"duration": "slow"}, {}]}
    ) == [0.3, 1.0, 0.0]
    assert evaluator._median([]) == 0.0
    assert evaluator._median([3.0]) == 3.0
    assert evaluator._median([1.0, 9.0, 3.0]) == 3.0
    assert evaluator._median([1.0, 3.0]) == 2.0
    assert evaluator._count_tokens("") == 0
    assert evaluator._count_tokens("abcdefgh") == 2


def test_run_pytest_parses_json_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path)
    report = {"tests": [{"duration": 0.1}], "passed": 1, "failed": 0, "skipped": 0}
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=f"pytest prelude\n{json.dumps(report)}\n")

    monkeypatch.setattr(subprocess, "run", run)

    assert evaluator._run_pytest("test_component.py") == report
    command, kwargs = calls[0]
    assert command[:3] == ["python", "-m", "pytest"]
    assert "--json-report-file=-" in command
    assert kwargs["timeout"] == 180
    assert kwargs["cwd"] == str(tmp_path)


def test_run_pytest_handles_timeout_and_invalid_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path)

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=180)

    monkeypatch.setattr(subprocess, "run", timeout)
    timed_out = evaluator._run_pytest("test_component.py")
    assert timed_out["_error"] == "timeout"
    assert timed_out["passed"] == 0

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="not json\n"),
    )
    assert evaluator._run_pytest("test_component.py")["tests"] == []


def test_run_pytest_rejects_missing_test_file(tmp_path: Path) -> None:
    evaluator = _evaluator(tmp_path)

    with pytest.raises(FileNotFoundError, match=r"missing\.py"):
        evaluator._run_pytest("missing.py")


def test_run_baseline_builds_metrics_from_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path)
    report = {
        "passed": 3,
        "failed": 1,
        "skipped": 1,
        "tests": [{"duration": 0.1}, {"duration": 0.3}],
    }
    monkeypatch.setattr(evaluator, "_run_pytest", lambda _path: report)

    metrics = evaluator.run_baseline()

    assert metrics.test_count == 5
    assert metrics.test_pass == 3
    assert metrics.test_fail == 1
    assert metrics.test_skip == 1
    assert metrics.median_wall_ms == pytest.approx(200.0)
    assert metrics.llm_tokens > 0
    assert metrics.compute_cost_usd == 0.0


def test_read_file_handles_existing_missing_and_io_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path)
    assert evaluator._read_file("component.py") == "value = 1\n"
    assert evaluator._read_file("missing.py") == ""

    original_read_text = Path.read_text

    def broken_read(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path.name == "component.py":
            raise OSError("unreadable")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", broken_read)
    assert evaluator._read_file("component.py") == ""


def test_gateway_call_model_receives_budget_and_profile(tmp_path: Path) -> None:
    gateway = CallModelGateway("answer")
    evaluator = _evaluator(tmp_path, gateway=gateway)

    assert evaluator._invoke_gateway("prompt") == "answer"
    assert gateway.calls == [
        {
            "profile_id": "test-profile",
            "messages": [{"role": "user", "content": "prompt"}],
            "estimated_cost": 0.05,
            "budget_remaining": 2.0,
        }
    ]


def test_gateway_complete_fallback_and_failures(tmp_path: Path) -> None:
    complete = CompleteGateway("completed")
    evaluator = _evaluator(tmp_path, gateway=complete)
    assert evaluator._invoke_gateway("prompt") == "completed"
    assert complete.prompts == ["prompt"]

    with pytest.raises(RuntimeError, match="no model gateway"):
        _evaluator(tmp_path, gateway=None)._invoke_gateway("prompt")
    with pytest.raises(RuntimeError, match="neither call_model nor complete"):
        _evaluator(tmp_path, gateway=object())._invoke_gateway("prompt")


def test_gateway_wraps_non_runtime_failures(tmp_path: Path) -> None:
    class BrokenGateway:
        def complete(self, _prompt: str) -> None:
            raise ValueError("provider failed")

    with pytest.raises(RuntimeError, match="Gateway call failed: provider failed"):
        _evaluator(tmp_path, gateway=BrokenGateway())._invoke_gateway("prompt")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("```python\nvalue = 2\n```", "value = 2"),
        ("```\nvalue = 3\n```", "value = 3"),
        ("value = 4\n", "value = 4"),
        ("# explanation only", ""),
    ],
)
def test_extract_code_block_accepts_code_only(
    tmp_path: Path,
    response: str,
    expected: str,
) -> None:
    assert _evaluator(tmp_path)._extract_code_block(response) == expected


def test_run_improvement_includes_source_tests_and_uses_code_block(tmp_path: Path) -> None:
    gateway = CallModelGateway("```python\nvalue = 9\n```")
    evaluator = _evaluator(tmp_path, gateway=gateway)

    assert evaluator.run_improvement() == "value = 9"
    prompt = gateway.prompts[0]
    assert "CURRENT SOURCE (component.py)" in prompt
    assert "value = 1" in prompt
    assert "TEST SUITE (test_component.py)" in prompt


def test_run_improvement_keeps_source_when_response_has_no_code(tmp_path: Path) -> None:
    evaluator = _evaluator(tmp_path, gateway=CallModelGateway("# no usable code"))
    assert evaluator.run_improvement() == "value = 1\n"


def test_validate_improved_measures_candidate_and_restores_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path)
    monkeypatch.setattr(
        evaluator,
        "_run_pytest",
        lambda _path: {
            "passed": 2,
            "failed": 1,
            "skipped": 1,
            "tests": [{"duration": 0.2}, {"duration": 0.4}],
        },
    )

    metrics = evaluator.validate_improved("value = 2\n")

    assert metrics.test_count == 4
    assert metrics.test_pass == 2
    assert metrics.test_fail == 1
    assert metrics.test_skip == 1
    assert metrics.median_wall_ms == pytest.approx(300.0)
    assert metrics.compute_cost_usd == 0.05
    assert (tmp_path / "component.py").read_text() == "value = 1\n"
    assert not (tmp_path / "component.py.bak").exists()


def test_validate_improved_supports_new_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path)
    (tmp_path / "component.py").unlink()
    monkeypatch.setattr(
        evaluator,
        "_run_pytest",
        lambda _path: {"passed": 1, "failed": 0, "skipped": 0, "tests": []},
    )

    metrics = evaluator.validate_improved("value = 3\n")

    assert metrics.test_pass == 1
    assert metrics.median_wall_ms >= 0
    assert (tmp_path / "component.py").read_text() == "value = 3\n"


@pytest.mark.parametrize(
    ("baseline", "improved", "thresholds", "reason"),
    [
        (
            RunMetrics(test_count=10, test_pass=9, median_wall_ms=10),
            RunMetrics(test_count=10, test_pass=8, median_wall_ms=10),
            {"min_pass_rate": 0.9},
            "pass_rate",
        ),
        (
            RunMetrics(test_count=10, test_pass=8, median_wall_ms=10),
            RunMetrics(test_count=10, test_pass=9, median_wall_ms=20),
            {"max_timing_increase_pct": 20},
            "timing increase",
        ),
        (
            RunMetrics(test_count=10, test_pass=8, median_wall_ms=10),
            RunMetrics(test_count=10, test_pass=9, median_wall_ms=10, compute_cost_usd=2),
            {"max_cost_usd": 1},
            "cost",
        ),
        (
            RunMetrics(test_count=10, test_pass=9, median_wall_ms=10),
            RunMetrics(test_count=10, test_pass=8, median_wall_ms=10),
            {},
            "regressed",
        ),
    ],
)
def test_thresholds_reject_regressions(
    tmp_path: Path,
    baseline: RunMetrics,
    improved: RunMetrics,
    thresholds: dict[str, float],
    reason: str,
) -> None:
    accepted, explanation = _evaluator(tmp_path, thresholds=thresholds)._check_thresholds(
        baseline, improved
    )
    assert accepted is False
    assert reason in explanation


def test_thresholds_accept_non_regressing_candidate(tmp_path: Path) -> None:
    baseline = RunMetrics(test_count=10, test_pass=8, median_wall_ms=10)
    improved = RunMetrics(test_count=10, test_pass=9, median_wall_ms=10)
    assert _evaluator(tmp_path)._check_thresholds(baseline, improved) == (True, "")


def test_evaluate_accepts_and_persists_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path, max_attempts=1)
    baseline = RunMetrics(test_count=2, test_pass=1, median_wall_ms=10)
    improved = RunMetrics(test_count=2, test_pass=2, median_wall_ms=10)
    monkeypatch.setattr(evaluator, "run_baseline", lambda: baseline)
    monkeypatch.setattr(evaluator, "run_improvement", lambda: "value = 8\n")
    monkeypatch.setattr(evaluator, "validate_improved", lambda _code: improved)

    result = evaluator.evaluate()

    assert result.improved is True
    assert result.baseline is baseline
    assert result.improved_metrics is improved
    assert (tmp_path / "component.py").read_text() == "value = 8\n"


def test_evaluate_records_baseline_and_attempt_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_failure = _evaluator(tmp_path)

    def fail_baseline() -> RunMetrics:
        raise RuntimeError("baseline broke")

    monkeypatch.setattr(baseline_failure, "run_baseline", fail_baseline)
    baseline_result = baseline_failure.evaluate()
    assert baseline_result.errors == ["baseline failed: baseline broke"]
    assert baseline_result.improvement_attempted is False

    attempt_failure = _evaluator(tmp_path, max_attempts=2)
    monkeypatch.setattr(attempt_failure, "run_baseline", lambda: RunMetrics())

    def fail_attempt() -> str:
        raise ValueError("candidate broke")

    monkeypatch.setattr(attempt_failure, "run_improvement", fail_attempt)
    attempt_result = attempt_failure.evaluate()
    assert attempt_result.errors == [
        "attempt 1 failed: candidate broke",
        "attempt 2 failed: candidate broke",
    ]
    assert attempt_result.revert_reason == "candidate broke"


def test_evaluate_retries_threshold_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path, max_attempts=2, thresholds={"min_pass_rate": 1.0})
    monkeypatch.setattr(
        evaluator,
        "run_baseline",
        lambda: RunMetrics(test_count=2, test_pass=2, median_wall_ms=10),
    )
    monkeypatch.setattr(evaluator, "run_improvement", lambda: "value = 2")
    monkeypatch.setattr(
        evaluator,
        "validate_improved",
        lambda _code: RunMetrics(test_count=2, test_pass=1, median_wall_ms=10),
    )

    result = evaluator.evaluate()

    assert result.improvement_attempted is True
    assert result.improvement_accepted is False
    assert result.revert_reason == "thresholds not met after max attempts"


def test_persist_revert_and_report_are_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path)
    evaluator._persist_improvement("value = 7\n")
    assert (tmp_path / "component.py").read_text() == "value = 7\n"

    original = tmp_path / "component.py.orig"
    original.write_text("backup")
    evaluator.revert("test cleanup")
    evaluator.revert("idempotent")
    assert not original.exists()

    result = SelfImproveResult(
        component="component",
        provider="local",
        baseline=RunMetrics(test_count=2, test_pass=1, median_wall_ms=20, llm_tokens=10),
        improved_metrics=RunMetrics(test_count=2, test_pass=2, median_wall_ms=10, llm_tokens=12),
        improvement_attempted=True,
        improvement_accepted=True,
    )
    monkeypatch.setattr(evaluator, "evaluate", lambda: result)
    report = evaluator.report()
    assert report["baseline"]["pass_rate"] == 0.5
    assert report["improved_metrics"]["pass_rate"] == 1.0
    assert report["deltas"]["timing_ms"] == -10
    assert report["improved"] is True


def test_run_self_improve_validates_target_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SelfImproveResult(component="target", provider="local")
    monkeypatch.setattr(SelfImproveEvaluator, "evaluate", lambda _self: expected)

    assert run_self_improve("game_generator", gateway=object(), max_attempts=1) is expected
    with pytest.raises(ValueError, match=r"Unknown target.*Available"):
        run_self_improve("missing", gateway=object())


def test_run_all_isolates_target_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    failed_name = next(iter(SELF_IMPROVE_TARGETS))

    def run_one(name: str, _gateway: object, **_kwargs: object) -> SelfImproveResult:
        if name == failed_name:
            raise RuntimeError("one target failed")
        return SelfImproveResult(component=name, provider="local")

    monkeypatch.setattr(evaluator_module, "run_self_improve", run_one)

    results = run_all_self_improve(object(), max_attempts=1)

    assert len(results) == len(SELF_IMPROVE_TARGETS)
    failed = next(result for result in results if result.component == failed_name)
    assert failed.errors == ["one target failed"]


def test_print_report_renders_acceptance(capsys: pytest.CaptureFixture[str]) -> None:
    print_report(
        [
            SelfImproveResult(
                component="component",
                provider="local",
                baseline=RunMetrics(test_count=2, test_pass=1),
                improved_metrics=RunMetrics(test_count=2, test_pass=2),
                improvement_attempted=True,
                improvement_accepted=True,
            )
        ]
    )

    output = capsys.readouterr().out
    assert "Component" in output
    assert "component" in output
    assert "1/2 pass" in output
    assert "2/2 pass" in output
    assert "YES" in output
