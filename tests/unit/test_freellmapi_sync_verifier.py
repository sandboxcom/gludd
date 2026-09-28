"""Offline-first contracts for the v0.1.2 FreeLLMAPI sync verifier."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from general_ludd.models.freellmapi_sync_verifier import (
    FREELLMAPI_SYNC_PROTOCOL,
    FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
    FreeLLMAPISyncFault,
    build_owned_shim_diff,
    verify_sync_admission,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _release(*, tag: str, commit: str, tree: str, label: str) -> dict[str, object]:
    archive = _digest(f"{label}-archive")
    return {
        "upstream": {
            "repository": "tashfeenahmed/freellmapi",
            "tag": tag,
            "commit": commit,
            "tree": tree,
            "archive_sha256": archive,
        },
        "supply_chain": {
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
        },
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


def test_offline_admission_is_deterministic_content_free_and_non_runnable() -> None:
    request = _request()

    first = verify_sync_admission(request)
    second = verify_sync_admission(copy.deepcopy(request))

    assert first == second
    assert first == {
        "schema_version": 1,
        "protocol": FREELLMAPI_SYNC_RECEIPT_PROTOCOL,
        "decision": "admitted_for_shadow",
        "runtime_admitted": False,
        "fault": None,
        "manifest_sha256": first["manifest_sha256"],
        "baseline_identity_sha256": first["baseline_identity_sha256"],
        "candidate_identity_sha256": first["candidate_identity_sha256"],
        "owned_shim_inventory_sha256": first["owned_shim_inventory_sha256"],
        "plans_sha256": first["plans_sha256"],
        "rollback_sha256": first["rollback_sha256"],
        "update_diff": {
            "added": ["quota_normalizer"],
            "changed": ["score_adapter"],
            "removed": [],
            "unchanged": ["catalog_adapter"],
            "counts": {"added": 1, "changed": 1, "removed": 0, "unchanged": 1},
        },
    }
    encoded = json.dumps(first, sort_keys=True)
    assert "scoreCandidate" not in encoded
    assert "src/general_ludd" not in encoded


def test_inventory_order_does_not_change_diff_or_receipt() -> None:
    first_request = _request()
    second_request = copy.deepcopy(first_request)
    second_request["owned_shims"]["baseline"].reverse()  # type: ignore[index,union-attr]
    second_request["owned_shims"]["candidate"].reverse()  # type: ignore[index,union-attr]
    second_request["plans"]["test_reuse"]["gludd_suites"].reverse()  # type: ignore[index,union-attr]

    assert verify_sync_admission(first_request) == verify_sync_admission(second_request)


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

    receipt = verify_sync_admission(request)

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
    }


def test_rejection_receipt_does_not_echo_untrusted_content() -> None:
    request = _request()
    secret = "TOP-SECRET-UPSTREAM-CONTENT"
    request["owned_shims"]["candidate"][0]["upstream_exports"] = [secret]  # type: ignore[index]
    request["owned_shims"]["candidate"][0]["owner"] = secret  # type: ignore[index]

    receipt = verify_sync_admission(request)

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

    blocked = verify_sync_admission(request)
    admitted = verify_sync_admission(request, allow_live=True)

    assert blocked["fault"] == FreeLLMAPISyncFault.LIVE_MODE_NOT_ADMITTED.value
    assert admitted["decision"] == "admitted_for_shadow"
    assert admitted["runtime_admitted"] is False


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
    request["live_boundary"][field] = value  # type: ignore[index]

    receipt = verify_sync_admission(request, allow_live=True)

    assert receipt["fault"] == FreeLLMAPISyncFault.LIVE_BOUNDARY_INVALID.value


def test_unknown_fields_and_duplicate_inventory_ids_are_rejected() -> None:
    unknown = _request()
    unknown["candidate"]["upstream"]["mutable_ref"] = "main"  # type: ignore[index]
    duplicate = _request()
    duplicate["owned_shims"]["candidate"].append(  # type: ignore[index,union-attr]
        copy.deepcopy(duplicate["owned_shims"]["candidate"][0])  # type: ignore[index]
    )

    assert verify_sync_admission(unknown)["fault"] == (
        FreeLLMAPISyncFault.IMMUTABLE_IDENTITY_INVALID.value
    )
    assert verify_sync_admission(duplicate)["fault"] == (
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

    receipt = verify_sync_admission(request)

    assert receipt["decision"] == "rejected"
    assert receipt["fault"] == fault.value
    assert len(receipt["manifest_sha256"]) == 64
