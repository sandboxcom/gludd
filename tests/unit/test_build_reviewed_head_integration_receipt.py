from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.makefile_layout import compose_makefile

if TYPE_CHECKING:
    import build_reviewed_head_integration_receipt as builder
else:
    from scripts import build_reviewed_head_integration_receipt as builder

from general_ludd.git_release.reviewed_head_integration import (
    encode_reviewed_head_integration_receipt,
    load_reviewed_head_integration_receipt,
)

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        cwd=repo.resolve(),
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
    review_path = tmp_path / "review.json"
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
    (tmp_path / "review.json").write_text(
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
        manifest_dir=repo.parent,
        gate_attestation=attestation,
        repo_root=repo,
    )

    output = repo.parent / "receipt.json"
    builder.write_receipt(output, receipt)
    parsed = load_reviewed_head_integration_receipt(
        output,
        expected_final_sha=_git(repo, "rev-parse", "HEAD"),
    )

    assert parsed.final_sha == _git(repo, "rev-parse", "HEAD")
    expected_digest = hashlib.sha256(
        (repo.parent / "review.json").read_bytes()
    ).hexdigest()
    assert parsed.plan.heads[0].review_receipt_sha256 == expected_digest


def test_builds_receipt_only_when_cherry_pick_patch_identity_matches(
    tmp_path: Path,
) -> None:
    repo, manifest, attestation = _cherry_pick_fixture(tmp_path)

    receipt = builder.build_receipt_from_manifest(
        manifest,
        manifest_dir=repo.parent,
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
            manifest_dir=repo.parent,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_nonlocal_gate_evidence(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    attestation["lane"] = "hosted"
    with pytest.raises(ValueError, match="local exact gate"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
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
            manifest_dir=repo.parent,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_review_receipt_identity_drift(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    review_path = repo.parent / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["source_sha"] = "f" * 40
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match="review receipt identity"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
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
    review_path = repo.parent / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review[field] = value
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
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
            manifest_dir=repo.parent,
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
            manifest_dir=repo.parent,
            gate_attestation=attestation,
            repo_root=repo,
        )

    source = manifest["heads"][0]["source_sha"]  # type: ignore[index]
    _git(repo, "branch", "-f", "fix/release-continuity", source)
    _commit(repo, "later.txt", "later\n")
    with pytest.raises(ValueError, match="repository HEAD"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
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
            manifest_dir=repo.parent,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_rejects_repository_mutation_after_clean_gate(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    (repo / "post-gate.txt").write_text("mutation\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after the exact clean gate"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_main_validate_only_never_writes_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    manifest_path = repo.parent / "manifest.json"
    gate_path = repo.parent / "gate.json"
    output = repo.parent / "receipt.json"
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
    manifest_path = repo.parent / "manifest.json"
    gate_path = repo.parent / "gate.json"
    output = tmp_path / "receipt.json"
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


def test_main_rejects_output_inside_attested_repository(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    manifest_path = repo.parent / "manifest.json"
    gate_path = repo.parent / "gate.json"
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
    ) == 2
    assert not output.exists()


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
            str(tmp_path / "repo"),
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
            str(tmp_path / "repo"),
            "--output",
            str(output),
        ]
    ) == 2
    assert not output.exists()


def test_write_receipt_rejects_symbolic_link_output(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest,
        manifest_dir=repo.parent,
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


def test_default_receipt_refuses_symlink_arriving_during_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, manifest, gate = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest, manifest_dir=repo.parent, gate_attestation=gate, repo_root=repo,
    )
    output, foreign = tmp_path / "receipt.json", tmp_path / "foreign.json"
    foreign.write_bytes(b"preserve foreign owner\n")
    metadata = foreign.stat()
    original_resolve = Path.resolve
    resolutions: list[Path] = []

    def resolve_with_arrival(path: Path, strict: bool = False) -> Path:
        if path == output and not resolutions:
            resolutions.append(path)
            output.symlink_to(foreign)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_arrival)
    with pytest.raises(ValueError, match="symbolic link"):
        builder.write_receipt(output, receipt)
    assert resolutions == [output]
    assert output.is_symlink()
    assert output.readlink() == foreign
    assert foreign.read_bytes() == b"preserve foreign owner\n"
    assert foreign.stat().st_ino == metadata.st_ino
    assert foreign.stat().st_mtime_ns == metadata.st_mtime_ns
    assert not list(tmp_path.glob(".*.tmp"))


def test_exclusive_receipt_reuses_identical_bytes_without_replacement(tmp_path: Path) -> None:
    repo, manifest, gate = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest, manifest_dir=repo.parent, gate_attestation=gate, repo_root=repo,
    )
    output = tmp_path / "receipt.json"
    builder.write_receipt(output, receipt, exclusive=True)
    original, metadata = output.read_bytes(), output.stat()
    builder.write_receipt(output, receipt, exclusive=True)
    assert output.read_bytes() == original
    assert output.stat().st_ino == metadata.st_ino
    assert output.stat().st_mtime_ns == metadata.st_mtime_ns
    assert not list(tmp_path.glob(".receipt.json.*.tmp"))


@pytest.mark.parametrize("state", ["divergent", "symlink", "directory"])
def test_exclusive_receipt_refuses_foreign_existing_outputs(tmp_path: Path, state: str) -> None:
    repo, manifest, gate = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest, manifest_dir=repo.parent, gate_attestation=gate, repo_root=repo,
    )
    output, foreign = tmp_path / "receipt.json", tmp_path / "foreign.json"
    foreign.write_bytes(b"preserve foreign owner\n")
    if state == "symlink":
        output.symlink_to(foreign)
    elif state == "directory":
        output.mkdir()
    else:
        output.write_bytes(foreign.read_bytes())
    with pytest.raises(ValueError, match=r"symbolic|owner|output"):
        builder.write_receipt(output, receipt, exclusive=True)
    assert foreign.read_bytes() == b"preserve foreign owner\n"
    if state == "symlink":
        assert output.is_symlink()
    elif state == "directory":
        assert output.is_dir()
    else:
        assert output.read_bytes() == foreign.read_bytes()
    assert not list(tmp_path.glob(".receipt.json.*.tmp"))


@pytest.mark.parametrize("state", ["same", "divergent", "symlink"])
def test_exclusive_receipt_admits_only_identical_concurrent_arrival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    repo, manifest, gate = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest, manifest_dir=repo.parent, gate_attestation=gate, repo_root=repo,
    )
    expected = encode_reviewed_head_integration_receipt(receipt).encode("utf-8")
    output, foreign = tmp_path / "receipt.json", tmp_path / "foreign.json"
    foreign.write_bytes(b"preserve concurrent owner\n")
    real_link = os.link
    arrivals: list[tuple[int, int]] = []

    def arrive(source: Path, destination: Path) -> None:
        assert destination == output
        if state == "symlink":
            destination.symlink_to(foreign)
        else:
            destination.write_bytes(expected if state == "same" else foreign.read_bytes())
        metadata = destination.lstat()
        arrivals.append((metadata.st_ino, metadata.st_mtime_ns))
        real_link(source, destination)

    monkeypatch.setattr(os, "link", arrive)
    if state == "same":
        builder.write_receipt(output, receipt, exclusive=True)
        assert output.read_bytes() == expected
    else:
        with pytest.raises(ValueError, match=r"symbolic|owner|output"):
            builder.write_receipt(output, receipt, exclusive=True)
        assert output.read_bytes() == foreign.read_bytes()
    assert len(arrivals) == 1
    metadata = output.lstat()
    assert arrivals == [(metadata.st_ino, metadata.st_mtime_ns)]
    assert output.is_symlink() is (state == "symlink")
    assert foreign.read_bytes() == b"preserve concurrent owner\n"
    assert not list(tmp_path.glob(".receipt.json.*.tmp"))


def test_default_receipt_writer_retains_legacy_atomic_replacement(tmp_path: Path) -> None:
    repo, manifest, gate = _merge_fixture(tmp_path)
    receipt = builder.build_receipt_from_manifest(
        manifest, manifest_dir=repo.parent, gate_attestation=gate, repo_root=repo,
    )
    output = tmp_path / "receipt.json"
    output.write_bytes(b"legacy caller may replace\n")
    builder.write_receipt(output, receipt)
    assert output.read_text(encoding="utf-8") == encode_reviewed_head_integration_receipt(receipt)


def test_review_receipt_path_cannot_escape_manifest_directory(tmp_path: Path) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    head = manifest["heads"][0]  # type: ignore[index]
    head["review_receipt_file"] = "../outside.json"

    with pytest.raises(ValueError, match="review receipt path"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
            gate_attestation=attestation,
            repo_root=repo,
        )


def test_review_receipt_path_rejects_absolute_and_resolved_escape(
    tmp_path: Path,
) -> None:
    repo, manifest, attestation = _merge_fixture(tmp_path)
    head = manifest["heads"][0]  # type: ignore[index]
    head["review_receipt_file"] = str((repo.parent / "review.json").resolve())
    with pytest.raises(ValueError, match="relative"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
            gate_attestation=attestation,
            repo_root=repo,
        )

    outside = tmp_path.parent / f"outside-{tmp_path.name}.json"
    outside.write_text("{}", encoding="utf-8")
    head["review_receipt_file"] = f"../{outside.name}"
    with pytest.raises(ValueError, match="escapes"):
        builder.build_receipt_from_manifest(
            manifest,
            manifest_dir=repo.parent,
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
    makefile = compose_makefile(ROOT / "Makefile")
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


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_native_commit_metadata_matches_ordered_legacy_queries_and_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, object_format: str,
) -> None:
    monkeypatch.setenv("GIT_DEFAULT_HASH", object_format)
    repo, _manifest, _attestation = _merge_fixture(tmp_path)
    after = _git(repo, "rev-parse", "HEAD")
    source = _git(repo, "rev-parse", "fix/release-continuity")
    base = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    expected = {
        sha: (_git(repo, "rev-parse", f"{sha}^{{tree}}"),
              tuple(_git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]))
        for sha in (after, source)
    }
    original = builder._git
    commands: list[tuple[str, ...]] = []

    def recorded(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        result = original(root, *arguments, input_bytes=input_bytes)
        commands.append(arguments)
        return result

    monkeypatch.setattr(builder, "_git", recorded)
    assert builder._commit_metadata(repo, after, source, source) == expected
    assert len(commands) == 1
    assert commands[0] == (
        "show", "--no-patch", "--no-color", "--no-decorate", "--no-notes",
        "--no-show-signature", "--format=%H%x20%T%x20%P", after, source, "--",
    )
    assert builder._commit_metadata(repo, base, base) == {
        base: (_git(repo, "rev-parse", f"{base}^{{tree}}"), ()),
    }
    assert len(commands) == 2
    assert commands[1][-2:] == (base, "--")


def test_native_commit_metadata_refreshes_effective_object_view_each_call(tmp_path: Path) -> None:
    repo, _manifest, _attestation = _merge_fixture(tmp_path)
    after = _git(repo, "rev-parse", "HEAD")
    source = _git(repo, "rev-parse", "fix/release-continuity")
    base = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    first = builder._commit_metadata(repo, after, source)
    replacement = _git(
        repo, "commit-tree", _git(repo, "rev-parse", f"{after}^{{tree}}"),
        "-p", base, "-m", "native replacement view",
    )
    _git(repo, "replace", source, replacement)
    second = builder._commit_metadata(repo, after, source)
    assert first[source] != second[source]
    for sha in (after, source):
        assert second[sha] == (
            _git(repo, "rev-parse", f"{sha}^{{tree}}"),
            tuple(_git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]),
        )


def test_native_commit_metadata_ignores_configured_signature_display(
    tmp_path: Path,
) -> None:
    import shlex

    repo, _manifest, _attestation = _merge_fixture(tmp_path)
    source = _git(repo, "rev-parse", "fix/release-continuity")
    tree = _git(repo, "rev-parse", f"{source}^{{tree}}")
    signed = (
        f"tree {tree}\nparent {source}\n"
        "author Native Fixture <fixture@gludd.invalid> 1 +0000\n"
        "committer Native Fixture <fixture@gludd.invalid> 1 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n \n ZmFrZQ==\n"
        " -----END PGP SIGNATURE-----\n\nfixture signature boundary\n"
    ).encode("ascii")
    sha = builder._git(repo, "hash-object", "-t", "commit", "-w", "--stdin", input_bytes=signed).decode("ascii").strip()
    marker = repo / ".git" / "gpg-must-not-run"
    program = repo / ".git" / "fixture-gpg"
    program.write_text(
        "#!/bin/sh\nprintf called > " + shlex.quote(str(marker)) + "\nexit 77\n",
        encoding="utf-8",
    )
    program.chmod(0o700)
    _git(repo, "config", "gpg.program", str(program))
    _git(repo, "config", "log.showSignature", "true")
    subprocess.run(
        ["git", "-C", str(repo), "show", "--no-patch", "--show-signature", sha],
        check=False, capture_output=True, timeout=10,
    )
    assert marker.read_text(encoding="utf-8") == "called"
    marker.unlink()
    expected = {
        sha: (_git(repo, "rev-parse", f"{sha}^{{tree}}"),
              tuple(_git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:])),
    }
    assert builder._commit_metadata(repo, sha) == expected
    assert not marker.exists()


@pytest.mark.parametrize("shas", [
    (), ("HEAD",), ("--help",), ("a" * 39,), ("g" * 40,),
    ("a" * 40, "b" * 64), ("a" * 40, "b" * 40, "c" * 40),
])
def test_native_commit_metadata_refuses_nonexact_or_unbounded_input_before_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shas: tuple[str, ...],
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("invalid metadata request launched Git")

    monkeypatch.setattr(builder, "_git", forbidden)
    with pytest.raises(ValueError, match="metadata"):
        builder._commit_metadata(tmp_path, *shas)


@pytest.mark.parametrize("mutation", [
    "empty", "missing", "duplicate", "unexpected", "missing-tree",
    "bad-tree", "short-key", "bad-parent", "non-ascii", "oversized",
])
def test_native_commit_metadata_refuses_malformed_keyed_native_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    repo, _manifest, _attestation = _merge_fixture(tmp_path)
    after = _git(repo, "rev-parse", "HEAD")
    source = _git(repo, "rev-parse", "fix/release-continuity")
    expected = builder._commit_metadata(repo, after, source)
    rows = [
        " ".join((sha, tree, *parents))
        for sha, (tree, parents) in expected.items()
    ]
    if mutation == "empty":
        encoded = b""
    elif mutation == "missing":
        encoded = (rows[0] + "\n").encode("ascii")
    elif mutation == "duplicate":
        encoded = (rows[0] + "\n" + rows[0] + "\n" + rows[1]).encode("ascii")
    elif mutation == "unexpected":
        encoded = (rows[0] + "\n" + "f" * 40 + " " + expected[source][0]).encode("ascii")
    elif mutation == "missing-tree":
        encoded = (after + "\n" + rows[1]).encode("ascii")
    elif mutation == "bad-tree":
        encoded = (after + " " + "g" * 40 + "\n" + rows[1]).encode("ascii")
    elif mutation == "short-key":
        encoded = (after[:-1] + " " + expected[after][0] + "\n" + rows[1]).encode("ascii")
    elif mutation == "bad-parent":
        encoded = (after + " " + expected[after][0] + " invalid-parent\n" + rows[1]).encode("ascii")
    elif mutation == "non-ascii":
        encoded = b"\xff"
    else:
        encoded = b"\n" * (1_048_576 + 1)

    def substituted(*_args: object, **_kwargs: object) -> bytes:
        return encoded

    monkeypatch.setattr(builder, "_git", substituted)
    with pytest.raises(ValueError):
        builder._commit_metadata(repo, after, source)


def test_native_commit_metadata_maps_records_by_key_and_preserves_native_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _manifest, _attestation = _merge_fixture(tmp_path)
    after = _git(repo, "rev-parse", "HEAD")
    source = _git(repo, "rev-parse", "fix/release-continuity")
    expected = builder._commit_metadata(repo, after, source)
    original = builder._git

    def reversed_records(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        result = original(root, *arguments, input_bytes=input_bytes)
        return b"\n".join(reversed([row for row in result.splitlines() if row]))

    monkeypatch.setattr(builder, "_git", reversed_records)
    assert builder._commit_metadata(repo, after, source) == expected
    monkeypatch.setattr(builder, "_git", original)
    with pytest.raises(ValueError, match="repository evidence"):
        builder._commit_metadata(repo, "f" * 40)

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("native Git unavailable")

    monkeypatch.setattr(subprocess, "run", unavailable)
    with pytest.raises(ValueError, match="repository evidence"):
        builder._commit_metadata(repo, after, source)
