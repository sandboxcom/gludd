"""Deterministic review receipts for PyInstaller warning-graph changes."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from scripts import audit_pyinstaller_warnings as warning_audit
from scripts import compare_pyinstaller_warning_graphs as comparison
from scripts.makefile_layout import compose_makefile


def _warning(module: str, importer: str, flags: str = "optional") -> str:
    return f"missing module named {module} - imported by {importer} ({flags})\n"


def _digest(module: str, importer: str, flags: str = "optional") -> str:
    rendered = f"missing {module} <- {importer} ({flags})"
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _inputs(
    tmp_path: Path,
    *,
    before: str,
    after: str,
    accepted_before_digest: str,
) -> comparison.ComparisonInputs:
    before_path = tmp_path / "before.txt"
    after_path = tmp_path / "after.txt"
    allowlist_path = tmp_path / "allowlist.json"
    spec_path = tmp_path / "gludd.spec"
    before_path.write_text(before, encoding="utf-8")
    after_path.write_text(after, encoding="utf-8")
    allowlist_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "platform": "linux",
                "pyinstaller_version": "6.20.0",
                "transitive_warning_sha256_by_architecture": {
                    "aarch64": accepted_before_digest,
                },
                "reviewed_transitive_warning_sha256_alternates_by_architecture": {},
                "baseline_pinned_project_modules": [],
                "allowed_missing_imports": [],
            }
        ),
        encoding="utf-8",
    )
    spec_path.write_text("a = Analysis([], excludes=[])\n", encoding="utf-8")
    return comparison.ComparisonInputs(
        before=before_path,
        after=after_path,
        allowlist=allowlist_path,
        spec=spec_path,
        platform="linux",
        architecture="arm64",
        pyinstaller_version="6.20.0",
    )


def test_safe_transitive_change_produces_complete_deterministic_receipt(
    tmp_path: Path,
) -> None:
    before = _warning("old_backend", "dependency.compat")
    after = _warning("new_backend", "dependency.compat")
    inputs = _inputs(
        tmp_path,
        before=before,
        after=after,
        accepted_before_digest=_digest("old_backend", "dependency.compat"),
    )

    first = comparison.compare_warning_graphs(inputs)
    second = comparison.compare_warning_graphs(inputs)

    assert first == second
    assert first["schema_version"] == 1
    assert first["architecture"] == "aarch64"
    assert first["before"]["transitive_count"] == 1
    assert first["after"]["transitive_count"] == 1
    assert first["delta"] == {
        "added_count": 1,
        "removed_count": 1,
        "added": ["missing new_backend <- dependency.compat (optional)"],
        "removed": ["missing old_backend <- dependency.compat (optional)"],
    }


def test_comparison_rejects_unrelated_or_unaccepted_before_graph(
    tmp_path: Path,
) -> None:
    inputs = _inputs(
        tmp_path,
        before=_warning("old_backend", "dependency.compat"),
        after=_warning("new_backend", "dependency.compat"),
        accepted_before_digest="f" * 64,
    )

    with pytest.raises(comparison.ComparisonError, match="before graph is not accepted"):
        comparison.compare_warning_graphs(inputs)


def test_comparison_rejects_project_import_regressions(tmp_path: Path) -> None:
    before = _warning("old_backend", "dependency.compat")
    inputs = _inputs(
        tmp_path,
        before=before,
        after=before + _warning("required_backend", "general_ludd.cli", "top-level"),
        accepted_before_digest=_digest("old_backend", "dependency.compat"),
    )

    with pytest.raises(comparison.ComparisonError, match="actionable import edge"):
        comparison.compare_warning_graphs(inputs)


def test_receipt_writer_is_atomic_and_canonical(tmp_path: Path) -> None:
    receipt_path = tmp_path / "review.json"
    receipt = {"z": [2, 1], "a": {"value": True}}

    comparison.write_receipt(receipt_path, receipt)

    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert receipt_path.read_text(encoding="utf-8") == json.dumps(
        receipt,
        indent=2,
        sort_keys=True,
    ) + "\n"
    assert list(tmp_path.glob(".review.json.*.tmp")) == []


def test_comparator_reuses_the_fail_closed_audit_parser() -> None:
    assert comparison.parse_warning_file is warning_audit._parse_warning_file
    assert comparison.warning_digest is warning_audit._warning_digest


def test_makefile_exposes_review_receipt_target() -> None:
    makefile = compose_makefile(Path(__file__).resolve().parents[2] / "Makefile")

    assert "\ncompare-linux-pyinstaller-warnings:" in makefile
    assert '--before "$(PYINSTALLER_WARNING_BEFORE)"' in makefile
    assert '--after "$(PYINSTALLER_WARNING_AFTER)"' in makefile
    assert '--receipt "$(PYINSTALLER_WARNING_REVIEW_RECEIPT)"' in makefile


def test_cli_writes_receipt_and_reports_exact_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = _inputs(
        tmp_path,
        before=_warning("old_backend", "dependency.compat"),
        after=_warning("new_backend", "dependency.compat"),
        accepted_before_digest=_digest("old_backend", "dependency.compat"),
    )
    receipt_path = tmp_path / "receipt.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_pyinstaller_warning_graphs.py",
            "--before",
            str(inputs.before),
            "--after",
            str(inputs.after),
            "--allowlist",
            str(inputs.allowlist),
            "--spec",
            str(inputs.spec),
            "--platform",
            inputs.platform,
            "--architecture",
            inputs.architecture,
            "--pyinstaller-version",
            inputs.pyinstaller_version,
            "--receipt",
            str(receipt_path),
        ],
    )

    assert comparison.main() == 0
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["delta"]["added_count"] == 1
    assert "PASS: PyInstaller warning review receipt" in capsys.readouterr().out


def test_cli_fails_without_writing_receipt_for_unaccepted_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = _inputs(
        tmp_path,
        before=_warning("old_backend", "dependency.compat"),
        after=_warning("new_backend", "dependency.compat"),
        accepted_before_digest="f" * 64,
    )
    receipt_path = tmp_path / "receipt.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_pyinstaller_warning_graphs.py",
            "--before",
            str(inputs.before),
            "--after",
            str(inputs.after),
            "--allowlist",
            str(inputs.allowlist),
            "--spec",
            str(inputs.spec),
            "--platform",
            inputs.platform,
            "--architecture",
            inputs.architecture,
            "--pyinstaller-version",
            inputs.pyinstaller_version,
            "--receipt",
            str(receipt_path),
        ],
    )

    assert comparison.main() == 1
    assert not receipt_path.exists()
    assert "before graph is not accepted" in capsys.readouterr().err


def test_malformed_warning_input_becomes_comparison_error(tmp_path: Path) -> None:
    inputs = _inputs(
        tmp_path,
        before="not a PyInstaller warning\n",
        after="",
        accepted_before_digest=hashlib.sha256(b"").hexdigest(),
    )

    with pytest.raises(comparison.ComparisonError, match="unrecognized warning-file line"):
        comparison.compare_warning_graphs(inputs)


def test_raw_artifact_digest_wraps_io_failure(tmp_path: Path) -> None:
    with pytest.raises(comparison.ComparisonError, match="cannot read warning artifact"):
        comparison._file_digest(tmp_path)


def test_atomic_writer_removes_temporary_file_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(
        "scripts.compare_pyinstaller_warning_graphs.os.replace",
        fail_replace,
    )

    with pytest.raises(OSError, match="synthetic replace failure"):
        comparison.write_receipt(tmp_path / "review.json", {"ok": True})
    assert list(tmp_path.iterdir()) == []
