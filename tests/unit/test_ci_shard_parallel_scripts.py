from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_NAMES = (
    "ci_shards_parallel_status.py",
    "run_ci_shard_summary.py",
    "run_ci_shards_parallel.py",
    "start_ci_shards_parallel_bg.py",
)


def test_local_ci_shard_helper_scripts_compile() -> None:
    for script_name in SCRIPT_NAMES:
        py_compile.compile(str(ROOT / "scripts" / script_name), doraise=True)


def test_ci_shards_parallel_status_fails_closed_without_state(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "ci_shards_parallel_status.py"

    result = subprocess.run(
        [sys.executable, str(script), "--lines", "1"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "CI-SHARDS-STATUS missing state" in result.stdout
