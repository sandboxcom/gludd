"""Tests for the fail-closed release asset matrix."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.verify_release_asset_matrix import (
    FOUNDATION_RELEASE_NAMES,
    MAX_PUBLISHED_ROLLBACK_ERRORS,
    REQUIRED_SMOKE_CHECKS,
    ROLLBACK_RECEIPT_SCHEMA_VERSION,
    distribution_version,
    main,
    referenced_collection_artifacts,
    verify_published_rollback_receipt,
    verify_release_asset_matrix,
    write_release_manifest,
    write_rollback_receipt,
)

VERSION = "0.1.0-beta.4"
DIST_VERSION = "0.1.0b4"
PRIOR_VERSION = "0.1.0-beta.3"
SOURCE_SHA = "a" * 40
PRIOR_ROUTE_SHA256 = "b" * 64
ACTIVE_WORK_SHA256 = "c" * 64


def _collection_tar(path: Path, name: str, version: str) -> None:
    payload = json.dumps(
        {"collection_info": {"namespace": "general_ludd", "name": name, "version": version}}
    ).encode()
    member = tarfile.TarInfo("MANIFEST.json")
    member.size = len(payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(member, io.BytesIO(payload))


def _native_tar(path: Path, *, executable: bool = True) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in (
            ("gludd", b"#!/usr/bin/env sh\nexit 0\n"),
            ("install.sh", b"#!/usr/bin/env bash\nset -euo pipefail\n"),
        ):
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if executable else 0o644
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def _windows_zip(path: Path, *, include_executable: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if include_executable:
            archive.writestr("gludd.exe", b"MZ")


def _python_distributions(
    assets: Path,
    *,
    metadata_name: str = "general-ludd-agent",
    metadata_version: str = DIST_VERSION,
) -> None:
    wheel = assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/__init__.py", "")
        archive.writestr("general_ludd/cli.py", "def main():\n    return 0\n")
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/METADATA",
            "Metadata-Version: 2.4\n"
            f"Name: {metadata_name}\n"
            f"Version: {metadata_version}\n",
        )
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/entry_points.txt",
            "[console_scripts]\ngludd = general_ludd.cli:main\n",
        )

    sdist = assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        root = f"general_ludd_agent-{DIST_VERSION}"
        for name, payload in (
            (
                "PKG-INFO",
                (
                    "Metadata-Version: 2.4\n"
                    f"Name: {metadata_name}\n"
                    f"Version: {metadata_version}\n"
                ).encode(),
            ),
            ("src/general_ludd/__init__.py", b""),
            ("src/general_ludd/cli.py", b"def main():\n    return 0\n"),
            ("pyproject.toml", b"[project.scripts]\ngludd = 'general_ludd.cli:main'\n"),
        ):
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def _refresh_checksums(assets: Path) -> None:
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(assets.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _receipt_evidence_sha256(payload: dict[str, object]) -> str:
    evidence = dict(payload)
    evidence.pop("evidence_sha256", None)
    encoded = json.dumps(
        evidence,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_valid_rollback_receipt(assets: Path) -> Path:
    return write_rollback_receipt(
        assets,
        VERSION,
        source_sha=SOURCE_SHA,
        prior_version=PRIOR_VERSION,
        candidate_asset=f"gludd-{VERSION}-linux-x86_64.tar.gz",
        candidate_observed_version=VERSION,
        restored_observed_version=PRIOR_VERSION,
        candidate_health="passed",
        restored_health="passed",
        prior_route_before_sha256=PRIOR_ROUTE_SHA256,
        prior_route_after_sha256=PRIOR_ROUTE_SHA256,
        active_work_before_sha256=ACTIVE_WORK_SHA256,
        active_work_after_sha256=ACTIVE_WORK_SHA256,
    )


def _complete_matrix(tmp_path: Path) -> tuple[Path, Path]:
    repo, assets = tmp_path / "repo", tmp_path / "assets"
    config = repo / "config" / "ansible"
    config.mkdir(parents=True)
    assets.mkdir()
    (config / "requirements.yml").write_text(
        "---\ncollections:\n"
        "  - name: ../../dist/collections/general_ludd-agent-0.2.0.tar.gz\n"
        "    type: file\n"
        "  - name: ../../dist/collections/general_ludd-language-0.1.0.tar.gz\n"
        "    type: file\n",
        encoding="utf-8",
    )
    for name in (
        "execution-environment.yml",
        "requirements.txt",
        "bindep.txt",
        "runtime-lock.json",
        "managed-host-python.lock.json",
        "collection-python-boundary-inventory.json",
    ):
        (config / name).write_text("{}\n", encoding="utf-8")

    for name in (
        f"gludd_{VERSION}_amd64.deb",
        f"gludd-{VERSION}-1.x86_64.rpm",
        f"gludd-{VERSION}-macos-arm64.dmg",
        f"gludd-{VERSION}-setup-x86_64.exe",
        "LICENSE",
        "THIRD_PARTY_LICENSES.md",
    ):
        (assets / name).write_bytes(b"artifact")
    for name in (
        f"gludd-{VERSION}-linux-x86_64.tar.gz",
        f"gludd-{VERSION}-macos-arm64.tar.gz",
        f"gludd-{VERSION}-linux-aarch64.tar.gz",
    ):
        _native_tar(assets / name)
    _windows_zip(assets / f"gludd-{VERSION}-windows-x86_64.zip")
    for source_name, release_name in FOUNDATION_RELEASE_NAMES.items():
        (assets / release_name).write_bytes((config / source_name).read_bytes())

    install = assets / "install.sh"
    install.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
    install.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    (assets / "sbom.json").write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [{}]}),
        encoding="utf-8",
    )
    digest_ref = "ghcr.io/sandboxcom/gludd@sha256:" + "a" * 64
    for prefix in ("gludd-ee-image", "gludd-container"):
        (assets / f"{prefix}-{VERSION}.json").write_text(
            json.dumps({"version": VERSION, "image": digest_ref}), encoding="utf-8"
        )
    (assets / f"gludd-smoke-all-{VERSION}.json").write_text(
        json.dumps(
            {"version": VERSION, "checks": {name: "passed" for name in REQUIRED_SMOKE_CHECKS}}
        ),
        encoding="utf-8",
    )

    for filename in referenced_collection_artifacts(repo):
        collection_name = filename.removeprefix("general_ludd-").split("-")[0]
        collection_version = filename.removesuffix(".tar.gz").rsplit("-", maxsplit=1)[1]
        _collection_tar(assets / filename, collection_name, collection_version)
    (assets / f"gludd-collections-{VERSION}.json").write_text(
        json.dumps({"version": VERSION, "artifacts": sorted(referenced_collection_artifacts(repo))}),
        encoding="utf-8",
    )

    _python_distributions(assets)

    _write_valid_rollback_receipt(assets)

    manifest = assets / f"gludd-release-manifest-{VERSION}.json"
    manifest.write_text(
        json.dumps(
            {
                "version": VERSION,
                "schema_version": 1,
                "source_sha": "a" * 40,
                "assets": sorted(
                    path.name
                    for path in assets.iterdir()
                    if path.name not in {manifest.name, "SHA256SUMS"}
                ),
            }
        ),
        encoding="utf-8",
    )

    _refresh_checksums(assets)
    return assets, repo


def test_complete_release_asset_matrix_passes(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    assert verify_release_asset_matrix(assets, VERSION, repo) == []


def test_rollback_receipt_is_checksum_bound_and_fans_in_every_category(
    tmp_path: Path,
) -> None:
    assets, _repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))

    assert payload["schema_version"] == ROLLBACK_RECEIPT_SCHEMA_VERSION
    assert payload["version"] == VERSION
    assert payload["source_sha"] == SOURCE_SHA
    assert payload["candidate"] == {
        "activation": "passed",
        "asset": f"gludd-{VERSION}-linux-x86_64.tar.gz",
        "health": "passed",
        "observed_version": VERSION,
        "sha256": hashlib.sha256(
            (assets / f"gludd-{VERSION}-linux-x86_64.tar.gz").read_bytes()
        ).hexdigest(),
    }
    assert payload["rollback"] == {
        "health": "passed",
        "immutable": True,
        "observed_version": PRIOR_VERSION,
        "prior_route_sha256": PRIOR_ROUTE_SHA256,
        "prior_version": PRIOR_VERSION,
        "restoration": "passed",
        "restored_route_sha256": PRIOR_ROUTE_SHA256,
    }
    assert payload["active_work"] == {
        "after_sha256": ACTIVE_WORK_SHA256,
        "before_sha256": ACTIVE_WORK_SHA256,
        "unchanged": True,
    }
    fan_in = payload["platform_fan_in"]
    assert fan_in["status"] == "passed"
    assert fan_in["categories"] == sorted(REQUIRED_SMOKE_CHECKS)
    assert set(fan_in["attestations"]) == {
        f"gludd-smoke-all-{VERSION}.json"
    }
    assert payload["evidence_sha256"] == _receipt_evidence_sha256(payload)

    checksum_line = next(
        line
        for line in (assets / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if line.endswith(f"  {receipt.name}")
    )
    assert checksum_line.split()[0] == hashlib.sha256(receipt.read_bytes()).hexdigest()


def test_published_rollback_receipt_revalidates_downloaded_evidence(
    tmp_path: Path,
) -> None:
    """The post-publication verifier must replay the exact downloaded bundle."""
    assets, _repo = _complete_matrix(tmp_path)

    assert verify_published_rollback_receipt(assets, VERSION) == []


def test_published_rollback_receipt_requires_manifest_and_checksum_bindings(
    tmp_path: Path,
) -> None:
    """A valid inner receipt is insufficient without both publication bindings."""
    assets, _repo = _complete_matrix(tmp_path)
    receipt_name = f"gludd-rollback-receipt-{VERSION}.json"
    manifest = assets / f"gludd-release-manifest-{VERSION}.json"
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["assets"].remove(receipt_name)
    manifest.write_text(
        json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_checksums(assets)
    checksum_lines = (assets / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    (assets / "SHA256SUMS").write_text(
        "\n".join(
            ("d" * 64 + f"  {receipt_name}") if line.endswith(f"  {receipt_name}") else line
            for line in checksum_lines
        )
        + "\n",
        encoding="utf-8",
    )

    errors = verify_published_rollback_receipt(assets, VERSION)

    assert "published manifest does not inventory rollback receipt" in errors
    assert "published checksum mismatch: rollback receipt" in errors


def test_published_rollback_receipt_rechecks_smoke_contents_after_download(
    tmp_path: Path,
) -> None:
    """Digest-consistent hosted smoke bytes must still prove every category."""
    assets, _repo = _complete_matrix(tmp_path)
    smoke = assets / f"gludd-smoke-all-{VERSION}.json"
    smoke.write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "passed"}}),
        encoding="utf-8",
    )
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    receipt_payload["platform_fan_in"]["attestations"][smoke.name] = hashlib.sha256(
        smoke.read_bytes()
    ).hexdigest()
    receipt_payload["evidence_sha256"] = _receipt_evidence_sha256(receipt_payload)
    receipt.write_text(json.dumps(receipt_payload), encoding="utf-8")
    _refresh_checksums(assets)

    errors = verify_published_rollback_receipt(assets, VERSION)

    assert (
        "published smoke attestations do not prove complete passing fan-in" in errors
    )


def test_published_rollback_failures_are_bounded_and_content_free(
    tmp_path: Path,
) -> None:
    """Malformed hosted bytes cannot echo content or create unbounded diagnostics."""
    assets, _repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    sensitive_marker = "operator-private-material-must-not-appear"
    receipt.write_text(
        '{"candidate":"' + sensitive_marker + '",',
        encoding="utf-8",
    )
    (assets / "SHA256SUMS").write_text(
        "\n".join(f"malformed-{number}-{sensitive_marker}" for number in range(30))
        + "\n",
        encoding="utf-8",
    )

    errors = verify_published_rollback_receipt(assets, VERSION)

    assert errors
    assert len(errors) == MAX_PUBLISHED_ROLLBACK_ERRORS
    assert errors[-1] == "published rollback validation reached its failure limit"
    assert sensitive_marker not in "\n".join(errors)


def test_published_rollback_receipt_rejects_missing_and_ambiguous_evidence(
    tmp_path: Path,
) -> None:
    """Hosted evidence must stay bounded, unique, complete, and source-identical."""
    assert verify_published_rollback_receipt(tmp_path / "absent", VERSION) == [
        "published rollback evidence directory is missing"
    ]

    assets, _repo = _complete_matrix(tmp_path)
    receipt_name = f"gludd-rollback-receipt-{VERSION}.json"
    candidate_name = f"gludd-{VERSION}-linux-x86_64.tar.gz"
    smoke_name = f"gludd-smoke-all-{VERSION}.json"
    manifest = assets / f"gludd-release-manifest-{VERSION}.json"
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["source_sha"] = "d" * 40
    manifest_payload["assets"] = [receipt_name, receipt_name]
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    _refresh_checksums(assets)

    errors = verify_published_rollback_receipt(assets, VERSION)

    assert "published release manifest has duplicate assets" in errors
    assert "published manifest does not inventory candidate artifact" in errors
    assert "published manifest does not inventory smoke attestations" in errors
    assert "published manifest and rollback source SHA differ" in errors
    assert candidate_name not in "\n".join(errors)
    assert smoke_name not in "\n".join(errors)


@pytest.mark.parametrize(
    ("section", "field", "value", "error_fragment"),
    [
        ("candidate", "activation", "failed", "candidate activation"),
        ("candidate", "observed_version", "wrong", "candidate version"),
        ("candidate", "health", "failed", "candidate health"),
        ("rollback", "restoration", "failed", "prior restoration"),
        ("rollback", "observed_version", "wrong", "restored version"),
        ("rollback", "health", "failed", "restored health"),
        ("rollback", "restored_route_sha256", "d" * 64, "not immutable"),
        ("active_work", "after_sha256", "d" * 64, "active work changed"),
    ],
)
def test_rollback_receipt_rejects_false_or_inconsistent_proof(
    tmp_path: Path,
    section: str,
    field: str,
    value: object,
    error_fragment: str,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload[section][field] = value
    payload["evidence_sha256"] = _receipt_evidence_sha256(payload)
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _refresh_checksums(assets)

    assert any(
        error_fragment in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_rollback_receipt_rejects_incomplete_or_rebound_platform_fan_in(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["platform_fan_in"]["categories"].pop()
    payload["platform_fan_in"]["attestations"] = {"invented.json": "d" * 64}
    payload["evidence_sha256"] = _receipt_evidence_sha256(payload)
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("platform category fan-in is incomplete" in error for error in errors)
    assert any("smoke attestation digest fan-in is stale" in error for error in errors)


def test_rollback_receipt_rejects_tampering_even_with_refreshed_outer_checksum(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["active_work"]["unchanged"] = False
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("receipt evidence digest mismatch" in error for error in errors)
    assert any("active work changed" in error for error in errors)


def test_rollback_receipt_is_bound_to_manifest_source_and_candidate_bytes(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["source_sha"] = "d" * 40
    payload["evidence_sha256"] = _receipt_evidence_sha256(payload)
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    candidate = assets / f"gludd-{VERSION}-linux-x86_64.tar.gz"
    candidate.write_bytes(candidate.read_bytes() + b"rebound")
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("source SHA does not match release manifest" in error for error in errors)
    assert any("candidate checksum mismatch" in error for error in errors)


def test_missing_or_malformed_rollback_receipt_fails_closed(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    receipt.unlink()
    assert any(
        "missing rollback receipt" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )

    receipt.write_text("[]\n", encoding="utf-8")
    _refresh_checksums(assets)
    assert any(
        "JSON root must be an object" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


@pytest.mark.parametrize(
    ("case", "error_fragment"),
    [
        ("root_fields", "schema fields"),
        ("schema_version", "schema version"),
        ("release_version", "release version"),
        ("source_sha", "source SHA is invalid"),
        ("candidate_object", "candidate evidence must be an object"),
        ("candidate_fields", "candidate evidence fields"),
        ("candidate_asset_unsafe", "candidate asset name is unsafe"),
        ("candidate_asset_missing", "candidate asset is missing"),
        ("candidate_sha", "candidate checksum mismatch"),
        ("rollback_object", "prior restoration evidence must be an object"),
        ("rollback_fields", "prior restoration fields"),
        ("prior_version_empty", "prior version is invalid"),
        ("prior_version_same", "prior version is not distinct"),
        ("rollback_digest", "prior route digests are invalid"),
        ("rollback_immutable", "prior restoration is not immutable"),
        ("active_work_object", "active work evidence must be an object"),
        ("active_work_fields", "active work evidence fields"),
        ("active_work_digest", "active work changed"),
        ("fan_in_object", "platform fan-in must be an object"),
        ("fan_in_fields", "platform fan-in fields"),
        ("fan_in_status", "platform fan-in is not passed"),
    ],
)
def test_rollback_receipt_schema_refuses_incomplete_or_ambiguous_claims(
    tmp_path: Path,
    case: str,
    error_fragment: str,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))

    if case == "root_fields":
        payload["unexpected"] = True
    elif case == "schema_version":
        payload["schema_version"] = 2
    elif case == "release_version":
        payload["version"] = "wrong"
    elif case == "source_sha":
        payload["source_sha"] = "short"
    elif case == "candidate_object":
        payload["candidate"] = []
    elif case == "candidate_fields":
        payload["candidate"].pop("activation")
    elif case == "candidate_asset_unsafe":
        payload["candidate"]["asset"] = "../artifact"
    elif case == "candidate_asset_missing":
        payload["candidate"]["asset"] = "absent.tar.gz"
    elif case == "candidate_sha":
        payload["candidate"]["sha256"] = "d" * 64
    elif case == "rollback_object":
        payload["rollback"] = []
    elif case == "rollback_fields":
        payload["rollback"].pop("restoration")
    elif case == "prior_version_empty":
        payload["rollback"]["prior_version"] = ""
    elif case == "prior_version_same":
        payload["rollback"]["prior_version"] = VERSION
        payload["rollback"]["observed_version"] = VERSION
    elif case == "rollback_digest":
        payload["rollback"]["prior_route_sha256"] = "short"
    elif case == "rollback_immutable":
        payload["rollback"]["immutable"] = False
    elif case == "active_work_object":
        payload["active_work"] = []
    elif case == "active_work_fields":
        payload["active_work"].pop("unchanged")
    elif case == "active_work_digest":
        payload["active_work"]["before_sha256"] = "short"
    elif case == "fan_in_object":
        payload["platform_fan_in"] = []
    elif case == "fan_in_fields":
        payload["platform_fan_in"].pop("status")
    elif case == "fan_in_status":
        payload["platform_fan_in"]["status"] = "failed"
    else:
        raise AssertionError(case)

    payload["evidence_sha256"] = _receipt_evidence_sha256(payload)
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _refresh_checksums(assets)

    assert any(
        error_fragment in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_write_rollback_receipt_refuses_unproven_or_ambiguous_evidence(
    tmp_path: Path,
) -> None:
    assets, _repo = _complete_matrix(tmp_path)
    candidate = assets / f"gludd-{VERSION}-linux-x86_64.tar.gz"
    candidate_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="active work"):
        write_rollback_receipt(
            assets,
            VERSION,
            source_sha=SOURCE_SHA,
            prior_version=PRIOR_VERSION,
            candidate_asset=candidate.name,
            candidate_observed_version=VERSION,
            restored_observed_version=PRIOR_VERSION,
            candidate_health="passed",
            restored_health="passed",
            prior_route_before_sha256=PRIOR_ROUTE_SHA256,
            prior_route_after_sha256=PRIOR_ROUTE_SHA256,
            active_work_before_sha256=ACTIVE_WORK_SHA256,
            active_work_after_sha256="d" * 64,
        )

    with pytest.raises(ValueError, match="distinct"):
        write_rollback_receipt(
            assets,
            VERSION,
            source_sha=SOURCE_SHA,
            prior_version=PRIOR_VERSION,
            candidate_asset=candidate.name,
            candidate_observed_version=VERSION,
            restored_observed_version=PRIOR_VERSION,
            candidate_health="passed",
            restored_health="passed",
            prior_route_before_sha256=candidate_sha256,
            prior_route_after_sha256=candidate_sha256,
            active_work_before_sha256=ACTIVE_WORK_SHA256,
            active_work_after_sha256=ACTIVE_WORK_SHA256,
        )


@pytest.mark.parametrize(
    ("field", "value", "error_fragment"),
    [
        ("source_sha", "short", "source SHA"),
        ("prior_version", VERSION, "prior version"),
        ("candidate_observed_version", "wrong", "candidate observed version"),
        ("restored_observed_version", "wrong", "restored observed version"),
        ("candidate_health", "failed", "health"),
        ("restored_health", "failed", "health"),
        ("candidate_asset", "../unsafe", "safe basename"),
        ("candidate_asset", "absent.tar.gz", "non-empty staged file"),
        ("prior_route_before_sha256", "short", "prior route evidence"),
        ("prior_route_after_sha256", "d" * 64, "not immutable"),
        ("active_work_before_sha256", "short", "active work evidence"),
    ],
)
def test_write_rollback_receipt_validates_every_external_claim(
    tmp_path: Path,
    field: str,
    value: str,
    error_fragment: str,
) -> None:
    assets, _repo = _complete_matrix(tmp_path)
    kwargs = {
        "source_sha": SOURCE_SHA,
        "prior_version": PRIOR_VERSION,
        "candidate_asset": f"gludd-{VERSION}-linux-x86_64.tar.gz",
        "candidate_observed_version": VERSION,
        "restored_observed_version": PRIOR_VERSION,
        "candidate_health": "passed",
        "restored_health": "passed",
        "prior_route_before_sha256": PRIOR_ROUTE_SHA256,
        "prior_route_after_sha256": PRIOR_ROUTE_SHA256,
        "active_work_before_sha256": ACTIVE_WORK_SHA256,
        "active_work_after_sha256": ACTIVE_WORK_SHA256,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=error_fragment):
        write_rollback_receipt(assets, VERSION, **kwargs)


def test_write_rollback_receipt_requires_complete_smoke_fan_in(tmp_path: Path) -> None:
    assets, _repo = _complete_matrix(tmp_path)
    smoke = assets / f"gludd-smoke-all-{VERSION}.json"
    smoke.write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "passed"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="platform smoke fan-in incomplete"):
        _write_valid_rollback_receipt(assets)


def test_missing_platform_package_fails_closed(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-{VERSION}-1.x86_64.rpm").unlink()
    assert any("linux rpm" in error for error in verify_release_asset_matrix(assets, VERSION, repo))


def test_smoke_attestations_must_cover_every_check(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-smoke-all-{VERSION}.json").write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "passed"}}), encoding="utf-8"
    )
    assert any(
        "smoke checks missing" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_every_runtime_collection_tarball_is_required(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / "general_ludd-language-0.1.0.tar.gz").unlink()
    assert any(
        "general_ludd-language-0.1.0.tar.gz" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_checksums_must_cover_and_match_every_asset(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / "LICENSE").write_text("changed after checksum\n", encoding="utf-8")
    assert any(
        "checksum mismatch: LICENSE" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_image_metadata_requires_digest_pinned_reference(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-ee-image-{VERSION}.json").write_text(
        json.dumps({"version": VERSION, "image": "ghcr.io/sandboxcom/gludd:latest"}),
        encoding="utf-8",
    )
    assert any(
        "digest-pinned" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_install_script_must_be_executable_and_fail_fast(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    install = assets / "install.sh"
    install.write_text("#!/bin/sh\necho unsafe\n", encoding="utf-8")
    install.chmod(stat.S_IRUSR | stat.S_IWUSR)
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert "install.sh must be executable" in errors
    assert "install.sh must enable set -euo pipefail" in errors


def test_install_script_comment_cannot_fake_fail_fast(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    install = assets / "install.sh"
    install.write_text(
        "#!/usr/bin/env bash\n# set -euo pipefail\necho unsafe\n",
        encoding="utf-8",
    )
    _refresh_checksums(assets)

    assert "install.sh must enable set -euo pipefail" in (
        verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_install_script_non_utf8_is_a_bounded_verification_error(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    install = assets / "install.sh"
    install.write_bytes(b"#!/usr/bin/env bash\nset -euo pipefail\n\xff")
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert any("install.sh is not readable UTF-8" in error for error in errors)


def test_install_script_verification_has_a_size_ceiling(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    install = assets / "install.sh"
    install.write_bytes(
        b"#!/usr/bin/env bash\nset -euo pipefail\n" + (b"#" * 1_048_577)
    )
    _refresh_checksums(assets)

    assert "install.sh exceeds the 1048576-byte verification limit" in (
        verify_release_asset_matrix(assets, VERSION, repo)
    )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("0.1.0-alpha.2", "0.1.0a2"),
        ("0.1.0-beta.4", "0.1.0b4"),
        ("0.1.0-rc.1", "0.1.0rc1"),
        ("1.0.0", "1.0.0"),
    ],
)
def test_distribution_version_normalization(raw: str, normalized: str) -> None:
    assert distribution_version(raw) == normalized


@pytest.mark.parametrize(
    "contents",
    ["[]\n", "{}\n", "collections: nope\n", "collections: [invalid]\n"],
)
def test_malformed_collection_requirements_fail_closed(
    tmp_path: Path, contents: str
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (repo / "config" / "ansible" / "requirements.yml").write_text(contents, encoding="utf-8")
    assert referenced_collection_artifacts(repo) == set()
    assert any(
        "references no collection" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_invalid_collection_archive_and_identity_are_rejected(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    agent = assets / "general_ludd-agent-0.2.0.tar.gz"
    agent.write_bytes(b"not a tar")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("invalid collection archive" in error for error in errors)

    _collection_tar(agent, "wrong_name", "0.2.0")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("collection identity" in error for error in errors)


def test_invalid_python_distribution_archives_are_rejected(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl").write_bytes(b"bad")
    (assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz").write_bytes(b"bad")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("wheel is invalid" in error for error in errors)
    assert any("sdist is invalid" in error for error in errors)


def test_python_distribution_metadata_identity_must_match_beta4_filename(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    _python_distributions(
        assets,
        metadata_name="renamed-project",
        metadata_version="9.9",
    )
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert any("wheel METADATA identity is stale" in error for error in errors)
    assert any("sdist PKG-INFO identity is stale" in error for error in errors)


def test_python_distribution_metadata_is_unique_utf8_and_complete(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    wheel = assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/__init__.py", "")
        archive.writestr("general_ludd/cli.py", "")
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/METADATA", b"\xff"
        )
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/entry_points.txt",
            "[console_scripts]\ngludd = general_ludd.cli:main\n",
        )
    sdist = assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        root = f"general_ludd_agent-{DIST_VERSION}"
        for name, payload in (
            ("PKG-INFO", b"\xff"),
            ("src/general_ludd/__init__.py", b""),
            ("src/general_ludd/cli.py", b""),
            ("pyproject.toml", b""),
        ):
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert any("wheel METADATA is not UTF-8" in error for error in errors)
    assert any("sdist PKG-INFO is not UTF-8" in error for error in errors)

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/cli.py", "")
        archive.writestr("one.dist-info/METADATA", "Name: one\nVersion: 1\n")
        archive.writestr("two.dist-info/METADATA", "Name: two\nVersion: 2\n")
        archive.writestr("one.dist-info/entry_points.txt", "wrong = target\n")
    with tarfile.open(sdist, "w:gz") as archive:
        root = f"general_ludd_agent-{DIST_VERSION}"
        for name in ("one/PKG-INFO", "two/PKG-INFO", "pyproject.toml"):
            payload = b"Name: wrong\nVersion: 1\n"
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert "wheel does not contain general_ludd/__init__.py" in errors
    assert "wheel does not contain distribution METADATA" in errors
    assert "wheel does not declare the gludd console entrypoint" in errors
    assert "sdist does not contain PKG-INFO" in errors
    assert "sdist does not contain src/general_ludd/__init__.py" in errors
    assert "sdist does not contain src/general_ludd/cli.py" in errors


def test_native_archives_must_contain_installed_executables(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    _native_tar(assets / f"gludd-{VERSION}-linux-x86_64.tar.gz", executable=False)
    _windows_zip(
        assets / f"gludd-{VERSION}-windows-x86_64.zip", include_executable=False
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert "linux tar executable gludd must be executable" in errors
    assert "windows zip does not contain gludd.exe" in errors


def test_native_archives_reject_corrupt_empty_and_duplicate_payloads(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-{VERSION}-linux-x86_64.tar.gz").write_bytes(b"not-a-tar")
    windows_zip = assets / f"gludd-{VERSION}-windows-x86_64.zip"
    with zipfile.ZipFile(windows_zip, "w") as archive:
        archive.writestr("gludd.exe", b"")
    _refresh_checksums(assets)

    errors = verify_release_asset_matrix(assets, VERSION, repo)

    assert any("linux tar archive is invalid" in error for error in errors)
    assert "windows zip gludd.exe is empty" in errors

    _native_tar(assets / f"gludd-{VERSION}-linux-x86_64.tar.gz")
    with zipfile.ZipFile(windows_zip, "w") as archive:
        archive.writestr("first/gludd.exe", b"MZ")
        archive.writestr("second/gludd.exe", b"MZ")
    _refresh_checksums(assets)

    assert "windows zip contains multiple gludd.exe files" in (
        verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_python_distributions_must_install_the_gludd_entrypoint(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    wheel = assets / f"general_ludd_agent-{DIST_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("general_ludd/__init__.py", "")
        archive.writestr(
            f"general_ludd_agent-{DIST_VERSION}.dist-info/METADATA",
            f"Version: {DIST_VERSION}\n",
        )
    sdist = assets / f"general_ludd_agent-{DIST_VERSION}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        payload = f"Version: {DIST_VERSION}\n".encode()
        member = tarfile.TarInfo(f"general_ludd_agent-{DIST_VERSION}/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert "wheel does not contain general_ludd/cli.py" in errors
    assert "wheel does not declare the gludd console entrypoint" in errors
    assert "sdist does not contain src/general_ludd/cli.py" in errors
    assert "sdist does not contain pyproject.toml" in errors


def test_release_manifest_must_inventory_every_staged_asset(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    manifest = assets / f"gludd-release-manifest-{VERSION}.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": VERSION,
                "source_sha": "a" * 40,
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    assert "release manifest asset inventory is stale or incomplete" in (
        verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_smoke_attestation_schema_conflicts_and_failures_are_rejected(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    primary = assets / f"gludd-smoke-all-{VERSION}.json"
    primary.write_text(
        json.dumps({"version": "wrong", "checks": {"linux_tar": 3}}),
        encoding="utf-8",
    )
    secondary = assets / f"gludd-smoke-second-{VERSION}.json"
    secondary.write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "failed"}}),
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("smoke version mismatch" in error for error in errors)
    assert any("names/statuses must be strings" in error for error in errors)
    assert any("smoke checks not passed" in error for error in errors)

    primary.write_text(
        json.dumps({"version": VERSION, "checks": {"linux_tar": "passed"}}),
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("conflicting smoke status" in error for error in errors)


def test_missing_and_invalid_smoke_attestations_fail_closed(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    smoke = assets / f"gludd-smoke-all-{VERSION}.json"
    smoke.unlink()
    assert "smoke attestations are missing" in verify_release_asset_matrix(
        assets, VERSION, repo
    )
    smoke.write_text("{", encoding="utf-8")
    assert any(
        "not valid JSON" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )
    smoke.write_text(json.dumps({"version": VERSION, "checks": []}), encoding="utf-8")
    assert any(
        "checks must be an object" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )


def test_checksum_manifest_rejects_malformed_missing_extra_and_duplicate(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    checksums = assets / "SHA256SUMS"
    original = checksums.read_text(encoding="utf-8")
    checksums.write_text(original + "malformed\n", encoding="utf-8")
    assert any(
        "is malformed" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )

    new_asset = assets / "unexpected.txt"
    new_asset.write_text("new\n", encoding="utf-8")
    assert any(
        "checksums missing" in error
        for error in verify_release_asset_matrix(assets, VERSION, repo)
    )

    new_asset.unlink()
    checksums.write_text(
        original + f"{'0' * 64}  absent.txt\n" + original.splitlines()[0] + "\n",
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("reference absent assets" in error for error in errors)
    assert any("unsafe or duplicate" in error for error in errors)


def test_foundation_sbom_and_release_manifest_must_match_contract(tmp_path: Path) -> None:
    assets, repo = _complete_matrix(tmp_path)
    foundation = repo / "config" / "ansible" / "runtime-lock.json"
    foundation.unlink()
    (assets / "sbom.json").write_text(json.dumps({"bomFormat": "other"}), encoding="utf-8")
    (assets / f"gludd-release-manifest-{VERSION}.json").write_text(
        json.dumps({"schema_version": 2, "version": "wrong", "source_sha": "bad"}),
        encoding="utf-8",
    )
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("missing canonical foundation input" in error for error in errors)
    assert "sbom.json is not a CycloneDX component inventory" in errors
    assert "release manifest schema/version/source SHA is invalid" in errors


def test_empty_asset_stale_foundation_and_installer_shebang_are_rejected(
    tmp_path: Path,
) -> None:
    assets, repo = _complete_matrix(tmp_path)
    (assets / f"gludd-{VERSION}-macos-arm64.dmg").write_bytes(b"")
    (assets / "ansible-ee-requirements.txt").write_text("stale\n", encoding="utf-8")
    install = assets / "install.sh"
    install.write_text("#!/bin/sh\nset -euo pipefail\n", encoding="utf-8")
    errors = verify_release_asset_matrix(assets, VERSION, repo)
    assert any("empty macos dmg" in error for error in errors)
    assert any("stale execution-environment metadata" in error for error in errors)
    assert "install.sh must use the repository bash entrypoint" in errors


def test_write_manifest_and_cli_modes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assets, repo = _complete_matrix(tmp_path)
    assert main(
        [
            "write-rollback-receipt",
            str(assets),
            VERSION,
            "--source-sha",
            "b" * 40,
            "--prior-version",
            PRIOR_VERSION,
            "--candidate-asset",
            f"gludd-{VERSION}-linux-x86_64.tar.gz",
            "--candidate-observed-version",
            VERSION,
            "--restored-observed-version",
            PRIOR_VERSION,
            "--candidate-health",
            "passed",
            "--restored-health",
            "passed",
            "--prior-route-before-sha256",
            PRIOR_ROUTE_SHA256,
            "--prior-route-after-sha256",
            PRIOR_ROUTE_SHA256,
            "--active-work-before-sha256",
            ACTIVE_WORK_SHA256,
            "--active-work-after-sha256",
            ACTIVE_WORK_SHA256,
        ]
    ) == 0
    assert "ROLLBACK_RECEIPT_WRITTEN" in capsys.readouterr().out

    manifest = write_release_manifest(assets, VERSION, "b" * 40)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["source_sha"] == "b" * 40
    assert "SHA256SUMS" not in payload["assets"]

    with pytest.raises(ValueError, match="source SHA"):
        write_release_manifest(assets, VERSION, "short")

    assert main(["write-manifest", str(assets), VERSION, "--source-sha", "b" * 40]) == 0
    assert "RELEASE_MANIFEST_WRITTEN" in capsys.readouterr().out

    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(assets.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main(["verify-published-rollback", str(assets), VERSION]) == 0
    assert "PUBLISHED_ROLLBACK_RECEIPT_PASS" in capsys.readouterr().out

    receipt = assets / f"gludd-rollback-receipt-{VERSION}.json"
    receipt_bytes = receipt.read_bytes()
    receipt.unlink()
    assert main(["verify-published-rollback", str(assets), VERSION]) == 1
    assert "PUBLISHED_ROLLBACK_RECEIPT_FAIL" in capsys.readouterr().out
    receipt.write_bytes(receipt_bytes)

    assert main(["verify", str(assets), VERSION, "--repository-root", str(repo)]) == 0
    assert "RELEASE_ASSET_MATRIX_PASS" in capsys.readouterr().out

    (assets / "LICENSE").unlink()
    assert main(["verify", str(assets), VERSION, "--repository-root", str(repo)]) == 1
    assert "RELEASE_ASSET_MATRIX_FAIL" in capsys.readouterr().out
