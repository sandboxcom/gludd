from __future__ import annotations

import os
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
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix="broken_plugin_", delete=False
    ) as f:
        f.write("THIS is NOT valid TypeScript {{{ [[[ ;;;\n")
        broken_path = f.name

    try:
        plugin_dir = ROOT / ".opencode" / "plugin"
        orig_files = list(plugin_dir.glob("*.ts"))

        # Symlink broken file into plugin dir so the script finds it
        target = plugin_dir / "zzz_broken_test.ts"
        os.symlink(broken_path, str(target))

        try:
            result = subprocess.run(
                ["python", str(SCRIPT)],
                capture_output=True, text=True, timeout=30, cwd=str(ROOT),
            )
            assert result.returncode == 1, (
                f"Expected exit 1 for invalid syntax, got {result.returncode}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )
            assert "SYNTAX ERROR" in result.stdout or "SYNTAX ERROR" in result.stderr
        finally:
            os.unlink(target)

    finally:
        os.unlink(broken_path)
