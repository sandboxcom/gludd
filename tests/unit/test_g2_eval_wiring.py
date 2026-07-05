"""Tests for G2 eval harness wiring — run_single, run_benchmark scoring, daemon endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app
from general_ludd.eval.harness import EvalHarness
from general_ludd.eval.model import ModelEvaluator
from general_ludd.eval.schema import EvalCase, EvalResult
from general_ludd.models.gateway import ModelGateway, ModelResponse


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GLUDD_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")


PATCH_TEXT = (
    "--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n"
    " def foo(x):\n-    return x.bar()\n+    return x.bar() if x else None\n"
)


def _make_case(
    case_id: str = "c1",
    description: str = "Fix NPE",
    input_files: dict[str, str] | None = None,
    expected_patch: str = PATCH_TEXT,
) -> EvalCase:
    return EvalCase(
        id=case_id,
        description=description,
        input_files=input_files or {"main.py": "def foo(x): return x.bar()\n"},
        expected_patch=expected_patch,
    )


def _make_evaluator() -> ModelEvaluator:
    gateway = MagicMock(spec=ModelGateway)
    response = MagicMock(spec=ModelResponse)
    response.content = PATCH_TEXT
    gateway.call_model.return_value = response
    return ModelEvaluator(gateway, profile_id="sonnet")


class TestRunSingle:
    def test_run_single_returns_eval_result(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case()

        result = harness.run_single(case)

        assert isinstance(result, EvalResult)
        assert result.case_id == "c1"
        assert result.actual_patch == PATCH_TEXT

    def test_run_single_scores_high_for_perfect_match(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case(expected_patch=PATCH_TEXT)

        result = harness.run_single(case)

        assert result.passed is True
        assert result.score > 0.9

    def test_run_single_no_evaluator_returns_error(self):
        harness = EvalHarness(model="sonnet", evaluator=None)
        case = _make_case()

        result = harness.run_single(case)

        assert result.passed is False
        assert "no evaluator configured" in result.errors

    def test_run_single_records_duration(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case()

        result = harness.run_single(case)

        assert result.duration_ms > 0


class TestRunBenchmark:
    def test_run_benchmark_uses_composite_score(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case(expected_patch=PATCH_TEXT)

        results = harness.run_benchmark([case])

        assert len(results) == 1
        assert results[0].score > 0.9
        assert results[0].passed is True

    def test_run_benchmark_no_evaluator(self):
        harness = EvalHarness(model="sonnet", evaluator=None)
        case = _make_case()

        results = harness.run_benchmark([case])

        assert len(results) == 1
        assert results[0].passed is False
        assert "no evaluator configured" in results[0].errors

    def test_run_benchmark_stores_last_results(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case()

        harness.run_benchmark([case])

        stored = harness.last_results
        assert len(stored) == 1
        assert stored[0].case_id == "c1"

    def test_last_results_returns_copy_not_reference(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        harness.run_benchmark([_make_case()])

        copy1 = harness.last_results
        copy2 = harness.last_results
        assert copy1 is not copy2

    def test_run_benchmark_multiple_cases(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        cases = [
            _make_case(case_id="c1"),
            _make_case(case_id="c2"),
            _make_case(case_id="c3"),
        ]

        results = harness.run_benchmark(cases)

        assert len(results) == 3
        assert [r.case_id for r in results] == ["c1", "c2", "c3"]
        assert all(r.passed for r in results)


class TestEvalDaemonEndpoints:
    @pytest.fixture
    def app(self):
        return create_daemon_app(tick_interval=0.01)

    def test_post_eval_run_without_evaluator_returns_503(self, app):
        with TestClient(app) as client:
            resp = client.post(
                "/admin/eval/run",
                json={"cases": [{"id": "c1", "description": "test"}]},
            )
            assert resp.status_code == 503
            assert "no evaluator configured" in resp.json()["detail"]

    def test_post_eval_run_without_cases_returns_422(self, app):
        with TestClient(app) as client:
            resp = client.post("/admin/eval/run", json={})
            assert resp.status_code == 422
            assert "cases" in resp.json()["detail"]

    def test_get_eval_results_returns_empty_before_run(self, app):
        with TestClient(app) as client:
            resp = client.get("/admin/eval/results")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 0
            assert data["passed"] == 0
            assert data["results"] == []
