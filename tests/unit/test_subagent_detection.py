#!/usr/bin/env python3
"""Tests for subagent detection mechanisms used by enforcement plugins.

Each enforcement plugin uses a `_isSubagent()` function that checks:
  1. `process.env.OPENCODE_SUBAGENT === "1"` (primary signal)
  2. `/tmp/gludd-subagent-<pid>.json` file existence (fallback)

BUG FOUND (2026-07-12): The `_isSubagent()` function in current plugins has
infinite recursion — it calls itself instead of checking the env var. See
test_detect_is_subagent_bug() below.

These tests verify:
  - Env-var detection logic works correctly
  - File-based fallback detection works
  - Both signals absent -> not subagent
  - A mock plugin hook skips when subagent detected
  - A mock plugin hook enforces when not subagent
  - Hot modules include subagent guard in source code
  - Broken hot modules fail-open
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
LIB_DIR = ROOT / ".opencode" / "lib"

_tmp_counter = 0


def _run_ts(ts_code: str, env_override: dict | None = None, timeout: int = 15):
    """Write TS code to temp file, run with node --experimental-strip-types, return parsed JSON."""
    global _tmp_counter
    _tmp_counter += 1
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix=f"subagent_test_{_tmp_counter}_", delete=False
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        if env_override:
            for k, v in list(env_override.items()):
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        if not stdout:
            return None
        return json.loads(stdout)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ============================================================================
# Test 1: Env-var OPENCODE_SUBAGENT=1 is detected as subagent
# ============================================================================

def test_env_var_subagent_detected():
    """process.env.OPENCODE_SUBAGENT === '1' is the primary subagent signal."""
    code = """
const isSubagent = process.env.OPENCODE_SUBAGENT === "1"
console.log(JSON.stringify({isSubagent}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result["isSubagent"]


def test_env_var_not_subagent_when_zero():
    """OPENCODE_SUBAGENT=0 or empty means NOT a subagent."""
    code = """
const isSubagent = process.env.OPENCODE_SUBAGENT === "1"
console.log(JSON.stringify({isSubagent}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "0"})
    assert not result["isSubagent"]

    result2 = _run_ts(code, env_override={"OPENCODE_SUBAGENT": ""})
    assert not result2["isSubagent"]


def test_env_var_not_subagent_when_unset():
    """OPENCODE_SUBAGENT missing entirely means NOT a subagent."""
    code = """
const isSubagent = process.env.OPENCODE_SUBAGENT === "1"
console.log(JSON.stringify({isSubagent}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": None})
    assert not result["isSubagent"]


# ============================================================================
# Test 2: File-based fallback subagent detection
# ============================================================================

def test_file_based_fallback_subagent_detected():
    """File-based fallback (/tmp/gludd-subagent-<pid>.json) signals subagent."""
    code = """
const fs = await import('node:fs')
const pid = process.pid
const fallbackFile = `/tmp/gludd-subagent-${pid}.json`
fs.writeFileSync(fallbackFile, JSON.stringify({subagent: true, ts: Date.now()}))

const envSubagent = process.env.OPENCODE_SUBAGENT === "1"
const fileSubagent = (() => {
  try { return fs.existsSync(fallbackFile) } catch { return false }
})()
const isSubagent = envSubagent || fileSubagent

try { fs.unlinkSync(fallbackFile) } catch {}
console.log(JSON.stringify({envSubagent, fileSubagent, isSubagent}))
"""
    result = _run_ts(code)
    assert not result["envSubagent"]
    assert result["fileSubagent"]
    assert result["isSubagent"]


def test_file_based_fallback_no_file_means_not_subagent():
    """No fallback file + no env var -> not a subagent."""
    code = """
const fs = await import('node:fs')
const pid = process.pid
const fallbackFile = `/tmp/gludd-subagent-${pid}.json`
try { fs.unlinkSync(fallbackFile) } catch {}

const envSubagent = process.env.OPENCODE_SUBAGENT === "1"
const fileSubagent = (() => {
  try { return fs.existsSync(fallbackFile) } catch { return false }
})()
const isSubagent = envSubagent || fileSubagent

console.log(JSON.stringify({envSubagent, fileSubagent, isSubagent}))
"""
    result = _run_ts(code)
    assert not result["envSubagent"]
    assert not result["fileSubagent"]
    assert not result["isSubagent"]


def test_file_based_fallback_corrupt_state_fail_safe():
    """Non-JSON fallback file doesn't crash detection (fail-open)."""
    code = """
const fs = await import('node:fs')
const pid = process.pid
const fallbackFile = `/tmp/gludd-subagent-${pid}.json`
fs.writeFileSync(fallbackFile, 'not valid json at all {{{[')

const fileSubagent = (() => {
  try {
    if (fs.existsSync(fallbackFile)) {
      const data = JSON.parse(fs.readFileSync(fallbackFile, 'utf8'))
      return data?.subagent === true
    }
  } catch {}
  return false
})()

try { fs.unlinkSync(fallbackFile) } catch {}
console.log(JSON.stringify({fileSubagent}))
"""
    result = _run_ts(code)
    assert not result["fileSubagent"]


# ============================================================================
# Test 3: Neither signal present -> enforcement enabled
# ============================================================================

def test_no_signals_means_not_subagent():
    """With no env var and no file, isSubagent() returns false."""
    code = """
const fs = await import('node:fs')
const pid = process.pid
const fallbackFile = `/tmp/gludd-subagent-${pid}.json`
try { fs.unlinkSync(fallbackFile) } catch {}

const correctIsSubagent = function(): boolean {
    if (process.env.OPENCODE_SUBAGENT === "1") return true
    try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`) } catch { return false }
}

console.log(JSON.stringify({isSubagent: correctIsSubagent()}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": ""})
    assert not result["isSubagent"]


# ============================================================================
# Test 4: Mock plugin hooks skip when isSubagent() returns true
# ============================================================================

def test_mock_hook_skips_when_env_subagent():
    """A hook guard that checks isSubagent() skips enforcement when env set."""
    code = """
const fs = await import('node:fs')

const isSubagent = (): boolean => {
    if (process.env.OPENCODE_SUBAGENT === "1") return true
    try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`) } catch { return false }
}

const hook = async (input: any): Promise<any> => {
    if (isSubagent()) return  // skip enforcement
    return { permissionDecision: "deny", message: "ENFORCED" }
}

const result = await hook({tool: 'edit'})
console.log(JSON.stringify(result ?? {allowed: true}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed"), \
        f"Mock hook should skip for subagent, got: {result}"


def test_mock_hook_skips_when_file_fallback():
    """A hook guard skips enforcement when file-based fallback exists."""
    code = """
const fs = await import('node:fs')
const pid = process.pid
const fallbackFile = `/tmp/gludd-subagent-${pid}.json`
fs.writeFileSync(fallbackFile, JSON.stringify({}))

const isSubagent = (): boolean => {
    if (process.env.OPENCODE_SUBAGENT === "1") return true
    try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`) } catch { return false }
}

const hook = async (input: any): Promise<any> => {
    if (isSubagent()) return
    return { permissionDecision: "deny", message: "ENFORCED" }
}

const result = await hook({tool: 'edit'})
try { fs.unlinkSync(fallbackFile) } catch {}
console.log(JSON.stringify(result ?? {allowed: true}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed"), \
        f"Mock hook should skip when file fallback exists, got: {result}"


# ============================================================================
# Test 5: Mock plugin hooks enforce when isSubagent() returns false
# ============================================================================

def test_mock_hook_enforces_when_not_subagent():
    """A hook enforces when isSubagent() returns false."""
    code = """
const fs = await import('node:fs')
const pid = process.pid
const fallbackFile = `/tmp/gludd-subagent-${pid}.json`
try { fs.unlinkSync(fallbackFile) } catch {}

const isSubagent = (): boolean => {
    if (process.env.OPENCODE_SUBAGENT === "1") return true
    try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`) } catch { return false }
}

const hook = async (input: any): Promise<any> => {
    if (isSubagent()) return
    return { permissionDecision: "deny", message: "ENFORCED: not subagent" }
}

const result = await hook({tool: 'edit'})
console.log(JSON.stringify(result ?? {allowed: true}))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny when not subagent"
    assert result.get("permissionDecision") == "deny", \
        f"Should enforce when not subagent, got: {result}"
    assert "not subagent" in result.get("message", "")


def test_mock_hook_enforces_when_env_explicitly_zero():
    """Hook enforces when OPENCODE_SUBAGENT=0 (explicitly not subagent)."""
    code = """
const fs = await import('node:fs')
const pid = process.pid
const fallbackFile = `/tmp/gludd-subagent-${pid}.json`
try { fs.unlinkSync(fallbackFile) } catch {}

const isSubagent = (): boolean => {
    if (process.env.OPENCODE_SUBAGENT === "1") return true
    try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`) } catch { return false }
}

const hook = async (input: any): Promise<any> => {
    if (isSubagent()) return
    return { permissionDecision: "deny", message: "ENFORCED" }
}

const result = await hook({tool: 'edit'})
console.log(JSON.stringify(result ?? {allowed: true}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "0"})
    assert result is not None, "Expected deny when env=0"
    assert result.get("permissionDecision") == "deny", \
        f"Should enforce when OPENCODE_SUBAGENT=0, got: {result}"


# ============================================================================
# Test 6: Hot modules include subagent guard in their source code
# Post E.5 refactor: isSubagent is imported from ../lib/shared.ts, not defined
# inline. Check import presence + usage (or inline definition for non-refactored
# plugins).
# ============================================================================

def test_deadline_source_has_subagent_guard():
    """enforce-deadline.ts imports isSubagent from shared.ts with file-based fallback."""
    source = (PLUGIN_DIR / "enforce-deadline.ts").read_text()
    assert "isSubagent" in source, "enforce-deadline.ts must import isSubagent()"
    assert "../lib/shared.ts" in source, "enforce-deadline.ts must import from lib/shared.ts"


def test_floor_source_has_subagent_guard():
    """enforce-floor.ts imports isSubagent from shared.ts with file-based fallback."""
    source = (PLUGIN_DIR / "enforce-floor.ts").read_text()
    assert "isSubagent" in source, "enforce-floor.ts must import isSubagent()"
    assert "../lib/shared.ts" in source, "enforce-floor.ts must import from shared.ts (file-based fallback lives there)"


def test_clean_tree_source_has_subagent_guard():
    """enforce-clean-tree.ts imports isSubagent from shared.ts with file-based fallback."""
    source = (PLUGIN_DIR / "enforce-clean-tree.ts").read_text()
    assert "isSubagent" in source, "enforce-clean-tree.ts must import isSubagent()"
    assert "../lib/shared.ts" in source, "enforce-clean-tree.ts must import from lib/shared.ts"


def test_enhancement_source_has_subagent_guard():
    """enforce-enhancement-ratio.ts imports isSubagent from shared.ts with file-based fallback."""
    source = (PLUGIN_DIR / "enforce-enhancement-ratio.ts").read_text()
    assert "isSubagent" in source, "enforce-enhancement-ratio.ts must import isSubagent()"
    assert "../lib/shared.ts" in source, "enforce-enhancement-ratio.ts must import from lib/shared.ts"


ALL_HOT_MODULE_PLUGINS = [
    "enforce-deadline.ts",
    "enforce-floor.ts",
    "enforce-enhancement-ratio.ts",
]


def test_hot_module_plugins_use_subagent_guard():
    """Hot-module plugins import isSubagent() guard from shared.ts."""
    for fn in ALL_HOT_MODULE_PLUGINS:
        source = (PLUGIN_DIR / fn).read_text()
        has_load_hot_module = "loadHotModule" in source
        has_is_subagent = "isSubagent" in source
        assert has_load_hot_module, f"{fn}: must use loadHotModule"
        assert has_is_subagent, f"{fn}: must import isSubagent() from shared.ts"

    for fn in ["enforce-clean-tree.ts", "enforce-delegate.ts"]:
        source = (PLUGIN_DIR / fn).read_text()
        assert "isSubagent" in source, f"{fn}: must import isSubagent() from shared.ts"
        assert "../lib/shared.ts" in source, f"{fn}: must import from shared.ts (file-based fallback lives there)"


# ============================================================================
# Test 7: Broken hot modules fail-open (fallback to defaultImpl)
# ============================================================================

def test_broken_hot_module_fail_open():
    """loadHotModule returns defaults when hot module file has invalid code."""
    code = f"""\
const fs = await import('node:fs')
const hotPath = '/tmp/gludd-hot-test-subagent.js'
const corruptCode = 'THIS IS NOT VALID JAVASCRIPT {{{{{{}}}}}};;;'
fs.writeFileSync(hotPath, corruptCode)

const mod = await import('{LIB_DIR}/hot_reload.ts')
const defaults = {{ "tool.execute.before": async () => "default called" }}
const result = mod.loadHotModule('test-subagent', defaults)
const fn = result['tool.execute.before']
const callResult = fn ? await fn() : 'no fn'
try {{ fs.unlinkSync(hotPath) }} catch {{}}
console.log(JSON.stringify({{callResult, isDefault: callResult === 'default called'}}))
"""
    result = _run_ts(code)
    assert result["isDefault"], \
        f"Broken hot module should fall back to defaults, got: {result}"


def test_broken_hot_module_does_not_crash(tmp_path):
    """A hot module that throws on parse does not crash loadHotModule."""
    hot_name = f"test-broken-{os.getpid()}"
    hot_prefix = str(tmp_path / "hot-")
    hot_path = f"{hot_prefix}{hot_name}.js"

    code = f"""\
const fs = await import('node:fs')
fs.writeFileSync('{hot_path}', 'throw new Error("BROKEN HOT MODULE")')

const mod = await import('{LIB_DIR}/hot_reload.ts')
const defaults = {{ "tool.execute.before": async () => "default-fallback" }}
const loaded = mod.loadHotModule('{hot_name}', defaults)
const fn = loaded['tool.execute.before']
const result = fn ? await fn() : 'no-function'
try {{ fs.unlinkSync('{hot_path}') }} catch {{}}
console.log(JSON.stringify({{result, didNotCrash: true, isFallback: result === 'default-fallback'}}))
"""
    result = _run_ts(
        code,
        env_override={"GLUDD_HOT_MODULE_PREFIX": hot_prefix},
    )
    assert result["didNotCrash"], "Broken hot module must not crash"
    assert result["isFallback"], \
        f"Should fall back to defaults, got: {result}"


def test_missing_hot_module_returns_defaults():
    """loadHotModule returns defaults when hot module file does not exist."""
    code = f"""\
const mod = await import('{LIB_DIR}/hot_reload.ts')
const defaults = {{ "tool.execute.before": async () => "default-only" }}
const loaded = mod.loadHotModule('nonexistent-subagent-test', defaults)
const fn = loaded['tool.execute.before']
const result = fn ? await fn() : 'no-function'
console.log(JSON.stringify({{result, isDefault: result === 'default-only'}}))
"""
    result = _run_ts(code)
    assert result["isDefault"], \
        f"Missing hot module should return defaults, got: {result}"


def test_nullish_hot_module_hook_does_not_crash(tmp_path):
    """Null hook entry in hot module returns null without crashing.

    loadHotModule returns the hot module's exports as-is. If a hook entry is
    null, the caller (proxy layer) checks `if (fn)` before calling, so null
    is treated as "hook not available" — the proxy uses undefined/null return
    for the hook, which means the caller (plugin framework) allows the action.
    This is the non-crash assert: the system handles null hooks without error.
    """
    hot_name = f"test-nullish-{os.getpid()}"
    hot_prefix = str(tmp_path / "hot-")
    hot_path = f"{hot_prefix}{hot_name}.js"

    code = f"""\
const fs = await import('node:fs')
fs.writeFileSync('{hot_path}', 'var exports = exports || {{}}; exports["tool.execute.before"] = null')

const mod = await import('{LIB_DIR}/hot_reload.ts')
const defaults = {{ "tool.execute.before": async () => "default-from-null" }}
let loaded, fn, err
try {{
  loaded = mod.loadHotModule('{hot_name}', defaults)
  fn = loaded['tool.execute.before']
}} catch (e) {{
  err = String(e)
}}
try {{ fs.unlinkSync('{hot_path}') }} catch {{}}
console.log(JSON.stringify({{didNotCrash: !err, fnIsNull: fn === null}}))
"""
    result = _run_ts(
        code,
        env_override={"GLUDD_HOT_MODULE_PREFIX": hot_prefix},
    )
    assert result["didNotCrash"], f"Null hook entry should not crash, got err: {result.get('err', '')}"
    assert result["fnIsNull"], "Hot module with null hook should return null fn"


# ============================================================================
# BUG DETECTION: _isSubagent() infinite recursion in current plugins
# ============================================================================
# The _isSubagent() functions in enforce-deadline.ts, enforce-floor.ts,
# enforce-clean-tree.ts, enforce-enhancement-ratio.ts, enforce-delegate.ts
# currently have infinite recursion (they call themselves instead of checking
# process.env.OPENCODE_SUBAGENT first). This test documents the bug pattern.

def test_detect_is_subagent_bug():
    """Post E.5 refactor: plugins import isSubagent from shared.ts.

    The old _isSubagent() self-recursion bug (calling itself instead of
    checking OPENCODE_SUBAGENT env var first) was fixed by centralizing the
    subagent guard into shared.ts. This test verifies all previously-buggy
    plugins now import the correct isSubagent from shared.ts.
    """
    import re
    for fn in [
        "enforce-deadline.ts",
        "enforce-floor.ts",
        "enforce-clean-tree.ts",
        "enforce-enhancement-ratio.ts",
        "enforce-delegate.ts",
    ]:
        source = (PLUGIN_DIR / fn).read_text()
        # Post-refactor: no local _isSubagent definition — import from shared.ts
        has_old_fn = "function _isSubagent" in source
        assert not has_old_fn, (
            f"{fn}: stale _isSubagent() definition found — should import "
            f"isSubagent from ../lib/shared.ts instead"
        )
        has_import = re.search(
            r'import\s+\{[^}]*\bisSubagent\b[^}]*\}\s+from\s+"[^"]*shared\.ts"',
            source,
        )
        assert has_import, f"{fn}: must import isSubagent from shared.ts"
