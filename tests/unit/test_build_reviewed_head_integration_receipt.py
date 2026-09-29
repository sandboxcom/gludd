from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from scripts import build_reviewed_head_integration_receipt as builder

from general_ludd.git_release.reviewed_head_integration import (
    load_reviewed_head_integration_receipt,
)

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _merge_fixture(tmp_path: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "development")
    _git(repo, "config", "user.name", "Gludd Tests")
    _git(repo, "config", "user.email", "tests@gludd.invalid")
    base = _commit(repo, "base.txt", "base\n")
    _git(repo, "switch", "-c", "fix/release-continuity")
    source = _commit(repo, "fix.txt", "fixed\n")
    _git(repo, "switch", "development")
    _commit(repo, "candidate.txt", "candidate\n")
    before = _git(repo, "rev-parse", "HEAD")
    _git(repo, "merge", "--no-ff", "fix/release-continuity", "-m", "merge fix")
    final = _git(repo, "rev-parse", "HEAD")

    review = {
        "schema_version": 1,
        "source_ref": "fix/release-continuity",
        "source_sha": source,
        "reviewed_base_sha": base,
        "reviewer": "release-review",
        "verdict": "approved",
        "focused_validation_ids": ["test-hook-runtime", "test-release-readiness"],
    }
    review_path = repo / "review.json"
    review_path.write_text(json.dumps(review, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "base_sha": before,
        "heads": [
            {
                "source_ref": "fix/release-continuity",
                "source_sha": source,
                "reviewed_base_sha": base,
                "review_receipt_file": "review.json",
                "focused_validation_ids": [
                    "test-hook-runtime",
                    "test-release-readiness",
                ],
                "application_mode": "merge_two_parent",
                "prerequisite_ancestry": [
                    {"ancestor_sha": base, "resolution": "already_reachable"}
                ],
                "before_sha": before,
                "after_sha": final,
                "parent_shas": [before, source],
            }
        ],
        "exact_gate_id": "gate",
        "focused_validation": {
            "tip_sha": final,
            "source_shas": [source],
            "command_ids": ["test-hook-runtime", "test-release-readiness"],
            "run_count": 1,
            "passed": True,
        },
    }
    attestation = {
        "schema_version": 3,
        "lane": "local",
        "identity": {
            "head_sha": final,
            "expected_sha": final,
            "branch": "development",
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
        },
        "status": "pass",
        "returncode": 0,
    }
    return repo, manifest, attestation


def _cherry_pick_fixture(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "development")
    _git(repo, "config", "user.name", "Gludd Tests")
    _git(repo, "config", "user.email", "tests@gludd.invalid")
    reviewed_base = _commit(repo, "base.txt", "base\n")
    _git(repo, "switch", "-c", "fix/cherry")
    source = _commit(repo, "fix.txt", "fixed\n")
    _git(repo, "switch", "development")
    before = _commit(repo, "candidate.txt", "candidate\n")
    _git(repo, "cherry-pick", source)
    final = _git(repo, "rev-parse", "HEAD")
    review = {
        "schema_version": 1,
        "source_ref": "fix/cherry",
        "source_sha": source,
        "reviewed_base_sha": reviewed_base,
        "reviewer": "release-review",
        "verdict": "approved",
        "focused_validation_ids": ["test-release-readiness"],
    }
    (repo / "review.json").write_text(
        json.dumps(review, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "base_sha": before,
        "heads": [
            {
                "source_ref": "fix/cherry",
                "source_sha": source,
                "reviewed_base_sha": reviewed_base,
                "review_receipt_file": "review.json",
                "focused_validation_ids": ["test-release-readiness"],
                "application_mode": "cherry_pick",
                "prerequisite_ancestry": [
                    {
                        "ancestor_sha": reviewed_base,
                        "resolution": "already_reachable",
                    }
                ],
                "before_sha": before,
                "after_sha": final,
                "parent_shas": [before],
            }
        ],
        "exact_gate_id": "gate",
        "focused_validation": {
            "tip_sha": final,
            "source_shas": [source],
            "command_ids": ["test-release-readiness"],
            "run_count": 1,
            "passed": True,
        },
    }
    attestation = {
        "schema_version": 3,
        "lane": "local",
        "identity": {
            "head_sha": final,
            "expected_sha": final,
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
        },
        "status": "pass",
        "returncode": 0,
    }
    return repo, manifest, attestation


def test_builds_canonical_receipt_from_real_merge_and_gate_evidence(
    tmp_path: Path,
) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest,
        manifest_dir=repo,
        gate_attestation=attestation,
        repo_root=repo,
    )

    output = repo / "receipt.json"
    builder.write_receipt(output, receipt)
    parsed = load_reviewed_head_integration_receipt(
        output,
        expected_final_sha=_git(repo, "rev-parse", "HEAD"),
    )

    assert parsed.final_sha == _git(repo, "rev-parse", "HEAD")
    expected_digest = hashlib.sha256((repo / "review.json").read_bytes()).hexdigest()
    assert parsed.plan.heads[0].review_receipt_sha256 == expected_digest


def test_builds_receipt_only_when_cherry_pick_patch_identity_matches(
    tmp_path: Path,
) -> None:
    repo, manifest, attestation = _cherry_pick_fixture(tmp_path)

    receipt = builder.build_receipt_from_manifest(
        manifest,
        manifest_dir=repo,
        gate_attestation=attestation,
        repo_root=repo,
    )

    assert receipt.final_sha == _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"status": "fail"}, "passing exact gate"),
        ({"returncode": 1}, "passing exact gate"),
        ({"schema_version": 2}, "schema version"),
    ],
)
def test_rejects_ineligible_gate_attestation(
    tmp_path: Path,
    mutation: dict[str, object],
    match: str,
) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    attestation.update(mutation)

    with pytest.raises(ValueError, match=match):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_nonlocal_gate_evidence(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    attestation["lane"] = "hosted"
    with pytest.raises(ValueError, match="local exact gate"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("head_sha", "f" * 40),
        ("expected_sha", "f" * 40),
        ("clean", False),
        ("exact_sha", False),
        ("queries_ok", False),
    ],
)
def test_rejects_wrong_candidate_gate_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    identity = attestation["identity"]
    assert isinstance(identity, dict)
    identity[field] = value
    with pytest.raises(ValueError, match="exact clean candidate"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_review_receipt_identity_drift(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    review_path = repo / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["source_sha"] = "f" * 40
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match="review receipt identity"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("verdict", "changes_requested", "approved versioned review"),
        ("reviewer", "", "reviewer is missing"),
    ],
)
def test_rejects_unapproved_or_anonymous_review_receipt(
    tmp_path: Path,
    field: str,
    value: str,
    match: str,
) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    review_path = repo / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review[field] = value
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_manifest_parent_claim_that_breaks_attribution(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    head = manifest["heads"][0]  # type: ignore[index]
    head["parent_shas"] = [head["before_sha"], "d" * 40]

    with pytest.raises(ValueError, match="parent attribution"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_source_ref_drift_and_unintegrated_repository_head(
    tmp_path: Path,
) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    _git(repo, "branch", "-f", "fix/release-continuity", "development")
    with pytest.raises(ValueError, match="source ref"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )

    source = manifest["heads"][0]["source_sha"]  # type: ignore[index]
    _git(repo, "branch", "-f", "fix/release-continuity", source)
    _commit(repo, "later.txt", "later\n")
    with pytest.raises(ValueError, match="repository HEAD"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_unsupported_patch_equivalent_prerequisite(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    prerequisite = manifest["heads"][0]["prerequisite_ancestry"][0]  # type: ignore[index]
    prerequisite["resolution"] = "patch_equivalent"

    with pytest.raises(ValueError, match="patch-equivalent"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_main_validate_only_never_writes_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    manifest_path = repo / "manifest.json"
    gate_path = repo / "gate.json"
    output = repo / "receipt.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    gate_path.write_text(json.dumps(attestation), encoding="utf-8")

    assert builder.main(
        [
            "--manifest",
            str(manifest_path),
            "--gate-attestation",
            str(gate_path),
            "--repo-root",
            str(repo),
            "--output",
            str(output),
            "--validate-only",
        ]
    ) == 0
    assert not output.exists()
    assert "reviewed-head receipt valid" in capsys.readouterr().out


def test_main_writes_validated_receipt_atomically(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    manifest_path = repo / "manifest.json"
    gate_path = repo / "gate.json"
    output = repo / "receipt.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    gate_path.write_text(json.dumps(attestation), encoding="utf-8")

    assert builder.main(
        [
            "--manifest",
            str(manifest_path),
            "--gate-attestation",
            str(gate_path),
            "--repo-root",
            str(repo),
            "--output",
            str(output),
        ]
    ) == 0
    assert load_reviewed_head_integration_receipt(
        output,
        expected_final_sha=_git(repo, "rev-parse", "HEAD"),
    )


def test_main_rejects_ambiguous_manifest_without_writing(tmp_path: Path) -> None:
    output = tmp_path / "receipt.json"
    manifest = tmp_path / "manifest.json"
    gate = tmp_path / "gate.json"
    manifest.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    gate.write_text("{}", encoding="utf-8")

    assert builder.main(
        [
            "--manifest",
            str(manifest),
            "--gate-attestation",
            str(gate),
            "--repo-root",
            str(tmp_path),
            "--output",
            str(output),
        ]
    ) == 2
    assert not output.exists()


@pytest.mark.parametrize("manifest_state", ["missing", "empty"])
def test_main_rejects_missing_or_empty_manifest(
    tmp_path: Path,
    manifest_state: str,
) -> None:
    output = tmp_path / "receipt.json"
    manifest = tmp_path / "manifest.json"
    gate = tmp_path / "gate.json"
    gate.write_text("{}", encoding="utf-8")
    if manifest_state == "empty":
        manifest.write_bytes(b"")

    assert builder.main(
        [
            "--manifest",
            str(manifest),
            "--gate-attestation",
            str(gate),
            "--repo-root",
            str(tmp_path),
            "--output",
            str(output),
        ]
    ) == 2
    assert not output.exists()


def test_write_receipt_rejects_symbolic_link_output(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest,
        manifest_dir=repo,
        gate_attestation=attestation,
        repo_root=repo,
    )
    destination = repo / "destination.json"
    destination.write_text("preserve\n", encoding="utf-8")
    output = repo / "receipt.json"
    output.symlink_to(destination)

    with pytest.raises(ValueError, match="symbolic link"):
        builder.write_receipt(output, receipt)
    assert destination.read_text(encoding="utf-8") == "preserve\n"


def test_review_receipt_path_cannot_escape_manifest_directory(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    head = manifest["heads"][0]  # type: ignore[index]
    head["review_receipt_file"] = "../outside.json"

    with pytest.raises(ValueError, match="review receipt path"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_review_receipt_path_rejects_absolute_and_resolved_escape(
    tmp_path: Path,
) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    head = manifest["heads"][0]  # type: ignore[index]
    head["review_receipt_file"] = str((repo / "review.json").resolve())
    with pytest.raises(ValueError, match="relative"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    head["review_receipt_file"] = "../outside.json"
    with pytest.raises(ValueError, match="escapes"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo,
            gate_attestation=attestation,
            repo_root=repo,
        )


@pytest.mark.parametrize(
    ("manifest", "match"),
    [
        ([], "must be an object"),
        ({"schema_version": 1, "base_sha": "", "heads": []}, "nonempty string"),
        ({"schema_version": 1, "base_sha": "a" * 40, "heads": []}, "nonempty"),
        (
            {"schema_version": 1, "base_sha": "a" * 40, "heads": "invalid"},
            "must be an array",
        ),
    ],
)
def test_rejects_malformed_manifest_shapes(
    tmp_path: Path,
    manifest: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=tmp_path,
            gate_attestation={},
            repo_root=tmp_path,
        )


def test_make_target_is_documented_and_has_safe_validate_only_contract() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    assert "\nreviewed-head-receipt:" in makefile
    assert "scripts/build_reviewed_head_integration_receipt.py" in makefile
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "reviewed-head-receipt"
    )
    assert entry["make_variables"] == [
        "REVIEWED_HEAD_RECEIPT_MANIFEST",
        "REVIEWED_HEAD_GATE_ATTESTATION",
        "REVIEWED_HEAD_RECEIPT_OUTPUT",
        "REVIEWED_HEAD_RECEIPT_REPO_ROOT",
        "REVIEWED_HEAD_RECEIPT_VALIDATE_ONLY",
    ]
    completed = subprocess.run(
        entry["behavior"].split(),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "REVIEWED-HEAD-RECEIPT-PLAN" in completed.stdout
