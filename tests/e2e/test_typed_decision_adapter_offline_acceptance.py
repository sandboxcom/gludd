"""Hermetic OpenJev/Laya acceptance across every offline adapter stage."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import itertools
import json
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scripts import accept_typed_decision_adapter_candidate as acceptance
from scripts import check_typed_decision_adapter_update as update
from scripts import normalize_typed_decision_adapter_fixture as normalizer
from scripts import plan_typed_decision_adapter_batch as planner

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs/releases/typed-decision-adapter-contract-v1.schema.json"
EXAMPLES_PATH = ROOT / "docs/releases/typed-decision-adapter-contract-v1.examples.json"
EXPECTED_SCHEMA_DIGEST = (
    "sha256:1082d5b5a024f62514fd0bf6c23f2cbe2deaed2f26900010082c98dabdb60468"
)
EXPECTED_ACCEPTANCE_DIGEST = (
    "sha256:f88d65ccdf11664ed81062b702ed7e7dddab0fc16bd58bdfbbe02e77d0f3873c"
)
EXPECTED_STAGE_DIGESTS = {
    "openjev": (
        "sha256:71b2b22c984e539fb059eeb8162cfbad1a276ded507655c791ff2c9522934bd4",
        "sha256:26c00b82ad67b135c476c5282a44467f0c72aece18b2136bb806d5287ea72a5a",
    ),
    "laya": (
        "sha256:81773ae7b825ce8db2891224e3f88963aa17c2951c6b2229a27d8e37bd5a4ada",
        "sha256:4ec7fa28d05ee5980e75d0d08b1173a28f5ee5fba2c84ca074efc088c646cf73",
    ),
}


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


def _load_object(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _schema() -> dict[str, object]:
    return _load_object(SCHEMA_PATH)


def _bundle() -> dict[str, object]:
    return _load_object(EXAMPLES_PATH)


def _profiles(index: int, provider: str) -> tuple[dict[str, object], dict[str, object]]:
    profiles = _bundle()["profiles"]
    assert isinstance(profiles, list) and isinstance(profiles[index], dict)
    baseline = _replace_digests(
        copy.deepcopy(profiles[index]),
        _digests(f"acceptance-{provider}-baseline"),
    )
    assert isinstance(baseline, dict)
    baseline["example_only"] = False
    source = baseline["source_pin"]
    identity = baseline["identity"]
    residency = baseline["residency"]
    assert isinstance(source, dict) and isinstance(identity, dict)
    assert isinstance(residency, dict)
    source["release"] = "v1.0.0"
    source["commit"] = hashlib.sha1(f"{provider}-baseline".encode()).hexdigest()
    identity["model_revision"] = f"{provider}-model@v1.0.0"
    if residency["ownership"] == "universal_model_worker":
        residency["preload_identity_fingerprints"] = [identity["fingerprint"]]
        residency["max_loaded_identities"] = 2
        residency["resident_byte_budget"] = 8192

    candidate = copy.deepcopy(baseline)
    candidate_source = candidate["source_pin"]
    candidate_identity = candidate["identity"]
    candidate_residency = candidate["residency"]
    assert isinstance(candidate_source, dict) and isinstance(candidate_identity, dict)
    assert isinstance(candidate_residency, dict)
    candidate_source["release"] = "v1.0.1"
    candidate_source["commit"] = hashlib.sha1(
        f"{provider}-candidate".encode(),
    ).hexdigest()
    candidate_source["source_archive_digest"] = _digest(
        f"{provider}-candidate-archive",
    )
    candidate_source["reviewed_at"] = "2026-09-28"
    candidate_identity["fingerprint"] = _digest(f"{provider}-candidate-fingerprint")
    if candidate_residency["ownership"] == "universal_model_worker":
        candidate_residency["preload_identity_fingerprints"] = [
            candidate_identity["fingerprint"],
        ]
    return baseline, candidate


def _example(name: str, request_id: str) -> acceptance.NormalizationFixture:
    examples = _bundle()["normalization_examples"]
    assert isinstance(examples, list)
    item = next(
        value
        for value in examples
        if isinstance(value, dict) and value.get("name") == name
    )
    request = copy.deepcopy(item["upstream_request"])
    response = copy.deepcopy(item["upstream_response"])
    assert isinstance(request, dict) and isinstance(response, dict)
    return acceptance.NormalizationFixture(
        fixture_id=name,
        request_id=request_id,
        request_schema_version="jev-systemone-observed-request/2026-09-27",
        response_schema_version="jev-systemone-observed-response/2026-09-27",
        upstream_request=request,
        upstream_response=response,
    )


def _question_facts(request: Mapping[str, object]) -> tuple[tuple[str, ...], int, int]:
    questions = request["questions"]
    assert isinstance(questions, dict)
    kinds: list[str] = []
    choice_options = 0
    score_levels = 0
    for question in questions.values():
        assert isinstance(question, dict) and isinstance(question.get("type"), str)
        kind = question["type"]
        kinds.append(kind)
        criteria = question["criteria"]
        if kind == "choice":
            assert isinstance(criteria, dict)
            choice_options = max(choice_options, len(criteria))
        elif kind == "score":
            assert isinstance(criteria, list)
            score_levels = max(score_levels, len(criteria))
    return tuple(sorted(set(kinds))), choice_options, score_levels


def _batch_request(
    profile: Mapping[str, object],
    fixture: acceptance.NormalizationFixture,
) -> planner.BatchRequest:
    identity = profile["identity"]
    assert isinstance(identity, dict) and isinstance(identity["contract_version"], str)
    assert isinstance(profile["profile_id"], str)
    state = fixture.upstream_request["state"]
    questions = fixture.upstream_request["questions"]
    assert isinstance(questions, dict)
    kinds, choice_options, score_levels = _question_facts(fixture.upstream_request)
    return planner.BatchRequest(
        request_id=fixture.request_id,
        profile_id=profile["profile_id"],
        tenant_digest=_digest("acceptance-tenant"),
        schema_version=identity["contract_version"],
        question_schema_digest=update.canonical_digest(
            fixture.upstream_request["questions"],
        ),
        token_budget=128,
        trust_sensitivity_class="confidential",
        deadline_bucket_ms=1000,
        question_types=kinds,
        state_shape="string",
        state_bytes=len(update.canonical_json_bytes(state)),
        question_count=len(questions),
        max_choice_options=choice_options,
        max_score_levels=score_levels,
        request_bytes=len(update.canonical_json_bytes(fixture.upstream_request)),
    )


def _lane(
    tmp_path: Path,
    *,
    provider: str,
    profile_index: int,
    examples: tuple[tuple[str, str], ...],
) -> acceptance.AdapterAcceptanceLane:
    baseline, candidate = _profiles(profile_index, provider)
    shadow = copy.deepcopy(candidate)
    shadow["profile_state"] = "shadow"
    fixtures = tuple(_example(name, request_id) for name, request_id in examples)
    identity = candidate["identity"]
    residency = candidate["residency"]
    assert isinstance(identity, dict) and isinstance(identity["fingerprint"], str)
    assert isinstance(residency, dict)
    measurements = (
        ()
        if residency["ownership"] == "external_endpoint"
        else (
            planner.IdentityMeasurement(
                fingerprint=identity["fingerprint"],
                measured_bytes=1024,
            ),
        )
    )
    return acceptance.AdapterAcceptanceLane(
        lane_id=provider,
        baseline_profile=baseline,
        candidate_profile=candidate,
        shadow_profile=shadow,
        event_id=f"acceptance-{provider}-2026-09-28",
        recorded_at="2026-09-28T12:30:45Z",
        actor_id=f"secret-{provider}-operator@example.invalid",
        prepared_update_path=tmp_path / f"{provider}-prepared-update.json",
        requests=tuple(_batch_request(shadow, fixture) for fixture in fixtures),
        bounds=planner.PlannerBounds(
            max_requests=8,
            max_profiles=2,
            max_loaded_identities=2,
            resident_byte_budget=8192,
            max_total_request_bytes=131072,
        ),
        snapshot=planner.ResidencySnapshot(loaded=()),
        measurements=measurements,
        normalization_fixtures=fixtures,
    )


def _lanes(tmp_path: Path) -> tuple[acceptance.AdapterAcceptanceLane, ...]:
    return (
        _lane(
            tmp_path,
            provider="openjev",
            profile_index=0,
            examples=(
                ("openjev-choice-normalization", "acceptance-choice-1"),
                ("openjev-score-normalization", "acceptance-score-1"),
            ),
        ),
        _lane(
            tmp_path,
            provider="laya",
            profile_index=1,
            examples=(("laya-noul-normalization", "acceptance-noul-1"),),
        ),
    )


def test_exact_pinned_pipeline_is_redacted_idempotent_and_replayable(
    tmp_path: Path,
) -> None:
    schema = _schema()
    lanes = _lanes(tmp_path)

    first = acceptance.run_offline_acceptance(schema, lanes)
    second = acceptance.run_offline_acceptance(schema, lanes)

    assert first == second
    assert acceptance.verify_offline_acceptance(first.to_dict(), schema, lanes) == first
    assert first.schema_digest == EXPECTED_SCHEMA_DIGEST
    assert first.receipt_digest == EXPECTED_ACCEPTANCE_DIGEST
    lane_ids = {
        acceptance._reference("lane_id", lane.lane_id): lane.lane_id for lane in lanes
    }
    stage_digests = {
        lane_ids[lane.lane_reference]: (
            lane.update_plan_digest,
            lane.batch_plan_digest,
        )
        for lane in first.lanes
    }
    assert stage_digests == EXPECTED_STAGE_DIGESTS
    assert {kind for lane in first.lanes for kind in lane.normalization_kinds} == {
        "choice",
        "noul",
        "score",
    }
    assert all(lane.persistence_idempotent for lane in first.lanes)
    assert all(lane.rollback_verified for lane in first.lanes)
    assert all(lane.replay_verified for lane in first.lanes)
    assert all(
        getattr(first, flag) is False for flag in acceptance.INERT_SIDE_EFFECT_FLAGS
    )
    assert all(
        stat.S_IMODE(lane.prepared_update_path.stat().st_mode) == 0o600
        for lane in lanes
    )

    rendered = update.canonical_json_bytes(first.to_dict())
    for secret in (
        b"secret-openjev-operator",
        b"secret-laya-operator",
        b"The dependency timed out",
        b"The error disappeared",
        b"The queue is growing",
    ):
        assert secret not in rendered
    assert b'"answers":' not in rendered
    assert b'"state":' not in rendered
    assert b'"endpoint":' not in rendered


@pytest.mark.parametrize(
    ("handoff", "expected"),
    [
        ("update", "update"),
        ("residency", "update"),
        ("projection", "shadow_projection"),
        ("batch", "batch"),
        ("batch-normalization", "batch_normalization_link"),
        ("normalization", "normalization"),
    ],
)
def test_every_stage_handoff_fails_closed_and_redacted(
    tmp_path: Path,
    handoff: str,
    expected: str,
) -> None:
    lanes = list(_lanes(tmp_path))
    lane_index = 1 if handoff == "residency" else 0
    lane = lanes[lane_index]
    if handoff == "update":
        candidate = copy.deepcopy(lane.candidate_profile)
        redaction = candidate["redaction"]
        assert isinstance(redaction, dict)
        redaction["observer_mode"] = "mutable"
        lane = dataclasses.replace(lane, candidate_profile=candidate)
    elif handoff == "residency":
        candidate = copy.deepcopy(lane.candidate_profile)
        residency = candidate["residency"]
        assert isinstance(residency, dict)
        residency["resident_byte_budget"] = 4096
        lane = dataclasses.replace(lane, candidate_profile=candidate)
    elif handoff == "projection":
        shadow = copy.deepcopy(lane.shadow_profile)
        limits = shadow["limits"]
        assert isinstance(limits, dict)
        limits["queue_depth"] = 1
        lane = dataclasses.replace(lane, shadow_profile=shadow)
    elif handoff == "batch":
        request = dataclasses.replace(lane.requests[0], profile_id="missing-profile")
        lane = dataclasses.replace(lane, requests=(request, *lane.requests[1:]))
    elif handoff == "batch-normalization":
        request = dataclasses.replace(
            lane.requests[0],
            question_schema_digest=_digest("wrong-question-schema"),
        )
        lane = dataclasses.replace(lane, requests=(request, *lane.requests[1:]))
    else:
        fixture = lane.normalization_fixtures[0]
        response = copy.deepcopy(fixture.upstream_response)
        assert isinstance(response, dict)
        response["secret_extension"] = "must-never-leak"
        fixture = dataclasses.replace(fixture, upstream_response=response)
        lane = dataclasses.replace(
            lane,
            normalization_fixtures=(fixture, *lane.normalization_fixtures[1:]),
        )
    lanes[lane_index] = lane

    with pytest.raises(
        acceptance.AcceptanceError,
        match=rf"offline acceptance rejected: {expected}$",
    ) as caught:
        acceptance.run_offline_acceptance(_schema(), tuple(lanes))
    assert "must-never-leak" not in str(caught.value)
    assert "secret-openjev-operator" not in str(caught.value)


def test_atomic_persistence_conflict_is_fail_closed_without_overwrite(tmp_path: Path) -> None:
    lanes = _lanes(tmp_path)
    target = lanes[0].prepared_update_path
    target.write_text("conflicting-secret-evidence\n", encoding="utf-8")
    target.chmod(0o600)

    with pytest.raises(
        acceptance.AcceptanceError,
        match=r"offline acceptance rejected: persistence$",
    ) as caught:
        acceptance.run_offline_acceptance(_schema(), lanes)

    assert str(caught.value) == "offline acceptance rejected: persistence"
    assert target.read_text(encoding="utf-8") == "conflicting-secret-evidence\n"


def test_receipt_tamper_and_every_inert_flag_fail_closed(tmp_path: Path) -> None:
    schema = _schema()
    lanes = _lanes(tmp_path)
    receipt = acceptance.run_offline_acceptance(schema, lanes).to_dict()

    mutations: list[dict[str, object]] = []
    for flag in acceptance.INERT_SIDE_EFFECT_FLAGS:
        document = copy.deepcopy(receipt)
        document[flag] = True
        mutations.append(document)
    unknown = copy.deepcopy(receipt)
    unknown["unknown"] = "must-never-leak"
    mutations.append(unknown)
    digest = copy.deepcopy(receipt)
    digest["receipt_digest"] = _digest("tampered-receipt")
    mutations.append(digest)
    rollback = copy.deepcopy(receipt)
    lane_documents = rollback["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    lane_documents[0]["rollback_digest"] = _digest("weakened-rollback")
    mutations.append(rollback)
    normalization = copy.deepcopy(receipt)
    lane_documents = normalization["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    record_digests = lane_documents[0]["normalization_record_digests"]
    assert isinstance(record_digests, list)
    record_digests[0] = _digest("tampered-normalization")
    mutations.append(normalization)

    for document in mutations:
        with pytest.raises(
            acceptance.AcceptanceError,
            match=r"offline acceptance rejected: receipt$",
        ) as caught:
            acceptance.verify_offline_acceptance(document, schema, lanes)
        assert "must-never-leak" not in str(caught.value)


def test_input_lifecycle_and_fixture_coverage_boundaries_fail_closed(
    tmp_path: Path,
) -> None:
    schema = _schema()
    lanes = _lanes(tmp_path)

    duplicate = dataclasses.replace(lanes[1], lane_id=lanes[0].lane_id)
    invalid_id = dataclasses.replace(lanes[0], lane_id="invalid lane id")
    disabled_shadow = copy.deepcopy(lanes[0].shadow_profile)
    assert isinstance(disabled_shadow, dict)
    disabled_shadow["profile_state"] = "disabled"
    invalid_shadow = dataclasses.replace(lanes[0], shadow_profile=disabled_shadow)
    no_capacity = dataclasses.replace(
        lanes[1],
        bounds=planner.PlannerBounds(
            max_requests=8,
            max_profiles=2,
            max_loaded_identities=0,
            resident_byte_budget=0,
            max_total_request_bytes=131072,
        ),
    )

    cases = (
        ((), "inputs"),
        ((lanes[0], duplicate), "inputs"),
        ((invalid_id, lanes[1]), "lane"),
        ((invalid_shadow, lanes[1]), "shadow_projection"),
        ((lanes[0], no_capacity), "batch"),
        ((lanes[0],), "fixture_coverage"),
    )
    for candidate_lanes, stage in cases:
        with pytest.raises(
            acceptance.AcceptanceError,
            match=rf"offline acceptance rejected: {stage}$",
        ):
            acceptance.run_offline_acceptance(schema, candidate_lanes)


def test_receipt_shape_is_strict_before_stage_replay(tmp_path: Path) -> None:
    schema = _schema()
    lanes = _lanes(tmp_path)
    receipt = acceptance.run_offline_acceptance(schema, lanes).to_dict()
    mutations: list[Mapping[str, object]] = [cast(Mapping[str, object], [])]

    bad_lanes = copy.deepcopy(receipt)
    bad_lanes["lanes"] = "not-a-list"
    mutations.append(bad_lanes)
    bad_lane = copy.deepcopy(receipt)
    bad_lane["lanes"] = [{}]
    mutations.append(bad_lane)
    bad_version = copy.deepcopy(receipt)
    lane_documents = bad_version["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    lane_documents[0]["version"] = "unknown"
    mutations.append(bad_version)
    bad_proof = copy.deepcopy(receipt)
    lane_documents = bad_proof["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    lane_documents[0]["persistence_idempotent"] = False
    mutations.append(bad_proof)
    bad_digest = copy.deepcopy(receipt)
    lane_documents = bad_digest["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    lane_documents[0]["batch_plan_digest"] = "not-a-digest"
    mutations.append(bad_digest)
    bad_id = copy.deepcopy(receipt)
    lane_documents = bad_id["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    lane_documents[0]["lane_id"] = "invalid lane"
    mutations.append(bad_id)
    bad_kinds = copy.deepcopy(receipt)
    lane_documents = bad_kinds["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    lane_documents[0]["normalization_kinds"] = []
    mutations.append(bad_kinds)

    for document in mutations:
        with pytest.raises(
            acceptance.AcceptanceError,
            match=r"offline acceptance rejected: receipt$",
        ):
            acceptance.verify_offline_acceptance(document, schema, lanes)


def test_internal_handoff_shapes_reject_ambiguous_or_noncanonical_inputs(
    tmp_path: Path,
) -> None:
    lane = _lanes(tmp_path)[0]
    request = lane.requests[0]
    fixture = lane.normalization_fixtures[0]

    with pytest.raises(acceptance.AcceptanceError, match="rejected: shape"):
        acceptance._mapping(None, stage="shape")
    with pytest.raises(acceptance.AcceptanceError, match="rejected: shape"):
        acceptance._string({}, "missing", stage="shape")

    enabled_candidate = copy.deepcopy(lane.candidate_profile)
    assert isinstance(enabled_candidate, dict)
    enabled_candidate["profile_state"] = "shadow"
    with pytest.raises(acceptance.AcceptanceError, match="rejected: shadow_projection"):
        acceptance._validate_shadow_projection(enabled_candidate, lane.shadow_profile)
    noncanonical_shadow = copy.deepcopy(lane.shadow_profile)
    assert isinstance(noncanonical_shadow, dict)
    noncanonical_shadow["unknown"] = object()
    with pytest.raises(acceptance.AcceptanceError, match="rejected: shadow_projection"):
        acceptance._validate_shadow_projection(lane.candidate_profile, noncanonical_shadow)

    malformed_questions: tuple[Mapping[str, object], ...] = (
        {"questions": {}},
        {"questions": {"q": {"type": "unknown", "criteria": {}}}},
        {"questions": {"q": {"type": "score", "criteria": {}}}},
    )
    for upstream_request in malformed_questions:
        with pytest.raises(
            acceptance.AcceptanceError,
            match="rejected: batch_normalization_link",
        ):
            acceptance._question_facts(upstream_request)

    assert acceptance._state_shape({}) == "object"
    assert acceptance._state_shape([]) == "array"
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: batch_normalization_link",
    ):
        acceptance._state_shape(1)

    candidate = lane.candidate_profile
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: batch_normalization_link",
    ):
        acceptance._validate_batch_normalization_link(candidate, (), ())
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: batch_normalization_link",
    ):
        acceptance._validate_batch_normalization_link(
            candidate,
            (request, request),
            (fixture, fixture),
        )

    second_request = dataclasses.replace(request, request_id="second-request")
    second_fixture = dataclasses.replace(
        fixture,
        request_id="second-request",
    )
    duplicate_fixture_id = dataclasses.replace(
        second_fixture,
        fixture_id=fixture.fixture_id,
    )
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: batch_normalization_link",
    ):
        acceptance._validate_batch_normalization_link(
            candidate,
            (request, second_request),
            (fixture, duplicate_fixture_id),
        )

    mismatched_fixture = dataclasses.replace(fixture, request_id="different-request")
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: batch_normalization_link",
    ):
        acceptance._validate_batch_normalization_link(
            candidate,
            (request,),
            (mismatched_fixture,),
        )

    nonmapping_fixture = dataclasses.replace(
        fixture,
        upstream_request=cast(Mapping[str, object], []),
    )
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: batch_normalization_link",
    ):
        acceptance._validate_batch_normalization_link(
            candidate,
            (request,),
            (nonmapping_fixture,),
        )

    noncanonical_request = copy.deepcopy(fixture.upstream_request)
    assert isinstance(noncanonical_request, dict)
    noncanonical_request["opaque"] = object()
    noncanonical_fixture = dataclasses.replace(
        fixture,
        upstream_request=noncanonical_request,
    )
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: batch_normalization_link",
    ):
        acceptance._validate_batch_normalization_link(
            candidate,
            (request,),
            (noncanonical_fixture,),
        )


def test_stage_chain_rejects_every_reorder_skip_and_duplication(tmp_path: Path) -> None:
    receipt = acceptance.run_offline_acceptance(_schema(), _lanes(tmp_path)).to_dict()
    lane_documents = receipt["lanes"]
    assert isinstance(lane_documents, list) and isinstance(lane_documents[0], dict)
    chain = lane_documents[0]["stage_chain"]
    assert isinstance(chain, list)
    assert [checkpoint["stage"] for checkpoint in chain] == list(
        acceptance.ACCEPTANCE_STAGE_ORDER,
    )

    for order in itertools.permutations(range(len(chain))):
        if order == tuple(range(len(chain))):
            continue
        tampered = copy.deepcopy(receipt)
        lanes = tampered["lanes"]
        assert isinstance(lanes, list) and isinstance(lanes[0], dict)
        lanes[0]["stage_chain"] = [copy.deepcopy(chain[index]) for index in order]
        with pytest.raises(acceptance.AcceptanceError, match="rejected: receipt"):
            acceptance._validate_receipt_shape(tampered)

    for changed_chain in (chain[:-1], [*chain, copy.deepcopy(chain[-1])]):
        tampered = copy.deepcopy(receipt)
        lanes = tampered["lanes"]
        assert isinstance(lanes, list) and isinstance(lanes[0], dict)
        lanes[0]["stage_chain"] = changed_chain
        with pytest.raises(acceptance.AcceptanceError, match="rejected: receipt"):
            acceptance._validate_receipt_shape(tampered)


def test_crash_before_projection_and_failed_partial_handoff_recover_rollback(
    tmp_path: Path,
) -> None:
    schema = _schema()
    lanes = _lanes(tmp_path)
    lane = lanes[0]
    prepared = acceptance.prepare_preprojection_handoff(schema, lane)

    assert acceptance.recover_preprojection_handoff(schema, lane) == prepared
    assert prepared.phase == "prepared_before_projection"
    assert prepared.rollback_required is True
    assert prepared.rollback_target_state == "disabled"
    assert all(
        getattr(prepared, flag) is False for flag in acceptance.INERT_SIDE_EFFECT_FLAGS
    )

    invalid_shadow = copy.deepcopy(lane.shadow_profile)
    assert isinstance(invalid_shadow, dict)
    invalid_shadow["limits"] = {"partial": "handoff"}
    failed_lane = dataclasses.replace(lane, shadow_profile=invalid_shadow)
    with pytest.raises(
        acceptance.AcceptanceError,
        match="rejected: shadow_projection",
    ):
        acceptance.run_offline_acceptance(schema, (failed_lane, lanes[1]))
    assert acceptance.recover_preprojection_handoff(schema, lane) == prepared


def test_receipts_cannot_cross_wire_version_profile_or_tenant(
    tmp_path: Path,
) -> None:
    schema = _schema()
    lanes = _lanes(tmp_path)
    receipt = acceptance.run_offline_acceptance(schema, lanes).to_dict()

    version_fixture = dataclasses.replace(
        lanes[0].normalization_fixtures[0],
        request_schema_version="jev-systemone-observed-request/2099-01-01",
    )
    version_lane = dataclasses.replace(
        lanes[0],
        normalization_fixtures=(
            version_fixture,
            *lanes[0].normalization_fixtures[1:],
        ),
    )

    other_root = tmp_path / "other-profile"
    other_root.mkdir()
    profile_lane = _lane(
        other_root,
        provider="other-openjev",
        profile_index=0,
        examples=(
            ("openjev-choice-normalization", "acceptance-choice-1"),
            ("openjev-score-normalization", "acceptance-score-1"),
        ),
    )

    tenant_requests = tuple(
        dataclasses.replace(request, tenant_digest=_digest("other-tenant"))
        for request in lanes[0].requests
    )
    tenant_lane = dataclasses.replace(lanes[0], requests=tenant_requests)

    for changed_lanes in (
        (version_lane, lanes[1]),
        (profile_lane, lanes[1]),
        (tenant_lane, lanes[1]),
    ):
        with pytest.raises(acceptance.AcceptanceError):
            acceptance.verify_offline_acceptance(receipt, schema, changed_lanes)


@given(
    value=st.from_regex(
        r"[A-Za-z][A-Za-z0-9._:-]{7,63}",
        fullmatch=True,
    ),
)
@settings(max_examples=50, deadline=None)
def test_digest_references_are_domain_separated(value: str) -> None:
    references = {
        acceptance._reference(domain, value)
        for domain in ("lane_id", "fixture_id", "request_id", "stage")
    }
    assert len(references) == 4
    assert all(reference.startswith("sha256:") for reference in references)
    assert value not in references
    aliased_evidence = _digest(value)
    chain = acceptance._build_stage_chain(
        _digest(f"root-{value}"),
        {stage: aliased_evidence for stage in acceptance.ACCEPTANCE_STAGE_ORDER},
    )
    assert len({checkpoint.checkpoint_digest for checkpoint in chain}) == len(chain)


def test_secret_identifiers_and_payloads_never_reach_acceptance_evidence(
    tmp_path: Path,
) -> None:
    secret = "LeakMarker-Adapter-Secret-927"
    lanes = _lanes(tmp_path)
    fixture = lanes[0].normalization_fixtures[0]
    upstream_request = copy.deepcopy(fixture.upstream_request)
    assert isinstance(upstream_request, dict)
    upstream_request["state"] = secret
    secret_fixture = dataclasses.replace(
        fixture,
        fixture_id=f"{secret}-fixture",
        request_id=f"{secret}-request",
        upstream_request=upstream_request,
    )
    secret_request = _batch_request(lanes[0].shadow_profile, secret_fixture)
    secret_lane = dataclasses.replace(
        lanes[0],
        lane_id=secret,
        actor_id=secret,
        requests=(secret_request, *lanes[0].requests[1:]),
        normalization_fixtures=(
            secret_fixture,
            *lanes[0].normalization_fixtures[1:],
        ),
    )

    receipt = acceptance.run_offline_acceptance(
        _schema(),
        (secret_lane, lanes[1]),
    )
    assert secret.encode() not in update.canonical_json_bytes(receipt.to_dict())
    assert secret.encode() not in secret_lane.prepared_update_path.read_bytes()


def test_stage_side_effect_flag_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = _schema()
    lanes = _lanes(tmp_path)
    unsafe_plan = dataclasses.replace(
        acceptance._plan_batch(schema, lanes[1]),
        network_accessed=True,
    )
    original_plan = acceptance._plan_batch

    def inject_unsafe_plan(
        plan_schema: Mapping[str, object],
        lane: acceptance.AdapterAcceptanceLane,
    ) -> planner.BatchResidencyPlan:
        if lane.lane_id == lanes[1].lane_id:
            return unsafe_plan
        return original_plan(plan_schema, lane)

    monkeypatch.setattr(acceptance, "_plan_batch", inject_unsafe_plan)
    with pytest.raises(acceptance.AcceptanceError, match="rejected: stage_binding"):
        acceptance.run_offline_acceptance(schema, lanes)

    monkeypatch.setattr(acceptance, "_plan_batch", original_plan)
    original_normalize = normalizer.normalize_fixture

    def inject_unsafe_normalization(
        record_schema: Mapping[str, object],
        profile: Mapping[str, object],
        *,
        request_id: str,
        task_kind: str,
        request_schema_version: str,
        response_schema_version: str,
        upstream_request: Mapping[str, object],
        upstream_response: Mapping[str, object],
    ) -> normalizer.NormalizationRecord:
        record = original_normalize(
            record_schema,
            profile,
            request_id=request_id,
            task_kind=task_kind,
            request_schema_version=request_schema_version,
            response_schema_version=response_schema_version,
            upstream_request=upstream_request,
            upstream_response=upstream_response,
        )
        return dataclasses.replace(record, network_accessed=True)

    monkeypatch.setattr(
        normalizer,
        "normalize_fixture",
        inject_unsafe_normalization,
    )
    with pytest.raises(acceptance.AcceptanceError, match="rejected: normalization"):
        acceptance.run_offline_acceptance(schema, lanes)
