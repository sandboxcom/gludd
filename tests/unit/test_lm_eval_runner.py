"""Unit tests for lm_eval_runner — initialization, task loading, result parsing,
error handling, timeout, invalid config, empty results."""

from __future__ import annotations

import hashlib
import sys
from unittest.mock import MagicMock, patch

from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.lm_eval_runner import (
    _DEFAULT_TASKS,
    _DEFAULT_THRESHOLD,
    _DEFAULT_THRESHOLDS,
    _SUITE_REVISION,
    LMEvalRunner,
    _extract_primary_score,
    _extract_score_from_results,
    _try_import_lm_eval,
    run_benchmark,
    to_capability_evidence,
)


class TestTryImportLmEval:
    def test_returns_bool(self) -> None:
        result = _try_import_lm_eval()
        assert isinstance(result, bool)


class TestExtractPrimaryScore:
    def test_exact_match_flexible_extract(self) -> None:
        metrics = {"exact_match,flexible-extract": 0.42}
        assert _extract_primary_score(metrics) == 0.42

    def test_acc_none_first_priority(self) -> None:
        metrics = {"acc,none": 0.75, "f1,none": 0.60}
        assert _extract_primary_score(metrics) == 0.75

    def test_acc_norm_none(self) -> None:
        metrics = {"acc_norm,none": 0.88}
        assert _extract_primary_score(metrics) == 0.88

    def test_f1_none(self) -> None:
        metrics = {"f1,none": 0.55}
        assert _extract_primary_score(metrics) == 0.55

    def test_fallback_first_numeric_value(self) -> None:
        metrics = {"some_custom_metric": 0.33, "another": 0.44}
        assert _extract_primary_score(metrics) == 0.33

    def test_empty_metrics_returns_none(self) -> None:
        assert _extract_primary_score({}) is None

    def test_no_numeric_values_returns_none(self) -> None:
        metrics = {"status": "ok", "message": "done"}
        assert _extract_primary_score(metrics) is None

    def test_skips_non_numeric_dict_values(self) -> None:
        metrics = {"notes": "some text", "acc,none": 0.65}
        assert _extract_primary_score(metrics) == 0.65

    def test_prioritizes_priority_key_over_other_numerics(self) -> None:
        metrics = {"random": 0.10, "exact_match,flexible-extract": 0.90}
        assert _extract_primary_score(metrics) == 0.90


class TestExtractScoreFromResults:
    def test_extracts_requested_task_scores(self) -> None:
        results = {
            "results": {
                "mmlu": {"acc,none": 0.35, "acc_norm,none": 0.38},
                "gsm8k": {"exact_match,flexible-extract": 0.25},
            }
        }
        scores = _extract_score_from_results(results, ["mmlu", "gsm8k"])
        assert scores == {"mmlu": 0.35, "gsm8k": 0.25}

    def test_ignores_unrequested_tasks(self) -> None:
        results = {
            "results": {
                "mmlu": {"acc,none": 0.35},
                "hellaswag": {"acc_norm,none": 0.72},
            }
        }
        scores = _extract_score_from_results(results, ["mmlu"])
        assert scores == {"mmlu": 0.35}

    def test_empty_results_dict_returns_empty(self) -> None:
        assert _extract_score_from_results({}, ["mmlu"]) == {}

    def test_missing_results_key_returns_empty(self) -> None:
        assert _extract_score_from_results({"other": 1}, ["mmlu"]) == {}

    def test_non_dict_results_value_returns_empty(self) -> None:
        assert _extract_score_from_results({"results": [1, 2, 3]}, ["mmlu"]) == {}

    def test_skips_task_with_non_dict_metrics(self) -> None:
        results = {"results": {"mmlu": "not_a_dict"}}
        scores = _extract_score_from_results(results, ["mmlu"])
        assert scores == {}

    def test_task_with_no_numeric_metrics_skipped(self) -> None:
        results = {"results": {"mmlu": {"status": "ok"}}}
        scores = _extract_score_from_results(results, ["mmlu"])
        assert scores == {}

    def test_handles_mixed_valid_and_invalid_tasks(self) -> None:
        results = {
            "results": {
                "mmlu": {"acc,none": 0.40},
                "gsm8k": "broken",
                "arc_easy": {},
            }
        }
        scores = _extract_score_from_results(results, ["mmlu", "gsm8k", "arc_easy"])
        assert scores == {"mmlu": 0.40}


class TestLMEvalRunnerInit:
    def test_default_initialization(self) -> None:
        runner = LMEvalRunner()
        assert runner.model_id == "hf"
        assert runner.batch_size == "auto"
        assert runner.device is None
        assert runner.limit is None
        assert isinstance(runner.available, bool)

    def test_custom_initialization(self) -> None:
        runner = LMEvalRunner(
            model_id="meta-llama/Llama-3.2-1B",
            batch_size="16",
            device="cuda:0",
            limit=100,
        )
        assert runner.model_id == "meta-llama/Llama-3.2-1B"
        assert runner.batch_size == "16"
        assert runner.device == "cuda:0"
        assert runner.limit == 100

    def test_default_tasks_matches_module_constant(self) -> None:
        runner = LMEvalRunner()
        assert runner.default_tasks == _DEFAULT_TASKS

    def test_default_tasks_contains_expected_benchmarks(self) -> None:
        runner = LMEvalRunner()
        tasks = runner.default_tasks
        assert "mmlu" in tasks
        assert "gsm8k" in tasks
        assert "hellaswag" in tasks
        assert "arc_easy" in tasks
        assert "arc_challenge" in tasks
        assert "truthfulqa_mc2" in tasks

    def test_available_property_reflects_lm_eval_presence(self) -> None:
        with patch(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            return_value=True,
        ):
            runner = LMEvalRunner()
            assert runner.available is True

        with patch(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            return_value=False,
        ):
            runner = LMEvalRunner()
            assert runner.available is False


class TestBuildCommand:
    def test_basic_command_structure(self) -> None:
        runner = LMEvalRunner(model_id="Qwen/Qwen2.5-0.5B")
        cmd = runner._build_command(["mmlu", "gsm8k"])
        assert cmd[0] == "lm_eval"
        assert "--model" in cmd
        assert "hf" in cmd
        assert "--model_args" in cmd
        assert "pretrained=Qwen/Qwen2.5-0.5B" in cmd
        assert "--batch_size" in cmd
        assert "--tasks" in cmd
        assert "mmlu,gsm8k" in cmd

    def test_command_includes_device_when_specified(self) -> None:
        runner = LMEvalRunner(device="cuda:0")
        cmd = runner._build_command(["mmlu"])
        assert "--device" in cmd
        assert "cuda:0" in cmd

    def test_command_excludes_device_when_none(self) -> None:
        runner = LMEvalRunner(device=None)
        cmd = runner._build_command(["mmlu"])
        assert "--device" not in cmd

    def test_command_includes_limit_when_specified(self) -> None:
        runner = LMEvalRunner(limit=50)
        cmd = runner._build_command(["mmlu"])
        assert "--limit" in cmd
        assert "50" in cmd

    def test_command_excludes_limit_when_none(self) -> None:
        runner = LMEvalRunner(limit=None)
        cmd = runner._build_command(["mmlu"])
        assert "--limit" not in cmd


class TestRunBenchmark:
    def test_import_missing_returns_empty(self) -> None:
        with patch(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            return_value=False,
        ):
            result = run_benchmark("foo", ["mmlu"])
            assert result == {}

    def test_import_error_returns_empty(self) -> None:
        with patch(
            "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
            return_value=True,
        ):
            result = run_benchmark("foo", ["mmlu"])
            assert result == {}

    def test_calls_simple_evaluate_with_correct_kwargs(self) -> None:
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate.return_value = {"results": {"mmlu": {"acc,none": 0.42}}}

        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict(sys.modules, {"lm_eval": mock_lm_eval}),
        ):
            result = run_benchmark("test-model", ["mmlu"])

        assert result == {"mmlu": 0.42}
        call_kwargs = mock_lm_eval.simple_evaluate.call_args.kwargs
        assert call_kwargs["model"] == "hf"
        assert call_kwargs["model_args"] == "pretrained=test-model"
        assert call_kwargs["batch_size"] == "auto"
        assert call_kwargs["tasks"] == ["mmlu"]
        assert call_kwargs["log_samples"] is False

    def test_passes_device_when_specified(self) -> None:
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate.return_value = {"results": {}}

        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict(sys.modules, {"lm_eval": mock_lm_eval}),
        ):
            run_benchmark("model", ["mmlu"], device="cuda:1")

        call_kwargs = mock_lm_eval.simple_evaluate.call_args.kwargs
        assert call_kwargs["device"] == "cuda:1"

    def test_passes_limit_when_specified(self) -> None:
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate.return_value = {"results": {}}

        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict(sys.modules, {"lm_eval": mock_lm_eval}),
        ):
            run_benchmark("model", ["mmlu"], limit=10)

        call_kwargs = mock_lm_eval.simple_evaluate.call_args.kwargs
        assert call_kwargs["limit"] == 10

    def test_omits_device_when_none(self) -> None:
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate.return_value = {"results": {}}

        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict(sys.modules, {"lm_eval": mock_lm_eval}),
        ):
            run_benchmark("model", ["mmlu"], device=None)

        call_kwargs = mock_lm_eval.simple_evaluate.call_args.kwargs
        assert "device" not in call_kwargs

    def test_omits_limit_when_none(self) -> None:
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate.return_value = {"results": {}}

        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict(sys.modules, {"lm_eval": mock_lm_eval}),
        ):
            run_benchmark("model", ["mmlu"], limit=None)

        call_kwargs = mock_lm_eval.simple_evaluate.call_args.kwargs
        assert "limit" not in call_kwargs

    def test_runner_run_benchmark_delegates(self) -> None:
        mock_lm_eval = MagicMock()
        mock_lm_eval.simple_evaluate.return_value = {"results": {"mmlu": {"acc,none": 0.55}}}

        with (
            patch(
                "general_ludd.small_models.lm_eval_runner._try_import_lm_eval",
                return_value=True,
            ),
            patch.dict(sys.modules, {"lm_eval": mock_lm_eval}),
        ):
            runner = LMEvalRunner(model_id="test-model")
            result = runner.run_benchmark(["mmlu"])

        assert result == {"mmlu": 0.55}


class TestToCapabilityEvidence:
    def test_single_result_below_threshold(self) -> None:
        results = {"mmlu": 0.25}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="test-model",
        )
        assert len(evidence_list) == 1
        ev = evidence_list[0]
        assert ev.model_profile_id == "test-model"
        assert ev.task_kind == "mmlu"
        assert ev.role == TaskRole.ENUMERATOR
        assert ev.collection == "general_ludd.agent"
        assert ev.suite_id == "lm-eval-runner"
        assert ev.suite_revision == _SUITE_REVISION
        assert ev.collection_ok is True
        assert ev.local_only is True
        assert ev.passed_cases == 0
        assert ev.total_cases == 1

    def test_single_result_above_threshold(self) -> None:
        results = {"mmlu": 0.45}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="test-model",
        )
        ev = evidence_list[0]
        assert ev.passed_cases == 1

    def test_multiple_results(self) -> None:
        results = {"mmlu": 0.25, "gsm8k": 0.40}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="test-model",
        )
        assert len(evidence_list) == 2
        task_kinds = {ev.task_kind for ev in evidence_list}
        assert task_kinds == {"mmlu", "gsm8k"}

    def test_custom_thresholds_override_defaults(self) -> None:
        results = {"mmlu": 0.50}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="test-model",
            thresholds={"mmlu": 0.80},
        )
        ev = evidence_list[0]
        assert ev.passed_cases == 0

        evidence_list2 = to_capability_evidence(
            results,
            model_profile_id="test-model",
            thresholds={"mmlu": 0.40},
        )
        assert evidence_list2[0].passed_cases == 1

    def test_fallback_to_default_threshold(self) -> None:
        results = {"unknown_task": 0.30}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="test-model",
        )
        ev = evidence_list[0]
        assert ev.passed_cases == 0

    def test_fallback_to_hardcoded_default_when_neither_custom_nor_task_default(self) -> None:
        results = {"unknown_benchmark": 0.50}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="test-model",
        )
        ev = evidence_list[0]
        assert ev.passed_cases == 1

    def test_empty_results_returns_empty_list(self) -> None:
        evidence_list = to_capability_evidence({}, model_profile_id="test-model")
        assert evidence_list == []

    def test_custom_model_identity_digest(self) -> None:
        custom_digest = hashlib.sha256(b"custom-identity").hexdigest()
        results = {"mmlu": 0.50}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="test-model",
            model_identity_digest=custom_digest,
        )
        assert evidence_list[0].model_identity_digest == custom_digest

    def test_auto_generated_model_identity_digest(self) -> None:
        results = {"mmlu": 0.50}
        evidence_list = to_capability_evidence(
            results,
            model_profile_id="my-model",
        )
        expected = hashlib.sha256(b"my-model:unnamed").hexdigest()
        assert evidence_list[0].model_identity_digest == expected

    def test_evidence_digest_is_stable(self) -> None:
        results = {"mmlu": 0.35}
        e1 = to_capability_evidence(results, model_profile_id="test-model")
        e2 = to_capability_evidence(results, model_profile_id="test-model")
        assert e1[0].evidence_digest == e2[0].evidence_digest

    def test_acceptance_digest_differs_for_different_scores(self) -> None:
        e_pass = to_capability_evidence({"mmlu": 0.80}, model_profile_id="t")
        e_fail = to_capability_evidence({"mmlu": 0.10}, model_profile_id="t")
        assert e_pass[0].acceptance_contract_digest != e_fail[0].acceptance_contract_digest

    def test_all_default_tasks_produce_valid_evidence(self) -> None:
        results = {task: 0.50 for task in _DEFAULT_TASKS}
        evidence_list = to_capability_evidence(results, model_profile_id="test-model")
        assert len(evidence_list) == len(_DEFAULT_TASKS)
        for ev in evidence_list:
            assert ev.model_profile_id == "test-model"
            assert ev.role == TaskRole.ENUMERATOR
            assert ev.total_cases == 1
            assert ev.collection_ok is True
            assert ev.local_only is True

    def test_score_exactly_at_threshold_passes(self) -> None:
        results = {"mmlu": 0.30}
        evidence_list = to_capability_evidence(results, model_profile_id="test-model")
        assert evidence_list[0].passed_cases == 1

    def test_score_just_below_threshold_fails(self) -> None:
        results = {"mmlu": 0.299}
        evidence_list = to_capability_evidence(results, model_profile_id="test-model")
        assert evidence_list[0].passed_cases == 0


class TestDefaultThresholds:
    def test_default_thresholds_have_all_tasks(self) -> None:
        for task in _DEFAULT_TASKS:
            assert task in _DEFAULT_THRESHOLDS

    def test_default_thresholds_are_between_zero_and_one(self) -> None:
        for _task, threshold in _DEFAULT_THRESHOLDS.items():
            assert 0.0 < threshold < 1.0

    def test_default_threshold_fallback_is_reasonable(self) -> None:
        assert 0.0 < _DEFAULT_THRESHOLD < 1.0


class TestSuiteRevision:
    def test_suite_revision_is_non_empty_string(self) -> None:
        assert isinstance(_SUITE_REVISION, str)
        assert len(_SUITE_REVISION) > 0
        assert _SUITE_REVISION.startswith("v")
