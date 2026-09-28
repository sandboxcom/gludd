"""Offline batching and residency planning contracts for pinned adapters."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "docs" / "releases" / "typed-decision-adapter-contract-v1.schema.json"
EXAMPLES = (
    ROOT
    / "docs"
    / "releases"
    / "typed-decision-adapter-contract-v1.examples.json"
)


def _load_planner() -> ModuleType:
    return importlib.import_module("scripts.plan_typed_decision_adapter_batch")


planner = _load_planner()


class _RequestLike(Protocol):
    def to_dict(self) -> dict[str, object]:
        """Return reconstructable request metadata."""


class _MemberLike(Protocol):
    ordinal: int


class _GroupLike(Protocol):
    member_count: int
    reason: str
    group_key_digest: str
    members: tuple[_MemberLike, ...]


class _EvidenceLike(Protocol):
    redacted_fields: tuple[str, ...]


class _ResidencyLike(Protocol):
    fingerprint: str
    decision: str
    reason: str
    measured_bytes: int


class _PlanLike(Protocol):
    ready: bool
    reasons: tuple[str, ...]
    groups: tuple[_GroupLike, ...]
    admission_performed: bool
    network_accessed: bool
    runtime_invoked: bool
    models_downloaded: bool
    evidence: _EvidenceLike
    residency: tuple[_ResidencyLike, ...]
    evictions: tuple[_ResidencyLike, ...]

    def to_dict(self) -> dict[str, object]:
        """Return canonical plan evidence."""


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _digests(seed: str) -> Iterator[str]:
    index = 0
    while True:
        yield _digest(f"{seed}-{index}")
        index += 1


def _replace_digests(value: object, digests: Iterator[str]) -> object:
    if isinstance(value, dict):
        return {key: _replace_digests(item, digests) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_digests(item, digests) for item in value]
    if isinstance(value, str) and value.startswith("sha256:"):
        return next(digests)
    return value


def _schema() -> dict[str, object]:
    loaded = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _profile(index: int, *, batching: bool = True) -> dict[str, object]:
    bundle = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    assert isinstance(bundle, dict)
    profiles = bundle["profiles"]
    assert isinstance(profiles, list) and isinstance(profiles[index], dict)
    profile = _replace_digests(copy.deepcopy(profiles[index]), _digests(f"p{index}"))
    assert isinstance(profile, dict)
    profile["example_only"] = False
    profile["profile_state"] = "shadow"
    source = profile["source_pin"]
    identity = profile["identity"]
    batching_contract = profile["batching"]
    residency = profile["residency"]
    assert isinstance(source, dict)
    assert isinstance(identity, dict)
    assert isinstance(batching_contract, dict)
    assert isinstance(residency, dict)
    source["release"] = f"v1.0.{index}"
    source["commit"] = hashlib.sha1(f"source-{index}".encode()).hexdigest()
    identity["model_revision"] = f"model-{index}@v1.0.{index}"
    batching_contract["enabled"] = batching
    batching_contract["max_batch_size"] = 2 if batching else 1
    batching_contract["max_wait_ms"] = 10 if batching else 0
    if residency["ownership"] == "universal_model_worker":
        residency["preload_identity_fingerprints"] = [identity["fingerprint"]]
        residency["max_loaded_identities"] = 2
        residency["resident_byte_budget"] = 4096
    return profile


def _request(
    request_id: str,
    profile: dict[str, object],
    *,
    tenant: str = "tenant-a",
    sensitivity: str = "confidential",
    question_schema: str = "choice-v1",
    token_budget: int = 128,
    deadline_ms: int = 1000,
) -> _RequestLike:
    identity = profile["identity"]
    assert isinstance(identity, dict)
    contract_version = identity["contract_version"]
    assert isinstance(contract_version, str)
    profile_id = profile["profile_id"]
    assert isinstance(profile_id, str)
    return cast(
        _RequestLike,
        planner.BatchRequest(
            request_id=request_id,
            profile_id=profile_id,
            tenant_digest=_digest(tenant),
            schema_version=contract_version,
            question_schema_digest=_digest(question_schema),
            token_budget=token_budget,
            trust_sensitivity_class=sensitivity,
            deadline_bucket_ms=deadline_ms,
            question_types=("choice",),
            state_shape="string",
            state_bytes=128,
            question_count=2,
            max_choice_options=4,
            max_score_levels=0,
            request_bytes=1024,
        ),
    )


def _bounds(
    *,
    resident_bytes: int = 4096,
    max_loaded: int = 2,
) -> object:
    return planner.PlannerBounds(
        max_requests=32,
        max_profiles=4,
        max_loaded_identities=max_loaded,
        resident_byte_budget=resident_bytes,
        max_total_request_bytes=131072,
    )


def _snapshot(*loaded: object) -> object:
    return planner.ResidencySnapshot(loaded=tuple(loaded))


def _measurement(profile: dict[str, object], measured_bytes: int = 1024) -> object:
    identity = profile["identity"]
    assert isinstance(identity, dict) and isinstance(identity["fingerprint"], str)
    return planner.IdentityMeasurement(
        fingerprint=identity["fingerprint"],
        measured_bytes=measured_bytes,
    )


def _second_worker() -> dict[str, object]:
    profile = _profile(1)
    profile["profile_id"] = "laya-secondary-worker"
    source = profile["source_pin"]
    identity = profile["identity"]
    residency = profile["residency"]
    assert isinstance(source, dict)
    assert isinstance(identity, dict)
    assert isinstance(residency, dict)
    source["release"] = "v1.1.0"
    source["commit"] = hashlib.sha1(b"secondary-worker-source").hexdigest()
    source["source_archive_digest"] = _digest("secondary-worker-archive")
    identity["fingerprint"] = _digest("secondary-worker-fingerprint")
    identity["model_revision"] = "secondary-model@v1.1.0"
    identity["calibration_digest"] = _digest("secondary-worker-calibration")
    residency["preload_identity_fingerprints"] = [identity["fingerprint"]]
    return profile


def _build(
    profiles: list[dict[str, object]],
    requests: Sequence[object],
    *,
    bounds: object | None = None,
    snapshot: object | None = None,
    measurements: tuple[object, ...] = (),
) -> _PlanLike:
    return cast(
        _PlanLike,
        planner.build_batch_residency_plan(
            _schema(),
            profiles,
            requests,
            bounds=bounds or _bounds(),
            snapshot=snapshot or _snapshot(),
            measurements=measurements,
        ),
    )


def test_plan_is_deterministic_redacted_and_never_executes_runtime() -> None:
    external = _profile(0)
    worker = _profile(1)
    requests = [
        _request("external-request", external),
        _request("worker-secret-request-1", worker),
        _request("worker-secret-request-2", worker),
        _request("worker-secret-request-3", worker),
    ]
    measurement = _measurement(worker)
    first = _build([worker, external], requests, measurements=(measurement,))
    second = _build([external, worker], requests, measurements=(measurement,))
    assert first == second
    assert first.ready is True
    assert len(first.groups) == 3
    assert [group.member_count for group in first.groups] == [1, 2, 1]
    assert first.admission_performed is False
    assert first.network_accessed is False
    assert first.runtime_invoked is False
    assert first.models_downloaded is False
    assert first.evidence.redacted_fields == planner.REDACTED_BATCH_FIELDS
    rendered = planner.canonical_json_bytes(first.to_dict())
    assert b"worker-secret-request" not in rendered
    assert b"credentials" in rendered


def test_grouping_separates_every_capability_and_security_dimension() -> None:
    profile = _profile(0)
    requests = [
        _request("a", profile),
        _request("b", profile, tenant="tenant-b"),
        _request("c", profile, sensitivity="restricted"),
        _request("d", profile, question_schema="score-v1"),
        _request("e", profile, token_budget=256),
        _request("f", profile, deadline_ms=2000),
    ]
    plan = _build([profile], requests)
    assert len(plan.groups) == 6
    assert all(group.member_count == 1 for group in plan.groups)
    assert all(group.reason == "capability_and_security_key" for group in plan.groups)
    assert len({group.group_key_digest for group in plan.groups}) == 6


def test_batching_disabled_produces_ordered_singletons() -> None:
    profile = _profile(0, batching=False)
    plan = _build(
        [profile],
        [_request("one", profile), _request("two", profile)],
    )
    assert [group.member_count for group in plan.groups] == [1, 1]
    assert {group.reason for group in plan.groups} == {"profile_batching_disabled"}
    assert [member.ordinal for group in plan.groups for member in group.members] == [0, 1]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda profile: profile.__setitem__("profile_state", "disabled"), "admitted"),
        (lambda profile: profile.__setitem__("example_only", True), "example"),
        (
            lambda profile: profile["source_pin"].__setitem__("commit", "0" * 40),
            "source commit",
        ),
        (
            lambda profile: profile["identity"].__setitem__(
                "model_revision",
                "model-latest",
            ),
            "moving alias",
        ),
        (
            lambda profile: profile["batching"].__setitem__("grouping_keys", []),
            "grouping",
        ),
    ],
)
def test_profiles_must_be_admitted_schema_valid_and_immutably_pinned(
    mutation: object,
    expected: str,
) -> None:
    profile = _profile(0)
    assert callable(mutation)
    mutation(profile)
    with pytest.raises(planner.PlannerInputError, match=expected):
        _build([profile], [_request("request", profile)])


def test_worker_residency_contract_must_bind_its_exact_identity() -> None:
    profile = _profile(1)
    residency = profile["residency"]
    assert isinstance(residency, dict)
    residency["preload_identity_fingerprints"] = [_digest("other")]
    with pytest.raises(planner.PlannerInputError, match="preload identity"):
        _build(
            [profile],
            [_request("request", profile)],
            measurements=(_measurement(profile),),
        )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("question_types", ("unsupported",), "question type"),
        ("state_shape", "object", "state shape"),
        ("token_budget", 99999, "token budget"),
        ("state_bytes", 999999, "state bytes"),
        ("question_count", 99, "question count"),
        ("max_choice_options", 99, "choice options"),
        ("max_score_levels", 99, "score levels"),
        ("request_bytes", 999999, "request bytes"),
        ("deadline_bucket_ms", 99999, "deadline"),
        ("schema_version", "wrong-version", "schema version"),
    ],
)
def test_requests_must_fit_the_admitted_profile_capabilities(
    field: str,
    value: object,
    expected: str,
) -> None:
    profile = _profile(0)
    request = _request("request", profile)
    invalid = planner.BatchRequest(
        **{**request.to_dict(), field: value, "request_id": "request"},
    )
    with pytest.raises(planner.PlannerInputError, match=expected):
        _build([profile], [invalid])


def test_unknown_duplicate_and_over_capacity_requests_fail_closed() -> None:
    profile = _profile(0)
    unknown = _request("unknown", profile)
    unknown = planner.BatchRequest(
        **{**unknown.to_dict(), "request_id": "unknown", "profile_id": "absent"},
    )
    with pytest.raises(planner.PlannerInputError, match="unknown profile"):
        _build([profile], [unknown])

    duplicate = _request("same", profile)
    with pytest.raises(planner.PlannerInputError, match="duplicate request_id"):
        _build([profile], [duplicate, duplicate])

    limits = profile["limits"]
    assert isinstance(limits, dict)
    limits["concurrency"] = 1
    limits["queue_depth"] = 0
    with pytest.raises(planner.PlannerInputError, match="queue and concurrency"):
        _build(
            [profile],
            [_request("one", profile), _request("two", profile)],
        )


def test_resident_worker_is_reused_without_eviction() -> None:
    profile = _profile(1)
    identity = profile["identity"]
    assert isinstance(identity, dict) and isinstance(identity["fingerprint"], str)
    resident = planner.ResidentIdentity(
        fingerprint=identity["fingerprint"],
        measured_bytes=1024,
        lease_count=1,
        inflight_count=1,
        last_used_sequence=10,
    )
    plan = _build(
        [profile],
        [_request("request", profile)],
        snapshot=_snapshot(resident),
    )
    assert plan.ready is True
    assert [(item.decision, item.reason) for item in plan.residency] == [
        ("reuse", "identity_already_resident"),
    ]
    assert [(item.decision, item.reason) for item in plan.evictions] == [
        ("retain", "requested_identity"),
    ]


def test_eviction_selects_oldest_eligible_and_retains_active_identity() -> None:
    profile = _profile(1)
    old = planner.ResidentIdentity(
        fingerprint=_digest("old-unleased"),
        measured_bytes=2048,
        lease_count=0,
        inflight_count=0,
        last_used_sequence=1,
    )
    active = planner.ResidentIdentity(
        fingerprint=_digest("active"),
        measured_bytes=1024,
        lease_count=1,
        inflight_count=0,
        last_used_sequence=0,
    )
    plan = _build(
        [profile],
        [_request("request", profile)],
        bounds=_bounds(resident_bytes=3072, max_loaded=2),
        snapshot=_snapshot(active, old),
        measurements=(_measurement(profile, 1024),),
    )
    assert plan.ready is True
    assert [(item.decision, item.reason) for item in plan.residency] == [
        ("plan_preload", "evict_then_preload"),
    ]
    decisions = {item.fingerprint: (item.decision, item.reason) for item in plan.evictions}
    assert decisions[old.fingerprint] == ("plan_evict", "oldest_eligible_for_capacity")
    assert decisions[active.fingerprint] == ("retain", "active_lease")


def test_active_capacity_and_missing_measurement_produce_rejected_plan() -> None:
    profile = _profile(1)
    active = planner.ResidentIdentity(
        fingerprint=_digest("active"),
        measured_bytes=4096,
        lease_count=0,
        inflight_count=1,
        last_used_sequence=0,
    )
    blocked = _build(
        [profile],
        [_request("request", profile)],
        snapshot=_snapshot(active),
        measurements=(_measurement(profile, 1024),),
    )
    assert blocked.ready is False
    assert blocked.residency[0].decision == "reject"
    assert blocked.residency[0].reason == "leased_or_inflight_capacity"
    assert blocked.evictions[0].reason == "inflight_request"

    missing = _build([profile], [_request("request", profile)])
    assert missing.ready is False
    assert missing.residency[0].reason == "missing_measured_resident_bytes"


def test_external_endpoint_has_no_residency_or_eviction_claim() -> None:
    profile = _profile(0)
    plan = _build([profile], [_request("request", profile)])
    assert plan.ready is True
    assert [(item.decision, item.reason, item.measured_bytes) for item in plan.residency] == [
        ("external_unmanaged", "external_endpoint_no_residency_claim", 0),
    ]
    assert plan.evictions == ()


def test_replay_is_idempotent_and_every_tamper_fails_closed() -> None:
    profile = _profile(1)
    request = _request("secret-request", profile)
    measurement = _measurement(profile)
    plan = _build([profile], [request], measurements=(measurement,))
    document = plan.to_dict()
    assert planner.verify_batch_residency_plan(
        document,
        _schema(),
        [profile],
        [request],
        bounds=_bounds(),
        snapshot=_snapshot(),
        measurements=(measurement,),
    ) == plan

    for field, value in (
        ("runtime_invoked", True),
        ("models_downloaded", True),
        ("plan_digest", _digest("tampered")),
        ("ready", False),
    ):
        tampered = copy.deepcopy(document)
        tampered[field] = value
        with pytest.raises(planner.PlannerInputError, match=r"fail-closed|mismatch"):
            planner.verify_batch_residency_plan(
                tampered,
                _schema(),
                [profile],
                [request],
                bounds=_bounds(),
                snapshot=_snapshot(),
                measurements=(measurement,),
            )


def test_resource_bounds_reject_unbounded_inputs_and_invalid_snapshots() -> None:
    profile = _profile(0)
    request = _request("request", profile)
    tiny = planner.PlannerBounds(
        max_requests=1,
        max_profiles=1,
        max_loaded_identities=1,
        resident_byte_budget=1024,
        max_total_request_bytes=512,
    )
    with pytest.raises(planner.PlannerInputError, match="total request bytes"):
        _build([profile], [request], bounds=tiny)

    duplicate = planner.ResidentIdentity(
        fingerprint=_digest("duplicate"),
        measured_bytes=1,
        lease_count=0,
        inflight_count=0,
        last_used_sequence=0,
    )
    with pytest.raises(planner.PlannerInputError, match="duplicate resident"):
        _build(
            [profile],
            [request],
            snapshot=_snapshot(duplicate, duplicate),
        )

    with pytest.raises(planner.PlannerInputError, match="max_requests"):
        planner.PlannerBounds(
            max_requests=0,
            max_profiles=1,
            max_loaded_identities=1,
            resident_byte_budget=1,
            max_total_request_bytes=1,
        )


def test_typed_extractors_fail_closed_on_wrong_shapes() -> None:
    with pytest.raises(planner.PlannerInputError, match="must be an object"):
        planner._mapping({"value": []}, "value", context="fixture")
    with pytest.raises(planner.PlannerInputError, match="non-empty string"):
        planner._string({"value": ""}, "value", context="fixture")
    with pytest.raises(planner.PlannerInputError, match="must be an integer"):
        planner._integer({"value": True}, "value", context="fixture")
    with pytest.raises(planner.PlannerInputError, match="string array"):
        planner._strings({"value": [1]}, "value", context="fixture")
    assert planner._is_repeated("sha256:", prefix="sha256:") is True


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda profile: profile["source_pin"].__setitem__(
                "repository",
                "http://example.invalid/source",
            ),
            "repository",
        ),
        (
            lambda profile: profile["source_pin"].__setitem__("release", "latest"),
            "moving alias",
        ),
        (
            lambda profile: profile["source_pin"].__setitem__(
                "source_archive_digest",
                "sha256:" + "a" * 64,
            ),
            "archive digest",
        ),
        (
            lambda profile: profile["identity"].__setitem__(
                "runtime_digest",
                "sha256:" + "b" * 64,
            ),
            "runtime_digest",
        ),
        (
            lambda profile: profile["residency"].__setitem__(
                "resident_byte_budget",
                1,
            ),
            "external residency",
        ),
        (
            lambda profile: profile["transport"].__setitem__(
                "kind",
                "model_worker_jev_http",
            ),
            "ownership mismatch",
        ),
    ],
)
def test_additional_pin_and_external_ownership_violations_fail_closed(
    mutation: object,
    expected: str,
) -> None:
    profile = _profile(0)
    assert callable(mutation)
    mutation(profile)
    with pytest.raises(planner.PlannerInputError, match=expected):
        _build([profile], [_request("request", profile)])


def test_worker_transport_mismatch_fails_closed() -> None:
    profile = _profile(1)
    transport = profile["transport"]
    assert isinstance(transport, dict)
    transport["kind"] = "jev_http_external"
    with pytest.raises(planner.PlannerInputError, match="worker ownership mismatch"):
        _build(
            [profile],
            [_request("request", profile)],
            measurements=(_measurement(profile),),
        )


def test_score_capability_requires_complete_levels_and_supports_score_only() -> None:
    profile = _profile(0)
    original = _request("score-request", profile)
    incomplete = planner.BatchRequest(
        **{
            **original.to_dict(),
            "question_types": ("score",),
            "max_choice_options": 0,
            "max_score_levels": 1,
        },
    )
    with pytest.raises(planner.PlannerInputError, match="score levels are incomplete"):
        _build([profile], [incomplete])

    complete = planner.BatchRequest(
        **{
            **original.to_dict(),
            "question_types": ("score",),
            "max_choice_options": 0,
            "max_score_levels": 5,
        },
    )
    assert _build([profile], [complete]).ready is True


def test_snapshot_and_measurement_validation_rejects_each_unsafe_shape() -> None:
    profile = _profile(1)
    request = _request("request", profile)
    invalid_residents = (
        planner.ResidentIdentity("bad", 1, 0, 0, 0),
        planner.ResidentIdentity(_digest("typed"), 1, True, 0, 0),
        planner.ResidentIdentity(_digest("negative"), 1, 0, -1, 0),
    )
    for resident in invalid_residents:
        with pytest.raises(planner.PlannerInputError, match="resident"):
            _build(
                [profile],
                [request],
                snapshot=_snapshot(resident),
                measurements=(_measurement(profile),),
            )

    invalid_measurements = (
        (planner.IdentityMeasurement("bad", 1),),
        (
            _measurement(profile),
            _measurement(profile),
        ),
        (planner.IdentityMeasurement(_digest("unrequested"), 1),),
        (planner.IdentityMeasurement(_digest("unrequested"), 0),),
    )
    expected = ("fingerprint", "duplicate", "requested", "requested")
    for measurements, message in zip(invalid_measurements, expected, strict=True):
        with pytest.raises(planner.PlannerInputError, match=message):
            _build([profile], [request], measurements=measurements)

    fingerprint = profile["identity"]
    assert isinstance(fingerprint, dict) and isinstance(fingerprint["fingerprint"], str)
    zero = planner.IdentityMeasurement(fingerprint["fingerprint"], 0)
    with pytest.raises(planner.PlannerInputError, match="measured resident bytes"):
        _build([profile], [request], measurements=(zero,))


def test_residency_rejections_distinguish_profile_bytes_count_and_total_bytes() -> None:
    first = _profile(1)
    second = _second_worker()
    requests = [_request("first", first), _request("second", second)]

    profile_bound = _build(
        [first],
        [requests[0]],
        bounds=_bounds(resident_bytes=8192),
        measurements=(_measurement(first, 8192),),
    )
    assert profile_bound.reasons == ("profile_resident_byte_budget_exceeded",)

    byte_bound = _build(
        [first, second],
        requests,
        bounds=_bounds(resident_bytes=4096, max_loaded=2),
        measurements=(_measurement(first, 3000), _measurement(second, 3000)),
    )
    assert byte_bound.reasons == ("resident_byte_budget_blocked",)

    count_bound = _build(
        [first, second],
        requests,
        bounds=_bounds(resident_bytes=8192, max_loaded=1),
        measurements=(_measurement(first), _measurement(second)),
    )
    assert count_bound.reasons == ("loaded_identity_limit_blocked",)


def test_residency_retains_spare_capacity_and_rejects_overbound_requested() -> None:
    profile = _profile(1)
    oldest = planner.ResidentIdentity(_digest("oldest"), 2048, 0, 0, 1)
    spare = planner.ResidentIdentity(_digest("spare"), 1024, 0, 0, 2)
    plan = _build(
        [profile],
        [_request("request", profile)],
        snapshot=_snapshot(oldest, spare),
        measurements=(_measurement(profile),),
    )
    eviction_reasons = {item.fingerprint: item.reason for item in plan.evictions}
    assert eviction_reasons[oldest.fingerprint] == "oldest_eligible_for_capacity"
    assert eviction_reasons[spare.fingerprint] == "capacity_sufficient"

    identity = profile["identity"]
    assert isinstance(identity, dict) and isinstance(identity["fingerprint"], str)
    requested = planner.ResidentIdentity(identity["fingerprint"], 2048, 0, 0, 0)
    overbound = _build(
        [profile],
        [_request("request", profile)],
        bounds=_bounds(resident_bytes=1024),
        snapshot=_snapshot(requested),
    )
    assert overbound.reasons == ("snapshot_exceeds_resource_bounds",)


def test_empty_duplicate_and_replay_version_inputs_fail_closed() -> None:
    profile = _profile(0)
    request = _request("request", profile)
    with pytest.raises(planner.PlannerInputError, match="profile count"):
        _build([], [request])
    with pytest.raises(planner.PlannerInputError, match="request count"):
        _build([profile], [])
    with pytest.raises(planner.PlannerInputError, match="duplicate profile_id"):
        _build([profile, copy.deepcopy(profile)], [request])

    duplicate_identity = copy.deepcopy(profile)
    duplicate_identity["profile_id"] = "different-profile-id"
    with pytest.raises(planner.PlannerInputError, match="duplicate admitted identity"):
        _build([profile, duplicate_identity], [request])

    plan = _build([profile], [request])
    wrong_version = plan.to_dict()
    wrong_version["version"] = "unknown/v9"
    with pytest.raises(planner.PlannerInputError, match="version mismatch"):
        planner.verify_batch_residency_plan(
            wrong_version,
            _schema(),
            [profile],
            [request],
            bounds=_bounds(),
            snapshot=_snapshot(),
            measurements=(),
        )

    noncanonical = plan.to_dict()
    noncanonical["extra"] = {"not-json"}
    with pytest.raises(planner.PlannerInputError, match="not canonical JSON"):
        planner.verify_batch_residency_plan(
            noncanonical,
            _schema(),
            [profile],
            [request],
            bounds=_bounds(),
            snapshot=_snapshot(),
            measurements=(),
        )
