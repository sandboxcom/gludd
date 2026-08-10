"""Unit tests for src/general_ludd/small_models/eval_harness.py."""

from __future__ import annotations

import hashlib

from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.eval_harness import (
    STANDARD_TASKS,
    EleutherAIHarness,
    EvalTask,
    HarnessConfig,
    ParsedResult,
    _clean_metrics,
    _extract_primary_score,
    parse_lm_eval_output,
    result_to_evidence,
    score_passing,
)


class TestEvalTask:
    def test_enum_values(self) -> None:
        assert EvalTask.HELLASWAG == "hellaswag"
        assert EvalTask.MMLU == "mmlu"
        assert EvalTask.ARC_EASY == "arc_easy"
        assert EvalTask.ARC_CHALLENGE == "arc_challenge"
        assert EvalTask.TRUTHFULQA == "truthfulqa_mc2"

    def test_standard_tasks_count(self) -> None:
        assert len(STANDARD_TASKS) == 5

    def test_standard_tasks_members(self) -> None:
        assert EvalTask.HELLASWAG in STANDARD_TASKS
        assert EvalTask.MMLU in STANDARD_TASKS
        assert EvalTask.ARC_EASY in STANDARD_TASKS
        assert EvalTask.ARC_CHALLENGE in STANDARD_TASKS
        assert EvalTask.TRUTHFULQA in STANDARD_TASKS


class TestHarnessConfig:
    def test_default_values(self) -> None:
        config = HarnessConfig()
        assert config.model == "hf"
        assert config.model_args == ""
        assert config.batch_size == "auto"
        assert config.device is None
        assert config.limit is None

    def test_custom_values(self) -> None:
        config = HarnessConfig(
            model="test-model",
            model_args="pretrained=test",
            batch_size="4",
            device="cuda",
            limit=100,
        )
        assert config.model == "test-model"
        assert config.model_args == "pretrained=test"
        assert config.batch_size == "4"
        assert config.device == "cuda"
        assert config.limit == 100

    def test_to_cli_args_minimal(self) -> None:
        config = HarnessConfig()
        args = config.to_cli_args()
        assert "--model" in args
        assert "hf" in args
        assert "--model_args" in args
        assert "--batch_size" in args
        assert "auto" in args
        assert "--device" not in args
        assert "--limit" not in args

    def test_to_cli_args_with_device(self) -> None:
        config = HarnessConfig(device="cuda")
        args = config.to_cli_args()
        assert "--device" in args
        assert "cuda" in args
        idx_device = args.index("--device")
        assert args[idx_device + 1] == "cuda"

    def test_to_cli_args_with_limit(self) -> None:
        config = HarnessConfig(limit=50)
        args = config.to_cli_args()
        assert "--limit" in args
        assert "50" in args
        idx_limit = args.index("--limit")
        assert args[idx_limit + 1] == "50"

    def test_to_cli_args_with_device_and_limit(self) -> None:
        config = HarnessConfig(device="cuda", limit=200)
        args = config.to_cli_args()
        assert "--device" in args
        assert "cuda" in args
        assert "--limit" in args
        assert "200" in args


class TestHarness:
    def test_default_config(self) -> None:
        harness = EleutherAIHarness()
        assert harness.config.model == "hf"

    def test_custom_config(self) -> None:
        config = HarnessConfig(model="my-model")
        harness = EleutherAIHarness(config)
        assert harness.config.model == "my-model"

    def test_build_command(self) -> None:
        harness = EleutherAIHarness(HarnessConfig(model="test", batch_size="8"))
        cmd = harness._build_command([EvalTask.MMLU, EvalTask.ARC_EASY])
        assert cmd[0] == "lm_eval"
        assert "--tasks" in cmd
        tasks_idx = cmd.index("--tasks")
        task_str = cmd[tasks_idx + 1]
        assert "mmlu" in task_str
        assert "arc_easy" in task_str
        assert "test" in cmd

    def test_build_command_single_task(self) -> None:
        harness = EleutherAIHarness()
        cmd = harness._build_command([EvalTask.HELLASWAG])
        tasks_idx = cmd.index("--tasks")
        assert cmd[tasks_idx + 1] == "hellaswag"


class TestParseLmEvalOutput:
    def test_empty_output(self) -> None:
        result = parse_lm_eval_output({})
        assert result == []

    def test_missing_results_key(self) -> None:
        result = parse_lm_eval_output({"other": "data"})
        assert result == []

    def test_results_not_dict(self) -> None:
        result = parse_lm_eval_output({"results": "not_a_dict"})
        assert result == []

    def test_valid_single_task(self) -> None:
        output = {"results": {"hellaswag": {"acc,none": 0.45, "acc_norm,none": 0.47, "f1,none": 0.44}}}
        results = parse_lm_eval_output(output)
        assert len(results) == 1
        assert results[0].task_name == "hellaswag"
        assert results[0].score == 0.45

    def test_valid_multiple_tasks(self) -> None:
        output = {
            "results": {
                "hellaswag": {"acc,none": 0.45},
                "mmlu": {"acc,none": 0.38},
            }
        }
        results = parse_lm_eval_output(output)
        assert len(results) == 2

    def test_task_with_non_dict_metrics_skipped(self) -> None:
        output = {
            "results": {
                "hellaswag": {"acc,none": 0.45},
                "bad_task": "not_a_dict",
            }
        }
        results = parse_lm_eval_output(output)
        assert len(results) == 1
        assert results[0].task_name == "hellaswag"

    def test_task_with_no_numeric_score_skipped(self) -> None:
        output = {
            "results": {
                "hellaswag": {"acc,none": 0.45},
                "bad_task": {"description": "no numeric scores here"},
            }
        }
        results = parse_lm_eval_output(output)
        assert len(results) == 1

    def test_all_tasks_skipped(self) -> None:
        output = {
            "results": {
                "task_a": "not_a_dict",
                "task_b": {"desc": "no_numeric"},
            }
        }
        results = parse_lm_eval_output(output)
        assert results == []

    def test_raw_metrics_populated(self) -> None:
        output = {"results": {"hellaswag": {"acc,none": 0.45, "acc_norm,none": 0.47, "non_numeric": "skip"}}}
        results = parse_lm_eval_output(output)
        assert len(results) == 1
        r = results[0]
        assert r.raw_metrics == {"acc": 0.45, "acc_norm": 0.47}


class TestExtractPrimaryScore:
    def test_acc_none_priority(self) -> None:
        score = _extract_primary_score({"acc,none": 0.42, "acc_norm,none": 0.44, "f1,none": 0.40})
        assert score == 0.42

    def test_acc_norm_none_fallback(self) -> None:
        score = _extract_primary_score({"acc_norm,none": 0.44, "f1,none": 0.40})
        assert score == 0.44

    def test_f1_none_fallback(self) -> None:
        score = _extract_primary_score({"f1,none": 0.40, "other": 0.99})
        assert score == 0.40

    def test_int_value_converted_to_float(self) -> None:
        score = _extract_primary_score({"acc,none": 1})
        assert score == 1.0
        assert isinstance(score, float)

    def test_any_numeric_fallback(self) -> None:
        score = _extract_primary_score({"custom_metric": 0.88})
        assert score == 0.88

    def test_no_numeric_returns_none(self) -> None:
        score = _extract_primary_score({"description": "hello", "list_val": [1, 2, 3]})
        assert score is None

    def test_empty_dict_returns_none(self) -> None:
        score = _extract_primary_score({})
        assert score is None

    def test_numeric_value_first_found_wins(self) -> None:
        score = _extract_primary_score({"other1": 0.33, "other2": 0.88})
        assert score == 0.33


class TestCleanMetrics:
    def test_numeric_only(self) -> None:
        cleaned = _clean_metrics({"acc,none": 0.45, "acc_norm,none": 0.47, "extra": "string"})
        assert cleaned == {"acc": 0.45, "acc_norm": 0.47}

    def test_all_non_numeric_filtered(self) -> None:
        cleaned = _clean_metrics({"desc": "hello", "list_val": [1, 2]})
        assert cleaned == {}

    def test_suffixes_stripped(self) -> None:
        cleaned = _clean_metrics({"bleu,none": 0.55, "exact_match,strict": 0.33})
        assert cleaned == {"bleu": 0.55, "exact_match": 0.33}

    def test_no_comma_keys_unchanged(self) -> None:
        cleaned = _clean_metrics({"accuracy": 0.99, "f1": 0.88})
        assert cleaned == {"accuracy": 0.99, "f1": 0.88}

    def test_int_converted_to_float(self) -> None:
        cleaned = _clean_metrics({"acc,none": 1})
        assert cleaned == {"acc": 1.0}
        assert isinstance(cleaned["acc"], float)


class TestScorePassing:
    def test_scores_at_or_above_default_threshold_pass(self) -> None:
        assert score_passing("hellaswag", 0.35) is True
        assert score_passing("hellaswag", 0.50) is True

    def test_scores_below_default_threshold_fail(self) -> None:
        assert score_passing("hellaswag", 0.34) is False
        assert score_passing("hellaswag", 0.0) is False

    def test_mmlu_uses_default_0_30(self) -> None:
        assert score_passing("mmlu", 0.30) is True
        assert score_passing("mmlu", 0.29) is False

    def test_arc_challenge_uses_default_0_30(self) -> None:
        assert score_passing("arc_challenge", 0.30) is True
        assert score_passing("arc_challenge", 0.29) is False

    def test_unknown_task_uses_fallback_0_35(self) -> None:
        assert score_passing("unknown_target", 0.35) is True
        assert score_passing("unknown_target", 0.34) is False

    def test_explicit_threshold_overrides_default(self) -> None:
        assert score_passing("hellaswag", 0.30, threshold=0.25) is True
        assert score_passing("hellaswag", 0.24, threshold=0.25) is False

    def test_explicit_threshold_on_unknown_task(self) -> None:
        assert score_passing("novel_benchmark", 0.70, threshold=0.80) is False
        assert score_passing("novel_benchmark", 0.80, threshold=0.80) is True


class TestResultToEvidence:
    def _make_result(self, task_name: str = "mmlu", score: float = 0.45) -> ParsedResult:
        return ParsedResult(task_name=task_name, score=score, raw_metrics={"acc,none": score})

    def test_returns_evidence_with_correct_fields(self) -> None:
        result = self._make_result()
        evidence = result_to_evidence(
            result=result,
            model_profile_id="test-profile",
            model_identity_digest=hashlib.sha256(b"model-v1").hexdigest(),
            task_kind="test_kind",
            role=TaskRole.REVIEWER,
            collection="general_ludd.agent",
            suite_id="lm-eval-harness",
            suite_revision="v0.4.5",
        )

        assert isinstance(evidence, CapabilityEvidence)
        assert evidence.model_profile_id == "test-profile"
        assert evidence.task_kind == "test_kind"
        assert evidence.role == TaskRole.REVIEWER
        assert evidence.collection == "general_ludd.agent"
        assert evidence.suite_id == "lm-eval-harness"
        assert evidence.suite_revision == "v0.4.5"
        assert evidence.collection_ok is True
        assert evidence.local_only is True
        assert len(evidence.evidence_digest) == 64

    def test_passing_score_yields_passed_cases_1(self) -> None:
        result = self._make_result(task_name="mmlu", score=0.80)
        evidence = result_to_evidence(
            result=result,
            model_profile_id="p",
            model_identity_digest=hashlib.sha256(b"x").hexdigest(),
            task_kind="tk",
            role=TaskRole.CODER,
            collection="general_ludd.agent",
            suite_id="s",
            suite_revision="r",
        )
        assert evidence.passed_cases == 1
        assert evidence.total_cases == 1

    def test_failing_score_yields_passed_cases_0(self) -> None:
        result = self._make_result(task_name="mmlu", score=0.0)
        evidence = result_to_evidence(
            result=result,
            model_profile_id="p",
            model_identity_digest=hashlib.sha256(b"x").hexdigest(),
            task_kind="tk",
            role=TaskRole.CODER,
            collection="general_ludd.agent",
            suite_id="s",
            suite_revision="r",
        )
        assert evidence.passed_cases == 0
        assert evidence.total_cases == 1

    def test_acceptance_contract_digest_is_stable(self) -> None:
        result = self._make_result(task_name="mmlu", score=0.45)
        e1 = result_to_evidence(
            result=result,
            model_profile_id="p",
            model_identity_digest=hashlib.sha256(b"x").hexdigest(),
            task_kind="tk",
            role=TaskRole.EDITOR,
            collection="general_ludd.agent",
            suite_id="s",
            suite_revision="r",
        )
        e2 = result_to_evidence(
            result=result,
            model_profile_id="p",
            model_identity_digest=hashlib.sha256(b"x").hexdigest(),
            task_kind="tk",
            role=TaskRole.EDITOR,
            collection="general_ludd.agent",
            suite_id="s",
            suite_revision="r",
        )
        assert e1.acceptance_contract_digest == e2.acceptance_contract_digest

    def test_acceptance_contract_digest_differs_for_different_scores(self) -> None:
        r1 = self._make_result(task_name="mmlu", score=0.45)
        r2 = self._make_result(task_name="mmlu", score=0.80)
        e1 = result_to_evidence(
            result=r1,
            model_profile_id="p",
            model_identity_digest=hashlib.sha256(b"x").hexdigest(),
            task_kind="tk",
            role=TaskRole.EDITOR,
            collection="general_ludd.agent",
            suite_id="s",
            suite_revision="r",
        )
        e2 = result_to_evidence(
            result=r2,
            model_profile_id="p",
            model_identity_digest=hashlib.sha256(b"x").hexdigest(),
            task_kind="tk",
            role=TaskRole.EDITOR,
            collection="general_ludd.agent",
            suite_id="s",
            suite_revision="r",
        )
        assert e1.acceptance_contract_digest != e2.acceptance_contract_digest


class TestParseAndEvidence:
    def test_no_results_returns_empty_list(self) -> None:
        harness = EleutherAIHarness()
        evidence = harness.parse_and_evidence(
            output={},
            model_profile_id="test-profile",
            model_identity_digest=hashlib.sha256(b"m").hexdigest(),
            task_kind="tk",
            role=TaskRole.CODER,
        )
        assert evidence == []

    def test_valid_output_returns_evidence_list(self) -> None:
        harness = EleutherAIHarness()
        output = {
            "results": {
                "hellaswag": {"acc,none": 0.55, "acc_norm,none": 0.58},
                "mmlu": {"acc,none": 0.42},
            }
        }
        evidence = harness.parse_and_evidence(
            output=output,
            model_profile_id="test-profile",
            model_identity_digest=hashlib.sha256(b"m").hexdigest(),
            task_kind="task_kind",
            role=TaskRole.CODER,
        )
        assert len(evidence) == 2
        assert all(isinstance(e, CapabilityEvidence) for e in evidence)
        assert evidence[0].model_profile_id == "test-profile"
        assert evidence[0].task_kind == "task_kind"
        assert evidence[0].role == TaskRole.CODER

    def test_default_optional_args(self) -> None:
        harness = EleutherAIHarness()
        output = {
            "results": {
                "mmlu": {"acc,none": 0.80},
            }
        }
        evidence = harness.parse_and_evidence(
            output=output,
            model_profile_id="test-profile",
            model_identity_digest=hashlib.sha256(b"m").hexdigest(),
            task_kind="task_kind",
            role=TaskRole.CODER,
        )
        assert len(evidence) == 1
        assert evidence[0].suite_id == "lm-eval-harness"
        assert evidence[0].suite_revision == "v0.4.5"
        assert evidence[0].collection == "general_ludd.agent"

    def test_partial_results_with_skipped_tasks(self) -> None:
        harness = EleutherAIHarness()
        output = {
            "results": {
                "hellaswag": {"acc,none": 0.55},
                "bad_task": "not_a_dict",
                "no_score": {"description": "nothing"},
                "mmlu": {"acc,none": 0.42},
            }
        }
        evidence = harness.parse_and_evidence(
            output=output,
            model_profile_id="test-profile",
            model_identity_digest=hashlib.sha256(b"m").hexdigest(),
            task_kind="task_kind",
            role=TaskRole.CODER,
        )
        assert len(evidence) == 2


class TestParsedResult:
    def test_frozen_creation(self) -> None:
        result = ParsedResult(task_name="test", score=0.5, raw_metrics={"acc": 0.5})
        assert result.task_name == "test"
        assert result.score == 0.5
        assert result.raw_metrics == {"acc": 0.5}

    def test_equality(self) -> None:
        r1 = ParsedResult(task_name="a", score=0.5, raw_metrics={"x": 0.5})
        r2 = ParsedResult(task_name="a", score=0.5, raw_metrics={"x": 0.5})
        assert r1 == r2

    def test_different_task_name_not_equal(self) -> None:
        r1 = ParsedResult(task_name="a", score=0.5, raw_metrics={})
        r2 = ParsedResult(task_name="b", score=0.5, raw_metrics={})
        assert r1 != r2


class TestEdgeCases:
    def test_lm_eval_output_v0_4_5_format(self) -> None:
        output = {
            "results": {
                "mmlu": {
                    "alias": "mmlu",
                    "acc,none": 0.5234,
                    "acc_stderr,none": 0.0142,
                    "acc_norm,none": 0.5512,
                    "acc_norm_stderr,none": 0.0138,
                }
            },
            "versions": {"mmlu": 0},
            "config": {"model": "hf", "batch_size": "auto"},
        }
        results = parse_lm_eval_output(output)
        assert len(results) == 1
        assert results[0].task_name == "mmlu"
        assert results[0].score == 0.5234
        assert results[0].raw_metrics["acc"] == 0.5234
        assert results[0].raw_metrics["acc_norm"] == 0.5512

    def test_truthfulqa_mc2_format(self) -> None:
        output = {"results": {"truthfulqa_mc2": {"acc,none": 0.3881}}}
        results = parse_lm_eval_output(output)
        assert len(results) == 1
        assert results[0].task_name == "truthfulqa_mc2"
        assert results[0].score == 0.3881
        assert score_passing("truthfulqa_mc2", results[0].score) is True

    def test_hellaswag_below_threshold(self) -> None:
        output = {"results": {"hellaswag": {"acc,none": 0.33}}}
        results = parse_lm_eval_output(output)
        assert score_passing(results[0].task_name, results[0].score) is False

    def test_zero_score_with_valid_numeric(self) -> None:
        output = {"results": {"hellaswag": {"acc,none": 0.0}}}
        results = parse_lm_eval_output(output)
        assert len(results) == 1
        assert results[0].score == 0.0
        assert results[0].raw_metrics == {"acc": 0.0}
