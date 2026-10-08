"""Compatibility guards for exact-path consumers of split test modules."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

audit_observability = importlib.import_module("scripts.audit_observability")
check_target_contract = importlib.import_module("scripts.check_target_contract")

ROOT = Path(__file__).resolve().parents[2]
AUTOMATIC_DISK_CLEANUP_TESTS = (
    "tests/unit/test_automatic_disk_cleanup.py",
    "tests/unit/test_automatic_disk_cleanup_resources.py",
)
BEHAVIORAL_ENFORCEMENT_TESTS = (
    ROOT / "tests" / "unit" / "test_behavioral_enforcement.py",
    ROOT / "tests" / "unit" / "test_behavioral_enforcement_runtime.py",
)
FEATURE_RECORD = ROOT / "docs" / "features" / "COMPOSED_MAKEFILE_CONSUMER_CONTRACT.md"


def _target_block(makefile: str, target: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(target)}:.*?(?=^[A-Za-z0-9_.%-]+:|\Z)",
        makefile,
    )
    assert match is not None, f"missing Make target: {target}"
    return match.group(0)


def test_disk_validation_targets_select_both_cleanup_test_modules() -> None:
    """Validation mode must exercise both halves of the cleanup test family."""
    makefile = compose_makefile(ROOT / "Makefile")

    for target in ("disk-cleanup-preflight", "check-disk"):
        block = _target_block(makefile, target)
        for test_path in AUTOMATIC_DISK_CLEANUP_TESTS:
            assert block.count(test_path) == 1, f"{target} must select {test_path} once"


def test_observability_audit_reads_both_behavioral_test_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin covered only in the runtime half must remain visible to AB066."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "enforce-runtime-only.ts").write_text(
        "// BLOCKING runtime-only enforcement\n",
        encoding="utf-8",
    )
    original = tmp_path / "test_behavioral_enforcement.py"
    runtime = tmp_path / "test_behavioral_enforcement_runtime.py"
    original.write_text("# original split\n", encoding="utf-8")
    runtime.write_text("# runtime-only split\n", encoding="utf-8")

    monkeypatch.setattr(audit_observability, "PLUGIN_DIR", plugin_dir)
    monkeypatch.setattr(
        audit_observability,
        "BEHAVIORAL_TEST_FILES",
        (original, runtime),
    )

    assert audit_observability.check_ab066_enforcement_coverage() == {
        "spec": "AB066",
        "status": "PASS",
        "findings": [],
    }


def test_target_contract_reads_assertions_from_both_behavioral_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Target assertions moved to the runtime half must still be contract-checked."""
    original = tmp_path / "test_behavioral_enforcement.py"
    runtime = tmp_path / "test_behavioral_enforcement_runtime.py"
    original.write_text("class TestOriginal:\n    pass\n", encoding="utf-8")
    runtime.write_text(
        'class TestRuntime:\n    guard_exists_in_makefile("runtime-guard")\n',
        encoding="utf-8",
    )
    makefile = tmp_path / "Makefile"
    makefile.write_text("\nruntime-guard:\n\t@echo PASS\n", encoding="utf-8")

    monkeypatch.setattr(
        check_target_contract,
        "BEHAVIORAL_TEST_FILES",
        (original, runtime),
    )
    monkeypatch.setattr(check_target_contract, "MAKEFILE", makefile)

    assert check_target_contract.main() == 0
    output = capsys.readouterr().out
    assert "runtime-guard: recipe is pass-through only" in output


def test_acceptance_matrix_keeps_historical_runner_paths_and_digests() -> None:
    """Replay fixtures remain immutable evidence rather than live suite selectors."""
    matrix = json.loads(
        (ROOT / "config" / "self-improve" / "acceptance-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    cases = {case["case_id"]: case for case in matrix["cases"]}

    for case_id in ("AM-DEBUG-01", "AM-INTEGRATION-01"):
        case = cases[case_id]
        assert case["required_test_paths"] == [
            "tests/unit/test_self_improve_codex_runner.py"
        ]
        assert (
            "TESTFILES=tests/unit/test_self_improve_codex_runner.py"
            in case["task"]["canonical_make_commands"][0]
        )
        fixture = ROOT / case["fixture_path"]
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == case["fixture_digest"]


def test_composed_consumer_contract_documents_operational_invariants() -> None:
    """The split-layout repair remains reviewable without replaying its incident."""
    record = FEATURE_RECORD.read_text(encoding="utf-8")

    for invariant in (
        "compose_makefile",
        "make -n -I",
        "ExecutionDispatchMixin",
        "coverage_gap_test_mappings.json",
        "zero-downtime deployment",
    ):
        assert invariant in record
    assert record.count("https://stackoverflow.com/questions/") >= 3
