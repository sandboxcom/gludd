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
    """The script should not raise unhandled exceptions; exit 0 or 1 is valid."""
    result = subprocess.run(
        ["python", str(SCRIPT)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    assert result.returncode in (0, 1), (
        f"check_plugin_runtime exited {result.returncode} (expected 0 or 1)\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert (
        "runtime check skipped" in result.stdout
        or "runtime load + import checks OK" in result.stdout
        or "runtime/import check(s) failed" in result.stdout
    )


def test_dangerous_child_process_detected():
    """A .ts file with import child_process should be flagged."""
    result = _run_runtime_check_with_plugin(
        "import { exec } from 'child_process';\n"
        "export function foo() { return 1; }\n"
    )
    assert result.returncode == 1, (
        f"Expected exit 1 for child_process import, got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "child_process" in result.stdout


def test_bare_fs_import_detected():
    """A .ts file importing from 'fs' (not 'node:fs') should be flagged."""
    result = _run_runtime_check_with_plugin(
        'import { readFileSync } from "fs";\n'
        "export function foo() { return 1; }\n"
    )
    assert result.returncode == 1, (
        f"Expected exit 1 for bare 'fs' import, got {result.returncode}\n"
        f"stdout: {result.stdout}"
    )
    assert "bare 'fs'" in result.stdout or "fs" in result.stdout


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
    assert "RUNTIME LOAD FAILED" in result.stdout, (
        f"Expected 'RUNTIME LOAD FAILED' in output:\n{result.stdout}"
    )


def test_all_clean_plugins_exit_zero():
    """Script returns 0 when only valid plugins exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scripts_dir = Path(tmpdir) / "scripts"
        scripts_dir.mkdir()
        plugin_dir = Path(tmpdir) / ".opencode" / "plugin"
        plugin_dir.mkdir(parents=True)

        shutil.copy(SCRIPT, scripts_dir / "check_plugin_runtime.py")

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
        assert "checks OK" in result.stdout, (
            f"Expected 'checks OK' in output:\n{result.stdout}"
        )


def test_enforce_stop_loads_under_strip_types():
    """The REAL enforce-stop.ts must load under node --experimental-strip-types."""
    result = subprocess.run(
        [
            "node", "--experimental-strip-types", "-e",
            "(async()=>{await import('./.opencode/plugin/enforce-stop.ts'); console.log('OK')})()",
        ],
        capture_output=True, text=True, timeout=15,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"enforce-stop.ts failed strip-types: {result.stderr[:500]}"
    )
    assert "OK" in result.stdout, (
        f"Expected OK, got: {result.stdout[:200]}"
    )
