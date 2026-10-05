"""Tests for content-free, digest-bound candidate task classification."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from general_ludd.schemas.benchmark import TaskRole, TaskType
from general_ludd.scoring.task_embeddings import CANONICAL_TASK_DESCRIPTIONS
from general_ludd.self_improve import candidate_classification as classification_module
from general_ludd.self_improve.candidate_classification import (
    CANDIDATE_CLASSIFICATION_PROTOCOL,
    CAPABILITY_PRECEDENCE_VERSION,
    CandidateCapability,
    CandidateTaskClassification,
    classify_candidate_task,
    verify_candidate_task_classification,
)


def test_classification_composes_existing_classifiers_without_retaining_text() -> None:
    private_business_marker = "confidential-project-rule-do-not-retain"
    task_text = (
        f"{CANONICAL_TASK_DESCRIPTIONS[TaskType.FEATURE]} "
        f"{private_business_marker}"
    )

    classification = classify_candidate_task(task_text)

    assert classification.task_type is TaskType.FEATURE
    assert classification.task_kind == "coding"
    assert classification.task_role is TaskRole.CODER
    assert classification.task_text_digest == hashlib.sha256(
        task_text.encode("utf-8")
    ).hexdigest()
    assert classification.protocol == CANDIDATE_CLASSIFICATION_PROTOCOL
    assert classification.precedence_version == CAPABILITY_PRECEDENCE_VERSION
    assert private_business_marker not in repr(classification)
    assert private_business_marker not in repr(classification.payload())
    assert "task_text" not in classification.payload()


def test_versioned_precedence_is_independent_of_mapper_result_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        classification_module,
        "infer_task_type",
        lambda _text: TaskType.INTEGRATION,
    )
    monkeypatch.setattr(
        classification_module,
        "map_task_to_capabilities",
        lambda _text: [
            ("coding", TaskRole.CODER),
            ("schema_extraction", TaskRole.EDITOR),
            ("context_compaction", TaskRole.COMPACTOR),
            ("failure_classification", TaskRole.REVIEWER),
        ],
    )

    classification = classify_candidate_task("integrate mapped systems")

    assert classification.task_kind == "context_compaction"
    assert classification.task_role is TaskRole.COMPACTOR
    assert classification.matched_capabilities == (
        CandidateCapability("context_compaction", TaskRole.COMPACTOR),
        CandidateCapability("failure_classification", TaskRole.REVIEWER),
        CandidateCapability("schema_extraction", TaskRole.EDITOR),
        CandidateCapability("coding", TaskRole.CODER),
    )


def test_classification_is_deterministic_and_digest_binds_complete_artifact() -> None:
    task_text = CANONICAL_TASK_DESCRIPTIONS[TaskType.DOCUMENTATION]

    first = classify_candidate_task(task_text)
    second = classify_candidate_task(task_text)

    assert first == second
    assert first.classification_digest == second.classification_digest
    assert len(first.classification_digest) == 64
    changed = CandidateTaskClassification(
        task_text_digest=first.task_text_digest,
        task_type=TaskType.CODE_REVIEW,
        task_kind=first.task_kind,
        task_role=first.task_role,
        matched_capabilities=first.matched_capabilities,
    )
    assert changed.classification_digest != first.classification_digest


def test_expected_task_digest_prevents_input_substitution() -> None:
    expected = hashlib.sha256(b"approved input").hexdigest()

    with pytest.raises(ValueError, match="task text digest mismatch"):
        classify_candidate_task(
            "different implementation task",
            expected_task_text_digest=expected,
        )

    with pytest.raises(ValueError, match="expected_task_text_digest"):
        classify_candidate_task(
            "implementation task",
            expected_task_text_digest="not-a-digest",
        )


@pytest.mark.parametrize("task_text", [None, b"implement", "", "   ", "\x00code"])
def test_invalid_task_text_fails_closed(task_text: object) -> None:
    with pytest.raises(ValueError, match="task_text"):
        classify_candidate_task(task_text)  # type: ignore[arg-type]


def test_oversized_task_text_fails_before_classification() -> None:
    with pytest.raises(ValueError, match="no more than 256000 bytes"):
        classify_candidate_task("x" * 256_001)


def test_unmapped_task_fails_closed_even_when_task_type_can_be_inferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        classification_module,
        "infer_task_type",
        lambda _text: TaskType.OPTIMIZATION,
    )
    monkeypatch.setattr(
        classification_module,
        "map_task_to_capabilities",
        lambda _text: [],
    )

    with pytest.raises(ValueError, match="mapped capability"):
        classify_candidate_task("optimize performance and throughput")


@pytest.mark.parametrize(
    "mapped",
    [
        [("unknown", TaskRole.CODER)],
        [("coding", TaskRole.EDITOR)],
        [("coding", TaskRole.CODER), ("coding", TaskRole.CODER)],
        [("coding", "coder")],
    ],
)
def test_invalid_mapper_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    mapped: list[tuple[object, object]],
) -> None:
    monkeypatch.setattr(
        classification_module,
        "map_task_to_capabilities",
        lambda _text: mapped,
    )

    with pytest.raises(ValueError, match="capabilit"):
        classify_candidate_task("implement a feature")


@pytest.mark.parametrize("mapped", [("coding", TaskRole.CODER), ["coding"]])
def test_malformed_mapper_container_or_entry_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    mapped: object,
) -> None:
    monkeypatch.setattr(
        classification_module,
        "map_task_to_capabilities",
        lambda _text: mapped,
    )

    with pytest.raises(ValueError, match="mapped capability"):
        classify_candidate_task("implement a feature")


def test_invalid_task_type_result_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        classification_module,
        "infer_task_type",
        lambda _text: "feature",
    )

    with pytest.raises(ValueError, match="invalid TaskType"):
        classify_candidate_task("implement a feature")


def test_candidate_capability_rejects_untyped_or_unknown_pairs() -> None:
    with pytest.raises(ValueError, match="known task kind"):
        CandidateCapability("coding", "coder")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not admitted"):
        CandidateCapability("not_registered", TaskRole.CODER)


def test_manual_artifact_validation_rejects_mutable_or_inconsistent_state() -> None:
    digest = hashlib.sha256(b"task").hexdigest()
    capability = CandidateCapability("coding", TaskRole.CODER)

    with pytest.raises(ValueError, match="matched_capabilities must be a tuple"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type=TaskType.FEATURE,
            task_kind="coding",
            task_role=TaskRole.CODER,
            matched_capabilities=[capability],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="primary capability"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type=TaskType.FEATURE,
            task_kind="documentation_draft",
            task_role=TaskRole.EDITOR,
            matched_capabilities=(capability,),
        )
    with pytest.raises(ValueError, match="protocol"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type=TaskType.FEATURE,
            task_kind="coding",
            task_role=TaskRole.CODER,
            matched_capabilities=(capability,),
            protocol="future-unreviewed-protocol",
        )
    with pytest.raises(ValueError, match="task_type"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type="feature",  # type: ignore[arg-type]
            task_kind="coding",
            task_role=TaskRole.CODER,
            matched_capabilities=(capability,),
        )
    with pytest.raises(ValueError, match="precedence_version"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type=TaskType.FEATURE,
            task_kind="coding",
            task_role=TaskRole.CODER,
            matched_capabilities=(capability,),
            precedence_version="future-unreviewed-precedence",
        )
    with pytest.raises(ValueError, match="known CandidateCapability"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type=TaskType.FEATURE,
            task_kind="coding",
            task_role=TaskRole.CODER,
            matched_capabilities=(),
        )
    with pytest.raises(ValueError, match="unique and follow"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type=TaskType.FEATURE,
            task_kind="coding",
            task_role=TaskRole.CODER,
            matched_capabilities=(capability, capability),
        )
    with pytest.raises(ValueError, match="unique and follow"):
        CandidateTaskClassification(
            task_text_digest=digest,
            task_type=TaskType.FEATURE,
            task_kind="coding",
            task_role=TaskRole.CODER,
            matched_capabilities=(
                capability,
                CandidateCapability("documentation_draft", TaskRole.EDITOR),
            ),
        )


def test_artifacts_are_frozen() -> None:
    classification = classify_candidate_task("implement a feature")

    with pytest.raises(FrozenInstanceError):
        classification.task_kind = "documentation_draft"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        classification.matched_capabilities[0].task_kind = "coding"  # type: ignore[misc]


def test_verification_reclassifies_and_rejects_stale_or_substituted_inputs() -> None:
    original_text = "implement an integration module"
    classification = classify_candidate_task(original_text)

    assert verify_candidate_task_classification(classification, original_text)
    with pytest.raises(ValueError, match="task text digest mismatch"):
        verify_candidate_task_classification(classification, "implement another module")
    with pytest.raises(ValueError, match="classification digest mismatch"):
        verify_candidate_task_classification(
            CandidateTaskClassification(
                task_text_digest=classification.task_text_digest,
                task_type=TaskType.CODE_REVIEW,
                task_kind=classification.task_kind,
                task_role=classification.task_role,
                matched_capabilities=classification.matched_capabilities,
            ),
            original_text,
        )

    with pytest.raises(ValueError, match="CandidateTaskClassification"):
        verify_candidate_task_classification(object(), original_text)  # type: ignore[arg-type]


def test_classification_event_is_content_free_and_correlatable() -> None:
    task_text = "implement code using password=extremely-private"
    classification = classify_candidate_task(task_text)

    event = classification.event_payload()

    assert event == {
        "classification_digest": classification.classification_digest,
        "event": "self_improve_candidate_classified",
        "precedence_version": CAPABILITY_PRECEDENCE_VERSION,
        "protocol": CANDIDATE_CLASSIFICATION_PROTOCOL,
        "task_kind": "coding",
        "task_role": "coder",
        "task_text_digest": classification.task_text_digest,
        "task_type": classification.task_type.value,
    }
    assert "extremely-private" not in repr(event)
