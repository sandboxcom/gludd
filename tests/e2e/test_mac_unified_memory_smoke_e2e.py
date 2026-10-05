"""CLI-level smoke checks that stay safe on hosts without Apple GPU access."""

from __future__ import annotations

import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "mac_unified_memory_smoke.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )


def test_dry_run_is_credential_free_and_reports_memory_kind() -> None:
    completed = _run("--dry-run", "--backend", "auto")

    payload = json.loads(completed.stdout)
    # The dry-run contract: no network, a known memory-kind vocabulary, and a
    # fit verdict. The script exits 0 when the host provides the detection
    # surface; on non-Apple hosts it may fail closed (rc 2/3/4) while still
    # emitting the documented JSON — that fail-closed payload is the contract.
    if platform.system() == "Darwin":
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode in {0, 2, 3, 4}, f"unexpected rc {completed.returncode}: {completed.stderr}"
    assert payload["network"]["used"] is False
    assert payload["memory_policy"]["kind"] in {"unified", "discrete", "system", "unknown"}
    assert isinstance(payload["model_fit"]["fits"], bool)


def test_live_cpu_path_executes_or_fails_closed_without_torch() -> None:
    completed = _run(
        "--live",
        "--backend",
        "cpu",
        "--allow-cpu",
    )
    payload = json.loads(completed.stdout)
    if importlib.util.find_spec("torch") is None:
        assert completed.returncode == 3
        assert payload["kind"] == "capability"
    else:
        assert completed.returncode in {0, 4}, completed.stderr
        if completed.returncode == 0:
            assert payload["telemetry"]["backend"] == "cpu"
