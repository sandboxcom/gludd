"""Structural + behavioral pin for scripts/verify_plugin_manifest.py.

Proves the manifest checker:
  - detects _isSubagent() self-call recursion bug
  - accepts correct _isSubagent() with env-var check
  - flags _isSubagent() that lacks env-var check entirely
  - exit-code contract (1 = gaps found, 0 = clean)
  - gap categories: MISSING-FILE, UNREGISTERED, MISSING-GUARD, RECURSION-BUG
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_plugin_manifest.py"


def _run(repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )


def _setup_repo(repo_root: Path, opencode_json_plugins: list[str], plugin_contents: dict[str, str]) -> None:
    opencode_json = repo_root / "opencode.json"
    plugin_dir = repo_root / ".opencode" / "plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    opencode_json.write_text(json.dumps({"plugin": opencode_json_plugins}))

    for rel_path, content in plugin_contents.items():
        fpath = plugin_dir / rel_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)


# --- _isSubagent() function validators ---

def test_recursion_bug_detected(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/buggy.ts"],
        plugin_contents={
            "buggy.ts": """
import * as fs from "node:fs"

function _isSubagent(): boolean {
  if (_isSubagent()) return true;
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`); } catch { return false; }
}

export default async ({}) => ({})

export async function tool_execute_before(input: any) {
  if (_isSubagent()) return
  // enforcement logic
}
""",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}: {result.stdout}"
    assert "RECURSION-BUG" in result.stdout, result.stdout


def test_correct_is_subagent_accepted(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/good.ts"],
        plugin_contents={
            "good.ts": """
import * as fs from "node:fs"

function _isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true;
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`); } catch { return false; }
}

export default async ({}) => ({})

export async function tool_execute_before(input: any) {
  if (_isSubagent()) return
  // enforcement logic
}
""",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stdout}"
    assert "RECURSION" not in result.stdout


def test_is_subagent_without_env_var_rejected(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/missing_env.ts"],
        plugin_contents={
            "missing_env.ts": """
import * as fs from "node:fs"

function _isSubagent(): boolean {
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`); } catch { return false; }
}

export default async ({}) => ({})

export async function tool_execute_before(input: any) {
  if (_isSubagent()) return
}
""",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}: {result.stdout}"
    assert "MISSING-GUARD" in result.stdout, result.stdout


def test_clean_with_no_plugins(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[],
        plugin_contents={
            "watchdog.ts": """
export default async ({}) => ({})
""",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stdout}"


def test_missing_file_detected(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/ghost.ts"],
        plugin_contents={},
    )
    result = _run(tmp_path)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert "MISSING-FILE" in result.stdout, result.stdout


def test_unregistered_disk_file_detected(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[],
        plugin_contents={
            "orphan.ts": """
export default async ({}) => ({})
""",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert "UNREGISTERED" in result.stdout, result.stdout


def test_direct_env_var_guard_accepted(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/direct.ts"],
        plugin_contents={
            "direct.ts": """
export default async ({}) => ({})

export async function tool_execute_before(input: any) {
  if (process.env.OPENCODE_SUBAGENT === "1") return
  // enforcement logic
}
""",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stdout}"


def test_missing_guard_with_no_is_subagent(tmp_path: Path):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/unguarded.ts"],
        plugin_contents={
            "unguarded.ts": """
export default async ({}) => ({})

export async function tool_execute_before(input: any) {
  // enforcement logic with NO guard
  return { permissionDecision: "deny" }
}
""",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert "MISSING-GUARD" in result.stdout, result.stdout
