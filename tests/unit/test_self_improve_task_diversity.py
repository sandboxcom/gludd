"""Tests for task-shape-scoped self-improvement evidence selection."""

from __future__ import annotations

from typing import cast

import pytest

import general_ludd.self_improve.codex_comparison as comparison_module
import general_ludd.self_improve.task_diversity as task_diversity_module
from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.local_model import get_model
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.task_embeddings import CANONICAL_TASK_DESCRIPTIONS
from general_ludd.self_improve.model_candidate_planner import (
    PlannedModelCandidate,
    load_latest_failed_model_ids,
    plan_model_candidates,
    record_self_improve_outcome,
)
from general_ludd.self_improve.task_diversity import (
    export_representative_evidence,
    infer_task_type,
    select_representative_evidence,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def _store(tmp_path: object) -> CapabilityEvidenceStore:
    return CapabilityEvidenceStore(str(tmp_path) + "/shape-evidence.json")


def _hardware() -> HardwareInventory:
    return HardwareInventory(
        gpus=[GpuInfo("test GPU", 24.0, backend="metal")],
        total_ram_gb=32.0,
        disk_free_gb=100.0,
        cpu_cores=8,
    )


def _record(
    *,
    model: str,
    task_type: TaskType | None,
    task_kind: str = "coding",
    suite_id: str = "suite",
    registered_at: float = 1.0,
    passed_cases: int = 10,
) -> dict[str, object]:
    record: dict[str, object] = {
        "model_profile_id": model,
        "task_kind": task_kind,
        "role": "coder",
        "suite_id": suite_id,
        "suite_revision": "1",
        "passed_cases": passed_cases,
        "total_cases": 10,
        "collection_ok": True,
        "local_only": True,
        "registered_at": registered_at,
    }
    if task_type is not None:
        record["task_type"] = task_type.value
    return record


def _candidate() -> PlannedModelCandidate:
    config = get_model("qwen2.5-coder-0.5b")
    assert config is not None
    return PlannedModelCandidate(
        config=config,
        resolved_revision="a" * 40,
        evidence_score=0.0,
        escalation_level=0,
    )


@pytest.mark.parametrize(
    ("task_type", "description"),
    tuple(CANONICAL_TASK_DESCRIPTIONS.items()),
)
def test_inference_reuses_every_existing_task_type(
    task_type: TaskType,
    description: str,
) -> None:
    assert infer_task_type(description) is task_type


def test_store_query_is_exact_and_excludes_legacy_unscoped_evidence(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    store.register_evidence(_record(model="model-a", task_type=TaskType.FEATURE))
    store.register_evidence(_record(model="model-a", task_type=TaskType.BUG_FIX))
    store.register_evidence(_record(model="model-a", task_type=None))
    store.register_evidence(_record(model="model-b", task_type=TaskType.FEATURE))

    records = store.query_by_task_shape(
        TaskType.FEATURE,
        "coding",
        model_profile_id="model-a",
    )

    assert len(records) == 1
    assert records[0]["task_type"] == TaskType.FEATURE.value
    assert records[0]["model_profile_id"] == "model-a"


def test_representative_selection_is_bounded_diverse_and_deterministic() -> None:
    records = [
        _record(
            model="model-a",
            task_type=TaskType.FEATURE,
            suite_id="feature-old",
            registered_at=1.0,
        ),
        _record(
            model="model-a",
            task_type=TaskType.FEATURE,
            suite_id="feature-new",
            registered_at=2.0,
        ),
        _record(
            model="model-a",
            task_type=TaskType.BUG_FIX,
            suite_id="bug",
        ),
        _record(
            model="model-a",
            task_type=TaskType.DOCUMENTATION,
            task_kind="documentation_draft",
            suite_id="docs",
        ),
        _record(
            model="model-a",
            task_type=None,
            suite_id="legacy",
        ),
        _record(
            model="model-a",
            task_type=TaskType.REFACTOR,
            task_kind="invented_kind",
            suite_id="invented",
        ),
    ]

    selected = select_representative_evidence(records, max_cases=3)
    reversed_selected = select_representative_evidence(
        list(reversed(records)),
        max_cases=3,
    )

    assert [(item["task_type"], item["task_kind"]) for item in selected] == [
        (TaskType.BUG_FIX.value, "coding"),
        (TaskType.FEATURE.value, "coding"),
        (TaskType.DOCUMENTATION.value, "documentation_draft"),
    ]
    assert selected[1]["suite_id"] == "feature-new"
    assert selected == reversed_selected


def test_evidence_export_is_canonical_and_independent_of_input_order() -> None:
    records = [
        {
            "z": 2,
            "task_type": TaskType.FEATURE.value,
            "model_profile_id": "model-z",
            "task_kind": "coding",
        },
        {
            "task_kind": "coding",
            "model_profile_id": "model-a",
            "z": 1,
            "task_type": TaskType.BUG_FIX.value,
        },
    ]

    exported = export_representative_evidence(records)

    assert exported == (
        b'{"evidence":[{"model_profile_id":"model-a","task_kind":"coding",'
        b'"task_type":"bug_fix","z":1},{"model_profile_id":"model-z",'
        b'"task_kind":"coding","task_type":"feature","z":2}],'
        b'"schema_version":1}'
    )
    assert export_representative_evidence(list(reversed(records))) == exported


@pytest.mark.parametrize(
    "invalid_value",
    (
        pytest.param(float("nan"), id="non-finite-number"),
        pytest.param(object(), id="non-json-value"),
    ),
)
def test_evidence_export_rejects_values_outside_canonical_json(
    invalid_value: object,
) -> None:
    record = _record(model="model-a", task_type=TaskType.FEATURE)
    record["invalid"] = invalid_value

    with pytest.raises(ValueError, match="canonical JSON"):
        export_representative_evidence([record])


def test_evidence_export_rejects_non_string_mapping_keys() -> None:
    record = _record(model="model-a", task_type=TaskType.FEATURE)
    record[cast(str, 1)] = "ambiguous-key"

    with pytest.raises(ValueError, match="string keys"):
        export_representative_evidence([record])


@pytest.mark.parametrize(
    "invalid_record",
    (
        pytest.param(
            {"task_type": TaskType.FEATURE.value, "task_kind": "coding"},
            id="missing-model-profile-id",
        ),
        pytest.param(
            {
                "model_profile_id": "",
                "task_type": TaskType.FEATURE.value,
                "task_kind": "coding",
            },
            id="empty-model-profile-id",
        ),
        pytest.param(
            {
                "model_profile_id": 7,
                "task_type": TaskType.FEATURE.value,
                "task_kind": "coding",
            },
            id="non-string-model-profile-id",
        ),
        pytest.param(
            {
                "model_profile_id": "model-invalid",
                "task_type": 7,
                "task_kind": "coding",
            },
            id="non-string-task-type",
        ),
        pytest.param(
            {
                "model_profile_id": "model-invalid",
                "task_type": "unknown-task-type",
                "task_kind": "coding",
            },
            id="unknown-task-type",
        ),
        pytest.param(
            {
                "model_profile_id": "model-invalid",
                "task_type": TaskType.FEATURE.value,
            },
            id="missing-task-kind",
        ),
        pytest.param(
            {
                "model_profile_id": "model-invalid",
                "task_type": TaskType.FEATURE.value,
                "task_kind": 7,
            },
            id="non-string-task-kind",
        ),
    ),
)
def test_representative_selection_excludes_invalid_record_dimensions(
    invalid_record: dict[str, object],
) -> None:
    valid = _record(
        model="model-valid",
        task_type=TaskType.FEATURE,
        suite_id="valid",
    )

    assert select_representative_evidence([invalid_record, valid]) == (valid,)


def test_representative_selection_breaks_timestamp_ties_deterministically() -> None:
    tied_records = [
        _record(
            model="model-a",
            task_type=TaskType.FEATURE,
            suite_id="tie-a",
            registered_at=5.0,
        ),
        _record(
            model="model-a",
            task_type=TaskType.FEATURE,
            suite_id="tie-b",
            registered_at=5.0,
        ),
    ]

    selected = select_representative_evidence(tied_records, max_cases=1)
    reversed_selected = select_representative_evidence(
        list(reversed(tied_records)),
        max_cases=1,
    )

    assert selected[0]["suite_id"] == "tie-b"
    assert selected == reversed_selected


def test_representative_selection_serializes_each_record_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {
            "model_profile_id": "model-a",
            "task_type": TaskType.FEATURE.value,
            "task_kind": "coding",
            "record_id": record_id,
            "registered_at": 5.0,
        }
        for record_id in ("a", "b", "c", "d")
    ]
    original_identity = task_diversity_module._stable_record_identity
    serialization_calls = 0

    def counted_identity(record: object) -> str:
        nonlocal serialization_calls
        serialization_calls += 1
        return original_identity(cast(dict[str, object], record))

    monkeypatch.setattr(
        task_diversity_module,
        "_stable_record_identity",
        counted_identity,
    )

    exported = task_diversity_module.export_representative_evidence(
        records,
        max_cases=1,
    )

    assert exported == (
        b'{"evidence":[{"model_profile_id":"model-a","record_id":"d",'
        b'"registered_at":5.0,"task_kind":"coding","task_type":"feature"}],'
        b'"schema_version":1}'
    )
    assert serialization_calls == len(records)


def test_representative_selection_normalizes_non_finite_timestamps() -> None:
    records = [
        _record(
            model="model-a",
            task_type=TaskType.FEATURE,
            suite_id="non-finite-a",
            registered_at=float("nan"),
        ),
        _record(
            model="model-a",
            task_type=TaskType.FEATURE,
            suite_id="non-finite-b",
            registered_at=float("nan"),
        ),
    ]

    selected = select_representative_evidence(records, max_cases=1)
    reversed_selected = select_representative_evidence(
        list(reversed(records)),
        max_cases=1,
    )

    assert selected[0]["suite_id"] == "non-finite-b"
    assert selected == reversed_selected


def test_representative_selection_uses_distinct_records_in_second_round() -> None:
    records = [
        _record(
            model=model,
            task_type=task_type,
            suite_id=f"{task_type.value}-{model}",
        )
        for model in ("model-b", "model-a")
        for task_type in (TaskType.FEATURE, TaskType.BUG_FIX)
    ]

    selected = select_representative_evidence(records, max_cases=4)
    selected_suite_ids = [str(record["suite_id"]) for record in selected]

    assert selected_suite_ids == [
        "bug_fix-model-a",
        "feature-model-a",
        "bug_fix-model-b",
        "feature-model-b",
    ]
    assert len(selected_suite_ids) == len(set(selected_suite_ids))


@pytest.mark.parametrize("max_cases", [0, 11, cast(int, True)])
def test_representative_selection_has_a_task_type_sized_bound(
    max_cases: int,
) -> None:
    with pytest.raises(ValueError, match="max_cases"):
        select_representative_evidence([], max_cases=max_cases)


def test_planner_uses_only_exact_inferred_task_shape_evidence(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    for index in range(3):
        store.register_evidence(
            _record(
                model="qwen2.5-coder-1.5b",
                task_type=TaskType.FEATURE,
                suite_id=f"feature-{index}",
            )
        )
    store.register_evidence(
        _record(
            model="deepseek-coder-1.3b",
            task_type=TaskType.BUG_FIX,
            suite_id="bug-fix",
        )
    )
    store.register_evidence(
        _record(
            model="qwen2.5-coder-0.5b",
            task_type=None,
            suite_id="legacy-unscoped",
        )
    )

    candidates = plan_model_candidates(
        "Fix a defect in Python code and preserve existing behavior.",
        1024,
        (),
        _hardware(),
        store,
        lambda _repo: "a" * 40,
        max_candidates=2,
    )

    assert candidates[0].config.name == "deepseek-coder-1.3b"
    assert candidates[0].evidence_score > 0.0
    assert all(item.config.name != "qwen2.5-coder-0.5b" for item in candidates[:1])


def test_outcomes_are_task_type_scoped_even_for_same_contract_and_prompt(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    attempt_identity = "1" * 64

    record_self_improve_outcome(
        store,
        task_text="Implement a new product feature in Python.",
        candidate=_candidate(),
        succeeded=False,
        attempt_identity_digest=attempt_identity,
    )

    assert store.list_all()[0]["task_type"] == TaskType.FEATURE.value
    assert load_latest_failed_model_ids(
        store,
        task_text="Fix a defect in Python code.",
        attempt_identity_digest=attempt_identity,
    ) == ()
    assert load_latest_failed_model_ids(
        store,
        task_text="Implement another new product feature in Python.",
        attempt_identity_digest=attempt_identity,
    ) == ("qwen2.5-coder-0.5b",)



def test_prompt_only_failure_remains_auditable_but_is_ineligible_for_full_protocol(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    task_text = "Fix a defect in Python code."
    prompt_only_identity = "1" * 64
    full_identity = comparison_module.local_proposal_attempt_identity_digest(
        prompt_only_identity
    )

    record_self_improve_outcome(
        store,
        task_text=task_text,
        candidate=_candidate(),
        succeeded=False,
        attempt_identity_digest=prompt_only_identity,
    )

    assert store.list_all()[0]["attempt_identity_digest"] == prompt_only_identity
    assert load_latest_failed_model_ids(
        store,
        task_text=task_text,
        attempt_identity_digest=full_identity,
    ) == ()

    record_self_improve_outcome(
        store,
        task_text=task_text,
        candidate=_candidate(),
        succeeded=False,
        attempt_identity_digest=full_identity,
    )

    assert len(store.list_all()) == 2
    assert load_latest_failed_model_ids(
        store,
        task_text=task_text,
        attempt_identity_digest=full_identity,
    ) == ("qwen2.5-coder-0.5b",)
