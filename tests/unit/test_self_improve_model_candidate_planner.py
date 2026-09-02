"""Tests for deterministic self-improvement model candidate planning."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.local_model import get_model
from general_ludd.self_improve.model_candidate_planner import (
    PlannedModelCandidate,
    plan_model_candidates,
)
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


def test_no_evidence_fallback_is_deterministic_and_bounded(tmp_path: object) -> None:
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
        "qwen2.5-coder-0.5b",
        "deepseek-coder-1.3b",
        "qwen2.5-coder-1.5b",
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

    candidates = plan_model_candidates(
        "classify this failure and root cause",
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


def test_prior_failure_alias_is_excluded_and_sets_escalation_floor(tmp_path: object) -> None:
    store = _store(tmp_path)

    candidates = plan_model_candidates(
        "implement a Python fix",
        1024,
        ("qwen-coder-0.5b",),
        _hardware(),
        store,
        _revision,
        max_candidates=2,
    )

    assert [item.config.name for item in candidates] == [
        "deepseek-coder-1.3b",
        "qwen2.5-coder-1.5b",
    ]
    assert all(item.config.size_mb > 312 for item in candidates)


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
    smallest = get_model("qwen2.5-coder-0.5b")
    assert smallest is not None
    calls: list[str] = []
    unavailable: list[tuple[str, str]] = []

    def resolver(repo_id: str) -> str:
        calls.append(repo_id)
        if repo_id == smallest.repo:
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
        "deepseek-coder-1.3b",
        "qwen2.5-coder-1.5b",
    ]
    assert calls == [
        smallest.repo,
        candidates[0].config.repo,
        candidates[1].config.repo,
    ]
    assert unavailable == [(smallest.name, "repository unavailable")]


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

    assert candidates[0].config.name == "qwen2.5-coder-0.5b"


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
