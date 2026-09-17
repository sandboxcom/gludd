"""Tests for persistent self-improvement model outcome evidence."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
import general_ludd.self_improve.model_candidate_planner as planner_module
from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.local_model import get_model
from general_ludd.schemas.benchmark import TaskRole, TaskType
from general_ludd.self_improve.model_candidate_planner import (
    PlannedModelCandidate,
    load_latest_failed_model_ids,
    plan_model_candidates,
    record_self_improve_feedback,
    record_self_improve_outcome,
)
from general_ludd.self_improve.model_lifecycle import ModelArtifactIdentity
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.recommender import map_task_to_capabilities

_ATTEMPT_IDENTITY = "1" * 64


def _store(tmp_path: object) -> CapabilityEvidenceStore:
    return CapabilityEvidenceStore(str(tmp_path) + "/selection-evidence.json")


def _hardware() -> HardwareInventory:
    return HardwareInventory(
        gpus=[GpuInfo("test GPU", 24.0, backend="metal")],
        total_ram_gb=32.0,
        disk_free_gb=100.0,
        cpu_cores=8,
    )


def _candidate(
    model_id: str = "qwen2.5-coder-0.5b",
    revision: str = "a" * 40,
) -> PlannedModelCandidate:
    config = get_model(model_id)
    assert config is not None
    return PlannedModelCandidate(
        config=config,
        resolved_revision=revision,
        evidence_score=0.0,
        escalation_level=0,
    )


def _record(
    store: CapabilityEvidenceStore,
    *,
    task_text: str = "implement a focused Python change",
    model_id: str = "qwen2.5-coder-0.5b",
    revision: str = "a" * 40,
    succeeded: bool = False,
    attempt_identity_digest: str = _ATTEMPT_IDENTITY,
) -> int:
    return record_self_improve_outcome(
        store,
        task_text=task_text,
        candidate=_candidate(model_id, revision),
        succeeded=succeeded,
        attempt_identity_digest=attempt_identity_digest,
    )


def test_public_mapper_adds_coding_without_hiding_specific_capabilities() -> None:
    mapped = map_task_to_capabilities(
        "implement Python code to classify this failure",
    )

    assert mapped == [
        ("failure_classification", TaskRole.REVIEWER),
        ("coding", TaskRole.CODER),
    ]


@pytest.mark.parametrize(
    "description",
    [
        "Bind the model downloader to its configured isolated cache.",
        "Add an immutable cache manifest.",
        "Fix the lifecycle race.",
        "Integrate the model planner with the runner.",
        "Migrate the adapter to the collection.",
        "Remove the unused dependency.",
        "Replace the stale backend.",
        "Wire the lease manager into retries.",
    ],
)
def test_public_mapper_recognizes_action_oriented_coding_tasks(
    description: str,
) -> None:
    assert ("coding", TaskRole.CODER) in map_task_to_capabilities(description)


def test_record_writes_one_revision_bound_outcome_for_multi_match_task(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)

    count = _record(
        store,
        task_text="implement Python code to classify this failure",
    )

    records = store.list_all()
    assert count == 1
    assert len(records) == 1
    assert records[0]["task_kind"] == "failure_classification"
    assert records[0]["role"] == TaskRole.REVIEWER.value
    assert records[0]["suite_revision"] == "a" * 40
    assert records[0]["attempt_identity_digest"] == _ATTEMPT_IDENTITY
    assert records[0]["model_identity_digest"] != "a" * 64
    assert len(cast(str, records[0]["model_identity_digest"])) == 64
    assert records[0]["passed_cases"] == 0
    assert records[0]["total_cases"] == 1
    assert records[0]["collection_ok"] is True
    assert records[0]["local_only"] is True


def test_failure_is_loaded_for_same_mapped_task(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store)

    assert load_latest_failed_model_ids(
        store,
        task_text="implement another Python module",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == ("qwen2.5-coder-0.5b",)


def test_pre_fix_phantom_failure_is_ineligible_after_outcome_protocol_change(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_digest = "a" * 64
    protocol = comparison_module.LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL
    with monkeypatch.context() as scoped:
        scoped.setattr(
            comparison_module,
            "LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL",
            replace(
                protocol,
                version="self-improve-model-attempt-outcome-pre-cache-refusal-fix",
            ),
        )
        pre_fix_identity = (
            comparison_module.local_proposal_attempt_identity_digest(prompt_digest)
        )
    current_identity = comparison_module.local_proposal_attempt_identity_digest(
        prompt_digest
    )
    assert pre_fix_identity != current_identity

    store = _store(tmp_path)
    _record(
        store,
        succeeded=False,
        attempt_identity_digest=pre_fix_identity,
    )

    assert load_latest_failed_model_ids(
        store,
        task_text="implement another Python module",
        attempt_identity_digest=current_identity,
    ) == ()


def test_latest_success_clears_older_failure_for_same_attempt_identity(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    _record(store, revision="a" * 40, succeeded=False)
    _record(store, revision="b" * 40, succeeded=True)

    records = store.list_all()
    assert records[0]["model_identity_digest"] != records[1]["model_identity_digest"]
    assert load_latest_failed_model_ids(
        store,
        task_text="implement another Python module",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == ()


def test_new_failure_after_success_is_active(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store, succeeded=True)
    _record(store, revision="b" * 40, succeeded=False)

    assert load_latest_failed_model_ids(
        store,
        task_text="Implement another product feature in Python.",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == ("qwen2.5-coder-0.5b",)


def test_failures_are_deterministically_sorted(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store, model_id="qwen2.5-coder-1.5b")
    _record(store, model_id="deepseek-coder-1.3b")

    assert load_latest_failed_model_ids(
        store,
        task_text="implement code",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == (
        "deepseek-coder-1.3b",
        "qwen2.5-coder-1.5b",
    )


def test_unrelated_task_outcome_is_ignored(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(
        store,
        task_text="draft documentation for the README",
        succeeded=False,
    )

    assert load_latest_failed_model_ids(
        store,
        task_text="implement a Python module",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == ()


def test_malformed_later_success_cannot_clear_valid_failure(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store, succeeded=False)
    malformed = dict(store.list_all()[0])
    malformed["passed_cases"] = 1
    store.register_evidence(malformed)

    assert load_latest_failed_model_ids(
        store,
        task_text="implement a Python module",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == ("qwen2.5-coder-0.5b",)


def test_unrelated_benchmark_record_cannot_clear_failure(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store, succeeded=False)
    unrelated = dict(store.list_all()[0])
    unrelated["suite_id"] = "other_suite"
    unrelated["passed_cases"] = 1
    store.register_evidence(unrelated)

    assert load_latest_failed_model_ids(
        store,
        task_text="implement a Python module",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == ("qwen2.5-coder-0.5b",)


@pytest.mark.parametrize(
    ("task_text", "candidate", "succeeded", "message"),
    [
        ("unmapped prose", _candidate(), False, "mapped"),
        (
            "implement code",
            cast(PlannedModelCandidate, object()),
            False,
            "candidate",
        ),
        ("implement code", _candidate(), cast(bool, 1), "succeeded"),
    ],
)
def test_invalid_outcomes_fail_before_write(
    tmp_path: object,
    task_text: str,
    candidate: PlannedModelCandidate,
    succeeded: bool,
    message: str,
) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match=message):
        record_self_improve_outcome(
            store,
            task_text=task_text,
            candidate=candidate,
            succeeded=succeeded,
            attempt_identity_digest=_ATTEMPT_IDENTITY,
        )

    assert store.list_all() == []


def test_unmapped_load_fails_closed(tmp_path: object) -> None:
    with pytest.raises(ValueError, match="mapped"):
        load_latest_failed_model_ids(
            _store(tmp_path),
            task_text="unmapped prose",
            attempt_identity_digest=_ATTEMPT_IDENTITY,
        )


@pytest.mark.parametrize(
    "identity",
    [
        None,
        "",
        "a" * 63,
        "A" * 64,
        "g" * 64,
    ],
)
def test_invalid_attempt_identity_fails_closed_before_read_or_write(
    tmp_path: object,
    identity: object,
) -> None:
    store = _store(tmp_path)
    invalid_identity = cast(str, identity)

    with pytest.raises(ValueError, match="attempt_identity_digest"):
        record_self_improve_outcome(
            store,
            task_text="implement a focused Python change",
            candidate=_candidate(),
            succeeded=False,
            attempt_identity_digest=invalid_identity,
        )
    assert store.list_all() == []

    with pytest.raises(ValueError, match="attempt_identity_digest"):
        load_latest_failed_model_ids(
            store,
            task_text="implement another Python module",
            attempt_identity_digest=invalid_identity,
        )


def test_legacy_unscoped_failure_is_auditable_but_not_reused(
    tmp_path: object,
) -> None:
    source = _store(tmp_path)
    _record(source)
    legacy_record = dict(source.list_all()[0])
    legacy_record.pop("attempt_identity_digest")
    legacy_store = CapabilityEvidenceStore(str(tmp_path) + "/legacy-evidence.json")
    legacy_store.register_evidence(legacy_record)

    assert load_latest_failed_model_ids(
        legacy_store,
        task_text="implement another Python module",
        attempt_identity_digest=_ATTEMPT_IDENTITY,
    ) == ()
    assert "attempt_identity_digest" not in legacy_store.list_all()[0]


def test_old_prompt_identity_failure_does_not_exclude_compacted_attempt(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    old_identity = "1" * 64
    compacted_identity = "2" * 64

    record_self_improve_outcome(
        store,
        task_text="implement a focused Python change",
        candidate=_candidate(),
        succeeded=False,
        attempt_identity_digest=old_identity,
    )

    assert load_latest_failed_model_ids(
        store,
        task_text="implement another Python module",
        attempt_identity_digest=compacted_identity,
    ) == ()
    assert load_latest_failed_model_ids(
        store,
        task_text="implement another Python module",
        attempt_identity_digest=old_identity,
    ) == ("qwen2.5-coder-0.5b",)
    assert store.list_all()[0]["attempt_identity_digest"] == old_identity


def test_outcome_history_drives_deterministic_next_larger_candidate_only_for_shape(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    task_text = "Implement a focused Python product feature."
    attempt_identity = "a" * 64
    initial = plan_model_candidates(
        task_text,
        1024,
        (),
        _hardware(),
        store,
        lambda _repo: "a" * 40,
        max_candidates=3,
    )
    assert [candidate.config.name for candidate in initial] == [
        "qwen2.5-coder-1.5b",
        "qwen2.5-coder-3b",
        "codellama-7b",
    ]

    for candidate in initial[:2]:
        record_self_improve_outcome(
            store,
            task_text=task_text,
            candidate=candidate,
            succeeded=False,
            attempt_identity_digest=attempt_identity,
        )
    records = store.list_all()
    assert {
        (record["task_type"], record["task_kind"], record["attempt_identity_digest"])
        for record in records
    } == {(TaskType.FEATURE.value, "coding", attempt_identity)}

    def next_plan(text: str, identity: str) -> tuple[PlannedModelCandidate, ...]:
        return plan_model_candidates(
            text,
            1024,
            (),
            _hardware(),
            store,
            lambda _repo: "a" * 40,
            attempt_identity_digest=identity,
            max_candidates=1,
        )

    expected = next_plan(task_text, attempt_identity)
    assert [candidate.config.name for candidate in expected] == ["codellama-7b"]
    assert next_plan(task_text, attempt_identity) == expected
    assert next_plan(task_text, "b" * 64)[0].config.name == "qwen2.5-coder-1.5b"
    assert (
        next_plan("Fix a defect in Python code.", attempt_identity)[0].config.name
        == "qwen2.5-coder-1.5b"
    )


def test_persistent_sixty_point_failure_escalates_same_complex_attempt_identity(
    tmp_path: object,
) -> None:
    """Use the exact live-quality outcome to avoid retrying an under-capable model."""
    store = _store(tmp_path)
    task_text = "Implement a focused Python product feature."
    attempt_identity = "e" * 64
    failed = _candidate("qwen2.5-coder-1.5b")
    feedback = comparison_module.PlannerFeedbackExchange(
        plan_identity_digest="d" * 64,
        attempt_identity_digest=attempt_identity,
        attempt_number=1,
        model_identity=ModelArtifactIdentity(
            model_id=failed.config.name,
            repo_id=failed.config.repo,
            filename=failed.config.filename,
            revision=failed.resolved_revision,
        ),
        task_id="S83.133",
        task_objective=task_text,
        outcome=comparison_module.ComparisonResult(
            accepted=False,
            score=60.0,
            blockers=("tests failed",),
            changed_file_precision=1.0,
            changed_file_recall=1.0,
        ),
        source_artifact_digest="c" * 64,
    )

    assert record_self_improve_feedback(store, feedback=feedback) == 1
    candidates = plan_model_candidates(
        task_text,
        1024,
        (),
        _hardware(),
        store,
        lambda _repo: "b" * 40,
        input_tokens=1024,
        attempt_identity_digest=attempt_identity,
        task_shape=planner_module.CodeTaskShape(2, 1, 12_000),
        max_candidates=1,
    )

    assert [candidate.config.name for candidate in candidates] == [
        "qwen2.5-coder-3b"
    ]


def test_live_length_failures_escalate_past_both_models_for_same_identity(
    tmp_path: object,
) -> None:
    """Never repeat either exact 1024-token live failure on the next plan."""
    store = _store(tmp_path)
    task_text = "Implement a focused Python product feature."
    attempt_identity = "f" * 64
    for model_id in ("qwen2.5-coder-1.5b", "smollm2-1.7b"):
        _record(
            store,
            task_text=task_text,
            model_id=model_id,
            attempt_identity_digest=attempt_identity,
        )

    candidates = plan_model_candidates(
        task_text,
        4096,
        (),
        _hardware(),
        store,
        lambda _repo: "c" * 40,
        input_tokens=2803,
        attempt_identity_digest=attempt_identity,
        task_shape=planner_module.CodeTaskShape(2, 1, 16_041),
        max_candidates=2,
    )

    assert [candidate.config.name for candidate in candidates] == [
        "qwen2.5-coder-3b",
        "codellama-7b",
    ]
