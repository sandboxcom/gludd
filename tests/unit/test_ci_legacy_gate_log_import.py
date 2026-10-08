"""Bounded, non-reusable import contracts for completed legacy gate logs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from scripts.ci_legacy_gate_log_import import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_FAILURES,
    MAX_IMPORT_LINES,
    MAX_IMPORT_OUTPUT_BYTES,
    LegacyGateLogImporter,
    main,
    render_import_result,
)

_RUN_ID = "20261007T204256Z-a198e742"
_FAMILIES = (
    ("unit-2", 37),
    ("unit-2", 45),
    ("unit-3a", 18),
    ("unit-3a", 29),
    ("other", 23),
)


def _completed_log(
    *,
    run_id: str = _RUN_ID,
    families: tuple[tuple[str, int], ...] = _FAMILIES,
    summary_failed: int | None = None,
) -> str:
    lines = [
        f"[gate-background-watch] start pid=123 run_id={run_id}",
        "SERIAL-SHARD-PHASE phase=coverage:reset result=0",
        "password=hunter2 path=/private/operator/test.py node=test_secret::case",
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001 result=0",
        "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001:cleanup result=0",
        "SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0",
    ]
    for shard, batch in families:
        lines.extend(
            [
                f"SERIAL-SHARD-PHASE phase={shard}:batch-{batch:03d} result=1",
                f"SERIAL-SHARD-PHASE phase={shard}:batch-{batch:03d}:cleanup result=0",
                f"SHARD-FAIL shard={shard} batch={batch} rc=1; "
                "later-batches=continuing",
            ]
        )
    failed = len(families) if summary_failed is None else summary_failed
    lines.extend(
        [
            f"SERIAL-SHARD-SUMMARY total=4 failed={failed} "
            "failures={'sanitized': 1} phases={}",
            "=== GATE: FAILED ===",
            f"[gate-background-watch] outcome=finished pid=123 run_id={run_id}",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_log(root: Path, content: str, *, name: str = "gate-legacy.log") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


def test_importer_sanitizes_five_failure_families_and_preserves_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".gate-logs"
    source = _write_log(root, _completed_log())
    before_bytes = source.read_bytes()
    before_stat = source.stat()

    result = LegacyGateLogImporter(root).import_log(source)
    rendered = render_import_result(result)

    assert result.accepted is True
    assert result.reason == "imported-non-reusable"
    assert result.report is not None
    assert result.report["reuse_policy"] == "NON-REUSABLE"
    assert result.report["skip_authorized"] is False
    assert result.report["skips"] == 0
    assert result.report["batches"] == {
        "executed": 6,
        "failed": 5,
        "passed": 1,
    }
    assert result.report["phases"] == {
        "failed": 5,
        "not_started": 0,
        "observed": 13,
        "passed": 8,
    }
    failures = result.report["failure_receipts"]
    assert isinstance(failures, list)
    assert len(failures) == 5
    assert all(receipt["reuse_policy"] == "NON-REUSABLE" for receipt in failures)
    assert all(receipt["skip_authorized"] is False for receipt in failures)
    assert all(receipt["failure_class"] == "test-failure" for receipt in failures)
    assert all("batch_identity_sha256" in receipt for receipt in failures)
    assert len({receipt["batch_identity_sha256"] for receipt in failures}) == 5
    assert len(rendered.encode("ascii")) <= 32 * 1024
    assert source.read_bytes() == before_bytes
    assert source.stat() == before_stat
    assert hashlib.sha256(before_bytes).hexdigest() in rendered
    assert str(source) not in rendered
    assert "hunter2" not in rendered
    assert "test_secret" not in rendered
    assert "/private/operator" not in rendered
    assert "unit-2" not in rendered
    assert _RUN_ID not in rendered


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("truncated", "truncated-log"),
        ("mixed-run", "mixed-run-log"),
        ("wrong-summary", "summary-mismatch"),
        ("duplicate-failure", "ambiguous-failure"),
        ("passed-terminal", "mixed-terminal-log"),
    ],
)
def test_importer_rejects_malformed_truncated_and_mixed_evidence(
    tmp_path: Path,
    mutation: str,
    expected_reason: str,
) -> None:
    content = _completed_log()
    if mutation == "truncated":
        content = content.removesuffix("\n")
    elif mutation == "mixed-run":
        content = content.replace(
            "=== GATE: FAILED ===",
            "[gate-background-watch] heartbeat run_id=other-run\n"
            "=== GATE: FAILED ===",
        )
    elif mutation == "wrong-summary":
        content = _completed_log(summary_failed=4)
    elif mutation == "duplicate-failure":
        marker = "SHARD-FAIL shard=unit-2 batch=37 rc=1; later-batches=continuing\n"
        content = content.replace(marker, marker + marker)
    elif mutation == "passed-terminal":
        content = content.replace(
            "=== GATE: FAILED ===",
            "=== GATE: PASSED ===\n=== GATE: FAILED ===",
        )
    source = _write_log(tmp_path / ".gate-logs", content)

    result = LegacyGateLogImporter(source.parent).import_log(source)

    assert result.accepted is False
    assert result.reason == expected_reason
    rendered = render_import_result(result)
    assert "NON-REUSABLE" in rendered
    assert '"skips":0' in rendered
    assert str(source) not in rendered


def test_importer_rejects_symlink_traversal_and_outside_source(tmp_path: Path) -> None:
    root = tmp_path / ".gate-logs"
    source = _write_log(root, _completed_log())
    outside = _write_log(tmp_path / "outside", _completed_log())
    symlink = root / "linked.log"
    symlink.symlink_to(source)
    traversing = root / ".." / root.name / source.name
    importer = LegacyGateLogImporter(root)

    assert importer.import_log(symlink).reason == "source-symlink"
    assert importer.import_log(traversing).reason == "source-unconfined"
    assert importer.import_log(outside).reason == "source-unconfined"


@pytest.mark.parametrize(
    ("limit_name", "limit", "expected_reason"),
    [
        ("max_bytes", 128, "log-too-large"),
        ("max_lines", 4, "line-limit"),
        ("max_failures", 4, "failure-limit"),
        ("max_output_bytes", 256, "output-limit"),
    ],
)
def test_importer_enforces_byte_line_failure_and_output_bounds(
    tmp_path: Path,
    limit_name: str,
    limit: int,
    expected_reason: str,
) -> None:
    root = tmp_path / ".gate-logs"
    source = _write_log(root, _completed_log())
    kwargs = {limit_name: limit}

    result = LegacyGateLogImporter(root, **kwargs).import_log(source)

    assert result.accepted is False
    assert result.reason == expected_reason


def test_importer_rejects_configuration_wider_than_hard_bounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="failure bound"):
        LegacyGateLogImporter(
            tmp_path,
            max_failures=MAX_IMPORT_FAILURES + 1,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_bytes": 0}, "byte bound"),
        ({"max_bytes": MAX_IMPORT_BYTES + 1}, "byte bound"),
        ({"max_lines": 0}, "line bound"),
        ({"max_lines": MAX_IMPORT_LINES + 1}, "line bound"),
        ({"max_failures": 0}, "failure bound"),
        ({"max_output_bytes": 255}, "output bound"),
        ({"max_output_bytes": MAX_IMPORT_OUTPUT_BYTES + 1}, "output bound"),
    ],
)
def test_importer_rejects_every_out_of_range_configuration(
    tmp_path: Path,
    kwargs: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        LegacyGateLogImporter(tmp_path, **kwargs)


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("run-missing", "run-identity-missing"),
        ("summary-missing", "summary-invalid"),
        ("phase-invalid", "phase-marker-invalid"),
        ("primary-duplicate", "ambiguous-batch"),
        ("primary-not-started", "ambiguous-batch"),
        ("pass-invalid", "batch-marker-invalid"),
        ("pass-duplicate", "ambiguous-batch"),
        ("failure-invalid", "failure-marker-invalid"),
        ("pass-and-failure", "ambiguous-batch"),
        ("batch-incomplete", "batch-evidence-incomplete"),
        ("pass-result-mismatch", "batch-result-mismatch"),
        ("failure-result-mismatch", "batch-result-mismatch"),
        ("shard-summary-mismatch", "summary-mismatch"),
        ("phase-evidence-missing", "phase-evidence-missing"),
    ],
)
def test_importer_fails_closed_on_ambiguous_or_inconsistent_markers(
    tmp_path: Path,
    mutation: str,
    expected_reason: str,
) -> None:
    content = _completed_log()
    primary_pass = "SERIAL-SHARD-PHASE phase=unit-1a1:batch-001 result=0\n"
    pass_marker = "SHARD-BATCH-PASS shard=unit-1a1 batch=1 rc=0\n"
    failure_marker = (
        "SHARD-FAIL shard=unit-2 batch=37 rc=1; later-batches=continuing\n"
    )
    if mutation == "run-missing":
        content = content.replace(f" run_id={_RUN_ID}", "")
    elif mutation == "summary-missing":
        content = content.replace(
            "SERIAL-SHARD-SUMMARY total=4 failed=5 "
            "failures={'sanitized': 1} phases={}\n",
            "",
        )
    elif mutation == "phase-invalid":
        content = content.replace(
            "SERIAL-SHARD-PHASE phase=coverage:reset result=0",
            "SERIAL-SHARD-PHASE phase=coverage:reset result=invalid",
        )
    elif mutation == "primary-duplicate":
        content = content.replace(primary_pass, primary_pass + primary_pass)
    elif mutation == "primary-not-started":
        content = content.replace(
            primary_pass,
            "SERIAL-SHARD-PHASE "
            "phase=unit-1a1:batch-001 result=not-started\n",
        )
    elif mutation == "pass-invalid":
        content = content.replace(pass_marker, pass_marker.replace("rc=0", "rc=1"))
    elif mutation == "pass-duplicate":
        content = content.replace(pass_marker, pass_marker + pass_marker)
    elif mutation == "failure-invalid":
        content = content.replace(
            failure_marker,
            failure_marker.replace("later-batches=continuing", "later-batches=invalid"),
        )
    elif mutation == "pass-and-failure":
        content = content.replace(
            failure_marker,
            "SHARD-BATCH-PASS shard=unit-2 batch=37 rc=0\n" + failure_marker,
        )
    elif mutation == "batch-incomplete":
        content = content.replace(pass_marker, "")
    elif mutation == "pass-result-mismatch":
        content = content.replace(primary_pass, primary_pass.replace("result=0", "result=1"))
    elif mutation == "failure-result-mismatch":
        content = content.replace(
            "SERIAL-SHARD-PHASE phase=unit-2:batch-037 result=1",
            "SERIAL-SHARD-PHASE phase=unit-2:batch-037 result=2",
        )
    elif mutation == "shard-summary-mismatch":
        content = content.replace("SUMMARY total=4", "SUMMARY total=3")
    elif mutation == "phase-evidence-missing":
        content = "\n".join(
            line
            for line in content.splitlines()
            if not line.startswith("SERIAL-SHARD-PHASE ")
        ) + "\n"
    source = _write_log(tmp_path / ".gate-logs", content)

    result = LegacyGateLogImporter(source.parent).import_log(source)

    assert result.accepted is False
    assert result.reason == expected_reason
    assert result.report == {
        "reuse_policy": "NON-REUSABLE",
        "skip_authorized": False,
        "skips": 0,
    }


def test_importer_rejects_empty_unavailable_oversized_line_and_invalid_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".gate-logs"
    empty = _write_log(root, "", name="empty.log")
    missing = root / "missing.log"
    long_line = _write_log(
        root,
        "x" * (1024**2 + 1) + "\n",
        name="long-line.log",
    )
    invalid_utf8 = root / "invalid-utf8.log"
    invalid_utf8.write_bytes(b"\xff\n")
    importer = LegacyGateLogImporter(root)

    assert importer.import_log(empty).reason == "empty-log"
    assert importer.import_log(missing).reason == "source-unconfined"
    assert importer.import_log(long_line).reason == "line-too-large"
    assert importer.import_log(invalid_utf8).reason == "encoding-invalid"

    unreadable = _write_log(root, _completed_log(), name="unreadable.log")

    def raise_oserror(_path: Path) -> bytes:
        raise OSError("simulated read race")

    monkeypatch.setattr(Path, "read_bytes", raise_oserror)
    assert importer.import_log(unreadable).reason == "source-unavailable"


def test_importer_cli_emits_only_sanitized_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / ".gate-logs"
    source = _write_log(root, _completed_log())
    monkeypatch.setattr(
        sys,
        "argv",
        ["ci_legacy_gate_log_import.py", "--log", str(source), "--allowed-root", str(root)],
    )

    assert main() == 0
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "imported-non-reusable"
    assert str(source) not in output
    assert _RUN_ID not in output


def test_import_result_is_fixed_schema_and_ascii_bounded(tmp_path: Path) -> None:
    root = tmp_path / ".gate-logs"
    source = _write_log(root, _completed_log())
    result = LegacyGateLogImporter(root).import_log(source)
    payload = json.loads(render_import_result(result))

    assert set(payload) == {
        "accepted",
        "reason",
        "report",
        "schema_version",
    }
    assert payload["accepted"] is True
    assert payload["report"]["source"]["line_count"] > 0
