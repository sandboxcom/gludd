"""Tests for lightweight lm_eval runner integration."""

from __future__ import annotations

import hashlib
import sys
from unittest.mock import MagicMock, patch

from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.lm_eval_runner import (
    LMEvalRunner,
    _try_import_lm_eval,
    run_benchmark,
    to_capability_evidence,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _mock_lm_eval_module() -> MagicMock:
    mock = MagicMock()
    mock.__spec__ = MagicMock()
    return mock


class TestTryImportLmEval:
    def test_returns_true_when_lm_eval_available(self) -> None:
        with patch.dict("sys.modules", {"lm_eval": _mock_lm_eval_module()}):
            assert _try_import_lm_eval() is True

    def test_returns_false_when_lm_eval_unavailable(self) -> None:
        with patch.dict("sys.modules", {}, clear=True):
            assert _try_import_lm_eval() is False


class TestLMEvalRunnerInit:
    def test_default_construction(self) -> None:
        runner = LMEvalRunner()
        assert runner.model_id == "hf"
        assert runner.batch_size == "auto"
        assert runner.device is None
        assert runner.limit is None

    def test_construction_with_overrides(self) -> None:
        runner = LMEvalRunner(
            model_id="gpt2",
            batch_size="4",
            device="cpu",
            limit=100,
        )
        assert runner.model_id == "gpt2"
        assert runner.batch_size == "4"
        assert runner.device == "cpu"
        assert runner.limit == 100

    def test_default_tasks_has_expected_benchmarks(self) -> None:
        runner = LMEvalRunner()
        tasks = runner.default_tasks
        assert "mmlu" in tasks
        assert "gsm8k" in tasks
        assert "hellaswag" in tasks
        assert "arc_easy" in tasks
        assert "arc_challenge" in tasks
        assert "truthfulqa_mc2" in tasks
        assert len(tasks) == 6


class TestLMEvalRunnerBuildCommand:
    def test_builds_command_for_single_task(self) -> None:
        runner = LMEvalRunner(model_id="gpt2", device="cpu", limit=10)
        cmd = runner._build_command(["mmlu"])
        assert cmd[0] == "lm_eval"
        assert "--model" in cmd
        assert "hf" in cmd
        assert "--model_args" in cmd
        assert "pretrained=gpt2" in cmd
        assert "--batch_size" in cmd
        assert "--tasks" in cmd
        task_idx = cmd.index("--tasks")
        assert cmd[task_idx + 1] == "mmlu"
        assert "--limit" in cmd
        assert "10" in cmd

    def test_builds_command_for_multiple_tasks(self) -> None:
        runner = LMEvalRunner(model_id="gpt2")
        cmd = runner._build_command(["mmlu", "hellaswag"])
        task_idx = cmd.index("--tasks")
        tasks_arg = cmd[task_idx + 1]
        assert "mmlu" in tasks_arg
        assert "hellaswag" in tasks_arg
        assert "," in tasks_arg

    def test_omits_limit_when_none(self) -> None:
        runner = LMEvalRunner(model_id="gpt2")
        cmd = runner._build_command(["mmlu"])
        assert "--limit" not in cmd


class TestRunBenchmark:
    def test_returns_empty_dict_when_lm_eval_unavailable(self) -> None:
        with patch(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            return_value=False,
        ):
            result = run_benchmark("gpt2", ["mmlu", "hellaswag"])
            assert result == {}

    def test_graceful_skip_on_import_failure(self) -> None:
        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict("sys.modules", {"lm_eval": MagicMock()}, clear=True),
        ):
            sys.modules["lm_eval"].simple_evaluate = MagicMock(
                side_effect=ImportError("optional dependency missing"),
            )
            result = run_benchmark("gpt2", ["mmlu"])
            assert result == {}

    def test_returns_result_dict_on_success(self) -> None:
        mock_results = {
            "results": {
                "mmlu": {"acc,none": 0.55, "acc_norm,none": 0.57},
                "hellaswag": {"acc,none": 0.72, "acc_norm,none": 0.74},
            }
        }
        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict("sys.modules", {"lm_eval": MagicMock()}, clear=True),
        ):
            sys.modules["lm_eval"].simple_evaluate = MagicMock(
                return_value=mock_results,
            )
            result = run_benchmark("gpt2", ["mmlu", "hellaswag"])
            assert result == {"mmlu": 0.55, "hellaswag": 0.72}

    def test_filters_results_to_requested_tasks(self) -> None:
        mock_results = {
            "results": {
                "mmlu": {"acc,none": 0.55},
                "hellaswag": {"acc,none": 0.72},
            }
        }
        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict("sys.modules", {"lm_eval": MagicMock()}, clear=True),
        ):
            sys.modules["lm_eval"].simple_evaluate = MagicMock(
                return_value=mock_results,
            )
            result = run_benchmark("gpt2", ["mmlu"])
            assert "mmlu" in result
            assert "hellaswag" not in result

    def test_skips_tasks_with_non_numeric_scores(self) -> None:
        mock_results = {
            "results": {
                "mmlu": {"acc,none": 0.55},
                "bad_task": {"acc,none": "pending"},
            }
        }
        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict("sys.modules", {"lm_eval": MagicMock()}, clear=True),
        ):
            sys.modules["lm_eval"].simple_evaluate = MagicMock(
                return_value=mock_results,
            )
            result = run_benchmark("gpt2", ["mmlu", "bad_task"])
            assert "mmlu" in result
            assert "bad_task" not in result

    def test_passes_model_args_and_batch_size(self) -> None:
        mock_results = {"results": {"mmlu": {"acc,none": 0.55}}}
        runner = LMEvalRunner(model_id="gpt2", batch_size="2", limit=50)
        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict("sys.modules", {"lm_eval": MagicMock()}, clear=True),
        ):
            mock_eval = MagicMock(return_value=mock_results)
            sys.modules["lm_eval"].simple_evaluate = mock_eval
            runner.run_benchmark(["mmlu"])
            call_kwargs = mock_eval.call_args.kwargs
            assert call_kwargs["model"] == "hf"
            assert call_kwargs["model_args"] == "pretrained=gpt2"
            assert call_kwargs["batch_size"] == "2"
            assert call_kwargs["limit"] == 50


class TestToCapabilityEvidence:
    def test_returns_empty_list_for_empty_results(self) -> None:
        evidence = to_capability_evidence({}, "model-1")
        assert evidence == []

    def test_produces_capability_evidence_for_each_task(self) -> None:
        results = {"mmlu": 0.55, "hellaswag": 0.72}
        evidence = to_capability_evidence(results, "model-1")
        assert len(evidence) == 2
        for ev in evidence:
            assert isinstance(ev, CapabilityEvidence)
            assert ev.model_profile_id == "model-1"
            assert ev.collection == "general_ludd.agent"
            assert ev.suite_id == "lm-eval-runner"
            assert ev.local_only is True
            assert ev.collection_ok is True

    def test_sets_task_kind_and_role_from_task_name(self) -> None:
        results = {"mmlu": 0.55}
        evidence = to_capability_evidence(results, "model-1")
        ev = evidence[0]
        assert ev.task_kind == "mmlu"
        assert ev.role == TaskRole.ENUMERATOR
        assert ev.total_cases == 1

    def test_sets_passed_cases_based_on_score(self) -> None:
        results = {"mmlu": 0.20, "hellaswag": 0.80}
        evidence = to_capability_evidence(results, "model-1")
        ev_low = next(e for e in evidence if e.task_kind == "mmlu")
        ev_high = next(e for e in evidence if e.task_kind == "hellaswag")
        assert ev_low.passed_cases == 0
        assert ev_high.passed_cases == 1

    def test_uses_custom_thresholds_when_provided(self) -> None:
        results = {"mmlu": 0.55}
        evidence = to_capability_evidence(results, "model-1", thresholds={"mmlu": 0.60})
        ev = evidence[0]
        assert ev.passed_cases == 0

    def test_unknown_task_uses_default_threshold(self) -> None:
        results = {"some_task": 0.80}
        evidence = to_capability_evidence(results, "model-1")
        ev = evidence[0]
        assert ev.passed_cases == 1

    def test_passing_score_at_threshold(self) -> None:
        results = {"mmlu": 0.30}
        evidence = to_capability_evidence(results, "model-1")
        ev = evidence[0]
        assert ev.passed_cases == 1

    def test_produces_unique_digests_per_task(self) -> None:
        results = {"mmlu": 0.55, "hellaswag": 0.72}
        evidence = to_capability_evidence(results, "model-1")
        digests = [ev.evidence_digest for ev in evidence]
        assert len(set(digests)) == 2

    def test_produces_different_digests_for_different_models(self) -> None:
        results = {"mmlu": 0.55}
        e1 = to_capability_evidence(results, "model-1")
        e2 = to_capability_evidence(results, "model-2")
        assert e1[0].evidence_digest != e2[0].evidence_digest

    def test_respects_optional_model_identity_digest(self) -> None:
        results = {"mmlu": 0.55}
        evidence = to_capability_evidence(results, "model-1", model_identity_digest=_digest("custom-weights"))
        assert evidence[0].model_identity_digest == _digest("custom-weights")

    def test_uses_default_model_identity_digest(self) -> None:
        results = {"mmlu": 0.55}
        evidence = to_capability_evidence(results, "model-1")
        expected = _digest("model-1:unnamed")
        assert evidence[0].model_identity_digest == expected

    def test_handles_results_with_various_task_names(self) -> None:
        results = {f"task_{i}": 0.55 for i in range(3)}
        evidence = to_capability_evidence(results, "model-1")
        assert len(evidence) == 3
        task_names = {ev.task_kind for ev in evidence}
        assert task_names == {"task_0", "task_1", "task_2"}


class TestLMEvalRunnerRunBenchmark:
    def test_instance_method_delegates_to_module_function(self) -> None:
        runner = LMEvalRunner(model_id="gpt2", device="cpu", limit=20)
        mock_results = {"results": {"mmlu": {"acc,none": 0.55}}}
        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict("sys.modules", {"lm_eval": MagicMock()}, clear=True),
        ):
            sys.modules["lm_eval"].simple_evaluate = MagicMock(
                return_value=mock_results,
            )
            result = runner.run_benchmark(["mmlu"])
            assert result == {"mmlu": 0.55}

    def test_instance_method_handles_unavailable(self) -> None:
        runner = LMEvalRunner()
        with patch(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            return_value=False,
        ):
            assert runner.run_benchmark(["mmlu"]) == {}
