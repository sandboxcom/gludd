"""Tests for deterministic self-improvement model candidate planning."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

import general_ludd.self_improve.model_candidate_planner as planner_module
from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.local_model import get_model
from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.model_candidate_planner import (
    PlannedModelCandidate,
    plan_model_candidates,
)
from general_ludd.self_improve.task_diversity import infer_task_type
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def _hardware(vram_gb: float = 24.0) -> HardwareInventory:
    return HardwareInventory(
        gpus=[GpuInfo("test GPU", vram_gb, backend="metal")],
        total_ram_gb=32.0,
        disk_free_gb=100.0,
        cpu_cores=8,
    )


def _store(tmp_path: object) -> CapabilityEvidenceStore:
    return CapabilityEvidenceStore(str(tmp_path) + "/evidence.json")


def _revision(_repo_id: str) -> str:
    return "A" * 40


def test_code_task_shape_is_public_planner_contract() -> None:
    assert "CodeTaskShape" in planner_module.__all__
    assert "CODE_TASK_CAPABILITY_POLICY_ID" in planner_module.__all__


def _register_failure_evidence(
    store: CapabilityEvidenceStore,
    model_id: str,
    *,
    passed: int,
    total: int = 10,
    copies: int = 1,
) -> None:
    for index in range(copies):
        store.register_evidence(
            {
                "model_profile_id": model_id,
                "task_type": TaskType.BUG_FIX.value,
                "task_kind": "failure_classification",
                "role": "reviewer",
                "suite_id": f"suite-{index}",
                "suite_revision": "1",
                "passed_cases": passed,
                "total_cases": total,
                "collection_ok": True,
                "local_only": True,
            }
        )


def test_no_evidence_fallback_uses_quality_ladder_and_is_bounded(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    calls: list[str] = []

    def resolver(repo_id: str) -> str:
        calls.append(repo_id)
        return _revision(repo_id)

    first = plan_model_candidates(
        "implement a focused Python change",
        1024,
        (),
        _hardware(),
        store,
        resolver,
        max_candidates=3,
    )
    second = plan_model_candidates(
        "implement a focused Python change",
        1024,
        (),
        _hardware(),
        store,
        _revision,
        max_candidates=3,
    )

    assert [item.config.name for item in first] == [
        "qwen2.5-coder-1.5b",
        "qwen2.5-coder-3b",
        "codellama-7b",
    ]
    assert first == second
    assert [item.escalation_level for item in first] == [0, 1, 2]
    assert [item.config.size_mb for item in first] == sorted(
        item.config.size_mb for item in first
    )
    assert all(item.config.category == "coding" for item in first)
    assert all(item.resolved_revision == "a" * 40 for item in first)
    assert calls == [item.config.repo for item in first]


def test_capability_evidence_selects_anchor_then_escalates_by_size(tmp_path: object) -> None:
    store = _store(tmp_path)
    _register_failure_evidence(
        store,
        "qwen2.5-coder-1.5b",
        passed=10,
        copies=3,
    )
    _register_failure_evidence(
        store,
        "qwen2.5-coder-0.5b",
        passed=0,
    )

    task_text = "classify this failure and root cause"
    assert infer_task_type(task_text) is TaskType.BUG_FIX

    candidates = plan_model_candidates(
        task_text,
        1024,
        (),
        _hardware(),
        store,
        _revision,
        max_candidates=3,
    )

    assert candidates[0].config.name == "qwen2.5-coder-1.5b"
    assert candidates[0].evidence_score > 0.0
    assert [item.config.size_mb for item in candidates] == sorted(
        item.config.size_mb for item in candidates
    )


@pytest.mark.parametrize(
    "task_shape",
    [
        pytest.param((2, 0, 200), id="multiple-files"),
        pytest.param((1, 1, 200), id="test-file"),
        pytest.param((1, 0, 8_193), id="large-source"),
    ],
)
def test_complex_code_shape_overrides_tiny_model_evidence_with_capability_floor(
    tmp_path: object,
    task_shape: tuple[int, int, int],
) -> None:
    """Do not repeat the live under-capacity candidate class for complex edits."""
    store = _store(tmp_path)
    _register_failure_evidence(
        store,
        "qwen2.5-coder-0.5b",
        passed=10,
        copies=3,
    )
    shape = planner_module.CodeTaskShape(
        changed_files=task_shape[0],
        changed_test_files=task_shape[1],
        source_bytes=task_shape[2],
    )
    calls: list[str] = []

    def resolver(repo_id: str) -> str:
        calls.append(repo_id)
        return _revision(repo_id)

    candidates = plan_model_candidates(
        "classify this failure and root cause",
        1024,
        (),
        _hardware(),
        store,
        resolver,
        task_shape=shape,
        max_candidates=3,
    )

    assert [item.config.name for item in candidates] == [
        "qwen2.5-coder-1.5b",
        "qwen2.5-coder-3b",
        "codellama-7b",
    ]
    assert all(item.config.size_mb >= 900 for item in candidates)
    assert calls == [item.config.repo for item in candidates]


def test_tiny_single_file_non_test_task_retains_evidence_backed_small_model(
    tmp_path: object,
) -> None:
    """Keep the cheap proven candidate for a genuinely bounded task shape."""
    store = _store(tmp_path)
    _register_failure_evidence(
        store,
        "qwen2.5-coder-0.5b",
        passed=10,
        copies=3,
    )

    candidates = plan_model_candidates(
        "classify this failure and root cause",
        512,
        (),
        _hardware(),
        store,
        _revision,
        task_shape=planner_module.CodeTaskShape(1, 0, 8_192),
        max_candidates=1,
    )

    assert candidates[0].config.name == "qwen2.5-coder-0.5b"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ((True, 0, 0), "changed_files"),
        ((0, 0, 0), "changed_files"),
        ((33, 0, 0), "changed_files"),
        ((1, True, 0), "changed_test_files"),
        ((1, -1, 0), "changed_test_files"),
        ((1, 2, 0), "changed_test_files"),
        ((1, 0, True), "source_bytes"),
        ((1, 0, -1), "source_bytes"),
        ((1, 0, 67_108_865), "source_bytes"),
    ],
)
def test_code_task_shape_fails_closed_on_unbounded_or_ambiguous_values(
    values: tuple[int, int, int],
    message: str,
) -> None:
    """Task-shape policy inputs are typed and bounded before model lookup."""
    with pytest.raises(ValueError, match=message):
        planner_module.CodeTaskShape(*values)


def test_planner_rejects_non_task_shape_before_revision_resolution(
    tmp_path: object,
) -> None:
    """Never reinterpret an arbitrary object as trusted planner policy input."""
    with pytest.raises(ValueError, match="task_shape"):
        plan_model_candidates(
            "implement a change",
            100,
            (),
            _hardware(),
            _store(tmp_path),
            lambda _repo: pytest.fail("invalid task shape must not resolve a model"),
            task_shape=cast(Any, {"changed_files": 2}),
        )


def test_prior_failure_alias_is_excluded_and_sets_escalation_floor(tmp_path: object) -> None:
    store = _store(tmp_path)

    candidates = plan_model_candidates(
        "implement a Python fix",
        1024,
        ("qwen-coder-1.5b",),
        _hardware(),
        store,
        _revision,
        max_candidates=2,
    )

    assert [item.config.name for item in candidates] == [
        "qwen2.5-coder-3b",
        "codellama-7b",
    ]
    assert all(item.config.size_mb > 936 for item in candidates)


def test_failed_evidence_anchor_remains_excluded(tmp_path: object) -> None:
    store = _store(tmp_path)
    _register_failure_evidence(
        store,
        "qwen2.5-coder-1.5b",
        passed=10,
        copies=3,
    )

    candidates = plan_model_candidates(
        "classify this failure",
        1024,
        ("qwen2.5-coder-1.5b",),
        _hardware(),
        store,
        _revision,
        max_candidates=2,
    )

    assert all(item.config.name != "qwen2.5-coder-1.5b" for item in candidates)
    assert all(item.config.size_mb > 936 for item in candidates)


@pytest.mark.parametrize(
    ("output_tokens", "hardware"),
    [
        (40_000, _hardware()),
        (1024, HardwareInventory(total_ram_gb=32.0, disk_free_gb=100.0, cpu_cores=8)),
    ],
)
def test_context_and_hardware_filter_before_revision_calls(
    tmp_path: object,
    output_tokens: int,
    hardware: HardwareInventory,
) -> None:
    store = _store(tmp_path)
    calls: list[str] = []

    def resolver(repo_id: str) -> str:
        calls.append(repo_id)
        return _revision(repo_id)

    candidates = plan_model_candidates(
        "implement a change",
        output_tokens,
        (),
        hardware,
        store,
        resolver,
    )

    assert candidates == ()
    assert calls == []


def test_full_rendered_prompt_context_filters_native_overflow_before_resolution(
    tmp_path: object,
) -> None:
    candidates = plan_model_candidates(
        "implement a change",
        4096,
        ("qwen2.5-coder-0.5b", "deepseek-coder-1.3b", "qwen2.5-coder-1.5b"),
        _hardware(),
        _store(tmp_path),
        _revision,
        input_tokens=5000,
        max_candidates=3,
    )

    assert candidates
    assert all(item.config.context_size >= 5000 + 4096 + 512 for item in candidates)
    assert all(item.config.name != "smollm2-1.7b" for item in candidates)


@pytest.mark.parametrize("input_tokens", [0, -1, cast(int, True)])
def test_invalid_input_token_estimates_fail_before_revision_resolution(
    tmp_path: object,
    input_tokens: int,
) -> None:
    with pytest.raises(ValueError, match="input_tokens"):
        plan_model_candidates(
            "implement a change",
            1024,
            (),
            _hardware(),
            _store(tmp_path),
            lambda _repo: pytest.fail("invalid context must not resolve a model"),
            input_tokens=input_tokens,
        )


def test_revision_resolution_is_limited_to_selected_candidates(tmp_path: object) -> None:
    calls: list[str] = []

    def resolver(repo_id: str) -> str:
        calls.append(repo_id)
        return _revision(repo_id)

    candidates = plan_model_candidates(
        "implement a change",
        1024,
        (),
        _hardware(),
        _store(tmp_path),
        resolver,
        max_candidates=2,
    )

    assert len(candidates) == 2
    assert len(calls) == 2


def test_invalid_immutable_revision_fails_closed(tmp_path: object) -> None:
    with pytest.raises(RuntimeError, match="40-character hexadecimal"):
        plan_model_candidates(
            "implement a change",
            1024,
            (),
            _hardware(),
            _store(tmp_path),
            lambda _repo_id: "main",
        )


def test_unavailable_candidate_is_observable_and_next_models_fill_plan(
    tmp_path: object,
) -> None:
    preferred = get_model("qwen2.5-coder-1.5b")
    assert preferred is not None
    calls: list[str] = []
    unavailable: list[tuple[str, str]] = []

    def resolver(repo_id: str) -> str:
        calls.append(repo_id)
        if repo_id == preferred.repo:
            raise RuntimeError("repository unavailable")
        return _revision(repo_id)

    candidates = plan_model_candidates(
        "implement a change",
        1024,
        (),
        _hardware(),
        _store(tmp_path),
        resolver,
        max_candidates=2,
        on_resolution_failure=lambda model, reason: unavailable.append(
            (model.name, reason)
        ),
    )

    assert [candidate.config.name for candidate in candidates] == [
        "qwen2.5-coder-3b",
        "codellama-7b",
    ]
    assert calls == [
        preferred.repo,
        candidates[0].config.repo,
        candidates[1].config.repo,
    ]
    assert unavailable == [(preferred.name, "repository unavailable")]


@pytest.mark.parametrize(
    ("task_text", "output_tokens", "max_candidates", "message"),
    [
        (" ", 100, 1, "task_text"),
        ("implement", 0, 1, "output_tokens"),
        ("implement", True, 1, "output_tokens"),
        ("implement", 100, 0, "max_candidates"),
        ("implement", 100, True, "max_candidates"),
    ],
)
def test_invalid_requests_are_rejected(
    tmp_path: object,
    task_text: str,
    output_tokens: int,
    max_candidates: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        plan_model_candidates(
            task_text,
            output_tokens,
            (),
            _hardware(),
            _store(tmp_path),
            _revision,
            max_candidates=max_candidates,
        )


@pytest.mark.parametrize(
    ("resolved_revision", "evidence_score", "escalation_level", "message"),
    [
        ("main", 0.5, 0, "resolved_revision"),
        ("a" * 40, cast(float, True), 0, "evidence_score"),
        ("a" * 40, cast(float, "bad"), 0, "evidence_score"),
        ("a" * 40, -0.1, 0, "evidence_score"),
        ("a" * 40, 1.1, 0, "evidence_score"),
        ("a" * 40, 0.5, cast(int, True), "escalation_level"),
        ("a" * 40, 0.5, cast(int, 1.5), "escalation_level"),
        ("a" * 40, 0.5, -1, "escalation_level"),
    ],
)
def test_candidate_invariants_are_enforced(
    resolved_revision: str,
    evidence_score: float,
    escalation_level: int,
    message: str,
) -> None:
    coding = get_model("qwen2.5-coder-0.5b")
    assert coding is not None
    with pytest.raises(ValueError, match=message):
        PlannedModelCandidate(
            config=coding,
            resolved_revision=resolved_revision,
            evidence_score=evidence_score,
            escalation_level=escalation_level,
        )


def test_candidate_rejects_non_coding_profile() -> None:
    general = get_model("qwen-0.5b")
    assert general is not None
    with pytest.raises(ValueError, match="coding model"):
        PlannedModelCandidate(
            config=general,
            resolved_revision="a" * 40,
            evidence_score=0.5,
            escalation_level=0,
        )


@pytest.mark.parametrize(
    "failed_ids",
    [
        cast(tuple[str, ...], "qwen2.5-coder-0.5b"),
        (cast(str, None),),
        ("",),
    ],
)
def test_invalid_prior_failure_inputs_are_rejected(
    tmp_path: object,
    failed_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="prior_failed_model_ids"):
        plan_model_candidates(
            "implement a change",
            100,
            failed_ids,
            _hardware(),
            _store(tmp_path),
            _revision,
        )


def test_unknown_historical_failure_is_ignored(tmp_path: object) -> None:
    candidates = plan_model_candidates(
        "implement a change",
        100,
        ("retired-coding-model",),
        _hardware(),
        _store(tmp_path),
        _revision,
        max_candidates=1,
    )

    assert candidates[0].config.name == "qwen2.5-coder-1.5b"


@pytest.mark.parametrize(
    ("task_text", "output_tokens", "max_candidates", "message"),
    [
        (cast(str, None), 100, 1, "task_text"),
        ("implement", cast(int, "100"), 1, "output_tokens"),
        ("implement", -1, 1, "output_tokens"),
        ("implement", 100, cast(int, "1"), "max_candidates"),
        ("implement", 100, -1, "max_candidates"),
        ("implement", 100, 4, "max_candidates"),
    ],
)
def test_additional_invalid_request_shapes_are_rejected(
    tmp_path: object,
    task_text: str,
    output_tokens: int,
    max_candidates: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        plan_model_candidates(
            task_text,
            output_tokens,
            (),
            _hardware(),
            _store(tmp_path),
            _revision,
            max_candidates=max_candidates,
        )


def test_non_string_revision_fails_closed(tmp_path: object) -> None:
    def invalid_resolver(_repo_id: str) -> str:
        return cast(str, None)

    with pytest.raises(RuntimeError, match="40-character hexadecimal"):
        plan_model_candidates(
            "implement a change",
            100,
            (),
            _hardware(),
            _store(tmp_path),
            invalid_resolver,
            max_candidates=1,
        )


def test_candidate_is_immutable(tmp_path: object) -> None:
    candidate = plan_model_candidates(
        "implement a change",
        100,
        (),
        _hardware(),
        _store(tmp_path),
        _revision,
        max_candidates=1,
    )[0]

    assert isinstance(candidate, PlannedModelCandidate)
    attribute = "escalation_level"
    with pytest.raises(FrozenInstanceError):
        setattr(candidate, attribute, 2)
