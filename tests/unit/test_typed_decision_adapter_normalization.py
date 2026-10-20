"""Strict offline normalization contracts for pinned typed-decision fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import cast

import pytest
from scripts import normalize_typed_decision_adapter_fixture as normalizer

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    ROOT / "docs" / "releases" / "typed-decision-adapter-contract-v1.schema.json"
)
EXAMPLES_PATH = (
    ROOT / "docs" / "releases" / "typed-decision-adapter-contract-v1.examples.json"
)


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _digests(seed: str) -> Iterator[str]:
    index = 0
    while True:
        yield _digest(f"{seed}-{index}")
        index += 1


def _replace_digests(value: object, values: Iterator[str]) -> object:
    if isinstance(value, dict):
        return {key: _replace_digests(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_digests(item, values) for item in value]
    if isinstance(value, str) and value.startswith("sha256:"):
        return next(values)
    return value


def _bundle() -> dict[str, object]:
    loaded = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _schema() -> dict[str, object]:
    loaded = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _profile(index: int) -> dict[str, object]:
    profiles = _bundle()["profiles"]
    assert isinstance(profiles, list) and isinstance(profiles[index], dict)
    profile = _replace_digests(copy.deepcopy(profiles[index]), _digests(f"p{index}"))
    assert isinstance(profile, dict)
    profile["example_only"] = False
    source = profile["source_pin"]
    identity = profile["identity"]
    assert isinstance(source, dict) and isinstance(identity, dict)
    source["release"] = f"v1.0.{index}"
    source["commit"] = hashlib.sha1(f"source-{index}".encode()).hexdigest()
    identity["model_revision"] = f"model-{index}@v1.0.{index}"
    return profile


def _example(name: str) -> tuple[dict[str, object], dict[str, object]]:
    examples = _bundle()["normalization_examples"]
    assert isinstance(examples, list)
    item = next(
        candidate
        for candidate in examples
        if isinstance(candidate, dict) and candidate.get("name") == name
    )
    request = copy.deepcopy(item["upstream_request"])
    response = copy.deepcopy(item["upstream_response"])
    assert isinstance(request, dict) and isinstance(response, dict)
    return request, response


def _normalize(
    profile: dict[str, object],
    request: dict[str, object],
    response: dict[str, object],
    *,
    request_id: str = "fixture-request-1",
    request_schema_version: str = "jev-systemone-observed-request/2026-09-27",
    response_schema_version: str = "jev-systemone-observed-response/2026-09-27",
) -> normalizer.NormalizationRecord:
    return normalizer.normalize_fixture(
        _schema(),
        profile,
        request_id=request_id,
        task_kind="failure_classification",
        request_schema_version=request_schema_version,
        response_schema_version=response_schema_version,
        upstream_request=request,
        upstream_response=response,
    )


def _reseal_record(document: dict[str, object]) -> dict[str, object]:
    body = {key: value for key, value in document.items() if key != "record_digest"}
    document["record_digest"] = normalizer.canonical_digest(body)
    return document


@pytest.mark.parametrize(
    ("example_name", "profile_index", "kind", "outcome", "probabilities", "confidence"),
    [
        (
            "openjev-choice-normalization",
            0,
            "choice",
            "transient",
            (("configuration", 0.1), ("transient", 0.8), ("unknown", 0.1)),
            0.64,
        ),
        (
            "laya-noul-normalization",
            1,
            "noul",
            True,
            (("false", 0.2), ("true", 0.8)),
            None,
        ),
        (
            "openjev-score-normalization",
            0,
            "score",
            1.3,
            (("0", 0.1), ("1", 0.5), ("2", 0.4)),
            0.14,
        ),
    ],
)
def test_choice_noul_and_score_normalize_to_provider_neutral_outcomes(
    example_name: str,
    profile_index: int,
    kind: str,
    outcome: object,
    probabilities: tuple[tuple[str, float], ...],
    confidence: float | None,
) -> None:
    request, response = _example(example_name)
    record = _normalize(_profile(profile_index), request, response)

    assert record.ready is True
    assert len(record.outcomes) == 1
    normalized = record.outcomes[0]
    assert normalized.kind == kind
    assert normalized.outcome == outcome
    assert tuple((item.label, item.probability) for item in normalized.probabilities) == (
        probabilities
    )
    assert normalized.upstream_confidence == confidence
    assert normalized.disposition == "valid"
    assert normalized.reason_code is None
    assert normalizer.NormalizationRecord.from_dict(record.to_dict()) == record


def test_binding_is_exact_deterministic_redacted_and_replayable() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    first = _normalize(profile, request, response)
    second = _normalize(profile, copy.deepcopy(request), copy.deepcopy(response))

    assert first == second
    assert first.record_digest == second.record_digest
    assert first.binding.profile_digest == normalizer.canonical_digest(profile)
    assert first.binding.contract_schema_digest == normalizer.canonical_digest(_schema())
    assert first.binding.capability_digest == normalizer.canonical_digest(
        profile["capabilities"],
    )
    assert first.binding.request_digest == normalizer.canonical_digest(request)
    assert first.binding.response_digest == normalizer.canonical_digest(response)
    assert first.binding.request_schema_version.endswith("request/2026-09-27")
    assert first.binding.response_schema_version.endswith("response/2026-09-27")
    assert first.network_accessed is False
    assert first.runtime_invoked is False
    assert first.model_invoked is False
    assert first.admission_performed is False
    assert "state" not in json.dumps(first.to_dict(), sort_keys=True)
    assert "answers" not in json.dumps(first.to_dict(), sort_keys=True)
    assert normalizer.replay_normalization(
        first,
        _schema(),
        profile,
        request_id="fixture-request-1",
        task_kind="failure_classification",
        request_schema_version="jev-systemone-observed-request/2026-09-27",
        response_schema_version="jev-systemone-observed-response/2026-09-27",
        upstream_request=request,
        upstream_response=response,
    ) == first


def test_mapping_permutations_produce_the_same_canonical_record() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    question = request["questions"]
    answers = response["answers"]
    assert isinstance(question, dict) and isinstance(answers, dict)
    request["questions"] = {
        "z_score": {
            "type": "score",
            "instructions": "Score it.",
            "criteria": ["low", "high"],
        },
        **question,
    }
    response["answers"] = {
        **answers,
        "z_score": {
            "score": 0.25,
            "legend": {"1": "high", "0": "low"},
            "probabilities": {"1": 0.25, "0": 0.75},
            "confidence": 0.4,
        },
    }
    first = _normalize(profile, request, response)

    reordered_questions = request["questions"]
    reordered_answers = response["answers"]
    assert isinstance(reordered_questions, dict) and isinstance(reordered_answers, dict)
    request["questions"] = dict(reversed(list(reordered_questions.items())))
    response["answers"] = dict(reversed(list(reordered_answers.items())))
    choice = reordered_answers["failure_kind"]
    assert isinstance(choice, dict)
    choice["probabilities"] = {"unknown": 0.1, "transient": 0.8, "configuration": 0.1}
    second = _normalize(profile, request, response)

    assert first == second
    assert tuple(item.question_id for item in first.outcomes) == ("failure_kind", "z_score")


Mutation = Callable[[dict[str, object], dict[str, object], dict[str, object]], None]


def _request_extra(
    _profile_value: dict[str, object],
    request: dict[str, object],
    _response: dict[str, object],
) -> None:
    request["secret_extension"] = "must-not-appear"


def _question_extra(
    _profile_value: dict[str, object],
    request: dict[str, object],
    _response: dict[str, object],
) -> None:
    questions = request["questions"]
    assert isinstance(questions, dict) and isinstance(questions["failure_kind"], dict)
    questions["failure_kind"]["ambiguous"] = True


def _response_extra(
    _profile_value: dict[str, object],
    _request: dict[str, object],
    response: dict[str, object],
) -> None:
    response["provider_metadata"] = {"secret": "must-not-appear"}


def _answer_extra(
    _profile_value: dict[str, object],
    _request: dict[str, object],
    response: dict[str, object],
) -> None:
    answers = response["answers"]
    assert isinstance(answers, dict) and isinstance(answers["failure_kind"], dict)
    answers["failure_kind"]["explanation"] = "must-not-appear"


def _usage_extra(
    _profile_value: dict[str, object],
    _request: dict[str, object],
    response: dict[str, object],
) -> None:
    usage = response["usage"]
    assert isinstance(usage, dict)
    usage["cached_tokens"] = 3


@pytest.mark.parametrize(
    "mutation",
    [_request_extra, _question_extra, _response_extra, _answer_extra, _usage_extra],
)
def test_unknown_or_ambiguous_wire_fields_fail_closed(mutation: Mutation) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    mutation(profile, request, response)

    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code in {"request_malformed", "response_malformed"}
    assert len(str(raised.value)) <= 160
    assert "must-not-appear" not in str(raised.value)


@pytest.mark.parametrize(
    ("target", "value", "reason"),
    [
        ("request_model", "wrong-model", "identity_mismatch"),
        ("response_model", "wrong-model", "identity_mismatch"),
        ("profile_example", True, "profile_unpinned"),
        ("profile_state", "shadow", "profile_unpinned"),
        ("normalization_version", "normalization/v2", "schema_version_mismatch"),
        ("question_types", ["noul"], "capability_mismatch"),
    ],
)
def test_identity_pin_schema_and_capability_mismatches_fail_closed(
    target: str,
    value: object,
    reason: str,
) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    if target == "request_model":
        request["model"] = value
    elif target == "response_model":
        response["model"] = value
    elif target == "profile_example":
        profile["example_only"] = value
    elif target == "profile_state":
        profile["profile_state"] = value
    elif target == "normalization_version":
        compatibility = profile["compatibility"]
        assert isinstance(compatibility, dict)
        compatibility["normalization_version"] = value
    else:
        capabilities = profile["capabilities"]
        assert isinstance(capabilities, dict)
        capabilities["question_types"] = value

    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == reason


def test_schema_versions_request_id_and_task_kind_are_exact() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    for kwargs in (
        {"request_schema_version": "request/v2"},
        {"response_schema_version": "response/v2"},
        {"request_id": "secret request id"},
    ):
        with pytest.raises(normalizer.NormalizationError):
            _normalize(profile, request, response, **kwargs)

    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.normalize_fixture(
            _schema(),
            profile,
            request_id="fixture-request-1",
            task_kind="other_task",
            request_schema_version="jev-systemone-observed-request/2026-09-27",
            response_schema_version="jev-systemone-observed-response/2026-09-27",
            upstream_request=request,
            upstream_response=response,
        )
    assert raised.value.reason_code == "schema_version_mismatch"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("choice", "unknown"),
        ("probabilities", {"configuration": 0.2, "transient": 0.6, "unknown": 0.3}),
        ("probabilities", {"configuration": 0.4, "transient": 0.4, "unknown": 0.2}),
        ("probabilities", {"configuration": 0.1, "transient": float("nan"), "unknown": 0.9}),
        ("confidence", True),
    ],
)
def test_choice_semantics_reject_mismatch_ties_nonfinite_and_bool(
    field: str,
    value: object,
) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict) and isinstance(answers["failure_kind"], dict)
    answers["failure_kind"][field] = value
    with pytest.raises(normalizer.NormalizationError):
        _normalize(profile, request, response)


@pytest.mark.parametrize("value", [-0.1, 1.1, 0.5, True, float("inf")])
def test_noul_semantics_reject_out_of_range_ambiguous_bool_and_nonfinite(
    value: object,
) -> None:
    profile = _profile(1)
    request, response = _example("laya-noul-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict) and isinstance(answers["is_transient"], dict)
    answers["is_transient"]["noul"] = value
    with pytest.raises(normalizer.NormalizationError):
        _normalize(profile, request, response)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("score", 1.4),
        ("legend", {"0": "low", "1": "high", "2": "medium"}),
        ("legend", {"0": "low", "1": "medium"}),
        ("probabilities", {"0": 0.1, "1": 0.4, "2": 0.4}),
        ("probabilities", {"0": 0.1, "1": 0.5, "2": float("-inf")}),
        ("confidence", False),
    ],
)
def test_score_semantics_reject_legend_probability_and_expectation_mismatch(
    field: str,
    value: object,
) -> None:
    profile = _profile(0)
    request, response = _example("openjev-score-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict) and isinstance(answers["severity"], dict)
    answers["severity"][field] = value
    with pytest.raises(normalizer.NormalizationError):
        _normalize(profile, request, response)


def test_missing_extra_or_partial_answers_reject_the_entire_response() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict)
    answers.clear()
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "response_malformed"

    request, response = _example("openjev-choice-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict)
    answers["unrequested"] = {"noul": 0.9}
    with pytest.raises(normalizer.NormalizationError):
        _normalize(profile, request, response)


def test_profile_capability_and_byte_bounds_are_enforced() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    capabilities = profile["capabilities"]
    limits = profile["limits"]
    assert isinstance(capabilities, dict) and isinstance(limits, dict)

    capabilities["max_questions"] = 0
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code in {"profile_unpinned", "capability_mismatch"}

    profile = _profile(0)
    capabilities = profile["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["max_state_bytes"] = 1
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "capability_mismatch"

    profile = _profile(0)
    capabilities = profile["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["state_shapes"] = ["array"]
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "capability_mismatch"

    profile = _profile(0)
    capabilities = profile["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["max_choice_options"] = 2
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "capability_mismatch"

    profile = _profile(0)
    limits = profile["limits"]
    assert isinstance(limits, dict)
    limits["response_bytes"] = 1024
    questions = request["questions"]
    answers = response["answers"]
    assert isinstance(questions, dict) and isinstance(answers, dict)
    labels = [f"option-{index}-" + ("x" * 140) for index in range(10)]
    questions["failure_kind"] = {
        "type": "choice",
        "instructions": "Classify the failure.",
        "criteria": {label: f"Criterion {index}" for index, label in enumerate(labels)},
    }
    answers["failure_kind"] = {
        "choice": labels[0],
        "probabilities": {
            label: 1.0 if index == 0 else 0.0
            for index, label in enumerate(labels)
        },
        "confidence": 0.9,
    }
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "capability_mismatch"


@pytest.mark.parametrize("state", [{"safe": "value"}, ["safe", "value"]])
def test_profile_declared_object_and_array_state_shapes_are_normalized(
    state: object,
) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    capabilities = profile["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["state_shapes"] = ["object" if isinstance(state, dict) else "array"]
    request["state"] = state
    assert _normalize(profile, request, response).ready is True


def test_non_mapping_contract_request_and_response_envelopes_fail_closed() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    common = {
        "request_id": "fixture-request-1",
        "task_kind": "failure_classification",
        "request_schema_version": "jev-systemone-observed-request/2026-09-27",
        "response_schema_version": "jev-systemone-observed-response/2026-09-27",
    }
    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.normalize_fixture(
            cast(Mapping[str, object], []),
            profile,
            upstream_request=request,
            upstream_response=response,
            **common,
        )
    assert raised.value.reason_code == "contract_invalid"

    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.normalize_fixture(
            _schema(),
            profile,
            upstream_request=cast(Mapping[str, object], []),
            upstream_response=response,
            **common,
        )
    assert raised.value.reason_code == "request_malformed"

    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.normalize_fixture(
            _schema(),
            profile,
            upstream_request=request,
            upstream_response=cast(Mapping[str, object], []),
            **common,
        )
    assert raised.value.reason_code == "response_malformed"


def test_probability_label_identity_is_exact() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict) and isinstance(answers["failure_kind"], dict)
    answers["failure_kind"]["probabilities"] = {
        "configuration": 0.1,
        "transient": 0.9,
    }
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "response_semantic_mismatch"


def test_replay_requires_a_typed_record() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.replay_normalization(
            cast(normalizer.NormalizationRecord, {}),
            _schema(),
            profile,
            request_id="fixture-request-1",
            task_kind="failure_classification",
            request_schema_version="jev-systemone-observed-request/2026-09-27",
            response_schema_version="jev-systemone-observed-response/2026-09-27",
            upstream_request=request,
            upstream_response=response,
        )
    assert raised.value.reason_code == "replay_mismatch"


def test_errors_are_bounded_deterministic_and_do_not_expose_payloads() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    secret = "private-token-value-never-log"
    request["state"] = secret
    response[secret] = {"answers": secret}

    errors: list[normalizer.NormalizationError] = []
    for _ in range(2):
        with pytest.raises(normalizer.NormalizationError) as raised:
            _normalize(profile, request, response, request_id="safe-reference")
        errors.append(raised.value)

    assert str(errors[0]) == str(errors[1])
    assert errors[0].error_reference == errors[1].error_reference
    assert secret not in str(errors[0])
    assert "private" not in str(errors[0])
    assert len(str(errors[0])) <= 160


def test_replay_rejects_tamper_and_changed_inputs() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    record = _normalize(profile, request, response)
    tampered = copy.deepcopy(record.to_dict())
    tampered["record_digest"] = _digest("tampered")

    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.NormalizationRecord.from_dict(tampered)
    assert raised.value.reason_code == "replay_mismatch"

    changed = copy.deepcopy(response)
    usage = changed["usage"]
    assert isinstance(usage, dict)
    usage["input_tokens"] = 49
    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.replay_normalization(
            record,
            _schema(),
            profile,
            request_id="fixture-request-1",
            task_kind="failure_classification",
            request_schema_version="jev-systemone-observed-request/2026-09-27",
            response_schema_version="jev-systemone-observed-response/2026-09-27",
            upstream_request=request,
            upstream_response=changed,
        )
    assert raised.value.reason_code == "replay_mismatch"


def test_replay_parser_rejects_resealed_unsafe_shapes_and_flags() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    record = _normalize(profile, request, response)

    malformed_documents: list[dict[str, object]] = []

    unsafe = record.to_dict()
    unsafe["network_accessed"] = True
    malformed_documents.append(_reseal_record(unsafe))

    empty = record.to_dict()
    empty["outcomes"] = []
    malformed_documents.append(_reseal_record(empty))

    wrong_version = record.to_dict()
    wrong_version["version"] = "typed-decision-adapter-normalization-record/v2"
    malformed_documents.append(_reseal_record(wrong_version))

    bad_binding = record.to_dict()
    binding = bad_binding["binding"]
    assert isinstance(binding, dict)
    binding["capability_digest"] = "not-a-digest"
    malformed_documents.append(_reseal_record(bad_binding))

    no_probabilities = record.to_dict()
    outcomes = no_probabilities["outcomes"]
    assert isinstance(outcomes, list) and isinstance(outcomes[0], dict)
    outcomes[0]["probabilities"] = []
    malformed_documents.append(_reseal_record(no_probabilities))

    invalid_disposition = record.to_dict()
    outcomes = invalid_disposition["outcomes"]
    assert isinstance(outcomes, list) and isinstance(outcomes[0], dict)
    outcomes[0]["disposition"] = "invalid"
    malformed_documents.append(_reseal_record(invalid_disposition))

    invalid_outcome = record.to_dict()
    outcomes = invalid_outcome["outcomes"]
    assert isinstance(outcomes, list) and isinstance(outcomes[0], dict)
    outcomes[0]["outcome"] = {"ambiguous": True}
    malformed_documents.append(_reseal_record(invalid_outcome))

    malformed_digest = record.to_dict()
    malformed_digest["record_digest"] = "not-a-digest"
    malformed_documents.append(malformed_digest)

    for document in malformed_documents:
        with pytest.raises(normalizer.NormalizationError) as raised:
            normalizer.NormalizationRecord.from_dict(document)
        assert raised.value.reason_code == "replay_mismatch"


def test_record_and_nested_values_are_immutable() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    record = _normalize(profile, request, response)
    with pytest.raises((AttributeError, TypeError)):
        record.ready = False  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        record.outcomes[0].probabilities[0].probability = 0.9  # type: ignore[misc]
