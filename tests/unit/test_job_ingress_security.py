"""Security boundary tests for resource-bounded ``JobSpec`` ingress."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

import general_ludd.schemas.job as job_schema
from general_ludd.schemas.job import JobIngressLimits, JobSpec
from general_ludd.schemas.todo import ResourceProfile, WorkType


def _job_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "job-1_OK",
        "playbook": "validation/noop.yml",
        "queue": "core-priority",
    }
    payload.update(overrides)
    return payload


def _build_job(**overrides: object) -> JobSpec:
    """Build through the dynamic request shape used by the HTTP boundary."""

    return cast(JobSpec, cast(Any, JobSpec)(**_job_payload(**overrides)))


def test_unknown_job_fields_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _build_job(typoed_security_context="ignored")


@pytest.mark.parametrize(
    "job_id",
    ("job with spaces", "../job", "job/name", "job.name", "-job", "job\x00suffix"),
)
def test_job_id_rejects_unsafe_shape(job_id: str) -> None:
    with pytest.raises(ValidationError, match="job_id"):
        _build_job(job_id=job_id)


def test_safe_identifier_shape_preserves_case_hyphen_and_underscore() -> None:
    job = _build_job(job_id="Build-42_retry")
    assert job.job_id == "Build-42_retry"


def test_internal_string_enums_remain_compatible_with_json_ingress() -> None:
    job = _build_job(
        work_type=WorkType.CODE,
        resource_profile=ResourceProfile.AI_HEAVY,
    )

    assert job.work_type == "code"
    assert job.resource_profile == "ai_heavy"


@pytest.mark.parametrize(
    "playbook",
    ("../noop.yml", "/etc/passwd", "roles/../../noop.yml", "roles\\noop.yml", "bad name.yml"),
)
def test_playbook_rejects_traversal_absolute_and_ambiguous_shapes(playbook: str) -> None:
    with pytest.raises(ValidationError, match="playbook"):
        _build_job(playbook=playbook)


def test_playbook_accepts_safe_relative_segments_and_extensionless_names() -> None:
    assert _build_job(playbook="playbooks/review-task.yml").playbook == "playbooks/review-task.yml"
    assert _build_job(playbook="code").playbook == "code"


@pytest.mark.parametrize("queue", ("queue with spaces", "../core", "core/priority", "-core"))
def test_queue_rejects_non_slug_shape(queue: str) -> None:
    with pytest.raises(ValidationError, match="queue"):
        _build_job(queue=queue)


def test_payload_depth_is_bounded_before_field_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_schema, "JOB_INGRESS_LIMITS", JobIngressLimits(max_depth=2))
    nested: dict[str, Any] = {"leaf": "value"}
    for _ in range(4):
        nested = {"child": nested}

    with pytest.raises(ValidationError, match="nesting depth"):
        _build_job(budget_context=nested)


def test_payload_collection_items_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        job_schema,
        "JOB_INGRESS_LIMITS",
        JobIngressLimits(max_collection_items=16),
    )

    with pytest.raises(ValidationError, match="collection items"):
        _build_job(candidate_todos=[f"todo-{index}" for index in range(20)])


def test_payload_serialized_utf8_bytes_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        job_schema,
        "JOB_INGRESS_LIMITS",
        JobIngressLimits(max_serialized_bytes=256),
    )

    with pytest.raises(ValidationError, match="serialized bytes"):
        _build_job(prompt_text="\N{SNOWMAN}" * 100)


def test_byte_accounting_never_materializes_a_second_whole_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_dumps = json.dumps

    def scalar_dumps(value: object, **kwargs: Any) -> str:
        assert not isinstance(value, (dict, list, tuple))
        return real_dumps(value, **kwargs)

    monkeypatch.setattr(json, "dumps", scalar_dumps)

    job = _build_job(budget_context={"nested": ["safe", "values"]})
    assert job.budget_context == {"nested": ["safe", "values"]}


def test_non_json_payload_values_fail_closed() -> None:
    with pytest.raises(ValidationError, match="JSON-compatible"):
        _build_job(budget_context={"opaque": object()})


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_non_finite_payload_numbers_fail_closed(value: float) -> None:
    with pytest.raises(ValidationError, match="finite JSON serialization"):
        _build_job(budget_context={"number": value})


def test_non_string_payload_mapping_keys_fail_closed() -> None:
    with pytest.raises(ValidationError, match="mapping keys must be strings"):
        _build_job(budget_context=cast(Any, {1: "not-json"}))


def test_cyclic_payload_fails_closed_without_recursing() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValidationError, match="cycle"):
        _build_job(budget_context=cyclic)


def test_operator_limits_load_from_environment_mapping() -> None:
    limits = JobIngressLimits.from_environment(
        {
            "GLUDD_JOB_INGRESS_MAX_DEPTH": "8",
            "GLUDD_JOB_INGRESS_MAX_COLLECTION_ITEMS": "2048",
            "GLUDD_JOB_INGRESS_MAX_SERIALIZED_BYTES": "524288",
            "GLUDD_JOB_INGRESS_MAX_IDENTIFIER_CHARS": "64",
            "GLUDD_JOB_INGRESS_MAX_PLAYBOOK_CHARS": "128",
            "GLUDD_JOB_INGRESS_MAX_QUEUE_CHARS": "32",
        }
    )

    assert limits == JobIngressLimits(
        max_depth=8,
        max_collection_items=2048,
        max_serialized_bytes=524288,
        max_identifier_chars=64,
        max_playbook_chars=128,
        max_queue_chars=32,
    )


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("GLUDD_JOB_INGRESS_MAX_DEPTH", "not-an-integer"),
        ("GLUDD_JOB_INGRESS_MAX_COLLECTION_ITEMS", "15"),
        ("GLUDD_JOB_INGRESS_MAX_SERIALIZED_BYTES", "8388609"),
    ),
)
def test_invalid_or_unsafe_operator_limits_fail_closed(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        JobIngressLimits.from_environment({name: value})


def test_configured_shape_lengths_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        job_schema,
        "JOB_INGRESS_LIMITS",
        JobIngressLimits(
            max_identifier_chars=16,
            max_playbook_chars=16,
            max_queue_chars=8,
        ),
    )

    with pytest.raises(ValidationError, match="job_id"):
        _build_job(job_id="j" * 17)
    with pytest.raises(ValidationError, match="playbook"):
        _build_job(playbook="p" * 17)
    with pytest.raises(ValidationError, match="queue"):
        _build_job(queue="q" * 9)
