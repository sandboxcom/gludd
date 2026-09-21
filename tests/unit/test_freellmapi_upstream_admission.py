"""Contracts for provenance-bound FreeLLMAPI upstream candidates."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

import general_ludd.models.freellmapi_upstream_admission as admission
from general_ludd.models.freellmapi_upstream_admission import (
    FREELLMAPI_ADMISSION_SCHEMA_VERSION,
    FreeLLMAPIAdmissionError,
    FreeLLMAPIAdmissionFault,
    build_candidate_lock,
    validate_candidate_lock,
)

_ROOT = Path(__file__).resolve().parents[2]
_VENDOR = _ROOT / "src/general_ludd/models/vendor/freellmapi"
_BUNDLE = (_VENDOR / "scoring_kernel.js").read_bytes()
_MANIFEST = (_VENDOR / "scoring_kernel.json").read_bytes()
_COMMIT = "4" * 40
_TREE = "5" * 40
_TAG = "v0.11.1"
_SIGNATURE = "-----BEGIN PGP SIGNATURE-----\ntest-only-signature\n-----END PGP SIGNATURE-----"
_PAYLOAD = f"tree {_TREE}\nparent {'6' * 40}\n"
_EXPORTS = (
    "reliabilityPosterior",
    "expectedReliability",
    "speedScore",
    "headroomFactor",
    "rateWindowHeadroomFactor",
    "rateLimitFactor",
)
_SCORING = "\n".join(
    f"export function {name}(): number {{ return 1; }}" for name in _EXPORTS
).encode()
_LICENSE = b"""MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND.
"""
_PACKAGE_LOCK = json.dumps(
    {
        "name": "@freellmapi/monorepo",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {},
    },
    sort_keys=True,
).encode()


def _repository() -> dict[str, object]:
    return {
        "id": 8675309,
        "full_name": "tashfeenahmed/freellmapi",
        "html_url": "https://github.com/tashfeenahmed/freellmapi",
        "archived": False,
    }


def _release() -> dict[str, object]:
    return {
        "id": 112233,
        "tag_name": _TAG,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-20T15:14:00Z",
        "html_url": f"https://github.com/tashfeenahmed/freellmapi/releases/tag/{_TAG}",
    }


def _tag_ref() -> dict[str, object]:
    return {
        "ref": f"refs/tags/{_TAG}",
        "object": {"type": "commit", "sha": _COMMIT},
    }


def _commit() -> dict[str, object]:
    return {
        "sha": _COMMIT,
        "html_url": f"https://github.com/tashfeenahmed/freellmapi/commit/{_COMMIT}",
        "commit": {
            "tree": {"sha": _TREE},
            "verification": {
                "verified": True,
                "reason": "valid",
                "signature": _SIGNATURE,
                "payload": _PAYLOAD,
                "verified_at": "2026-09-20T15:12:00Z",
            },
        },
    }


def _archive(
    *,
    files: dict[str, bytes] | None = None,
    duplicate: str | None = None,
    symlink: str | None = None,
) -> bytes:
    root = "tashfeenahmed-freellmapi-4191d8e"
    entries = files or {
        "LICENSE": _LICENSE,
        "package-lock.json": _PACKAGE_LOCK,
        "server/src/services/scoring.ts": _SCORING,
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for relative, body in entries.items():
            info = tarfile.TarInfo(f"{root}/{relative}")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        if duplicate is not None:
            body = entries[duplicate]
            info = tarfile.TarInfo(f"{root}/{duplicate}")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        if symlink is not None:
            info = tarfile.TarInfo(f"{root}/{symlink}")
            info.type = tarfile.SYMTYPE
            info.linkname = f"{root}/LICENSE"
            archive.addfile(info)
    return stream.getvalue()


def _build(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "expected_tag": _TAG,
        "expected_commit": _COMMIT,
        "repository_metadata": _repository(),
        "release_metadata": _release(),
        "tag_ref_metadata": _tag_ref(),
        "commit_metadata": _commit(),
        "archive_bytes": _archive(),
        "admitted_manifest_bytes": _MANIFEST,
        "admitted_bundle_bytes": _BUNDLE,
    }
    values.update(overrides)
    return build_candidate_lock(**values)  # type: ignore[arg-type]


def _reidentify(lock: dict[str, object]) -> None:
    unsigned = copy.deepcopy(lock)
    unsigned.pop("candidate_id", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    lock["candidate_id"] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def test_build_candidate_lock_is_deterministic_non_admitting_and_content_free() -> None:
    archive = _archive()

    first = _build(archive_bytes=archive)
    second = _build(archive_bytes=archive)

    assert first == second
    assert first["schema_version"] == FREELLMAPI_ADMISSION_SCHEMA_VERSION
    assert first["candidate_id"].startswith("sha256:")
    assert first["decision"] == {
        "runtime_admitted": False,
        "state": "pending_frozen_delta",
        "required_gate": "freellmapi-frozen-delta-v1",
    }
    assert first["upstream"] == {
        "repository": "tashfeenahmed/freellmapi",
        "repository_id": 8675309,
        "release_id": 112233,
        "tag": _TAG,
        "commit": _COMMIT,
        "tree": _TREE,
        "published_at": "2026-09-20T15:14:00Z",
        "verified_at": "2026-09-20T15:12:00Z",
        "signature_sha256": hashlib.sha256(_SIGNATURE.encode()).hexdigest(),
        "signed_payload_sha256": hashlib.sha256(_PAYLOAD.encode()).hexdigest(),
    }
    assert first["archive"] == {
        "sha256": hashlib.sha256(archive).hexdigest(),
        "size_bytes": len(archive),
    }
    sources = first["sources"]
    assert isinstance(sources, dict)
    assert sources["license"] == {
        "path": "LICENSE",
        "sha256": hashlib.sha256(_LICENSE).hexdigest(),
        "size_bytes": len(_LICENSE),
        "spdx": "MIT",
    }
    assert sources["package_lock"]["sha256"] == hashlib.sha256(_PACKAGE_LOCK).hexdigest()
    assert sources["scoring"]["sha256"] == hashlib.sha256(_SCORING).hexdigest()
    assert sources["scoring"]["symbols_present"] == list(_EXPORTS)
    admitted = first["admitted_artifact"]
    assert admitted["abi_version"] == 1
    assert admitted["bundle_sha256"] == hashlib.sha256(_BUNDLE).hexdigest()
    assert admitted["manifest_sha256"] == hashlib.sha256(_MANIFEST).hexdigest()

    encoded = json.dumps(first, sort_keys=True)
    assert _SIGNATURE not in encoded
    assert _PAYLOAD not in encoded
    assert _SCORING.decode() not in encoded


def test_admission_boundary_declares_its_operator_facing_public_api() -> None:
    assert admission.__all__ == [
        "FREELLMAPI_ADMISSION_SCHEMA_VERSION",
        "FREELLMAPI_FROZEN_DELTA_GATE",
        "FREELLMAPI_REPOSITORY",
        "FREELLMAPI_SCORING_SOURCE",
        "FreeLLMAPIAdmissionError",
        "FreeLLMAPIAdmissionFault",
        "build_candidate_lock",
        "validate_candidate_lock",
    ]
    assert FreeLLMAPIAdmissionError.__module__ == (
        "general_ludd.models.freellmapi_upstream_source"
    )
    assert FreeLLMAPIAdmissionFault.__module__ == (
        "general_ludd.models.freellmapi_upstream_source"
    )


@pytest.mark.parametrize(
    ("release_patch", "fault"),
    [
        ({"draft": True}, FreeLLMAPIAdmissionFault.RELEASE_NOT_STABLE),
        ({"prerelease": True}, FreeLLMAPIAdmissionFault.RELEASE_NOT_STABLE),
        ({"tag_name": "v0.11.2"}, FreeLLMAPIAdmissionFault.RELEASE_IDENTITY),
        ({"published_at": None}, FreeLLMAPIAdmissionFault.RELEASE_IDENTITY),
    ],
)
def test_release_metadata_fails_closed(
    release_patch: dict[str, object], fault: FreeLLMAPIAdmissionFault
) -> None:
    release = _release()
    release.update(release_patch)

    with pytest.raises(FreeLLMAPIAdmissionError) as caught:
        _build(release_metadata=release)

    assert caught.value.fault is fault
    assert str(caught.value) == fault.value


@pytest.mark.parametrize(
    ("verification_patch", "fault"),
    [
        ({"verified": False}, FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED),
        ({"reason": "unsigned"}, FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED),
        ({"signature": ""}, FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED),
        ({"payload": None}, FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED),
        ({"verified_at": None}, FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED),
    ],
)
def test_commit_signature_evidence_fails_closed(
    verification_patch: dict[str, object], fault: FreeLLMAPIAdmissionFault
) -> None:
    commit = _commit()
    inner = commit["commit"]
    assert isinstance(inner, dict)
    verification = inner["verification"]
    assert isinstance(verification, dict)
    verification.update(verification_patch)

    with pytest.raises(FreeLLMAPIAdmissionError) as caught:
        _build(commit_metadata=commit)

    assert caught.value.fault is fault


def test_repository_tag_and_commit_identity_are_all_bound() -> None:
    repository = _repository()
    repository["full_name"] = "attacker/freellmapi"
    with pytest.raises(FreeLLMAPIAdmissionError) as repository_error:
        _build(repository_metadata=repository)
    assert repository_error.value.fault is FreeLLMAPIAdmissionFault.REPOSITORY_IDENTITY

    tag_ref = _tag_ref()
    tag_ref["object"] = {"type": "commit", "sha": "7" * 40}
    with pytest.raises(FreeLLMAPIAdmissionError) as tag_error:
        _build(tag_ref_metadata=tag_ref)
    assert tag_error.value.fault is FreeLLMAPIAdmissionFault.TAG_COMMIT_MISMATCH

    commit = _commit()
    commit["sha"] = "8" * 40
    with pytest.raises(FreeLLMAPIAdmissionError) as commit_error:
        _build(commit_metadata=commit)
    assert commit_error.value.fault is FreeLLMAPIAdmissionFault.COMMIT_IDENTITY


def test_archive_rejects_missing_duplicate_symlink_and_unsafe_members() -> None:
    missing = _archive(files={"LICENSE": _LICENSE, "package-lock.json": _PACKAGE_LOCK})
    with pytest.raises(FreeLLMAPIAdmissionError) as missing_error:
        _build(archive_bytes=missing)
    assert missing_error.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT

    duplicate = _archive(duplicate="LICENSE")
    with pytest.raises(FreeLLMAPIAdmissionError) as duplicate_error:
        _build(archive_bytes=duplicate)
    assert duplicate_error.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT

    symlink = _archive(
        files={"package-lock.json": _PACKAGE_LOCK, "server/src/services/scoring.ts": _SCORING},
        symlink="LICENSE",
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as symlink_error:
        _build(archive_bytes=symlink)
    assert symlink_error.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT

    unsafe = _archive(
        files={
            "LICENSE": _LICENSE,
            "package-lock.json": _PACKAGE_LOCK,
            "server/src/services/scoring.ts": _SCORING,
            "../escape": b"never extracted",
        }
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as unsafe_error:
        _build(archive_bytes=unsafe)
    assert unsafe_error.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT


def test_license_lock_and_current_admitted_artifact_fail_closed() -> None:
    incompatible = _archive(
        files={
            "LICENSE": b"proprietary",
            "package-lock.json": _PACKAGE_LOCK,
            "server/src/services/scoring.ts": _SCORING,
        }
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as license_error:
        _build(archive_bytes=incompatible)
    assert license_error.value.fault is FreeLLMAPIAdmissionFault.LICENSE_INCOMPATIBLE

    invalid_lock = _archive(
        files={
            "LICENSE": _LICENSE,
            "package-lock.json": b"[]",
            "server/src/services/scoring.ts": _SCORING,
        }
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as lock_error:
        _build(archive_bytes=invalid_lock)
    assert lock_error.value.fault is FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID

    with pytest.raises(FreeLLMAPIAdmissionError) as artifact_error:
        _build(admitted_bundle_bytes=_BUNDLE + b"\n// drift")
    assert artifact_error.value.fault is FreeLLMAPIAdmissionFault.ADMITTED_ARTIFACT_INVALID


def test_missing_candidate_symbols_records_rejection_without_replacing_runtime() -> None:
    archive = _archive(
        files={
            "LICENSE": _LICENSE,
            "package-lock.json": _PACKAGE_LOCK,
            "server/src/services/scoring.ts": b"export function unrelated() { return 1; }",
        }
    )

    lock = _build(archive_bytes=archive)

    assert lock["decision"] == {
        "runtime_admitted": False,
        "state": "rejected_source_symbols",
        "required_gate": "freellmapi-source-symbols-v1",
    }
    scoring = lock["sources"]["scoring"]
    assert scoring["symbols_present"] == []
    assert scoring["symbols_missing"] == list(_EXPORTS)


def test_partial_source_symbol_rejection_produces_a_self_consistent_lock() -> None:
    partial_scoring = "\n".join(
        f"export function {name}(): number {{ return 1; }}" for name in _EXPORTS[::2]
    ).encode()
    archive = _archive(
        files={
            "LICENSE": _LICENSE,
            "package-lock.json": _PACKAGE_LOCK,
            "server/src/services/scoring.ts": partial_scoring,
        }
    )

    lock = _build(archive_bytes=archive)

    validate_candidate_lock(
        lock,
        expected_tag=_TAG,
        expected_commit=_COMMIT,
        admitted_manifest_bytes=_MANIFEST,
        admitted_bundle_bytes=_BUNDLE,
    )
    scoring = lock["sources"]["scoring"]
    assert scoring["symbols_present"] == list(_EXPORTS[::2])
    assert scoring["symbols_missing"] == list(_EXPORTS[1::2])


def test_candidate_lock_validator_detects_any_material_tampering() -> None:
    lock = _build()
    validate_candidate_lock(
        lock,
        expected_tag=_TAG,
        expected_commit=_COMMIT,
        admitted_manifest_bytes=_MANIFEST,
        admitted_bundle_bytes=_BUNDLE,
    )
    tampered = copy.deepcopy(lock)
    tampered["upstream"]["commit"] = "9" * 40

    with pytest.raises(FreeLLMAPIAdmissionError) as caught:
        validate_candidate_lock(
            tampered,
            expected_tag=_TAG,
            expected_commit=_COMMIT,
            admitted_manifest_bytes=_MANIFEST,
            admitted_bundle_bytes=_BUNDLE,
        )

    assert caught.value.fault is FreeLLMAPIAdmissionFault.CANDIDATE_LOCK_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        lambda lock: lock.__setitem__("schema_version", 2),
        lambda lock: lock["upstream"].__setitem__("repository", "attacker/repo"),
        lambda lock: lock["upstream"].__setitem__("repository_id", False),
        lambda lock: lock["upstream"].__setitem__("tree", "not-a-sha"),
        lambda lock: lock["upstream"].__setitem__("published_at", "2026-09-20T15:14:00"),
        lambda lock: lock["upstream"].__setitem__("signature_sha256", "short"),
        lambda lock: lock["archive"].__setitem__("size_bytes", 0),
        lambda lock: lock["sources"]["license"].__setitem__("path", "COPYING"),
        lambda lock: lock["sources"]["license"].__setitem__("spdx", "UNKNOWN"),
        lambda lock: lock["sources"]["scoring"].__setitem__("symbols_present", "all"),
        lambda lock: lock["decision"].__setitem__("runtime_admitted", True),
        lambda lock: lock.__setitem__("admitted_artifact", {}),
    ],
)
def test_candidate_lock_schema_cannot_be_reidentified_after_tampering(mutation: object) -> None:
    lock = _build()
    mutation(lock)  # type: ignore[operator]
    _reidentify(lock)

    with pytest.raises(FreeLLMAPIAdmissionError) as caught:
        validate_candidate_lock(
            lock,
            expected_tag=_TAG,
            expected_commit=_COMMIT,
            admitted_manifest_bytes=_MANIFEST,
            admitted_bundle_bytes=_BUNDLE,
        )

    assert caught.value.fault is FreeLLMAPIAdmissionFault.CANDIDATE_LOCK_INVALID


def test_builder_rejects_archived_repo_unstable_tag_and_wrong_ref() -> None:
    repository = _repository()
    repository["archived"] = True
    with pytest.raises(FreeLLMAPIAdmissionError) as archived:
        _build(repository_metadata=repository)
    assert archived.value.fault is FreeLLMAPIAdmissionFault.REPOSITORY_IDENTITY

    with pytest.raises(FreeLLMAPIAdmissionError) as unstable:
        _build(expected_tag="v0.11.1-rc.1")
    assert unstable.value.fault is FreeLLMAPIAdmissionFault.RELEASE_IDENTITY

    tag_ref = _tag_ref()
    tag_ref["ref"] = "refs/tags/v0.11.0"
    with pytest.raises(FreeLLMAPIAdmissionError) as wrong_ref:
        _build(tag_ref_metadata=tag_ref)
    assert wrong_ref.value.fault is FreeLLMAPIAdmissionFault.TAG_COMMIT_MISMATCH


def test_builder_rejects_invalid_commit_tree_timestamp_payload_and_signature_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = _commit()
    inner = commit["commit"]
    assert isinstance(inner, dict)
    tree = inner["tree"]
    assert isinstance(tree, dict)
    tree["sha"] = "bad"
    with pytest.raises(FreeLLMAPIAdmissionError) as bad_tree:
        _build(commit_metadata=commit)
    assert bad_tree.value.fault is FreeLLMAPIAdmissionFault.COMMIT_IDENTITY

    commit = _commit()
    inner = commit["commit"]
    assert isinstance(inner, dict)
    verification = inner["verification"]
    assert isinstance(verification, dict)
    verification["verified_at"] = "not-a-date"
    with pytest.raises(FreeLLMAPIAdmissionError) as bad_timestamp:
        _build(commit_metadata=commit)
    assert bad_timestamp.value.fault is FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED

    commit = _commit()
    inner = commit["commit"]
    assert isinstance(inner, dict)
    verification = inner["verification"]
    assert isinstance(verification, dict)
    verification["payload"] = "parent only\n"
    with pytest.raises(FreeLLMAPIAdmissionError) as bad_payload:
        _build(commit_metadata=commit)
    assert bad_payload.value.fault is FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED

    monkeypatch.setattr(admission, "_MAX_SIGNATURE_BYTES", 8)
    with pytest.raises(FreeLLMAPIAdmissionError) as too_large:
        _build()
    assert too_large.value.fault is FreeLLMAPIAdmissionFault.COMMIT_UNVERIFIED


def test_builder_rejects_malformed_empty_multiroot_and_limited_archives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(FreeLLMAPIAdmissionError) as malformed:
        _build(archive_bytes=b"\x1f\x8bnot-a-tar")
    assert malformed.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_INVALID

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz"):
        pass
    with pytest.raises(FreeLLMAPIAdmissionError) as empty:
        _build(archive_bytes=stream.getvalue())
    assert empty.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for root, relative, body in (
            ("root-a", "LICENSE", _LICENSE),
            ("root-a", "package-lock.json", _PACKAGE_LOCK),
            ("root-a", "server/src/services/scoring.ts", _SCORING),
            ("root-b", "README.md", b"second root"),
        ):
            info = tarfile.TarInfo(f"{root}/{relative}")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    with pytest.raises(FreeLLMAPIAdmissionError) as multiroot:
        _build(archive_bytes=stream.getvalue())
    assert multiroot.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_LAYOUT

    archive_bytes = _archive()
    monkeypatch.setattr(admission, "_MAX_ARCHIVE_BYTES", len(archive_bytes) - 1)
    with pytest.raises(FreeLLMAPIAdmissionError) as limited:
        _build(archive_bytes=archive_bytes)
    assert limited.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_LIMIT


def test_builder_rejects_invalid_utf8_and_malformed_lock_content() -> None:
    invalid_license = _archive(
        files={
            "LICENSE": b"\xff",
            "package-lock.json": _PACKAGE_LOCK,
            "server/src/services/scoring.ts": _SCORING,
        }
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as license_error:
        _build(archive_bytes=invalid_license)
    assert license_error.value.fault is FreeLLMAPIAdmissionFault.LICENSE_INCOMPATIBLE

    invalid_json = _archive(
        files={
            "LICENSE": _LICENSE,
            "package-lock.json": b"{",
            "server/src/services/scoring.ts": _SCORING,
        }
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as json_error:
        _build(archive_bytes=invalid_json)
    assert json_error.value.fault is FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID

    wrong_lock = json.dumps({"name": "other", "lockfileVersion": 3, "packages": {}}).encode()
    invalid_identity = _archive(
        files={
            "LICENSE": _LICENSE,
            "package-lock.json": wrong_lock,
            "server/src/services/scoring.ts": _SCORING,
        }
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as identity_error:
        _build(archive_bytes=invalid_identity)
    assert identity_error.value.fault is FreeLLMAPIAdmissionFault.PACKAGE_LOCK_INVALID

    invalid_source = _archive(
        files={
            "LICENSE": _LICENSE,
            "package-lock.json": _PACKAGE_LOCK,
            "server/src/services/scoring.ts": b"\xff",
        }
    )
    with pytest.raises(FreeLLMAPIAdmissionError) as source_error:
        _build(archive_bytes=invalid_source)
    assert source_error.value.fault is FreeLLMAPIAdmissionFault.ARCHIVE_INVALID


@pytest.mark.parametrize(
    "manifest_patch",
    [
        {"exports": []},
        {"upstream_commit": "0" * 40},
        {"license": "UNKNOWN"},
    ],
)
def test_builder_rejects_drift_in_current_admitted_manifest(
    manifest_patch: dict[str, object],
) -> None:
    manifest = json.loads(_MANIFEST)
    manifest.update(manifest_patch)

    with pytest.raises(FreeLLMAPIAdmissionError) as caught:
        _build(admitted_manifest_bytes=json.dumps(manifest).encode())

    assert caught.value.fault is FreeLLMAPIAdmissionFault.ADMITTED_ARTIFACT_INVALID
