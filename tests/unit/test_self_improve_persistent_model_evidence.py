"""Tests for persistent self-improvement model outcome evidence."""

from __future__ import annotations

from typing import cast

import pytest

from general_ludd.local_model import get_model
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.self_improve.model_candidate_planner import (
    PlannedModelCandidate,
    load_latest_failed_model_ids,
    record_self_improve_outcome,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.recommender import map_task_to_capabilities


def _store(tmp_path: object) -> CapabilityEvidenceStore:
    return CapabilityEvidenceStore(str(tmp_path) + "/selection-evidence.json")


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
) -> int:
    return record_self_improve_outcome(
        store,
        task_text=task_text,
        candidate=_candidate(model_id, revision),
        succeeded=succeeded,
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
    ) == ("qwen2.5-coder-0.5b",)


def test_latest_success_clears_older_failure_across_revision(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store, revision="a" * 40, succeeded=False)
    _record(store, revision="b" * 40, succeeded=True)

    records = store.list_all()
    assert records[0]["model_identity_digest"] != records[1]["model_identity_digest"]
    assert load_latest_failed_model_ids(
        store,
        task_text="implement another Python module",
    ) == ()


def test_new_failure_after_success_is_active(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store, succeeded=True)
    _record(store, revision="b" * 40, succeeded=False)

    assert load_latest_failed_model_ids(
        store,
        task_text="write code for a module",
    ) == ("qwen2.5-coder-0.5b",)


def test_failures_are_deterministically_sorted(tmp_path: object) -> None:
    store = _store(tmp_path)
    _record(store, model_id="qwen2.5-coder-1.5b")
    _record(store, model_id="deepseek-coder-1.3b")

    assert load_latest_failed_model_ids(
        store,
        task_text="implement code",
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
        )

    assert store.list_all() == []


def test_unmapped_load_fails_closed(tmp_path: object) -> None:
    with pytest.raises(ValueError, match="mapped"):
        load_latest_failed_model_ids(
            _store(tmp_path),
            task_text="unmapped prose",
        )
