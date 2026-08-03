"""Deterministic tests for EleutherAI LM eval harness integration."""

from __future__ import annotations

import hashlib

import pytest

from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.eval_harness import (
    STANDARD_TASKS,
    EleutherAIHarness,
    EvalTask,
    HarnessConfig,
    ParsedResult,
    parse_lm_eval_output,
    result_to_evidence,
    score_passing,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _raw_output(*, task: str = "hellaswag", score: float = 0.72) -> dict:
    return {
        "results": {
            task: {
                "acc,none": score,
                "acc_norm,none": score + 0.02,
            }
        }
    }


def test_parse_lm_eval_output_extracts_scores() -> None:
    results = parse_lm_eval_output(_raw_output())
    assert len(results) == 1
    assert results[0].task_name == "hellaswag"
    assert results[0].score == pytest.approx(0.72)
    assert results[0].raw_metrics == {"acc": 0.72, "acc_norm": 0.74}


def test_parse_lm_eval_output_handles_empty_results() -> None:
    assert parse_lm_eval_output({"results": {}}) == []


def test_parse_lm_eval_output_skips_invalid_scores() -> None:
    output = {
        "results": {
            "hellaswag": {"acc,none": "pending"},
            "mmlu": {"acc,none": 0.55},
        }
    }
    results = parse_lm_eval_output(output)
    assert len(results) == 1
    assert results[0].task_name == "mmlu"


def test_parse_lm_eval_output_aggregates_metrics() -> None:
    output = {
        "results": {
            "arc_easy": {"acc,none": 0.80, "acc_norm,none": 0.82, "f1,none": 0.79},
        }
    }
    results = parse_lm_eval_output(output)
    assert len(results) == 1
    assert results[0].raw_metrics == {"acc": 0.80, "acc_norm": 0.82, "f1": 0.79}


def test_score_passing_uses_default_threshold() -> None:
    assert score_passing("hellaswag", 0.55) is True
    assert score_passing("hellaswag", 0.40) is True
    assert score_passing("hellaswag", 0.20) is False


def test_score_passing_unknown_task_defaults_sane() -> None:
    assert score_passing("unknown_task", 0.55) is True
    assert score_passing("unknown_task", 0.30) is False


def test_score_passing_custom_threshold() -> None:
    assert score_passing("hellaswag", 0.60, threshold=0.65) is False
    assert score_passing("hellaswag", 0.70, threshold=0.65) is True


def test_result_to_evidence_produces_capability_evidence() -> None:
    result = ParsedResult(
        task_name="hellaswag",
        score=0.72,
        raw_metrics={"acc": 0.72, "acc_norm": 0.74},
    )
    evidence = result_to_evidence(
        result=result,
        model_profile_id="test-model",
        model_identity_digest=_digest("test-weights"),
        task_kind="bounded_enumeration",
        role=TaskRole.ENUMERATOR,
        collection="general_ludd.agent",
        suite_id="lm-eval-harness",
        suite_revision="v0.4.5",
    )

    assert isinstance(evidence, CapabilityEvidence)
    assert evidence.model_profile_id == "test-model"
    assert evidence.task_kind == "bounded_enumeration"
    assert evidence.role == TaskRole.ENUMERATOR
    assert evidence.collection == "general_ludd.agent"
    assert evidence.suite_id == "lm-eval-harness"
    assert evidence.suite_revision == "v0.4.5"
    assert evidence.passed_cases == 1
    assert evidence.total_cases == 1
    assert evidence.collection_ok is True
    assert evidence.local_only is True
    assert len(evidence.evidence_digest) == 64


def test_result_to_evidence_failing_score_produces_zero_passed() -> None:
    result = ParsedResult(task_name="hellaswag", score=0.15, raw_metrics={})
    evidence = result_to_evidence(
        result=result,
        model_profile_id="test-model",
        model_identity_digest=_digest("test-weights"),
        task_kind="bounded_enumeration",
        role=TaskRole.ENUMERATOR,
        collection="general_ludd.agent",
        suite_id="lm-eval-harness",
        suite_revision="v0.4.5",
    )
    assert evidence.passed_cases == 0
    assert evidence.total_cases == 1


def test_harness_config_defaults() -> None:
    config = HarnessConfig()
    assert config.model == "hf"
    assert config.model_args == ""
    assert config.batch_size == "auto"
    assert config.device is None
    assert config.limit is None


def test_harness_config_with_overrides() -> None:
    config = HarnessConfig(
        model="hf-causal-experimental",
        model_args="pretrained=gpt2,trust_remote_code=True",
        batch_size="1",
        device="cuda:0",
        limit=20,
    )
    assert config.model == "hf-causal-experimental"
    assert config.model_args == "pretrained=gpt2,trust_remote_code=True"
    assert config.batch_size == "1"
    assert config.device == "cuda:0"
    assert config.limit == 20


def test_harness_config_to_cli_args() -> None:
    config = HarnessConfig(
        model="hf-causal-experimental",
        model_args="pretrained=gpt2",
        batch_size="4",
        device="cpu",
        limit=50,
    )
    args = config.to_cli_args()
    assert "--model" in args
    assert "hf-causal-experimental" in args
    assert "--model_args" in args
    assert "pretrained=gpt2" in args
    assert "--batch_size" in args
    assert "4" in args
    assert "--device" in args
    assert "cpu" in args
    assert "--limit" in args
    assert "50" in args


def test_harness_config_to_cli_args_omits_none() -> None:
    config = HarnessConfig(model="hf")
    args = config.to_cli_args()
    assert "--device" not in args
    assert "--limit" not in args


def test_harness_build_command_includes_tasks() -> None:
    config = HarnessConfig(model="hf")
    harness = EleutherAIHarness(config)
    cmd = harness._build_command(tasks=[EvalTask.HELLASWAG, EvalTask.MMLU])
    assert cmd[0] == "lm_eval"
    assert "--model" in cmd
    assert "--tasks" in cmd
    task_idx = cmd.index("--tasks")
    assert "hellaswag" in cmd[task_idx + 1]
    assert "mmlu" in cmd[task_idx + 1]


def test_harness_parse_and_evidence_returns_capability_evidence_list() -> None:
    config = HarnessConfig(model="hf")
    harness = EleutherAIHarness(config)
    output = {
        "results": {
            "hellaswag": {"acc,none": 0.72, "acc_norm,none": 0.74},
            "mmlu": {"acc,none": 0.55},
        }
    }
    evidence_list = harness.parse_and_evidence(
        output=output,
        model_profile_id="test-model",
        model_identity_digest=_digest("test-weights"),
        task_kind="bounded_enumeration",
        role=TaskRole.ENUMERATOR,
    )
    assert len(evidence_list) == 2
    for ev in evidence_list:
        assert isinstance(ev, CapabilityEvidence)
        assert ev.model_profile_id == "test-model"


def test_harness_parse_and_evidence_empty_results() -> None:
    config = HarnessConfig(model="hf")
    harness = EleutherAIHarness(config)
    evidence_list = harness.parse_and_evidence(
        output={"results": {}},
        model_profile_id="test-model",
        model_identity_digest=_digest("test-weights"),
        task_kind="bounded_enumeration",
        role=TaskRole.ENUMERATOR,
    )
    assert evidence_list == []


def test_standard_tasks_includes_expected() -> None:
    assert EvalTask.HELLASWAG in STANDARD_TASKS
    assert EvalTask.MMLU in STANDARD_TASKS
    assert EvalTask.ARC_EASY in STANDARD_TASKS
    assert EvalTask.ARC_CHALLENGE in STANDARD_TASKS
    assert EvalTask.TRUTHFULQA in STANDARD_TASKS
    assert len(STANDARD_TASKS) == 5


def test_eval_task_values_are_valid_lm_eval_task_names() -> None:
    assert EvalTask.HELLASWAG == "hellaswag"
    assert EvalTask.MMLU == "mmlu"
    assert EvalTask.ARC_EASY == "arc_easy"
    assert EvalTask.ARC_CHALLENGE == "arc_challenge"
    assert EvalTask.TRUTHFULQA == "truthfulqa_mc2"


def test_harness_config_device_is_optional() -> None:
    config = HarnessConfig()
    args = config.to_cli_args()
    assert "--device" not in args


def test_parsed_result_repr() -> None:
    result = ParsedResult(task_name="hellaswag", score=0.72, raw_metrics={"acc": 0.72})
    r = repr(result)
    assert "hellaswag" in r
    assert "0.72" in r


def test_result_to_evidence_different_tasks_produce_different_digests() -> None:
    r1 = ParsedResult(task_name="hellaswag", score=0.80, raw_metrics={"acc": 0.80})
    r2 = ParsedResult(task_name="mmlu", score=0.80, raw_metrics={"acc": 0.80})
    identity = _digest("weights")

    e1 = result_to_evidence(r1, "m", identity, "k", TaskRole.ENUMERATOR, "general_ludd.agent", "s", "r")
    e2 = result_to_evidence(r2, "m", identity, "k", TaskRole.ENUMERATOR, "general_ludd.agent", "s", "r")
    assert e1.evidence_digest != e2.evidence_digest
