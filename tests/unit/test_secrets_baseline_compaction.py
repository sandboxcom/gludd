"""Contracts for the auditable, compact detect-secrets baseline."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from scripts import manage_secrets_baseline as subject

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".secrets.baseline"
POLICY = ROOT / "config" / "detect_secrets_baseline_policy.json"
MANAGER = ROOT / "scripts" / "manage_secrets_baseline.py"


def _entry(
    marker: str, *, filename: str = "src/example.py", audited: bool = False
) -> dict[str, object]:
    entry: dict[str, object] = {
        "type": "Secret Keyword",
        "filename": filename,
        "hashed_secret": marker * 40,
        "is_verified": False,
        "line_number": 7,
    }
    if audited:
        entry["is_secret"] = False
    return entry


def _payload(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "version": "1.5.0",
        "plugins_used": [{"name": "KeywordDetector", "keyword_exclude": ""}],
        "filters_used": [
            {"path": "detect_secrets.filters.allowlist.is_line_allowlisted"},
            {"path": "detect_secrets.filters.common.is_baseline_file"},
            {
                "path": "detect_secrets.filters.regex.should_exclude_file",
                "pattern": ["policy-regex"],
            },
        ],
        "results": {"src/example.py": list(entries)} if entries else {},
        "generated_at": "2026-10-06T00:00:00Z",
    }


def test_compact_baseline_management_contract_is_installed() -> None:
    """The generated security artifact must remain below the global line cap."""
    assert POLICY.is_file(), "missing declarative detect-secrets baseline policy"
    assert MANAGER.is_file(), "missing deterministic baseline manager"
    with BASELINE.open(encoding="utf-8") as stream:
        physical_lines = sum(1 for _ in stream)
    assert physical_lines < 2_500, f"baseline has {physical_lines} physical lines"


def test_compact_baseline_is_canonical_json() -> None:
    """Whitespace-only regeneration must not create baseline churn."""
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    actual = BASELINE.read_text(encoding="utf-8")
    actual_digest = hashlib.sha256(actual.encode()).hexdigest()
    canonical_digest = hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    assert actual_digest == canonical_digest, (
        "baseline bytes are not deterministic canonical JSON: "
        f"actual={actual_digest}, canonical={canonical_digest}"
    )


def test_policy_excludes_only_generated_recursive_boundaries() -> None:
    """Known caches/vendors are excluded without hiding ordinary source paths."""
    policy = subject.load_policy(POLICY)
    excluded = re.compile(policy.exclude_files)
    for path in (
        ".secrets.baseline",
        "uv.lock",
        ".opencode/plugin-hashes.json",
        ".venv/lib/python/site.py",
        "dist/package.tar.gz",
        "frontend/node_modules/library/index.js",
        "src/vendor/generated.py",
        "tests/__pycache__/test_example.pyc",
        ".worktrees/feature/.secrets.baseline",
    ):
        assert excluded.search(path), f"generated boundary not excluded: {path}"
    for path in (
        "src/general_ludd/build_plan.py",
        "docs/vendor-selection.md",
        "tests/unit/test_secret_resolution.py",
    ):
        assert not excluded.search(path), f"source path over-excluded: {path}"


def test_compact_preserves_settings_fingerprints_and_audit_labels(
    tmp_path: Path,
) -> None:
    """Compaction changes only JSON layout, never security semantics."""
    baseline = tmp_path / ".secrets.baseline"
    payload = _payload(_entry("a", audited=True))
    baseline.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    policy = subject.load_policy(POLICY)

    before_settings = subject.summary(payload, physical_lines=1).settings_digest
    before_findings = subject.finding_counts(payload)
    evidence = subject.compact(baseline, policy)
    after = json.loads(baseline.read_text(encoding="utf-8"))

    assert evidence.physical_lines == 1
    assert evidence.settings_digest == before_settings
    assert subject.finding_counts(after) == before_findings
    assert after["results"]["src/example.py"][0]["is_secret"] is False


def test_refresh_prunes_only_scan_absences_and_preserves_survivor_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fresh mature scan is authoritative while surviving reviews remain."""
    baseline = tmp_path / ".secrets.baseline"
    previous = _payload(_entry("a", audited=True), _entry("b"))
    baseline.write_text(subject.canonical_text(previous), encoding="utf-8")
    fresh = _payload(_entry("a"), _entry("c"))
    fresh["generated_at"] = "2026-10-06T00:01:00Z"

    monkeypatch.setattr(subject, "_scan_fresh", lambda **_kwargs: fresh)
    before, after, removed, added = subject.refresh(
        baseline=baseline,
        policy=subject.load_policy(POLICY),
        repository=tmp_path,
        executable="detect-secrets",
    )
    result = json.loads(baseline.read_text(encoding="utf-8"))

    assert (before.findings, after.findings, removed, added) == (2, 2, 1, 1)
    assert result["results"]["src/example.py"][0]["is_secret"] is False
    assert len(baseline.read_text(encoding="utf-8").splitlines()) == 1


def test_refresh_rejects_plugin_drift_without_replacing_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scanner/config upgrade requires separate review and cannot ride refresh."""
    baseline = tmp_path / ".secrets.baseline"
    previous = _payload(_entry("a"))
    original = subject.canonical_text(previous)
    baseline.write_text(original, encoding="utf-8")
    drifted = _payload(_entry("a"))
    drifted["plugins_used"] = [{"name": "DifferentDetector"}]

    monkeypatch.setattr(subject, "_scan_fresh", lambda **_kwargs: drifted)
    with pytest.raises(subject.BaselineError, match="plugin configuration drifted"):
        subject.refresh(
            baseline=baseline,
            policy=subject.load_policy(POLICY),
            repository=tmp_path,
            executable="detect-secrets",
        )
    actual_digest = hashlib.sha256(baseline.read_bytes()).hexdigest()
    expected_digest = hashlib.sha256(original.encode()).hexdigest()
    assert actual_digest == expected_digest


def test_check_rejects_recursive_result_without_echoing_payload(tmp_path: Path) -> None:
    """Self-inventory fails with a content-free diagnostic."""
    baseline = tmp_path / ".secrets.baseline"
    recursive = _payload(
        _entry("a", filename=".secrets.baseline"),
    )
    recursive["results"] = {".secrets.baseline": recursive["results"]["src/example.py"]}
    baseline.write_text(subject.canonical_text(recursive), encoding="utf-8")

    with pytest.raises(subject.BaselineError) as raised:
        subject.check(baseline, subject.load_policy(POLICY))
    assert str(raised.value) == "baseline contains a policy-excluded result path"


def test_scan_command_uses_argument_vector_not_a_shell() -> None:
    """The policy regex and official filter cross the CLI boundary byte-exactly."""
    policy = subject.load_policy(POLICY)
    command = subject._scan_command("detect-secrets", policy, BASELINE)
    assert command[:4] == [
        "detect-secrets",
        "scan",
        "--baseline",
        str(BASELINE),
    ]
    assert command[4:] == ["--exclude-files", policy.exclude_files]


def test_summary_dictionary_is_content_free() -> None:
    """CI evidence contains aggregate metrics and digests, never identities."""
    evidence = subject.summary(_payload(_entry("a")), physical_lines=1).as_dict()
    assert set(evidence) == {
        "files",
        "findings",
        "physical_lines",
        "settings_digest",
        "findings_digest",
    }
    assert evidence["files"] == 1
    assert evidence["findings"] == 1


@pytest.mark.parametrize(
    ("text", "message"),
    [("{", "invalid JSON document"), ("[]", "JSON root must be an object")],
)
def test_json_loader_fails_closed(
    tmp_path: Path, text: str, message: str
) -> None:
    document = tmp_path / "document.json"
    document.write_text(text, encoding="utf-8")
    with pytest.raises(subject.BaselineError, match=message):
        subject._load_object(document)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"schema_version": 2}, "unsupported"),
        ({"max_physical_lines": 0}, "max_physical_lines"),
        ({"exclude_files": ""}, "exclude_files"),
        ({"exclude_files": "["}, "valid regex"),
        ({"required_filters": "filter"}, "required_filters"),
        ({"required_filters": [""]}, "required_filters"),
    ],
)
def test_policy_validation_rejects_unsafe_shapes(
    tmp_path: Path, update: dict[str, object], message: str
) -> None:
    policy_path = tmp_path / "policy.json"
    payload: dict[str, object] = {
        "schema_version": 1,
        "max_physical_lines": 2499,
        "exclude_files": "^excluded$",
        "required_filters": ["required.filter"],
    }
    payload.update(update)
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(subject.BaselineError, match=message):
        subject.load_policy(policy_path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"results": []}, "results must be an object"),
        ({"results": {1: []}}, "results are malformed"),
        ({"results": {"file": {}}}, "results are malformed"),
        ({"results": {"file": ["entry"]}}, "finding is malformed"),
    ],
)
def test_results_validation_rejects_malformed_shapes(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(subject.BaselineError, match=message):
        subject.finding_counts(payload)


@pytest.mark.parametrize("field", ["filename", "type", "hashed_secret"])
def test_finding_identity_requires_nonempty_strings(field: str) -> None:
    entry = _entry("a")
    entry[field] = ""
    payload = _payload(entry)
    with pytest.raises(subject.BaselineError, match="identity is malformed"):
        subject.finding_counts(payload)


@pytest.mark.parametrize(
    "filters",
    [None, ["entry"], [{}]],
)
def test_filter_validation_rejects_malformed_shapes(filters: object) -> None:
    payload = _payload()
    payload["filters_used"] = filters
    with pytest.raises(
        subject.BaselineError, match=r"filters_used|filter entry|filter path"
    ):
        subject.validate_payload(payload, subject.load_policy(POLICY))


@pytest.mark.parametrize("field", ["version", "plugins_used"])
def test_payload_requires_scanner_settings(field: str) -> None:
    payload = _payload()
    payload.pop(field)
    with pytest.raises(subject.BaselineError, match=field):
        subject.validate_payload(payload, subject.load_policy(POLICY))


def test_payload_requires_official_baseline_filter() -> None:
    payload = _payload()
    payload["filters_used"] = [
        {"path": "detect_secrets.filters.allowlist.is_line_allowlisted"}
    ]
    with pytest.raises(subject.BaselineError, match="baseline-file filter"):
        subject.validate_payload(payload, subject.load_policy(POLICY))


def test_finding_key_rejects_malformed_identity() -> None:
    with pytest.raises(subject.BaselineError, match="identity is malformed"):
        subject._finding_key("source.py", {"type": "Secret Keyword"})


def test_atomic_writer_creates_new_destination(tmp_path: Path) -> None:
    destination = tmp_path / "new.json"
    subject._write_atomic(destination, "{}\n")
    assert destination.read_text(encoding="utf-8") == "{}\n"


def test_compact_rejects_settings_change_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(subject.canonical_text(_payload()), encoding="utf-8")
    monkeypatch.setattr(subject, "_settings", lambda payload: {"id": id(payload)})
    with pytest.raises(subject.BaselineError, match="plugin settings"):
        subject.compact(baseline, subject.load_policy(POLICY))


def test_compact_rejects_fingerprint_change_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(subject.canonical_text(_payload()), encoding="utf-8")
    calls = iter((Counter({("a", "b", "c"): 1}), Counter()))
    monkeypatch.setattr(subject, "finding_counts", lambda _payload: next(calls))
    with pytest.raises(subject.BaselineError, match="finding identities"):
        subject.compact(baseline, subject.load_policy(POLICY))


def test_scan_failure_is_reported_as_safe_categories_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(subject.canonical_text(_payload()), encoding="utf-8")
    secret_text = b"Traceback\nValueError: credential-material-must-not-echo"
    completed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=secret_text)
    monkeypatch.setattr(subject.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(subject.BaselineError) as raised:
        subject._scan_fresh(
            executable="detect-secrets",
            policy=subject.load_policy(POLICY),
            repository=tmp_path,
            seed=seed,
            destination=tmp_path / "scan.json",
        )
    message = str(raised.value)
    assert "traceback" in message
    assert "ValueError" in message
    assert "credential-material" not in message


def test_refresh_rejects_non_policy_filter_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / ".secrets.baseline"
    previous = _payload(_entry("a"))
    baseline.write_text(subject.canonical_text(previous), encoding="utf-8")
    drifted = _payload(_entry("a"))
    drifted["filters_used"] = [
        *drifted["filters_used"],
        {"path": "custom.unreviewed.filter"},
    ]
    monkeypatch.setattr(subject, "_scan_fresh", lambda **_kwargs: drifted)

    with pytest.raises(subject.BaselineError, match="filter configuration drifted"):
        subject.refresh(
            baseline=baseline,
            policy=subject.load_policy(POLICY),
            repository=tmp_path,
            executable="detect-secrets",
        )


def test_refresh_preserves_timestamp_when_semantics_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / ".secrets.baseline"
    previous = _payload(_entry("a"))
    baseline.write_text(subject.canonical_text(previous), encoding="utf-8")
    fresh = _payload(_entry("a"))
    fresh["generated_at"] = "later"
    monkeypatch.setattr(subject, "_scan_fresh", lambda **_kwargs: fresh)

    subject.refresh(
        baseline=baseline,
        policy=subject.load_policy(POLICY),
        repository=tmp_path,
        executable="detect-secrets",
    )
    result = json.loads(baseline.read_text(encoding="utf-8"))
    assert result["generated_at"] == previous["generated_at"]


def test_check_rejects_noncanonical_and_over_limit_documents(tmp_path: Path) -> None:
    policy = subject.load_policy(POLICY)
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(json.dumps(_payload(), indent=2) + "\n", encoding="utf-8")
    with pytest.raises(subject.BaselineError, match="not canonical"):
        subject.check(baseline, policy)
    baseline.write_text(subject.canonical_text(_payload()), encoding="utf-8")
    tiny_limit = subject.Policy(0, policy.exclude_files, policy.required_filters)
    with pytest.raises(subject.BaselineError, match="physical line limit"):
        subject.check(baseline, tiny_limit)


@pytest.mark.parametrize("command", ["compact", "check", "summary"])
def test_main_content_free_commands_emit_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(subject.canonical_text(_payload()), encoding="utf-8")
    status = subject.main(
        [command, "--baseline", str(baseline), "--policy", str(POLICY)]
    )
    output = json.loads(capsys.readouterr().out)
    assert status == 0
    assert output["action"] == command
    assert "results" not in output


def test_main_refresh_emits_only_summary_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(subject.canonical_text(_payload()), encoding="utf-8")
    evidence = subject.summary(_payload(), physical_lines=1)
    monkeypatch.setattr(
        subject,
        "refresh",
        lambda **_kwargs: (evidence, evidence, 2, 1),
    )
    status = subject.main(
        ["refresh", "--baseline", str(baseline), "--policy", str(POLICY)]
    )
    output = json.loads(capsys.readouterr().out)
    assert status == 0
    assert output["removed_findings"] == 2
    assert output["added_findings"] == 1


def test_main_reports_content_free_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "credential-shaped-name.json"
    status = subject.main(
        ["check", "--baseline", str(missing), "--policy", str(POLICY)]
    )
    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert "invalid JSON document" in captured.err
