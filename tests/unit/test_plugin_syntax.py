from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_plugin_syntax.py"


def test_all_plugin_ts_files_parse_cleanly():
    result = subprocess.run(
        ["python", str(SCRIPT)],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"check_plugin_syntax exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "syntax OK" in result.stdout


def test_invalid_ts_file_detected():
    # Use an isolated temp directory instead of the real .opencode/plugin/ dir.
    # Writing a broken symlink into the real plugin dir races with the parallel
    # test_all_plugin_ts_files_parse_cleanly under pytest-xdist (both tests would
    # share the directory). The script now accepts an explicit dir argument.
    with tempfile.TemporaryDirectory(prefix="plugin_syntax_test_") as tmpdir:
        broken = Path(tmpdir) / "zzz_broken_test.ts"
        broken.write_text("THIS is NOT valid TypeScript {{{ [[[ ;;;\n")
        result = subprocess.run(
            ["python", str(SCRIPT), str(tmpdir)],
            capture_output=True, text=True, timeout=30, cwd=str(ROOT),
        )
        assert result.returncode == 1, (
            f"Expected exit 1 for invalid syntax, got {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "SYNTAX ERROR" in result.stdout or "SYNTAX ERROR" in result.stderr


def test_default_scan_ignores_transient_plugin_fixture():
    target = ROOT / ".opencode" / "plugin" / "zzz_broken_test.ts"
    target.write_text("THIS is NOT valid TypeScript {{{ [[[ ;;;\n")
    try:
        result = subprocess.run(
            ["python", str(SCRIPT)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
    finally:
        target.unlink()
    assert result.returncode == 0, (
        f"default scan should ignore transient fixtures\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
