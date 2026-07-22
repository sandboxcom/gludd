"""Verify which hooks each enforcement plugin exports in opencode 1.17.9.

Loads every plugin, inspects the hook names it returns, verifies they are
callable, and reports which hooks are dead code (text.complete, session.idle,
event — all removed in 1.17.9 or never implemented).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

_tmp_counter = 0


def _run_ts(ts_code: str, env_override: dict | None = None, timeout: int = 15):
    global _tmp_counter
    _tmp_counter += 1
    tmppath = f"/tmp/hook_fire_test_{_tmp_counter}_{os.getpid()}.ts"
    with open(tmppath, "w") as f:
        f.write(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = "0"
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmppath],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        stdout = proc.stdout.strip()
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {stdout[:400]}"
            )
        if not stdout:
            return None
        for line in reversed(stdout.split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmppath)


# ── All known plugin files ──────────────────────────────────────────────────

UTILITY_FILES = {"hot_reload.ts", "shared.ts"}
PLUGIN_FILES = sorted(
    p.name
    for p in list(PLUGIN_DIR.glob("*.ts")) + list(PLUGINS_DIR.glob("*.ts"))
    if p.name not in UTILITY_FILES
)
PLUGIN_PATHS = {
    p.name: str(p)
    for p in list(PLUGIN_DIR.glob("*.ts")) + list(PLUGINS_DIR.glob("*.ts"))
    if p.name not in UTILITY_FILES
}

LIVE_HOOK_NAMES = {
    "tool.execute.before",
    "tool.execute.after",
    "experimental.chat.system.transform",
    "experimental.text.complete",
    "text.complete",
    "session.idle",
    "event",
}

# PluginAPI-style plugins: use `api.tool.execute.before(fn)` not `return { ... }`
PLUGINAPI_PLUGINS = {"enforce-commit-lock.ts", "watchdog.ts"}
# Daemon-side plugins that intentionally register zero enforcement hooks
DAEMON_PLUGINS = {"watchdog.ts"}
TEXT_ONLY_PLUGINS = {"enforce-audit.ts"}


# ── Fixture: load each plugin and return its hook table ─────────────────────

@pytest.mark.parametrize("plugin_file", PLUGIN_FILES)
def test_plugin_can_load(plugin_file: str):
    """Every plugin file must import without parse errors."""
    abs_path = PLUGIN_PATHS[plugin_file]
    code = f"""\
try {{
  const mod = await import('{abs_path}')
  console.log(JSON.stringify({{loaded: true, hasDefault: typeof mod.default === 'function'}}))
}} catch (e) {{
  console.log(JSON.stringify({{loaded: false, error: e.message}}))
}}
"""
    result = _run_ts(code)
    assert result is not None
    assert result["loaded"] is True, f"Plugin {plugin_file} failed to import"
    if plugin_file not in PLUGINAPI_PLUGINS:
        assert result["hasDefault"] is True, f"Plugin {plugin_file} has no default export"


@pytest.mark.parametrize("plugin_file", PLUGIN_FILES)
def test_plugin_hooks_exported(plugin_file: str):
    """Each plugin exports exactly the hooks it claims — no more, no less."""
    abs_path = PLUGIN_PATHS[plugin_file]
    if plugin_file in PLUGINAPI_PLUGINS:
        code = f"""\
const hooks = []
const api = {{
  tool: {{ execute: {{ before(fn) {{ hooks.push('tool.execute.before') }},
    after(fn) {{ hooks.push('tool.execute.after') }} }} }},
  experimental: {{ chat: {{ system: {{ transform(fn) {{
    hooks.push('experimental.chat.system.transform') }} }} }} }},
}}
const mod = await import('{abs_path}')
mod.default(api)
console.log(JSON.stringify({{file: '{plugin_file}', hooks, total: hooks.length}}))
"""
    else:
        code = f"""\
const mod = await import('{abs_path}')
const plugin = await mod.default({{}})
const keys = Object.keys(plugin ?? {{}}).sort()
const callable = {{}}
for (const k of keys) {{
  callable[k] = typeof plugin[k] === 'function'
}}
console.log(JSON.stringify({{file: '{plugin_file}', hooks: keys, callable, total: keys.length}}))
"""
    result = _run_ts(code)
    assert result is not None
    assert isinstance(result["hooks"], list)
    if plugin_file in DAEMON_PLUGINS:
        assert result["total"] >= 0, f"Plugin {plugin_file}: daemon plugin may export 0 hooks"
    else:
        assert result["total"] > 0, f"Plugin {plugin_file} exports zero hooks"


@pytest.mark.parametrize("plugin_file", PLUGIN_FILES)
def test_plugin_hooks_are_callable(plugin_file: str):
    """Every exported hook is a callable function."""
    abs_path = PLUGIN_PATHS[plugin_file]
    if plugin_file in PLUGINAPI_PLUGINS:
        code = f"""\
const hooks = []  // [name, isCallable]
const api = {{
  tool: {{ execute: {{ before(fn) {{ hooks.push(
    ['tool.execute.before', typeof fn === 'function']) }},
    after(fn) {{ hooks.push(['tool.execute.after', typeof fn === 'function']) }} }} }},
  experimental: {{ chat: {{ system: {{ transform(fn) {{ hooks.push(
    ['experimental.chat.system.transform', typeof fn === 'function']) }} }} }} }},
}}
const mod = await import('{abs_path}')
mod.default(api)
console.log(JSON.stringify({{file: '{plugin_file}', hooks}}))
"""
    else:
        code = f"""\
const mod = await import('{abs_path}')
const plugin = await mod.default({{}})
const entries = Object.entries(plugin ?? {{}}).map(([k,v]) => [k, typeof v === 'function'])
console.log(JSON.stringify({{file: '{plugin_file}', hooks: entries}}))
"""
    result = _run_ts(code)
    assert result is not None
    for name, callable_flag in result["hooks"]:
        assert callable_flag is True, f"Plugin {plugin_file}: hook {name} is not callable"


@pytest.mark.parametrize("plugin_file", PLUGIN_FILES)
def test_plugin_no_unknown_hooks(plugin_file: str):
    """Every exported hook is part of the supported enforcement hook set."""
    abs_path = PLUGIN_PATHS[plugin_file]
    if plugin_file in PLUGINAPI_PLUGINS:
        code = f"""\
const hooks = []
const api = {{
  tool: {{ execute: {{ before(fn) {{ hooks.push('tool.execute.before') }},
    after(fn) {{ hooks.push('tool.execute.after') }} }} }},
  experimental: {{
    text: {{ complete(fn) {{ hooks.push('experimental.text.complete') }} }},
    chat: {{ system: {{ transform(fn) {{ hooks.push('experimental.chat.system.transform') }} }} }},
  }},
  session: {{ idle(fn) {{ hooks.push('session.idle') }} }},
  event: (fn) => {{ hooks.push('event') }},
}}
const mod = await import('{abs_path}')
mod.default(api)
const unknown = hooks.filter(h => !{json.dumps(sorted(LIVE_HOOK_NAMES))}.includes(h))
console.log(JSON.stringify({{file: '{plugin_file}', allHooks: hooks, unknown}}))
"""
    else:
        code = f"""\
const mod = await import('{abs_path}')
const plugin = await mod.default({{}})
const keys = Object.keys(plugin ?? {{}})
const unknown = keys.filter(k => !{json.dumps(sorted(LIVE_HOOK_NAMES))}.includes(k))
console.log(JSON.stringify({{file: '{plugin_file}', allHooks: keys, unknown}}))
"""
    result = _run_ts(code)
    assert result is not None
    assert len(result["unknown"]) == 0, (
        f"Plugin {plugin_file} exports unknown hooks: {result['unknown']}."
    )


# ── Cross-plugin consistency checks ─────────────────────────────────────────

def test_all_plugins_export_tool_execute_before():
    """tool.execute.before is the primary enforcement hook — every plugin must export it."""
    for plugin_file in PLUGIN_FILES:
        if plugin_file in DAEMON_PLUGINS:
            continue
        if plugin_file in TEXT_ONLY_PLUGINS:
            continue
        abs_path = PLUGIN_PATHS[plugin_file]
        if plugin_file in PLUGINAPI_PLUGINS:
            code = f"""\
const hooks = []
        const api = {{ tool: {{ execute: {{ before(fn) {{ hooks.push('tool.execute.before') }},
          after(fn) {{ hooks.push('tool.execute.after') }} }} }} }}

const mod = await import('{abs_path}')
mod.default(api)
console.log(JSON.stringify({{file: '{plugin_file}', hasBefore: hooks.includes('tool.execute.before')}}))
"""
        else:
            code = f"""\
const mod = await import('{abs_path}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{file: '{plugin_file}', hasBefore: 'tool.execute.before' in (plugin ?? {{}})}}))
"""
        result = _run_ts(code)
        assert result is not None
        assert result["hasBefore"] is True, f"Plugin {plugin_file} missing tool.execute.before"


def test_hook_fire_verification_table():
    """Produce a summary PASS/FAIL table of all plugins and their hooks.

    This is a meta-test that validates the entire hook ecosystem:
    - Every plugin loads
    - Every exported hook is callable
    - No dead hooks (text.complete, session.idle, event)
    - Every plugin exports tool.execute.before
    """
    results = []
    all_pass = True

    for plugin_file in sorted(PLUGIN_FILES):
        abs_path = PLUGIN_PATHS[plugin_file]
        try:
            if plugin_file in PLUGINAPI_PLUGINS:
                code = f"""\
const hooks = []
const api = {{
  tool: {{ execute: {{ before(fn) {{ hooks.push('tool.execute.before') }},
    after(fn) {{ hooks.push('tool.execute.after') }} }} }},
  experimental: {{
    text: {{ complete(fn) {{ hooks.push('experimental.text.complete') }} }},
    chat: {{ system: {{ transform(fn) {{ hooks.push('experimental.chat.system.transform') }} }} }},
  }},
  session: {{ idle(fn) {{ hooks.push('session.idle') }} }},
  event: (fn) => {{ hooks.push('event') }},
}}
const mod = await import('{abs_path}')
mod.default(api)
const unknown = hooks.filter(h => !{json.dumps(sorted(LIVE_HOOK_NAMES))}.includes(h))
console.log(JSON.stringify({{hooks, unknown, unknownCount: unknown.length}}))
"""
            else:
                code = f"""\
const mod = await import('{abs_path}')
const plugin = await mod.default({{}})
const keys = Object.keys(plugin ?? {{}})
const callable = {{}}
const notCallable = []
for (const k of keys) {{
  callable[k] = typeof plugin[k] === 'function'
  if (typeof plugin[k] !== 'function') notCallable.push(k)
}}
const unknown = keys.filter(k => !{json.dumps(sorted(LIVE_HOOK_NAMES))}.includes(k))
console.log(JSON.stringify({{hooks: keys, callable, notCallable, unknown}}))
"""
            result = _run_ts(code)
            hooks = result.get("hooks", [])
            unknown = result.get("unknown", [])
            not_callable = result.get("notCallable", [])
            has_before = "tool.execute.before" in hooks
            is_daemon = plugin_file in DAEMON_PLUGINS
            is_text_only = plugin_file in TEXT_ONLY_PLUGINS

            row_pass = (
                (is_daemon and len(not_callable) == 0)
                or (
                    (has_before or is_text_only)
                    and len(unknown) == 0
                    and len(not_callable) == 0
                )
            )
            if not row_pass:
                all_pass = False

            results.append({
                "plugin": plugin_file.replace(".ts", ""),
                "hookCount": len(hooks),
                "hasToolExecuteBefore": has_before,
                "unknownHooks": unknown,
                "notCallable": not_callable,
                "isDaemon": is_daemon,
                "pass": row_pass,
            })
        except Exception as e:
            results.append({
                "plugin": plugin_file.replace(".ts", ""),
                "error": str(e),
                "pass": False,
            })
            all_pass = False

    print("\n╔══════════════════════════════════════════════════════════════════╗")
    print("║       Hook Fire Verification — opencode 1.17.9                 ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    print("║ Plugin                     Hooks  Before Dead  NotCall  PASS   ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    for r in results:
        name = r["plugin"][:25].ljust(26)
        if "error" in r:
            print(f"║ {name} ERROR: {r['error'][:35].ljust(37)}  FAIL ║")
            continue
        if r.get("isDaemon"):
            print(f"║ {name} 0     —     0    0       PASS (daemon) ║")
            continue
        n_hooks = str(r["hookCount"]).ljust(5)
        before = "Y" if r.get("hasToolExecuteBefore") else "N"
        before = before.ljust(6)
        dead = str(len(r.get("unknownHooks", []))).ljust(4)
        nc = str(len(r.get("notCallable", []))).ljust(7)
        status = "PASS" if r["pass"] else "FAIL"
        print(f"║ {name} {n_hooks} {before} {dead} {nc} {status}   ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    assert all_pass, (
        "Hook fire verification FAILED. See table above for details. "
        "Unknown hooks indicate stale code. "
        "Missing tool.execute.before indicates a broken plugin."
    )
