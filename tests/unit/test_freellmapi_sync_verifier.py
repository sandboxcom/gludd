"""Offline-first contracts for the v0.1.2 FreeLLMAPI sync verifier."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator
from typing import cast

import pytest

import general_ludd.models.freellmapi_sync_verifier as sync_verifier
from general_ludd.models.freellmapi_sync_verifier import (
    FREELLMAPI_SYNC_PROTOCOL,
    FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
    FreeLLMAPISyncFault,
    build_owned_shim_diff,
    verify_sync_admission,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast("list[object]", value)


_PROJECT_IDENTITY = _digest("gludd-project-identity")
_OPERATION_ID = _digest("freellmapi-v012-operation")
_RELEASE_IDENTITY_PROTOCOL = "gludd-freellmapi-release-identity-v1"


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _release(*, tag: str, commit: str, tree: str, label: str) -> dict[str, object]:
    archive = _digest(f"{label}-archive")
    upstream: dict[str, object] = {
        "repository": "tashfeenahmed/freellmapi",
        "tag": tag,
        "commit": commit,
        "tree": tree,
        "archive_sha256": archive,
    }
    supply_chain: dict[str, object] = {
        "source_archive_sha256": archive,
        "package_lock_sha256": _digest(f"{label}-package-lock"),
        "license_sha256": _digest(f"{label}-license"),
        "build_recipe_sha256": _digest(f"{label}-recipe"),
        "artifact_sha256": _digest(f"{label}-artifact"),
        "artifact_signature_sha256": _digest(f"{label}-artifact-signature"),
        "sbom_sha256": _digest(f"{label}-sbom"),
        "provenance_sha256": _digest(f"{label}-provenance"),
        "tag_verified": True,
        "commit_verified": True,
        "tree_verified": True,
        "artifact_signature_verified": True,
    }
    return {
        "upstream": upstream,
        "supply_chain": supply_chain,
        "identity_sha256": _canonical_digest(
            {
                "protocol": _RELEASE_IDENTITY_PROTOCOL,
                "upstream": upstream,
                "supply_chain": supply_chain,
            }
        ),
    }


def _shim(
    shim_id: str,
    *,
    digest_label: str,
    path: str | None = None,
    exports: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": shim_id,
        "path": path or f"src/general_ludd/models/freellmapi_v012_{shim_id}.py",
        "kind": "adapter",
        "owner": "gludd",
        "sha256": _digest(digest_label),
        "upstream_exports": exports or ["scoreCandidate"],
        "copied_upstream_source": False,
        "file_type": "regular",
    }


def _request() -> dict[str, object]:
    baseline = _release(tag="v0.11.1", commit="1" * 40, tree="2" * 40, label="old")
    candidate = _release(tag="v0.12.0", commit="3" * 40, tree="4" * 40, label="new")
    baseline_supply = baseline["supply_chain"]
    baseline_upstream = baseline["upstream"]
    assert isinstance(baseline_supply, dict)
    assert isinstance(baseline_upstream, dict)
    return {
        "protocol": FREELLMAPI_SYNC_PROTOCOL,
        "receipt_scope": {
            "project_identity_sha256": _PROJECT_IDENTITY,
            "operation_id": _OPERATION_ID,
            "prior_receipt_sha256": None,
        },
        "mode": "offline",
        "baseline": baseline,
        "candidate": candidate,
        "owned_shims": {
            "baseline": [
                _shim("score_adapter", digest_label="score-v1"),
                _shim("catalog_adapter", digest_label="catalog-v1"),
            ],
            "candidate": [
                _shim("catalog_adapter", digest_label="catalog-v1"),
                _shim("score_adapter", digest_label="score-v2"),
                _shim(
                    "quota_normalizer",
                    digest_label="quota-v1",
                    exports=["normalizeQuota"],
                ),
            ],
        },
        "plans": {
            "schema": {
                "from_version": 1,
                "to_version": 2,
                "compatibility": "backward",
            },
            "migration": {
                "strategy": "expand-migrate-contract",
                "steps": ["expand", "dual_read", "backfill", "cutover"],
                "destructive": False,
                "contract_deferred": True,
            },
            "test_reuse": {
                "gludd_suites": [
                    "tests/unit/test_freellmapi_upstream_admission.py",
                    "tests/unit/test_freellmapi_upstream_build.py",
                ],
                "upstream_suites": ["npm:test:server"],
                "upstream_source": "ephemeral_verified_archive",
                "copied_upstream_tests": False,
            },
        },
        "live_boundary": {
            "network_enabled": False,
            "max_requests": 0,
            "timeout_seconds": 0,
            "max_response_bytes": 0,
            "download_artifacts": False,
            "mutate_config": False,
        },
        "zdd": {
            "strategy": "dual-read-shadow",
            "baseline_commit": baseline_upstream["commit"],
            "baseline_tree": baseline_upstream["tree"],
            "baseline_artifact_sha256": baseline_supply["artifact_sha256"],
            "rollback_test_id": "freellmapi-v012-rollback",
            "rollback_ready": True,
            "no_downtime": True,
            "state_write_before_admission": False,
        },
    }


def _verify(
    request: dict[str, object],
    *,
    allow_live: bool = False,
    project_identity_sha256: str = _PROJECT_IDENTITY,
    operation_id: str = _OPERATION_ID,
    prior_receipt_sha256: str | None = None,
) -> dict[str, object]:
    return verify_sync_admission(
        request,
        allow_live=allow_live,
        project_identity_sha256=project_identity_sha256,
        operation_id=operation_id,
        prior_receipt_sha256=prior_receipt_sha256,
    )


def test_offline_admission_is_deterministic_content_free_and_non_runnable() -> None:
    request = _request()

    first = _verify(request)
    second = _verify(copy.deepcopy(request))

    assert first == second
    assert first == {
        "schema_version": 1,
        "protocol": FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
        "decision": "admitted_for_shadow",
        "runtime_admitted": False,
        "fault": None,
        "declared_mode": "offline",
        "collection_observed": False,
        "receipt_scope_sha256": first["receipt_scope_sha256"],
        "manifest_sha256": first["manifest_sha256"],
        "baseline_identity_sha256": first["baseline_identity_sha256"],
        "candidate_identity_sha256": first["candidate_identity_sha256"],
        "owned_shim_inventory_sha256": first["owned_shim_inventory_sha256"],
        "plans_sha256": first["plans_sha256"],
        "rollback_sha256": first["rollback_sha256"],
        "live_boundary_sha256": first["live_boundary_sha256"],
        "update_diff": {
            "added": ["quota_normalizer"],
            "changed": ["score_adapter"],
            "removed": [],
            "unchanged": ["catalog_adapter"],
            "counts": {"added": 1, "changed": 1, "removed": 0, "unchanged": 1},
        },
    }
    baseline = _object_dict(request["baseline"])
    candidate = _object_dict(request["candidate"])
    assert first["baseline_identity_sha256"] == baseline["identity_sha256"]
    assert first["candidate_identity_sha256"] == candidate["identity_sha256"]
    assert first["live_boundary_sha256"] == _canonical_digest(
        request["live_boundary"]
    )
    encoded = json.dumps(first, sort_keys=True)
    assert "scoreCandidate" not in encoded
    assert "src/general_ludd" not in encoded


def test_inventory_order_does_not_change_diff_or_receipt() -> None:
    first_request = _request()
    second_request = copy.deepcopy(first_request)
    inventory = _object_dict(second_request["owned_shims"])
    _object_list(inventory["baseline"]).reverse()
    _object_list(inventory["candidate"]).reverse()
    plans = _object_dict(second_request["plans"])
    test_reuse = _object_dict(plans["test_reuse"])
    _object_list(test_reuse["gludd_suites"]).reverse()

    assert _verify(first_request) == _verify(second_request)


def test_owned_shim_diff_is_sorted_and_identifies_removal() -> None:
    inventory = _request()["owned_shims"]
    assert isinstance(inventory, dict)
    candidate = inventory["candidate"]
    assert isinstance(candidate, list)
    inventory["candidate"] = [item for item in candidate if item["id"] != "catalog_adapter"]

    diff = build_owned_shim_diff(inventory)

    assert diff == {
        "added": ["quota_normalizer"],
        "changed": ["score_adapter"],
        "removed": ["catalog_adapter"],
        "unchanged": [],
        "counts": {"added": 1, "changed": 1, "removed": 1, "unchanged": 0},
    }


@pytest.mark.parametrize(
    ("patch", "fault"),
    [
        (
            lambda request: request["candidate"]["upstream"].__setitem__("commit", "main"),
            FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
        ),
        (
            lambda request: request["candidate"]["upstream"].__setitem__("tree", "a" * 39),
            FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
        ),
        (
            lambda request: request["candidate"]["upstream"].__setitem__(
                "archive_sha256", "0" * 64
            ),
            FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
        ),
        (
            lambda request: request.__setitem__(
                "candidate", copy.deepcopy(request["baseline"])
            ),
            FreeLLMAPISyncFault.UPDATE_NOT_FORWARD,
        ),
        (
            lambda request: request["candidate"]["supply_chain"].__setitem__(
                "commit_verified", False
            ),
            FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID,
        ),
        (
            lambda request: request["candidate"]["supply_chain"].__setitem__(
                "source_archive_sha256", _digest("wrong")
            ),
            FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID,
        ),
        (
            lambda request: request["owned_shims"]["candidate"][0].__setitem__(
                "path", "src/general_ludd/models/vendor/freellmapi/copied.ts"
            ),
            FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY,
        ),
        (
            lambda request: request["owned_shims"]["candidate"][0].__setitem__(
                "copied_upstream_source", True
            ),
            FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY,
        ),
        (
            lambda request: request["owned_shims"]["candidate"][0].__setitem__(
                "kind", []
            ),
            FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY,
        ),
        (
            lambda request: request["plans"]["schema"].__setitem__("compatibility", "none"),
            FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID,
        ),
        (
            lambda request: request["plans"]["migration"].__setitem__("destructive", True),
            FreeLLMAPISyncFault.MIGRATION_PLAN_INVALID,
        ),
        (
            lambda request: request["plans"]["test_reuse"].__setitem__(
                "copied_upstream_tests", True
            ),
            FreeLLMAPISyncFault.TEST_REUSE_PLAN_INVALID,
        ),
        (
            lambda request: request["zdd"].__setitem__("rollback_ready", False),
            FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID,
        ),
        (
            lambda request: request["owned_shims"].__setitem__(
                "candidate", request["owned_shims"]["candidate"][1:]
            ),
            FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID,
        ),
    ],
)
def test_supply_chain_ownership_plans_and_rollback_fail_closed(
    patch: object,
    fault: FreeLLMAPISyncFault,
) -> None:
    request = _request()
    assert callable(patch)
    patch(request)

    receipt = _verify(request)

    assert receipt["decision"] == "rejected"
    assert receipt["runtime_admitted"] is False
    assert receipt["fault"] == fault.value
    assert set(receipt) == {
        "schema_version",
        "protocol",
        "decision",
        "runtime_admitted",
        "fault",
        "manifest_sha256",
        "receipt_scope_sha256",
    }


def test_artifact_signature_attestation_requires_exact_boolean() -> None:
    """Reject truthy substitutes for independently verified signatures."""
    request = _request()
    candidate = _object_dict(request["candidate"])
    supply_chain = _object_dict(candidate["supply_chain"])
    supply_chain["artifact_signature_verified"] = 1

    receipt = _verify(request)

    assert receipt["decision"] == "rejected"
    assert receipt["runtime_admitted"] is False
    assert receipt["fault"] == FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID.value
    assert "artifact_signature" not in json.dumps(receipt, sort_keys=True)


def test_rejection_receipt_does_not_echo_untrusted_content() -> None:
    request = _request()
    secret = "TOP-SECRET-UPSTREAM-CONTENT"
    inventory = _object_dict(request["owned_shims"])
    candidate = _object_list(inventory["candidate"])
    shim = _object_dict(candidate[0])
    shim["upstream_exports"] = [secret]
    shim["owner"] = secret

    receipt = _verify(request)

    assert receipt["fault"] == FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY.value
    assert secret not in json.dumps(receipt, sort_keys=True)


def test_offline_is_default_and_live_mode_is_explicit_bounded_metadata_only() -> None:
    request = _request()
    request["mode"] = "live"
    request["live_boundary"] = {
        "network_enabled": True,
        "max_requests": 3,
        "timeout_seconds": 10,
        "max_response_bytes": 65536,
        "download_artifacts": False,
        "mutate_config": False,
    }

    blocked = _verify(request)
    truthy_non_bool = _verify(request, allow_live=cast("bool", 1))
    admitted = _verify(request, allow_live=True)

    assert blocked["fault"] == FreeLLMAPISyncFault.LIVE_MODE_NOT_ADMITTED.value
    assert truthy_non_bool["fault"] == (
        FreeLLMAPISyncFault.LIVE_MODE_NOT_ADMITTED.value
    )
    assert admitted["decision"] == "admitted_for_shadow"
    assert admitted["runtime_admitted"] is False
    assert admitted["declared_mode"] == "live"
    assert admitted["collection_observed"] is False
    assert admitted["live_boundary_sha256"] == _canonical_digest(
        request["live_boundary"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_requests", 9),
        ("timeout_seconds", 31),
        ("max_response_bytes", 1_048_577),
        ("download_artifacts", True),
        ("mutate_config", True),
    ],
)
def test_live_boundary_is_fail_closed(field: str, value: object) -> None:
    request = _request()
    request["mode"] = "live"
    request["live_boundary"] = {
        "network_enabled": True,
        "max_requests": 1,
        "timeout_seconds": 1,
        "max_response_bytes": 1,
        "download_artifacts": False,
        "mutate_config": False,
    }
    _object_dict(request["live_boundary"])[field] = value

    receipt = _verify(request, allow_live=True)

    assert receipt["fault"] == FreeLLMAPISyncFault.LIVE_BOUNDARY_INVALID.value


def test_unknown_fields_and_duplicate_inventory_ids_are_rejected() -> None:
    unknown = _request()
    candidate_release = _object_dict(unknown["candidate"])
    _object_dict(candidate_release["upstream"])["mutable_ref"] = "main"
    duplicate = _request()
    inventory = _object_dict(duplicate["owned_shims"])
    candidate = _object_list(inventory["candidate"])
    candidate.append(copy.deepcopy(candidate[0]))

    assert _verify(unknown)["fault"] == (
        FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID.value
    )
    assert _verify(duplicate)["fault"] == (
        FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID.value
    )


@pytest.mark.parametrize(
    ("patch", "fault"),
    [
        (
            lambda request: request.__setitem__("protocol", {"unserializable"}),
            FreeLLMAPISyncFault.REQUEST_INVALID,
        ),
        (
            lambda request: request["candidate"]["upstream"].__setitem__(
                "repository", "attacker/freellmapi"
            ),
            FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
        ),
        (
            lambda request: request["candidate"]["upstream"].__setitem__("tag", "latest"),
            FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID,
        ),
        (
            lambda request: request["candidate"]["supply_chain"].__setitem__(
                "sbom_sha256", ""
            ),
            FreeLLMAPISyncFault.SUPPLY_CHAIN_INVALID,
        ),
        (
            lambda request: request["owned_shims"]["candidate"][0].__setitem__(
                "id", "Not-A-Stable-ID"
            ),
            FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID,
        ),
        (
            lambda request: request["owned_shims"].__setitem__("candidate", []),
            FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID,
        ),
        (
            lambda request: request["owned_shims"]["candidate"][0].__setitem__(
                "upstream_exports", []
            ),
            FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY,
        ),
        (
            lambda request: request["owned_shims"]["candidate"][0].__setitem__(
                "upstream_exports", ["not-an-export"]
            ),
            FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY,
        ),
        (
            lambda request: request["plans"]["test_reuse"].__setitem__(
                "gludd_suites",
                [
                    "tests/unit/test_freellmapi_upstream_build.py",
                    "tests/unit/test_freellmapi_upstream_build.py",
                ],
            ),
            FreeLLMAPISyncFault.TEST_REUSE_PLAN_INVALID,
        ),
        (
            lambda request: request["live_boundary"].__setitem__("network_enabled", True),
            FreeLLMAPISyncFault.LIVE_BOUNDARY_INVALID,
        ),
        (
            lambda request: request.__setitem__("mode", "ambient"),
            FreeLLMAPISyncFault.LIVE_BOUNDARY_INVALID,
        ),
        (
            lambda request: request["zdd"].__setitem__("rollback_test_id", ""),
            FreeLLMAPISyncFault.ZDD_ROLLBACK_INVALID,
        ),
        (
            lambda request: request["candidate"]["upstream"].__setitem__(
                "commit", request["baseline"]["upstream"]["commit"]
            ),
            FreeLLMAPISyncFault.UPDATE_NOT_FORWARD,
        ),
    ],
)
def test_malformed_manifest_shapes_have_stable_content_free_faults(
    patch: object,
    fault: FreeLLMAPISyncFault,
) -> None:
    request = _request()
    assert callable(patch)
    patch(request)

    receipt = _verify(request)

    assert receipt["decision"] == "rejected"
    assert receipt["fault"] == fault.value
    manifest_digest = receipt["manifest_sha256"]
    assert isinstance(manifest_digest, str)
    assert len(manifest_digest) == 64


@pytest.mark.parametrize(
    "path",
    [
        "src/general_ludd/models\\escaped.py",
        "src/general_ludd/models/./escaped.py",
        "src/general_ludd/models//escaped.py",
        "src/general_ludd/models/VENDOR/freellmapi/escaped.py",
        "src/general_ludd/models/escape\x00.py",
        "src/general_ludd/models/link/../../../../etc/escaped.py",
    ],
)
def test_owned_shim_paths_reject_traversal_and_noncanonical_aliases(path: str) -> None:
    request = _request()
    inventory = _object_dict(request["owned_shims"])
    candidate = _object_list(inventory["candidate"])
    _object_dict(candidate[0])["path"] = path

    receipt = _verify(request)

    assert receipt["fault"] == FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY.value


def test_owned_shim_rejects_symlink_attestation() -> None:
    request = _request()
    inventory = _object_dict(request["owned_shims"])
    candidate = _object_list(inventory["candidate"])
    _object_dict(candidate[0])["file_type"] = "symlink"

    receipt = _verify(request)

    assert receipt["fault"] == FreeLLMAPISyncFault.OWNED_SHIM_BOUNDARY.value


def test_owned_shim_paths_reject_case_collisions() -> None:
    request = _request()
    inventory = _object_dict(request["owned_shims"])
    candidate = _object_list(inventory["candidate"])
    first = _object_dict(candidate[0])
    second = _object_dict(candidate[1])
    first_path = first["path"]
    assert isinstance(first_path, str)
    second["path"] = first_path.upper()

    receipt = _verify(request)

    assert receipt["fault"] == FreeLLMAPISyncFault.OWNED_SHIM_INVENTORY_INVALID.value


@pytest.mark.parametrize("substitution", ["tag", "tree", "artifact"])
def test_release_identity_binding_rejects_digest_tag_and_tree_substitution(
    substitution: str,
) -> None:
    request = _request()
    candidate = _object_dict(request["candidate"])
    if substitution == "tag":
        _object_dict(candidate["upstream"])["tag"] = "v0.12.1"
    elif substitution == "tree":
        upstream = _object_dict(candidate["upstream"])
        upstream["commit"], upstream["tree"] = upstream["tree"], upstream["commit"]
    else:
        supply = _object_dict(candidate["supply_chain"])
        supply["artifact_sha256"], supply["sbom_sha256"] = (
            supply["sbom_sha256"],
            supply["artifact_sha256"],
        )

    receipt = _verify(request)

    assert receipt["fault"] == FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID.value


def test_oversized_manifest_is_bounded_before_schema_processing() -> None:
    request = _request()
    request["padding"] = "x" * (64 * 1024)

    receipt = _verify(request)

    assert receipt["fault"] == "manifest_limit"
    manifest_digest = receipt["manifest_sha256"]
    assert isinstance(manifest_digest, str)
    assert len(manifest_digest) == 64


def test_recursive_manifest_fails_closed_without_recursion_error() -> None:
    request = _request()
    request["cycle"] = request

    receipt = _verify(request)

    assert receipt["fault"] == FreeLLMAPISyncFault.REQUEST_INVALID.value


class _ExplodingList(list[object]):
    def __iter__(self) -> Iterator[object]:
        raise RuntimeError("must not iterate attacker-controlled subclasses")


class _ExplodingString(str):
    def __len__(self) -> int:
        raise RuntimeError("must not inspect attacker-controlled subclasses")


class _ExplodingInt(int):
    def __abs__(self) -> int:
        raise RuntimeError("must not inspect attacker-controlled subclasses")


class _ExplodingHashKey:
    armed = False

    def __hash__(self) -> int:
        if self.armed:
            raise RuntimeError("must not hash attacker-controlled keys")
        return 1


@pytest.mark.parametrize(
    ("payload", "fault"),
    [
        ([0] * 513, "manifest_limit"),
        ({index: None for index in range(513)}, "manifest_limit"),
        ({1: "non-string-key"}, "request_invalid"),
        (_ExplodingList(["untrusted"]), "request_invalid"),
        (_ExplodingString("untrusted"), "request_invalid"),
        (_ExplodingInt(1), "request_invalid"),
        (10**10_000, "manifest_limit"),
        ("\ud800", "request_invalid"),
    ],
    ids=[
        "wide-list",
        "wide-mapping",
        "non-string-key",
        "list-subclass",
        "string-subclass",
        "int-subclass",
        "huge-int",
        "lone-surrogate",
    ],
)
def test_malformed_and_pathological_manifest_values_fail_closed(
    payload: object,
    fault: str,
) -> None:
    request = _request()
    request["pathological"] = payload

    receipt = _verify(request)

    assert receipt["fault"] == fault


def test_manifest_depth_and_aliased_containers_are_bounded() -> None:
    nested: list[object] = []
    for _ in range(25):
        nested = [nested]
    repeated: list[object] = []
    aliased = _request()
    aliased["aliases"] = [repeated, repeated]
    deep = _request()
    deep["nested"] = nested

    assert _verify(aliased)["fault"] == FreeLLMAPISyncFault.REQUEST_INVALID.value
    assert _verify(deep)["fault"] == "manifest_limit"


@pytest.mark.parametrize(("from_version", "to_version"), [(2, 1), (2, 2)])
def test_schema_plan_rejects_downgrade_and_noop(
    from_version: int,
    to_version: int,
) -> None:
    request = _request()
    plans = _object_dict(request["plans"])
    schema = _object_dict(plans["schema"])
    schema["from_version"] = from_version
    schema["to_version"] = to_version

    receipt = _verify(request)

    assert receipt["fault"] == FreeLLMAPISyncFault.SCHEMA_PLAN_INVALID.value


def test_receipt_scope_rejects_replay_and_cross_project_reuse() -> None:
    request = _request()

    admitted = _verify(request)
    replayed = _verify(request, operation_id=_digest("another-operation"))
    cross_project = _verify(
        request,
        project_identity_sha256=_digest("another-project"),
    )
    scope_verifier = sync_verifier.verify_sync_receipt_scope

    assert replayed["fault"] == "receipt_scope_invalid"
    assert cross_project["fault"] == "receipt_scope_invalid"
    assert scope_verifier(
        admitted,
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=None,
    )
    assert not scope_verifier(
        admitted,
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_digest("replayed-operation"),
        prior_receipt_sha256=None,
    )


def test_receipt_scope_binds_prior_receipt_and_rejects_invalid_trusted_scope() -> None:
    prior = _digest("prior-admission-receipt")
    request = _request()
    _object_dict(request["receipt_scope"])["prior_receipt_sha256"] = prior

    admitted = _verify(request, prior_receipt_sha256=prior)
    wrong_chain = _verify(request)
    invalid_scope = verify_sync_admission(
        request,
        project_identity_sha256="not-a-digest",
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=prior,
    )
    hostile_scope = verify_sync_admission(
        request,
        project_identity_sha256=cast("str", _ExplodingString(_PROJECT_IDENTITY)),
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=prior,
    )
    scope_verifier = sync_verifier.verify_sync_receipt_scope

    assert admitted["decision"] == "admitted_for_shadow"
    assert wrong_chain["fault"] == "receipt_scope_invalid"
    assert invalid_scope["fault"] == "receipt_scope_invalid"
    assert hostile_scope["fault"] == "receipt_scope_invalid"
    assert not scope_verifier(
        admitted,
        project_identity_sha256="not-a-digest",
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=prior,
    )
    assert not scope_verifier(
        [],
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=None,
    )


def test_receipt_scope_rejects_non_string_key_without_rehashing_it() -> None:
    hostile_key = _ExplodingHashKey()
    receipt: object = {hostile_key: None}
    hostile_key.armed = True

    assert not sync_verifier.verify_sync_receipt_scope(
        receipt,
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=None,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("fault", "update_failed"),
        ("collection_observed", True),
        (
            "update_diff",
            {
                "added": ["quota_normalizer"],
                "changed": ["score_adapter"],
                "removed": [],
                "unchanged": ["catalog_adapter"],
                "counts": {
                    "added": 99,
                    "changed": 1,
                    "removed": 0,
                    "unchanged": 1,
                },
            },
        ),
    ],
)
def test_receipt_scope_rejects_semantically_forged_fields(
    field: str,
    value: object,
) -> None:
    admission = _verify(_request())
    admission[field] = value

    assert not sync_verifier.verify_sync_receipt_scope(
        admission,
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=None,
    )
    outcome = sync_verifier.verify_sync_outcome(
        admission,
        _outcome(
            "cancelled",
            mutation_started=False,
            rollback_attempted=False,
            rollback_succeeded=False,
            cleanup_succeeded=True,
        ),
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=None,
    )
    assert outcome["decision"] == "rejected"
    assert outcome["fault"] == FreeLLMAPISyncFault.RECEIPT_SCOPE_INVALID.value


def _outcome(
    state: str,
    *,
    mutation_started: bool,
    rollback_attempted: bool,
    rollback_succeeded: bool,
    cleanup_succeeded: bool,
) -> dict[str, object]:
    return {
        "state": state,
        "mutation_started": mutation_started,
        "rollback_attempted": rollback_attempted,
        "rollback_succeeded": rollback_succeeded,
        "cleanup_attempted": True,
        "cleanup_succeeded": cleanup_succeeded,
    }


def _verify_outcome(outcome: dict[str, object]) -> dict[str, object]:
    return sync_verifier.verify_sync_outcome(
        _verify(_request()),
        outcome,
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=None,
    )


@pytest.mark.parametrize(
    ("state", "mutation_started", "rollback_attempted", "rollback_succeeded", "fault"),
    [
        ("interrupted", True, True, True, "update_interrupted"),
        ("cancelled", False, False, False, "update_cancelled"),
        ("cancelled", True, True, True, "update_cancelled"),
    ],
)
def test_interruption_and_cancellation_are_content_free_non_runnable_outcomes(
    state: str,
    mutation_started: bool,
    rollback_attempted: bool,
    rollback_succeeded: bool,
    fault: str,
) -> None:
    receipt = _verify_outcome(
        _outcome(
            state,
            mutation_started=mutation_started,
            rollback_attempted=rollback_attempted,
            rollback_succeeded=rollback_succeeded,
            cleanup_succeeded=True,
        )
    )

    assert receipt["decision"] in {"aborted", "rolled_back"}
    assert receipt["runtime_admitted"] is False
    assert receipt["fault"] == fault
    assert set(receipt) == {
        "schema_version",
        "protocol",
        "decision",
        "runtime_admitted",
        "fault",
        "receipt_scope_sha256",
        "admission_receipt_sha256",
        "outcome_sha256",
    }


@pytest.mark.parametrize(
    ("rollback_succeeded", "cleanup_succeeded", "fault"),
    [
        (False, False, "rollback_failed"),
        (False, True, "rollback_failed"),
        (True, False, "cleanup_failed"),
        (True, True, "update_interrupted"),
    ],
)
def test_rollback_and_cleanup_failures_have_stable_precedence(
    rollback_succeeded: bool,
    cleanup_succeeded: bool,
    fault: str,
) -> None:
    receipt = _verify_outcome(
        _outcome(
            "interrupted",
            mutation_started=True,
            rollback_attempted=True,
            rollback_succeeded=rollback_succeeded,
            cleanup_succeeded=cleanup_succeeded,
        )
    )

    assert receipt["fault"] == fault


@pytest.mark.parametrize(
    ("outcome", "decision", "fault"),
    [
        (
            _outcome(
                "completed",
                mutation_started=True,
                rollback_attempted=False,
                rollback_succeeded=False,
                cleanup_succeeded=True,
            ),
            "shadow_updated",
            None,
        ),
        (
            _outcome(
                "failed",
                mutation_started=False,
                rollback_attempted=False,
                rollback_succeeded=False,
                cleanup_succeeded=True,
            ),
            "aborted",
            "update_failed",
        ),
    ],
)
def test_completed_and_primary_failure_outcomes_remain_non_runnable(
    outcome: dict[str, object],
    decision: str,
    fault: str | None,
) -> None:
    receipt = _verify_outcome(outcome)

    assert receipt["decision"] == decision
    assert receipt["fault"] == fault
    assert receipt["runtime_admitted"] is False


@pytest.mark.parametrize(
    "outcome",
    [
        {
            **_outcome(
                "unknown",
                mutation_started=False,
                rollback_attempted=False,
                rollback_succeeded=False,
                cleanup_succeeded=True,
            )
        },
        {
            **_outcome(
                "cancelled",
                mutation_started=False,
                rollback_attempted=False,
                rollback_succeeded=False,
                cleanup_succeeded=True,
            ),
            "state": [],
        },
        {
            **_outcome(
                "cancelled",
                mutation_started=False,
                rollback_attempted=False,
                rollback_succeeded=False,
                cleanup_succeeded=True,
            ),
            "cleanup_attempted": False,
        },
        _outcome(
            "interrupted",
            mutation_started=True,
            rollback_attempted=False,
            rollback_succeeded=False,
            cleanup_succeeded=True,
        ),
        _outcome(
            "completed",
            mutation_started=False,
            rollback_attempted=False,
            rollback_succeeded=False,
            cleanup_succeeded=True,
        ),
        {
            **_outcome(
                "cancelled",
                mutation_started=False,
                rollback_attempted=False,
                rollback_succeeded=False,
                cleanup_succeeded=True,
            ),
            "cleanup_succeeded": 1,
        },
    ],
)
def test_malformed_outcomes_fail_closed_and_keep_scope_binding(
    outcome: dict[str, object],
) -> None:
    admission = _verify(_request())
    receipt = _verify_outcome(outcome)

    assert receipt["decision"] == "rejected"
    assert receipt["fault"] == "update_outcome_invalid"
    assert receipt["receipt_scope_sha256"] == admission["receipt_scope_sha256"]


@pytest.mark.parametrize("removed", ["manifest_sha256", "candidate_identity_sha256"])
def test_outcome_rejects_truncated_or_forged_admission_receipt(removed: str) -> None:
    admission = _verify(_request())
    admission.pop(removed)
    receipt = sync_verifier.verify_sync_outcome(
        admission,
        _outcome(
            "cancelled",
            mutation_started=False,
            rollback_attempted=False,
            rollback_succeeded=False,
            cleanup_succeeded=True,
        ),
        project_identity_sha256=_PROJECT_IDENTITY,
        operation_id=_OPERATION_ID,
        prior_receipt_sha256=None,
    )

    assert receipt["decision"] == "rejected"
    assert receipt["fault"] == "receipt_scope_invalid"
