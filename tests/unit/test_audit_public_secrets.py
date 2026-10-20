"""Exact reviewed-finding authority and atomic baseline publication contracts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    import audit_public_secrets as subject
    import manage_secrets_baseline as manager
else:
    from scripts import audit_public_secrets as subject
    from scripts import manage_secrets_baseline as manager

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = ROOT / "scripts" / "audit_public_secrets.py"
POLICY = ROOT / "config" / "detect_secrets_baseline_policy.json"
DETECTOR = "Base64 High Entropy String"
REVIEWED = (
    ("tests/unit/test_ci_run_summary_target.py", DETECTOR,
     "fab08bfa9e87d50cec5bba75f20269b8ab6827c6"),
    ("tests/unit/test_item_progress.py", DETECTOR,
     "7e9d0072485183cb48eb9ab08acac44f7293227e"),
    ("tests/unit/test_item_progress.py", DETECTOR,
     "025e5b8af0f109ad6158df7675c43319861ae61c"),
    ("tests/unit/test_pipeline_status_ssh_transport.py", DETECTOR,
     "7e9d0072485183cb48eb9ab08acac44f7293227e"),
    ("tests/unit/test_secrets_baseline_compaction.py", DETECTOR,
     "7e9d0072485183cb48eb9ab08acac44f7293227e"),
    ("TASKS.md", "Secret Keyword", "3d87c12867a9e3a7e5aeeeaf9d820091df6cec8c"),
)


def _entry(identity: manager.FindingIdentity) -> manager.JsonObject:
    filename, detector, digest = identity
    return {
        "filename": filename,
        "type": detector,
        "hashed_secret": digest,
        "is_verified": False,
        "line_number": 19,
        "extension": {"unchanged": True},
    }


def _payload() -> manager.JsonObject:
    results: dict[str, list[manager.JsonObject]] = {}
    for identity in REVIEWED:
        results.setdefault(identity[0], []).append(_entry(identity))
    return {
        "version": "1.5.0",
        "plugins_used": [{"name": "Base64HighEntropyString", "limit": 4.5}],
        "filters_used": [
            {"path": "detect_secrets.filters.allowlist.is_line_allowlisted"},
            {"path": "detect_secrets.filters.common.is_baseline_file"},
        ],
        "generated_at": "2026-10-20T00:00:00Z",
        "results": results,
        "extension": {"retain": ["unknown", "settings"]},
    }


def _results(payload: manager.JsonObject) -> dict[str, list[manager.JsonObject]]:
    return cast(dict[str, list[manager.JsonObject]], payload["results"])


def _baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    payload: manager.JsonObject | None = None,
) -> Path:
    # Mirror the original utility's source inputs for meaningful failing-first
    # controls. These copies never grant authority to the replacement owner.
    for filename in dict.fromkeys(identity[0] for identity in REVIEWED):
        destination = tmp_path / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / filename, destination)
    policy = tmp_path / "config" / "detect_secrets_baseline_policy.json"
    policy.parent.mkdir()
    shutil.copyfile(POLICY, policy)
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(
        manager.canonical_text(_payload() if payload is None else payload),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return baseline


def _run(baseline: Path) -> int:
    return subject.main(["--baseline", str(baseline)])


@pytest.mark.parametrize(
    ("filename", "value"),
    [
        ("tests/unit/test_ci_run_summary_target.py",
         "CI_RUN_SUMMARY_REPO=" + "unreviewed/repository"),
        ("tests/unit/test_item_progress.py",
         "PIPELINE_STATUS_REPO=" + "unreviewed/repository"),
        ("tests/unit/test_pipeline_status_ssh_transport.py",
         "PIPELINE_STATUS_REPO=" + "unreviewed/repository"),
        ("tests/unit/test_secrets_baseline_compaction.py",
         "PIPELINE_STATUS_REPO=" + "unreviewed/repository"),
        ("tests/unit/test_item_progress.py",
         "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=" + "98765432109"),
    ],
)
def test_new_source_prefix_never_authorizes_an_unreviewed_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str, value: str,
) -> None:
    """A newly added matching source literal retains its existing review state."""
    payload = _payload()
    digest = hashlib.sha1(value.encode()).hexdigest()
    unknown = _entry((filename, DETECTOR, digest))
    _results(payload)[filename].append(unknown)
    baseline = _baseline(tmp_path, monkeypatch, payload)
    source = tmp_path / filename
    source.write_text(
        source.read_text(encoding="utf-8") + f"\nUNREVIEWED_INPUT = {value!r}\n",
        encoding="utf-8",
    )

    assert _run(baseline) == 0

    result = json.loads(baseline.read_text(encoding="utf-8"))
    findings = result["results"][filename]
    retained = next(finding for finding in findings if finding["hashed_secret"] == digest)
    assert retained == unknown


@pytest.mark.parametrize("review_label", [None, False, True])
def test_only_exact_path_detector_digest_is_reviewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, review_label: bool | None,
) -> None:
    """Same hash in another detector or path is a distinct unapproved identity."""
    payload = _payload()
    filename, _detector, digest = REVIEWED[0]
    wrong_detector = _entry((filename, "Another Detector", digest))
    wrong_path = _entry(("src/another.py", DETECTOR, digest))
    if review_label is not None:
        wrong_detector["is_secret"] = review_label
        wrong_path["is_secret"] = review_label
    _results(payload)[filename].append(wrong_detector)
    _results(payload)["src/another.py"] = [wrong_path]
    baseline = _baseline(tmp_path, monkeypatch, payload)

    assert _run(baseline) == 0

    after = json.loads(baseline.read_text(encoding="utf-8"))
    retained = next(
        entry for entry in after["results"][filename]
        if entry["type"] == "Another Detector"
    )
    assert retained == wrong_detector
    assert after["results"]["src/another.py"] == [wrong_path]


def test_public_review_preserves_every_other_field_and_canonical_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _payload()
    baseline = _baseline(tmp_path, monkeypatch, payload)
    baseline.chmod(0o640)
    before = manager.summary(payload, physical_lines=1)

    assert _run(baseline) == 0

    after = json.loads(baseline.read_text(encoding="utf-8"))
    for entries in _results(payload).values():
        for entry in entries:
            entry["is_secret"] = False
    assert after == manager.normalize_payload(payload)
    evidence = manager.check(baseline, manager.load_policy(POLICY))
    assert evidence.findings == before.findings == 6
    assert evidence.findings_digest == before.findings_digest
    assert evidence.settings_digest == before.settings_digest
    assert stat.S_IMODE(baseline.stat().st_mode) == 0o640
    output = json.loads(capsys.readouterr().out)
    assert output["reviewed_findings"] == 6
    assert output["findings"] == 6
    assert "hashed_secret" not in output
    assert "results" not in output


@pytest.mark.parametrize("change", ["missing", "duplicate", "detector", "filename", "bucket"])
def test_inconsistent_intended_identity_refuses_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _payload()
    filename = REVIEWED[0][0]
    entries = _results(payload)[filename]
    if change == "missing":
        entries.clear()
    elif change == "duplicate":
        entries.append(dict(entries[0]))
    elif change == "detector":
        entries[0]["type"] = "Wrong Detector"
    elif change == "filename":
        entries[0]["filename"] = "src/not-reviewed.py"
    else:
        _results(payload)["src/wrong-bucket.py"] = _results(payload).pop(filename)
    baseline = _baseline(tmp_path, monkeypatch, payload)
    before = baseline.read_bytes()

    assert _run(baseline) == 2

    assert baseline.read_bytes() == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "audit-public-secrets:" in captured.err
    assert REVIEWED[0][2] not in captured.err


@pytest.mark.parametrize(
    ("field", "value"),
    [("is_secret", "false"), ("is_secret", True), ("is_verified", True),
     ("is_verified", None)],
)
def test_conflicting_intended_review_state_refuses_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    payload = _payload()
    _results(payload)[REVIEWED[0][0]][0][field] = value
    baseline = _baseline(tmp_path, monkeypatch, payload)
    before = baseline.read_bytes()

    assert _run(baseline) == 2

    assert baseline.read_bytes() == before


@pytest.mark.parametrize(
    "update",
    [
        {"results": []},
        {"results": {"file.py": [1]}},
        {"version": None},
        {"plugins_used": {}},
        {"filters_used": []},
        {"results": {".secrets.baseline": []}},
    ],
)
def test_invalid_shape_or_policy_refuses_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, update: manager.JsonObject,
) -> None:
    payload = _payload()
    payload.update(update)
    baseline = _baseline(tmp_path, monkeypatch, payload)
    before = baseline.read_bytes()

    assert _run(baseline) == 2

    assert baseline.read_bytes() == before


@pytest.mark.parametrize("invalid", ["{", "[]"])
def test_invalid_json_refuses_without_replacing_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    baseline.write_text(invalid, encoding="utf-8")

    assert _run(baseline) == 2

    assert baseline.read_text(encoding="utf-8") == invalid


def test_missing_baseline_is_not_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    baseline.unlink()

    assert _run(baseline) == 2

    assert not baseline.exists()


def test_exact_review_does_not_read_or_trust_current_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    for filename in dict.fromkeys(identity[0] for identity in REVIEWED):
        (tmp_path / filename).unlink()

    assert _run(baseline) == 0

    assert manager.check(baseline, manager.load_policy(POLICY)).findings == 6


def test_atomic_replacement_failure_retains_bytes_mode_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    baseline.chmod(0o640)
    before = baseline.read_bytes()
    attempted: list[Path] = []

    def refuse(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        temporary = Path(source)
        assert Path(destination) == baseline
        assert temporary.parent == baseline.parent
        assert baseline.read_bytes() == before
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o640
        candidate = json.loads(temporary.read_text(encoding="utf-8"))
        assert all(
            entry["is_secret"] is False
            for entries in candidate["results"].values()
            for entry in entries
        )
        attempted.append(temporary)
        raise OSError("native replacement refused")

    monkeypatch.setattr(os, "replace", refuse)

    assert _run(baseline) == 2

    assert len(attempted) == 1
    assert all(not temporary.exists() for temporary in attempted)
    assert baseline.read_bytes() == before
    assert stat.S_IMODE(baseline.stat().st_mode) == 0o640
    assert "native replacement refused" in capsys.readouterr().err


def test_native_flush_failure_retains_original_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    before = baseline.read_bytes()

    def refuse(_descriptor: int) -> None:
        raise OSError("native fsync refused")

    monkeypatch.setattr(os, "fsync", refuse)

    assert _run(baseline) == 2

    assert baseline.read_bytes() == before
    assert list(tmp_path.glob("..secrets.baseline.*.tmp")) == []


def test_review_is_idempotent_and_uses_existing_native_atomic_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    original = manager._write_atomic
    calls: list[Path] = []

    def record(path: Path, text: str) -> None:
        calls.append(path)
        original(path, text)

    monkeypatch.setattr(manager, "_write_atomic", record)

    assert _run(baseline) == 0
    after_first = baseline.read_bytes()
    assert _run(baseline) == 0

    assert calls == [baseline, baseline]
    assert baseline.read_bytes() == after_first


def test_direct_script_binds_the_same_owner_and_policy(tmp_path: Path) -> None:
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(manager.canonical_text(_payload()), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(AUDITOR), "--baseline", str(baseline),
         "--policy", str(POLICY)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=10,
    )

    assert completed.returncode == 0, completed.stderr

    assert json.loads(completed.stdout)["reviewed_findings"] == 6
    assert manager.check(baseline, manager.load_policy(POLICY)).findings == 6


def test_public_authority_is_the_six_explicit_reviewed_identities() -> None:
    """A new source literal cannot implicitly expand the approval inventory."""
    assert len(subject.PUBLIC_FINDINGS) == len(set(subject.PUBLIC_FINDINGS)) == 6
    assert set(subject.PUBLIC_FINDINGS) == set(REVIEWED)


@pytest.mark.parametrize(
    "identities",
    [
        (),
        (REVIEWED[0], REVIEWED[0]),
        (("file.py", "", "a" * 40),),
        (("file.py", DETECTOR, "invalid-digest"),),
        (("file.py", DETECTOR),),
    ],
)
def test_malformed_or_duplicate_review_request_refuses_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    identities: tuple[tuple[str, ...], ...],
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    before = baseline.read_bytes()
    with pytest.raises(manager.BaselineError, match="review identities"):
        manager.review_findings(
            baseline, manager.load_policy(POLICY),
            cast(tuple[manager.FindingIdentity, ...], identities),
        )
    assert baseline.read_bytes() == before


def test_missing_explicit_intended_filename_refuses_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    _results(payload)[REVIEWED[0][0]][0].pop("filename")
    baseline = _baseline(tmp_path, monkeypatch, payload)
    before = baseline.read_bytes()

    assert _run(baseline) == 2

    assert baseline.read_bytes() == before


@pytest.mark.parametrize("field", ["is_verified", "hashed_secret", "plugins_used"])
def test_duplicate_json_object_keys_refuse_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    source = baseline.read_text(encoding="utf-8")
    needle = f'"{field}":'
    malformed = source.replace(needle, f'"{field}":null,' + needle, 1)
    baseline.write_text(malformed, encoding="utf-8")

    assert _run(baseline) == 2

    assert baseline.read_text(encoding="utf-8") == malformed


def test_invalid_utf8_refuses_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    baseline.write_bytes(b"\xff")

    assert _run(baseline) == 2

    assert baseline.read_bytes() == b"\xff"


def test_old_source_input_argument_is_rejected_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline(tmp_path, monkeypatch)
    before = baseline.read_bytes()
    with pytest.raises(SystemExit) as raised:
        subject.main(["--baseline", str(baseline), "--tasks", "TASKS.md"])
    assert raised.value.code == 2
    assert baseline.read_bytes() == before


def test_unknown_duplicate_occurrences_and_audit_fields_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    unknown = _entry(("src/unknown.py", "Secret Keyword", "a" * 40))
    unknown["is_secret"] = True
    unknown["future_audit_field"] = {"retain": [1, "exact"]}
    _results(payload)["src/unknown.py"] = [dict(unknown), dict(unknown)]
    baseline = _baseline(tmp_path, monkeypatch, payload)
    before_counts = manager.finding_counts(payload)

    assert _run(baseline) == 0

    after = json.loads(baseline.read_text(encoding="utf-8"))
    assert manager.finding_counts(after) == before_counts
    assert after["results"]["src/unknown.py"] == [unknown, unknown]


def test_pinned_scanner_keeps_review_manifest_digests_outside_review_authority(
    tmp_path: Path,
) -> None:
    """Public identifiers are scanned and require their own independent review."""
    canonical = ROOT / ".secrets.baseline"
    before = canonical.read_bytes()
    candidate = manager._load_object(canonical)
    candidate["results"] = {}
    disposable = tmp_path / "scan.json"
    disposable.write_text(manager.canonical_text(candidate), encoding="utf-8")
    executable = shutil.which("detect-secrets")
    assert executable is not None
    filenames = ("scripts/audit_public_secrets.py", "tests/unit/test_audit_public_secrets.py")
    completed = subprocess.run(
        [
            executable, "--cores", "1", "scan", "--baseline", str(disposable),
            "--exclude-files", manager.load_policy(POLICY).exclude_files, *filenames,
        ],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    scanned = manager._load_object(disposable)
    digests = {hashlib.sha1(identity[2].encode("ascii")).hexdigest() for identity in REVIEWED}
    expected = Counter({
        (filename, "Hex High Entropy String", digest): 1
        for filename in filenames for digest in digests
    })
    assert manager.finding_counts(scanned) == expected
    native_findings = [
        dict(entry) for entries in _results(scanned).values() for entry in entries
    ]
    for filename, entries in _results(_payload()).items():
        _results(scanned).setdefault(filename, []).extend(entries)
    disposable.write_text(manager.canonical_text(scanned), encoding="utf-8")

    assert subject.main([
        "--baseline", str(disposable), "--policy", str(POLICY),
    ]) == 0

    reviewed = manager._load_object(disposable)
    actual = [
        entry for entries in _results(reviewed).values() for entry in entries
        if entry["type"] == "Hex High Entropy String"
    ]
    assert len(actual) == 8
    assert all("is_secret" not in entry for entry in actual)
    assert canonical.read_bytes() == before
    print(manager.canonical_text({"native_public_digest_candidates": native_findings}), end="")
