"""Structural + behavioral pin for scripts/verify_plugin_manifest.py.

Proves the manifest checker:
  - detects _isSubagent() self-call recursion bug
  - accepts correct _isSubagent() with env-var check
  - flags _isSubagent() that lacks env-var check entirely
  - exit-code contract (1 = gaps found, 0 = clean)
  - gap categories: MISSING-FILE, UNREGISTERED, MISSING-GUARD, RECURSION-BUG

The script resolves its workspace from ``__file__`` (not ``cwd``), so the tests
import the module directly and monkey-patch the WORKSPACE / OPENCODE_JSON /
SEARCH_DIRS module-level constants to point at the per-test ``tmp_path``
fixture.  A subprocess + ``cwd=`` approach silently scans the real workspace
and produces false pass/fail verdicts.

Fixtures use the CURRENT plugin registration style (object-key syntax like
``"tool.execute.before": async (...)`` inside the default-export factory),
matching the pattern in ``.opencode/plugin/*.ts`` after the E.5 refactor that
removed named exports (``export async function tool_execute_before``).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_plugin_manifest.py"


def _load_module() -> ModuleType:
    """Import the script as an isolated module so we can patch its constants."""
    spec = importlib.util.spec_from_file_location(
        "_verify_plugin_manifest_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup_repo(
    repo_root: Path,
    opencode_json_plugins: list[str],
    plugin_contents: dict[str, str],
) -> None:
    opencode_json = repo_root / "opencode.json"
    plugin_dir = repo_root / ".opencode" / "plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    opencode_json.write_text(json.dumps({"plugin": opencode_json_plugins}))

    for rel_path, content in plugin_contents.items():
        fpath = plugin_dir / rel_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)


def _patch_workspace(
    mod: ModuleType, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirect the module's workspace constants at the per-test tmp_path."""
    monkeypatch.setattr(mod, "WORKSPACE", repo_root)
    monkeypatch.setattr(mod, "OPENCODE_JSON", repo_root / "opencode.json")
    monkeypatch.setattr(
        mod,
        "SEARCH_DIRS",
        [repo_root / ".opencode" / "plugin", repo_root / ".opencode" / "plugins"],
    )


def _verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    """Load the script with WORKSPACE patched to ``tmp_path``."""
    mod = _load_module()
    _patch_workspace(mod, tmp_path, monkeypatch)
    return mod


# --- _isSubagent() function validators ---


def test_recursion_bug_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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

export default async ({}) => ({
  "tool.execute.before": async (input: any, output: any) => {
    if (_isSubagent()) return
    // enforcement logic
  }
})
""",
        },
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, issues = mod.run()
    assert exit_code == 1, f"Expected exit 1, got {exit_code}: {issues}"
    assert any("RECURSION-BUG" in i for i in issues), issues


def test_correct_is_subagent_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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

export default async ({}) => ({
  "tool.execute.before": async (input: any, output: any) => {
    if (_isSubagent()) return
    // enforcement logic
  }
})
""",
        },
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, issues = mod.run()
    assert exit_code == 0, f"Expected exit 0, got {exit_code}: {issues}"
    assert not any("RECURSION" in i for i in issues)


def test_is_subagent_without_env_var_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/missing_env.ts"],
        plugin_contents={
            "missing_env.ts": """
import * as fs from "node:fs"

function _isSubagent(): boolean {
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`); } catch { return false; }
}

export default async ({}) => ({
  "tool.execute.before": async (input: any, output: any) => {
    if (_isSubagent()) return
  }
})
""",
        },
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, issues = mod.run()
    assert exit_code == 1, f"Expected exit 1, got {exit_code}: {issues}"
    assert any("MISSING-GUARD" in i for i in issues), issues


def test_clean_with_no_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Truly empty workspace: no registered plugins, no files on disk.
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[],
        plugin_contents={},
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, _ = mod.run()
    assert exit_code == 0, f"Expected exit 0, got {exit_code}"


def test_missing_file_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/ghost.ts"],
        plugin_contents={},
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, issues = mod.run()
    assert exit_code == 1, f"Expected exit 1, got {exit_code}: {issues}"
    assert any("MISSING-FILE" in i for i in issues), issues


def test_unregistered_disk_file_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[],
        plugin_contents={
            "orphan.ts": """
export default async ({}) => ({})
""",
        },
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, issues = mod.run()
    assert exit_code == 1, f"Expected exit 1, got {exit_code}: {issues}"
    assert any("UNREGISTERED" in i for i in issues), issues


def test_direct_env_var_guard_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/direct.ts"],
        plugin_contents={
            "direct.ts": """
export default async ({}) => ({
  "tool.execute.before": async (input: any, output: any) => {
    if (process.env.OPENCODE_SUBAGENT === "1") return
    // enforcement logic
  }
})
""",
        },
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, _ = mod.run()
    assert exit_code == 0, f"Expected exit 0, got {exit_code}"


def test_missing_guard_with_no_is_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _setup_repo(
        tmp_path,
        opencode_json_plugins=[".opencode/plugin/unguarded.ts"],
        plugin_contents={
            "unguarded.ts": """
export default async ({}) => ({
  "tool.execute.before": async (input: any, output: any) => {
    // enforcement logic with NO guard
    return { permissionDecision: "deny" }
  }
})
""",
        },
    )
    mod = _verifier(tmp_path, monkeypatch)
    exit_code, issues = mod.run()
    assert exit_code == 1, f"Expected exit 1, got {exit_code}: {issues}"
    assert any("MISSING-GUARD" in i for i in issues), issues
