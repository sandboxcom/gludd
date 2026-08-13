from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_plugin_runtime.py"


def _run_runtime_check_with_plugin(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="plugin_runtime_test_") as tmpdir:
        plugin = Path(tmpdir) / "bad_plugin.ts"
        plugin.write_text(source)
        return subprocess.run(
            ["python", str(SCRIPT), str(tmpdir)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )


def test_check_plugin_runtime_runs_without_crashing():
    """The script should load all checked plugins cleanly."""
    result = subprocess.run(
        ["python", str(SCRIPT)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    message = chr(10).join([
        f"check_plugin_runtime exited {result.returncode}",
        f"stdout: {result.stdout}",
        f"stderr: {result.stderr}",
    ])
    assert result.returncode == 0, message
    assert "PASS: all plugins load successfully" in result.stdout




def test_node_child_process_import_loads():
    """Runtime validation accepts resolvable Node builtins.

    Forbidden-import policy is covered separately by check_plugin_imports.py.
    """
    result = _run_runtime_check_with_plugin(
        "import { exec } from 'node:child_process';\n"
        "export function foo() { return 1; }\n"
    )
    assert result.returncode == 0, (
        f"Expected runtime load success, got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "PASS: all plugins load successfully" in result.stdout


def test_node_fs_import_loads():
    """Runtime validation accepts the policy-compliant node:fs specifier."""
    result = _run_runtime_check_with_plugin(
        'import { readFileSync } from "node:fs";\n'
        "export function foo() { return 1; }\n"
    )
    assert result.returncode == 0, (
        f"Expected runtime load success, got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "PASS: all plugins load successfully" in result.stdout


def test_wrong_package_detected():
    """A .ts file importing from '@opencode/plugin' should be flagged."""
    result = _run_runtime_check_with_plugin(
        'import { something } from "@opencode/plugin";\n'
        "export function foo() { return 1; }\n"
    )
    assert result.returncode == 1, (
        f"Expected exit 1 for wrong package, got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "wrong package" in result.stdout or "@opencode/plugin" in result.stdout


def test_make_target_exists():
    """The check-plugin-runtime make target should be wired in."""
    makefile = ROOT / "Makefile"
    content = makefile.read_text()
    assert "check-plugin-runtime:" in content, "Missing check-plugin-runtime target"
    assert "check_plugin_runtime.py" in content, "Missing script reference in Makefile"

    gate_dep_line = next(line for line in content.split("\n") if line.startswith("gate:"))
    assert "check-plugin-runtime" in gate_dep_line, "check-plugin-runtime not in gate deps"

    gate_lite_dep_line = next(
        line
        for line in content.split("\n")
        if line.lstrip().startswith("gate-lite:") and not line.lstrip().startswith("#")
    )
    assert "check-plugin-runtime" in gate_lite_dep_line, (
        f"check-plugin-runtime not in gate-lite deps: {gate_lite_dep_line}"
    )


def test_strip_types_load_failure_detected():
    """Plugin that fails under node --experimental-strip-types should exit 1."""
    result = _run_runtime_check_with_plugin(
        'import * as nonexistent from "nonexistent-module-xyzzy-99913";\n'
        "export default async () => ({});\n"
    )
    assert result.returncode == 1, (
        f"Expected exit 1 for strip-types load failure, got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "FAIL:" in result.stdout, (
        f"Expected failed-plugin summary in output:\n{result.stdout}"
    )


def test_all_clean_plugins_exit_zero():
    """Script returns 0 when only valid plugins exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scripts_dir = Path(tmpdir) / "scripts"
        scripts_dir.mkdir()
        plugin_dir = Path(tmpdir) / ".opencode" / "plugin"
        plugin_dir.mkdir(parents=True)

        shutil.copy(SCRIPT, scripts_dir / "check_plugin_runtime.py")
        shutil.copy(
            ROOT / "scripts" / "validate_plugins_runtime.mjs",
            scripts_dir / "validate_plugins_runtime.mjs",
        )

        clean_plugin = plugin_dir / "clean_plugin.ts"
        clean_plugin.write_text("export default async function() { return {}; }\n")

        result = subprocess.run(
            ["python", str(scripts_dir / "check_plugin_runtime.py")],
            capture_output=True, text=True, timeout=60, cwd=str(tmpdir),
        )
        assert result.returncode == 0, (
            f"Expected exit 0 for clean plugins, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PASS: all plugins load successfully" in result.stdout, (
            f"Expected successful runtime summary in output:\n{result.stdout}"
        )


def test_enforce_stop_loads_under_strip_types():
    """The real enforce-stop plugin must pass the canonical runtime loader."""
    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            str(ROOT / ".opencode" / "plugin"),
        ],
        capture_output=True, text=True, timeout=15,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"enforce-stop.ts failed strip-types: {result.stderr[:500]}"
    )
    assert "PASS: all plugins load successfully" in result.stdout, (
        f"Expected successful runtime summary, got: {result.stdout[:200]}"
    )
