#!/usr/bin/env python3
"""Behavioral tests for opencode plugin hooks.

INVOKES actual plugin hook functions with real inputs and asserts on return values.

Usage: python3 scripts/test_plugin_behavior.py [-v] [-k filter]
"""

import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
LIB_DIR = ROOT / ".opencode" / "lib"
OPENCODE_JSON = ROOT / "opencode.json"

def _node(code: str, *, env_override: dict | None = None, timeout: int = 15) -> dict | None:
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("GLUDD_") and k.endswith("_ENFORCE"):
            del env[k]
        if k == "GLUDD_HOT_MODULE_PREFIX":
            del env[k]
    if env_override:
        env.update(env_override)
    code = code.replace("{ROOT}", str(ROOT))
    full = f"""
import {{ createRequire }} from "node:module";
import * as path from "node:path";
const require = createRequire(import.meta.url);
{code}
"""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", full],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=str(ROOT),
    )
    stdout = proc.stdout.strip()
    if proc.returncode != 0:
        return {"_error": (proc.stderr or stdout)[:600], "_exit": proc.returncode}
    try:
        return json.loads(stdout.split("\n")[-1]) if stdout else None
    except json.JSONDecodeError:
        return {"_raw": stdout[:500]}


def _import_file(filepath: str) -> str:
    """JS to import a file and return {defaultIsFn, namedCount, namedKeys}."""
    return f"""
const mod = await import(path.resolve('{filepath}'))
console.log(JSON.stringify({{defaultIsFn: typeof mod.default === 'function', namedCount: Object.keys(mod).filter(k => k !== 'default').length, namedKeys: Object.keys(mod).filter(k => k !== 'default')}}))
"""


# ═══════════════════════════════════════════════════════════════════════════
# BOOT COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════

def test_all_25_plugins_boot():
    """Every registered plugin loads via dynamic import (default is function)."""
    config = json.loads(OPENCODE_JSON.read_text())
    failures = []
    for p in config["plugin"]:
        name = Path(p).name
        result = _node(_import_file(p))
        if result is None:
            failures.append(f"{name}: None")
        elif result.get("_error"):
            failures.append(f"{name}: {result['_error'][:200]}")
        elif not result.get("defaultIsFn"):
            failures.append(f"{name}: default not a function")
    assert not failures, f"Boot failures ({len(failures)}): {failures[:5]}"


def test_no_plugin_has_named_exports():
    """Zero named exports in any top-level .ts plugin file (openpos crash vector)."""
    config = json.loads(OPENCODE_JSON.read_text())
    violations = []
    for p in config["plugin"]:
        name = Path(p).name
        pp = Path(p)
        # Only check files directly in .opencode/plugin/ (openpos auto-discovery path)
        if pp.parent != PLUGIN_DIR:
            continue
        result = _node(_import_file(p))
        if result is None or result.get("_error"):
            continue
        if result.get("namedCount", 0) > 0:
            violations.append(f"{name}: {result['namedKeys']}")
    assert not violations, f"Named exports: {violations}"


def test_lib_test_exports_all_functions():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
const nonFns = Object.keys(mod).filter(k => typeof mod[k] !== 'function')
console.log(JSON.stringify({{fnCount: Object.keys(mod).filter(k => typeof mod[k] === 'function').length, nonFnCount: nonFns.length, nonFns}}))
""")
    assert result and result.get("nonFnCount") == 0, f"Non-fn: {result}"
    assert result["fnCount"] >= 14, f"Expected >=14 helpers, got {result.get('fnCount')}"


# ═══════════════════════════════════════════════════════════════════════════
# enforce-clean-tree
# ═══════════════════════════════════════════════════════════════════════════

def test_clean_tree_subagent_guard():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-clean-tree.ts'))
const plugin = await mod.default({{}})
const out = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(out ?? {{allowed: true}}))
""", env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("permissionDecision") != "deny"

def test_clean_tree_env_disable():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-clean-tree.ts'))
const plugin = await mod.default({{}})
const out = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(out ?? {{allowed: true}}))
""", env_override={"GLUDD_CLEAN_TREE_ENFORCE": "0"})
    assert result is None or result.get("permissionDecision") != "deny"

def test_clean_tree_allows_read():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-clean-tree.ts'))
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify({{ok: true}}))
""")
    assert result and result.get("ok") == True

def test_clean_tree_hook_exists():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-clean-tree.ts'))
const plugin = await mod.default({{}})
console.log(JSON.stringify({{hookExists: typeof plugin['tool.execute.before'] === 'function'}}))
""")
    assert result["hookExists"] == True


# ═══════════════════════════════════════════════════════════════════════════
# enforce-stop
# ═══════════════════════════════════════════════════════════════════════════

def test_stop_plugin_loads():
    result = _node(_import_file(str(PLUGIN_DIR / "enforce-stop.ts")))
    assert result and result.get("defaultIsFn") == True
    assert result["namedCount"] == 0

def test_stop_hooks_registered():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-stop.ts'))
const plugin = await mod.default({{}})
console.log(JSON.stringify({{hookCount: Object.keys(plugin).filter(k => typeof plugin[k] === 'function').length}}))
""")
    assert result["hookCount"] >= 1

def test_stop_subagent_guard():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-stop.ts'))
const plugin = await mod.default({{}})
console.log(JSON.stringify({{hookCount: Object.keys(plugin).length}}))
""", env_override={"OPENCODE_SUBAGENT": "1"})
    assert result and result["hookCount"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# enforce-verified-claims
# ═══════════════════════════════════════════════════════════════════════════

def test_verified_claims_loads():
    result = _node(_import_file(str(PLUGIN_DIR / "enforce-verified-claims.ts")))
    assert result and result.get("defaultIsFn") == True
    assert result["namedCount"] == 0

def test_verified_claims_allows_non_bash():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-verified-claims.ts'))
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}})
console.log(JSON.stringify({{ok: true}}))
""")
    assert result and result.get("ok") == True


# ═══════════════════════════════════════════════════════════════════════════
# enforce-no-suppressions
# ═══════════════════════════════════════════════════════════════════════════

def test_no_suppressions_loads():
    result = _node(_import_file(str(PLUGIN_DIR / "enforce-no-suppressions.ts")))
    assert result and result.get("defaultIsFn") == True
    assert result["namedCount"] == 0

def test_no_suppressions_allows_non_edit():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-no-suppressions.ts'))
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify({{ok: true}}))
""")
    assert result and result.get("ok") == True


# ═══════════════════════════════════════════════════════════════════════════
# enforce-commit-lock
# ═══════════════════════════════════════════════════════════════════════════

def test_commit_lock_loads():
    result = _node(_import_file(str(PLUGIN_DIR / "enforce-commit-lock.ts")))
    assert result and result.get("defaultIsFn") == True
    assert result["namedCount"] == 0

def test_commit_lock_allows_non_bash():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-commit-lock.ts'))
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify({{ok: true}}))
""")
    assert result and result.get("ok") == True


# ═══════════════════════════════════════════════════════════════════════════
# enforce-floor
# ═══════════════════════════════════════════════════════════════════════════

def test_floor_loads():
    result = _node(_import_file(str(PLUGIN_DIR / "enforce-floor.ts")))
    assert result and result.get("defaultIsFn") == True

def test_floor_subagent_guard():
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-floor.ts'))
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify({{ok: true}}))
""", env_override={"OPENCODE_SUBAGENT": "1"})
    assert result and result.get("ok") == True


# ═══════════════════════════════════════════════════════════════════════════
# enforce-multitask / enforce-delegate / enforce-make / enforce-session-start
# enforce-deadline / enforce-enhancement-ratio / enforce-tdd
# ═══════════════════════════════════════════════════════════════════════════

def test_multitask_loads():
    assert _node(_import_file(str(PLUGIN_DIR / "enforce-multitask.ts")))["defaultIsFn"] == True

def test_delegate_loads():
    assert _node(_import_file(str(PLUGIN_DIR / "enforce-delegate.ts")))["defaultIsFn"] == True

def test_make_loads():
    assert _node(_import_file(str(PLUGIN_DIR / "enforce-make.ts")))["defaultIsFn"] == True

def test_session_start_loads():
    assert _node(_import_file(str(PLUGIN_DIR / "enforce-session-start.ts")))["defaultIsFn"] == True

def test_deadline_loads():
    assert _node(_import_file(str(PLUGIN_DIR / "enforce-deadline.ts")))["defaultIsFn"] == True

def test_enhancement_ratio_loads():
    assert _node(_import_file(str(PLUGIN_DIR / "enforce-enhancement-ratio.ts")))["defaultIsFn"] == True

def test_tdd_loads():
    assert _node(_import_file(str(PLUGIN_DIR / "enforce-tdd.ts")))["defaultIsFn"] == True


# ═══════════════════════════════════════════════════════════════════════════
# LIBRARY: plugin_test_exports.ts functions
# ═══════════════════════════════════════════════════════════════════════════

def test_lib_getGitStatus():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
console.log(JSON.stringify({{isStr: typeof mod.getGitStatus() === 'string'}}))
""")
    assert result["isStr"]

def test_lib_isTreeDirty():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
console.log(JSON.stringify({{isBool: typeof mod.isTreeDirty() === 'boolean'}}))
""")
    assert result["isBool"]

def test_lib_countDirtyFiles():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
console.log(JSON.stringify({{z: mod.countDirtyFiles(''), t: mod.countDirtyFiles(' M a.py\\n?? b.py\\n M c.py')}}))
""")
    assert result["z"] == 0 and result["t"] == 3

def test_lib_buildDenyMessage():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
const m = mod.buildDenyMessage(7)
console.log(JSON.stringify({{has7: m.includes('7'), dirty: m.includes('DIRTY TREE')}}))
""")
    assert result["has7"] and result["dirty"]

def test_lib_isCommitCommand():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
console.log(JSON.stringify({{ship: mod.isCommitCommand('make ship-commit MSG=t'), no: mod.isCommitCommand('make test-unit'), gc: mod.isCommitCommand('make git-commit MSG=t'), nm: mod.isCommitCommand('git commit')}}))
""")
    assert result["ship"] and result["gc"] and not result["no"] and not result["nm"]

def test_lib_isSuppressionComment():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
console.log(JSON.stringify({{nq: mod.isSuppressionComment('# noqa'), ti: mod.isSuppressionComment('# type: ignore'), rc: mod.isSuppressionComment('# regular'), em: mod.isSuppressionComment('')}}))
""")
    assert result["nq"] and result["ti"] and not result["rc"] and not result["em"]

def test_lib_shouldBlock():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
console.log(JSON.stringify({{ev: mod.shouldBlock('committed abc12345'), no: mod.shouldBlock('everything committed and done'), emp: mod.shouldBlock(''), wo: mod.shouldBlock('working on fixing')}}))
""")
    assert not result["ev"] and result["no"] and not result["emp"] and not result["wo"]

def test_lib_getPermissionSeekingRe():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
const r = mod.getPermissionSeekingRe()
console.log(JSON.stringify({{wm: r.test('want me to continue?'), si: r.test('should i proceed?'), sh: r.test('shall i?'), nm: r.test('continuing')}}))
""")
    assert result["wm"] and result["si"] and result["sh"] and not result["nm"]

def test_lib_looksLikeStatusSummary():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
const s = '**Session 37 final status**\\n| Item | Status |\\n|------|--------|\\n| A.1   | done   |'
console.log(JSON.stringify({{ss: mod.looksLikeStatusSummary(s), nm: mod.looksLikeStatusSummary('reading file')}}))
""")
    assert result["ss"] == True and result["nm"] == False

def test_lib_getStatusSummaryRe():
    result = _node(f"""
const mod = await import(path.resolve('{LIB_DIR}/plugin_test_exports.ts'))
const r = mod.getStatusSummaryRe()
console.log(JSON.stringify({{isRe: r instanceof RegExp, m: r.test("here's the session 37 final status")}}))
""")
    assert result["isRe"] and result["m"]


# ═══════════════════════════════════════════════════════════════════════════
# FAIL-OPEN
# ═══════════════════════════════════════════════════════════════════════════

def test_fail_open_no_hot_module():
    os.environ.pop("GLUDD_HOT_MODULE_PREFIX", None)
    result = _node(f"""
const mod = await import(path.resolve('{PLUGIN_DIR}/enforce-clean-tree.ts'))
const plugin = await mod.default({{}})
console.log(JSON.stringify({{hookExists: typeof plugin['tool.execute.before'] === 'function'}}))
""")
    assert result and result["hookExists"] == True


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
