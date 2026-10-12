"""Read-only admission contracts for native temporary-artifact inspection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TARGET = "native-temp-artifact-inspect"
VARIABLE = "NATIVE_TEMP_ARTIFACT_PATH"
MODE = "NATIVE_TEMP_ARTIFACT_VALIDATE_ONLY"
SAMPLE = "/Users/shawnwilson/tmp/.gludd-native-inspect-contract.dylib"


def _run(path: str, mode: str = "1") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", TARGET, f"{VARIABLE}={path}", f"{MODE}={mode}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_native_temp_artifact_inspection_has_a_readonly_plan() -> None:
    """Plan mode must not require a real file or imply cleanup authority."""
    result = _run(SAMPLE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NATIVE-TEMP-INSPECTION-PLAN" in result.stdout
    assert "cleanup_authorized=false" in result.stdout


@pytest.mark.parametrize("mode", ["2", "yes", ""])
def test_native_temp_artifact_inspection_rejects_invalid_mode(mode: str) -> None:
    """Invalid execution selection must fail before any native tool starts."""
    result = _run(SAMPLE, mode)
    assert result.returncode != 0
    assert "must be 0 or 1" in result.stdout


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/Users/shawnwilson/tmp",
        "/tmp/unrelated.dylib",
        "/Users/shawnwilson/tmp/../secret.dylib",
        "/Users/shawnwilson/tmp/unrelated.txt",
    ],
)
def test_native_temp_artifact_inspection_rejects_unsafe_path(path: str) -> None:
    """Only one exact native-file candidate under the approved temp root is read."""
    result = _run(path)
    assert result.returncode != 0
    assert "exact native artifact" in result.stdout


def test_native_temp_artifact_inspection_preserves_literal_path_data() -> None:
    """Make functions must not interpret a diagnostic filename as program text."""
    path = "/Users/shawnwilson/tmp/.gludd-$(error must-not-expand).dylib"
    result = _run(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert path in result.stdout
    assert "*** must-not-expand" not in result.stderr


def test_native_temp_artifact_inspection_missing_file_is_visible() -> None:
    """Actual inspection cannot silently succeed with missing evidence."""
    result = _run(SAMPLE, "0")
    assert result.returncode != 0
    assert "regular non-symlink file required" in result.stdout


def test_native_temp_artifact_inspection_has_explicit_safe_contract() -> None:
    """The target has one documented no-mutation behavioral example."""
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    row = next(item for item in contract["targets"] if item["name"] == TARGET)
    assert row["make_variables"] == [VARIABLE, MODE]
    assert row["behavior"] == f"make {TARGET} {VARIABLE}={SAMPLE} {MODE}=1"


def test_stale_cleanup_uses_an_explicit_interpreter_as_literal_data(tmp_path: Path) -> None:
    """Use an existing matching runtime without changing global Python or syncing."""
    interpreter = tmp_path / "$(error interpreter-must-stay-data)-python"
    interpreter.write_text(
        "#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    interpreter.chmod(0o700)
    root = tmp_path / "gludd-resources"
    result = subprocess.run(
        [
            "make", "clean-stale-resource-namespaces",
            f"STALE_RESOURCE_NAMESPACE_PYTHON={interpreter}",
            f"STALE_RESOURCE_NAMESPACE_ROOT={root}",
            f"STALE_RESOURCE_NAMESPACE_PROJECT_ROOT={ROOT}",
            "STALE_RESOURCE_NAMESPACE_REGISTRY=",
            f"STALE_RESOURCE_NAMESPACE_RECEIPT={tmp_path / 'receipts.jsonl'}",
            "STALE_RESOURCE_NAMESPACE_GRACE_SECONDS=86400",
            "STALE_RESOURCE_NAMESPACE_MAX_CANDIDATES=1000",
            "STALE_RESOURCE_NAMESPACE_MAX_ENTRIES=100000",
            "STALE_RESOURCE_NAMESPACE_HEARTBEAT_SECONDS=5",
            "STALE_RESOURCE_NAMESPACE_VALIDATE_ONLY=1",
        ],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads(result.stdout)
    assert arguments[:2] == ["-m", "scripts.clean_stale_resource_namespaces"]
    assert arguments[arguments.index("--root") + 1] == str(root)
    assert arguments[-1] == "--validate-only"
    assert not root.exists()
