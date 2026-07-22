from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_plugin_imports.py"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
TS_FILES = sorted(PLUGIN_DIR.glob("*.ts"))

pytestmark = pytest.mark.xdist_group("plugin_imports")


def test_script_exits_zero_when_all_imports_valid():
    result = subprocess.run(
        ["python", str(SCRIPT)],
        capture_output=True, text=True, timeout=30, cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"check_plugin_imports exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "imports OK" in result.stdout


def test_no_bad_import_directly():
    for f in TS_FILES:
        text = f.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            assert "@opencode/plugin" not in line or "@opencode-ai/plugin" in line, (
                f"{f}:{i}: uses @opencode/plugin — must be @opencode-ai/plugin"
            )


def test_no_bare_fs_directly():
    for f in TS_FILES:
        text = f.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if 'from "fs"' in line or "from 'fs'" in line:
                assert "node:fs" in line, (
                    f"{f}:{i}: bare fs import — must be node:fs"
                )


def test_no_child_process_directly():
    for f in TS_FILES:
        text = f.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("@") or stripped.startswith("import type"):
                continue
            assert "child_process" not in line, (
                f"{f}:{i}: static child_process import at top level"
            )


def test_is_subagent_final_report_declared_before_use():
    stop_file = PLUGIN_DIR / "enforce-stop.ts"
    text = stop_file.read_text()
    lines = text.splitlines()
    first_use = None
    first_decl = None
    for i, line in enumerate(lines, 1):
        if "const isSubagentFinalReport" in line and first_decl is None:
            first_decl = i
        if "isSubagentFinalReport" in line and first_use is None:
            first_use = i
    assert first_decl is not None, "isSubagentFinalReport not declared"
    assert first_use is not None, "isSubagentFinalReport not used"
    assert first_use >= first_decl, (
        f"isSubagentFinalReport used at line {first_use} before declaration at line {first_decl}"
    )


def test_invalid_file_detected():
    text = (
        'import { execSync } from "child_process";\n'
        'import * as fs from "fs";\n'
        'import type { PluginAPI } from "@opencode/plugin";\n'
        "export const hooks = { tool: { execute: { before: () => ({}) } } };\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix="bad_imports_", delete=False
    ) as fp:
        fp.write(text)
        bad_path = fp.name

    try:
        target = PLUGIN_DIR / "zzz_bad_imports_test.ts"
        target.symlink_to(bad_path)
        try:
            result = subprocess.run(
                ["python", str(SCRIPT)],
                capture_output=True, text=True, timeout=30, cwd=str(ROOT),
            )
            assert result.returncode == 1, (
                f"Expected exit 1 for invalid imports, got {result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert "violations found" in result.stdout
        finally:
            target.unlink()
    finally:
        Path(bad_path).unlink()
