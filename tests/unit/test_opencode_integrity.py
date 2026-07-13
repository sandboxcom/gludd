"""
Unit tests for check_opencode_integrity.py — 5+ test cases covering:
  missing directory, syntax error, missing default export, stale shared.ts, valid config.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

CHECKER = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_opencode_integrity.py"


def _run_checker(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(cwd)],
        capture_output=True, text=True, cwd=str(cwd),
    )


# ── Helper: build a minimal valid .opencode/ + opencode.json ─────────────

def _make_valid_opencode(root: Path) -> None:
    """Create a structurally valid .opencode/ tree that passes all checks."""
    (root / ".opencode" / "plugin").mkdir(parents=True)
    (root / ".opencode" / "plugins").mkdir(parents=True)
    (root / ".opencode" / "skill").mkdir(parents=True)
    (root / ".opencode" / "skills").mkdir(parents=True)
    (root / ".opencode" / "agent").mkdir(parents=True)

    (root / ".opencode" / "plugin" / "shared.ts").write_text(textwrap.dedent("""\
        export const FOO = 1
        export function bar() {}
        export default {}
    """))

    (root / ".opencode" / "plugin" / "enforce-make.ts").write_text(textwrap.dedent("""\
        import { FOO, bar } from "./shared"
        export default {}
    """))

    (root / ".opencode" / "plugins" / "watchdog.ts").write_text(textwrap.dedent("""\
        import { FOO } from "../plugin/shared"
        export default {}
    """))

    (root / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "bash": {
                "*": "deny",
                "make *": "allow",
            },
            "doom_loop": "deny",
        },
        "plugin": [
            "./.opencode/plugin/enforce-make.ts",
        ],
    }, indent=2))


# ── Test 1: valid config passes ──────────────────────────────────────────

def test_valid_config_passes(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "ALL PASSED" in result.stdout


# ── Test 2: missing required subdirectory ────────────────────────────────

def test_missing_directory_fails(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    (tmp_path / ".opencode" / "plugin").rename(tmp_path / ".opencode" / "plugin_renamed")
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "MISSING: .opencode/plugin/" in result.stdout


# ── Test 3: syntax error in .ts file ─────────────────────────────────────

def test_syntax_error_fails(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    (tmp_path / ".opencode" / "plugin" / "enforce-make.ts").write_text("}}}")
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "SYNTAX ERROR" in result.stdout
    assert result.returncode == 1
    assert "SYNTAX ERROR" in result.stdout


# ── Test 4: missing default export ───────────────────────────────────────

def test_missing_default_export_fails(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    (tmp_path / ".opencode" / "plugin" / "enforce-make.ts").write_text(
        'export const x = 1;\n'
    )
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "NO DEFAULT EXPORT" in result.stdout


# ── Test 5: stale shared.ts (imported name not exported) ─────────────────

def test_stale_shared_import_fails(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    # enforce-make.ts imports FOO and bar from shared — change shared to not export bar
    (tmp_path / ".opencode" / "plugin" / "shared.ts").write_text(textwrap.dedent("""\
        export const FOO = 1
        export default {}
    """))
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "SHARED MISMATCH" in result.stdout
    assert "bar" in result.stdout


# ── Test 6: shared.ts has no default export (fine, not a plugin) ─────────

def test_shared_has_default_is_fine(tmp_path: Path):
    """shared.ts doesn't need a default export — the checker skips it."""
    _make_valid_opencode(tmp_path)
    (tmp_path / ".opencode" / "plugin" / "shared.ts").write_text(
        "export const FOO = 1;\nexport function bar() {}\n"
    )
    result = _run_checker(tmp_path)
    assert result.returncode == 0


# ── Test 6b: hot_reload.ts has no default export (fine, helper module) ──

def test_hot_reload_no_default_is_fine(tmp_path: Path):
    """hot_reload.ts doesn't need a default export — the checker skips it."""
    _make_valid_opencode(tmp_path)
    (tmp_path / ".opencode" / "plugin" / "hot_reload.ts").write_text(
        "export function loadHotModule() {}\n"
    )
    result = _run_checker(tmp_path)
    assert result.returncode == 0


# ── Test 7: bad permission ordering in opencode.json ─────────────────────

def test_bad_permission_ordering_fails(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    (tmp_path / "opencode.json").write_text(json.dumps({
        "permission": {
            "bash": {
                "make *": "allow",  # wrong order
                "*": "deny",
            },
            "doom_loop": "deny",
        },
        "plugin": ["./.opencode/plugin/enforce-make.ts"],
    }))
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "BAD ORDER" in result.stdout


# ── Test 8: invalid JSON in opencode.json ────────────────────────────────

def test_invalid_json_fails(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    (tmp_path / "opencode.json").write_text("{ not valid json ]")
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "INVALID JSON" in result.stdout


# ── Test 9: non-ts files in plugin/ are ignored ──────────────────────────

def test_non_ts_files_ignored(tmp_path: Path):
    _make_valid_opencode(tmp_path)
    (tmp_path / ".opencode" / "plugin" / "README.md").write_text("hello")
    result = _run_checker(tmp_path)
    assert result.returncode == 0


# ── Test 10: no .opencode/ directory at all ──────────────────────────────

def test_no_opencode_dir_at_all(tmp_path: Path):
    (tmp_path / "opencode.json").write_text(json.dumps({
        "permission": {"bash": {"*": "deny", "make *": "allow"}, "doom_loop": "deny"},
        "plugin": [],
    }))
    result = _run_checker(tmp_path)
    assert result.returncode == 1
    assert "MISSING: .opencode/ directory" in result.stdout
