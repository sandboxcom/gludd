"""Adversarial properties for the offline typed-decision normalizer."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import unicodedata
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
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


def _load(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _schema() -> dict[str, object]:
    return _load(SCHEMA_PATH)


def _bundle() -> dict[str, object]:
    return _load(EXAMPLES_PATH)


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
    selected = next(
        item
        for item in examples
        if isinstance(item, dict) and item.get("name") == name
    )
    request = copy.deepcopy(selected["upstream_request"])
    response = copy.deepcopy(selected["upstream_response"])
    assert isinstance(request, dict) and isinstance(response, dict)
    return request, response


def _normalize(
    profile: dict[str, object],
    request: dict[str, object],
    response: dict[str, object],
) -> normalizer.NormalizationRecord:
    return normalizer.normalize_fixture(
        _schema(),
        profile,
        request_id="property-request-1",
        task_kind="failure_classification",
        request_schema_version="jev-systemone-observed-request/2026-09-27",
        response_schema_version="jev-systemone-observed-response/2026-09-27",
        upstream_request=request,
        upstream_response=response,
    )


def _replay(
    record: normalizer.NormalizationRecord,
    profile: dict[str, object],
    request: dict[str, object],
    response: dict[str, object],
) -> normalizer.NormalizationRecord:
    return normalizer.replay_normalization(
        record,
        _schema(),
        profile,
        request_id="property-request-1",
        task_kind="failure_classification",
        request_schema_version="jev-systemone-observed-request/2026-09-27",
        response_schema_version="jev-systemone-observed-response/2026-09-27",
        upstream_request=request,
        upstream_response=response,
    )


def _reseal(document: dict[str, object]) -> dict[str, object]:
    body = {key: value for key, value in document.items() if key != "record_digest"}
    document["record_digest"] = normalizer.canonical_digest(body)
    return document


SAFE_UNICODE = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cc", "Cf", "Cs", "Co", "Cn"),
        blacklist_characters=("/", "\\"),
    ),
    min_size=1,
    max_size=18,
).map(lambda value: unicodedata.normalize("NFC", value))


@st.composite
def _distinct_labels(draw: st.DrawFn) -> tuple[str, str, str]:
    values = draw(
        st.lists(SAFE_UNICODE, min_size=3, max_size=3, unique=True),
    )
    return values[0], values[1], values[2]


def test_contract_schema_digest_is_a_code_owned_exact_pin() -> None:
    assert normalizer.canonical_digest(_schema()) == normalizer.CONTRACT_SCHEMA_DIGEST


@given(_distinct_labels(), SAFE_UNICODE)
@settings(max_examples=24, deadline=None)
def test_nfc_unicode_identifiers_are_deterministic_and_round_trip(
    labels: tuple[str, str, str],
    question_id: str,
) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    questions = request["questions"]
    answers = response["answers"]
    assert isinstance(questions, dict) and isinstance(answers, dict)
    questions.clear()
    answers.clear()
    questions[question_id] = {
        "type": "choice",
        "instructions": "Choose the exact label.",
        "criteria": {label: f"criterion-{index}" for index, label in enumerate(labels)},
    }
    answers[question_id] = {
        "choice": labels[1],
        "probabilities": {labels[2]: 0.1, labels[1]: 0.8, labels[0]: 0.1},
        "confidence": 0.7,
    }

    first = _normalize(profile, request, response)
    questions[question_id]["criteria"] = dict(
        reversed(list(questions[question_id]["criteria"].items())),
    )
    answers[question_id]["probabilities"] = dict(
        reversed(list(answers[question_id]["probabilities"].items())),
    )
    second = _normalize(profile, request, response)

    assert first == second
    assert first.outcomes[0].question_id == question_id
    assert tuple(item.label for item in first.outcomes[0].probabilities) == tuple(
        sorted(labels),
    )
    assert _replay(first, profile, request, response) == first


@pytest.mark.parametrize(
    "unsafe",
    ["e\u0301", "unsafe\u0000id", "unsafe\u202eid", "unsafe\ud800id"],
)
def test_ambiguous_or_control_unicode_identifiers_fail_closed(unsafe: str) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    questions = request["questions"]
    answers = response["answers"]
    assert isinstance(questions, dict) and isinstance(answers, dict)
    question = questions.pop("failure_kind")
    answer = answers.pop("failure_kind")
    questions[unsafe] = question
    answers[unsafe] = answer

    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "request_malformed"
    assert unsafe not in str(raised.value)


@given(
    st.floats(
        min_value=0,
        max_value=1,
        allow_nan=False,
        allow_infinity=False,
        width=64,
    ).filter(lambda value: value != 0.5),
)
@settings(max_examples=32, deadline=None)
def test_finite_probability_edges_have_canonical_typed_numbers(probability: float) -> None:
    profile = _profile(1)
    request, response = _example("laya-noul-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict) and isinstance(answers["is_transient"], dict)
    answers["is_transient"]["noul"] = probability

    record = _normalize(profile, request, response)
    values = record.outcomes[0].probabilities
    assert values[0].probability + values[1].probability == pytest.approx(1.0)
    assert all(
        value.probability != 0.0 or math.copysign(1.0, value.probability) == 1.0
        for value in values
    )
    assert normalizer.NormalizationRecord.from_dict(record.to_dict()) == record


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), 2**63, 10**1000],
)
def test_nonfinite_and_overflow_values_fail_closed_without_echo(value: object) -> None:
    profile = _profile(1)
    request, response = _example("laya-noul-normalization")
    answers = response["answers"]
    assert isinstance(answers, dict) and isinstance(answers["is_transient"], dict)
    answers["is_transient"]["noul"] = value

    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code in {
        "response_malformed",
        "response_semantic_mismatch",
    }
    assert len(str(raised.value)) <= 160


def test_usage_counter_overflow_fails_closed() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    usage = response["usage"]
    assert isinstance(usage, dict)
    usage["input_tokens"] = 2**63
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "response_malformed"


@pytest.mark.parametrize(
    ("kind", "mutation"),
    [
        (
            "choice_tie",
            {"choice": "configuration", "probabilities": {
                "configuration": 0.45,
                "transient": 0.45,
                "unknown": 0.1,
            }, "confidence": 0.5},
        ),
        (
            "score_duplicate",
            ["low", "medium", "medium"],
        ),
    ],
)
def test_ties_and_duplicate_score_levels_fail_closed(
    kind: str,
    mutation: object,
) -> None:
    profile = _profile(0)
    if kind == "choice_tie":
        request, response = _example("openjev-choice-normalization")
        answers = response["answers"]
        assert isinstance(answers, dict)
        answers["failure_kind"] = mutation
    else:
        request, response = _example("openjev-score-normalization")
        questions = request["questions"]
        assert isinstance(questions, dict) and isinstance(questions["severity"], dict)
        questions["severity"]["criteria"] = mutation
    with pytest.raises(normalizer.NormalizationError):
        _normalize(profile, request, response)


@pytest.mark.parametrize("target", ["question_id", "choice_label"])
def test_identifiers_that_cannot_fit_normalized_schema_fail_closed(target: str) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    questions = request["questions"]
    answers = response["answers"]
    assert isinstance(questions, dict) and isinstance(answers, dict)
    oversized = "x" * 161
    if target == "question_id":
        question = questions.pop("failure_kind")
        answer = answers.pop("failure_kind")
        questions[oversized] = question
        answers[oversized] = answer
    else:
        question = questions["failure_kind"]
        answer = answers["failure_kind"]
        assert isinstance(question, dict) and isinstance(answer, dict)
        criteria = question["criteria"]
        probabilities = answer["probabilities"]
        assert isinstance(criteria, dict) and isinstance(probabilities, dict)
        criteria[oversized] = "Never selected."
        probabilities[oversized] = 0.0
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "request_malformed"


@given(st.permutations(("choice", "noul", "score")))
@settings(max_examples=12, deadline=None)
def test_question_answer_and_probability_permutations_preserve_digest(
    order: list[str],
) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    request["questions"] = {
        "choice": {
            "type": "choice",
            "instructions": "Choose.",
            "criteria": {"a": "A", "b": "B"},
        },
        "noul": {"type": "noul", "instructions": "Decide."},
        "score": {
            "type": "score",
            "instructions": "Score.",
            "criteria": ["low", "high"],
        },
    }
    response["answers"] = {
        "choice": {
            "choice": "b",
            "probabilities": {"a": 0.2, "b": 0.8},
            "confidence": 0.6,
        },
        "noul": {"noul": 0.75},
        "score": {
            "score": 0.25,
            "legend": {"0": "low", "1": "high"},
            "probabilities": {"0": 0.75, "1": 0.25},
            "confidence": 0.4,
        },
    }
    baseline = _normalize(profile, request, response)
    questions = request["questions"]
    answers = response["answers"]
    assert isinstance(questions, dict) and isinstance(answers, dict)
    request["questions"] = {key: questions[key] for key in order}
    response["answers"] = {key: answers[key] for key in reversed(order)}

    assert _normalize(profile, request, response) == baseline


@given(st.integers(min_value=33, max_value=96))
@settings(max_examples=16, deadline=None)
def test_deep_state_is_rejected_before_schema_recursion(depth: int) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    capabilities = profile["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["state_shapes"] = ["array"]
    state: object = "secret-deep-leaf"
    for _ in range(depth):
        state = [state]
    request["state"] = state

    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "capability_mismatch"
    assert "secret-deep-leaf" not in str(raised.value)


@given(st.integers(min_value=65_537, max_value=70_000))
@settings(max_examples=8, deadline=None)
def test_oversized_state_is_rejected_without_payload_echo(size: int) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    secret = "s" * size
    request["state"] = secret
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert raised.value.reason_code == "capability_mismatch"
    assert secret[:128] not in str(raised.value)


def test_cross_profile_replay_is_reported_only_as_replay_mismatch() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    record = _normalize(profile, request, response)
    other = _profile(1)

    with pytest.raises(normalizer.NormalizationError) as raised:
        _replay(record, other, request, response)
    assert raised.value.reason_code == "replay_mismatch"
    assert "model" not in str(raised.value)


@given(
    st.sampled_from(
        (
            "additionalProperties",
            "normalization_version",
            "response_schema_versions",
        ),
    ),
)
@settings(max_examples=9, deadline=None)
def test_schema_and_compatibility_confusion_fail_closed(field: str) -> None:
    schema = _schema()
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    if field == "additionalProperties":
        definitions = schema["$defs"]
        assert isinstance(definitions, dict) and isinstance(definitions["wireResponse"], dict)
        definitions["wireResponse"]["additionalProperties"] = True
    else:
        compatibility = profile["compatibility"]
        assert isinstance(compatibility, dict)
        compatibility[field] = (
            "gludd.typed-decision-normalization/v2"
            if field == "normalization_version"
            else ["jev-systemone-observed-request/2026-09-27"]
        )

    with pytest.raises(normalizer.NormalizationError):
        normalizer.normalize_fixture(
            schema,
            profile,
            request_id="property-request-1",
            task_kind="failure_classification",
            request_schema_version="jev-systemone-observed-request/2026-09-27",
            response_schema_version="jev-systemone-observed-response/2026-09-27",
            upstream_request=request,
            upstream_response=response,
        )


@given(
    st.sampled_from(
        (
            "capability_digest",
            "profile_digest",
            "request_digest",
            "response_digest",
        ),
    ),
    st.integers(min_value=7, max_value=70),
)
@settings(max_examples=24, deadline=None)
def test_resealed_digest_tamper_never_replays(field: str, offset: int) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    record = _normalize(profile, request, response)
    document = record.to_dict()
    binding = document["binding"]
    assert isinstance(binding, dict) and isinstance(binding[field], str)
    original = binding[field]
    index = min(offset, len(original) - 1)
    replacement = "a" if original[index] != "a" else "b"
    binding[field] = original[:index] + replacement + original[index + 1 :]
    parsed = normalizer.NormalizationRecord.from_dict(_reseal(document))

    with pytest.raises(normalizer.NormalizationError) as raised:
        _replay(parsed, profile, request, response)
    assert raised.value.reason_code == "replay_mismatch"


@given(st.sampled_from(("network_accessed", "runtime_invoked", "model_invoked", "admission_performed")))
@settings(max_examples=8, deadline=None)
def test_every_resealed_side_effect_flag_fails_closed(flag: str) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    document = _normalize(profile, request, response).to_dict()
    document[flag] = True
    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.NormalizationRecord.from_dict(_reseal(document))
    assert raised.value.reason_code == "replay_mismatch"


@given(st.text(min_size=8, max_size=80))
@settings(max_examples=24, deadline=None)
def test_arbitrary_unknown_field_secrets_never_reach_errors(secret: str) -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    marker = f"private::{secret}::value"
    response[marker] = {"nested": marker}
    with pytest.raises(normalizer.NormalizationError) as raised:
        _normalize(profile, request, response)
    assert marker not in str(raised.value)
    assert len(str(raised.value)) <= 160


def test_resealed_internal_identity_confusion_is_rejected_during_parse() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    document = _normalize(profile, request, response).to_dict()
    outcomes = document["outcomes"]
    assert isinstance(outcomes, list) and isinstance(outcomes[0], dict)
    outcomes[0]["identity_fingerprint"] = _digest("different-identity")
    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.NormalizationRecord.from_dict(_reseal(document))
    assert raised.value.reason_code == "replay_mismatch"


def test_resealed_numeric_outcome_overflow_is_bounded() -> None:
    profile = _profile(0)
    request, response = _example("openjev-score-normalization")
    document = _normalize(profile, request, response).to_dict()
    outcomes = document["outcomes"]
    assert isinstance(outcomes, list) and isinstance(outcomes[0], dict)
    outcomes[0]["outcome"] = 10**1000
    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.NormalizationRecord.from_dict(_reseal(document))
    assert raised.value.reason_code == "replay_mismatch"
    assert len(str(raised.value)) <= 160


def test_duplicate_outcome_identity_and_deep_record_fail_closed() -> None:
    profile = _profile(0)
    request, response = _example("openjev-choice-normalization")
    record = _normalize(profile, request, response)

    duplicate = record.to_dict()
    outcomes = duplicate["outcomes"]
    assert isinstance(outcomes, list)
    outcomes.append(copy.deepcopy(outcomes[0]))
    with pytest.raises(normalizer.NormalizationError):
        normalizer.NormalizationRecord.from_dict(_reseal(duplicate))

    reordered = record.to_dict()
    outcomes = reordered["outcomes"]
    assert isinstance(outcomes, list) and isinstance(outcomes[0], dict)
    probabilities = outcomes[0]["probabilities"]
    assert isinstance(probabilities, list)
    probabilities.reverse()
    with pytest.raises(normalizer.NormalizationError):
        normalizer.NormalizationRecord.from_dict(_reseal(reordered))

    deep = record.to_dict()
    outcomes = deep["outcomes"]
    assert isinstance(outcomes, list) and isinstance(outcomes[0], dict)
    nested: object = "hidden"
    for _ in range(12):
        nested = [nested]
    outcomes[0]["outcome"] = nested
    with pytest.raises(normalizer.NormalizationError) as raised:
        normalizer.NormalizationRecord.from_dict(_reseal(deep))
    assert raised.value.reason_code == "replay_mismatch"
    assert "hidden" not in str(raised.value)


def test_non_mapping_replay_document_is_bounded() -> None:
    with pytest.raises(normalizer.NormalizationError):
        normalizer.NormalizationRecord.from_dict(cast(Mapping[str, object], []))
