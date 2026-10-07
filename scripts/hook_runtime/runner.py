"""Node subprocess runner and TypeScript factory helpers."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from typing import Any

from .fixtures import PLUGIN_DIR, ROOT, _runtime_state_root

_tmp_counter = 0

def _run_ts(
    ts_code: str,
    env_override: dict[str, str] | None = None,
    timeout: int = 15,
    cwd: str | os.PathLike[str] | None = None,
) -> Any:
    """Write TS code to temp file, run with node --experimental-strip-types, return parsed JSON.

    Returns None if stdout is empty (hook returned undefined/void).

    Args:
        cwd: Working directory for the node subprocess. Defaults to ROOT.
            Tests that exercise filesystem-backed pending-work checks should
            pass an isolated project root here so repo state is hermetic.
    """
    global _tmp_counter
    _tmp_counter += 1
    state_root = _runtime_state_root()
    false_done_path = str(state_root / f"gludd-false-done-blocks-test-{os.getpid()}-{_tmp_counter}.json")
    dispatch_outcomes_path = str(
        state_root / f"gludd-dispatch-outcomes-test-{os.getpid()}-{_tmp_counter}.json"
    )
    streak_path = str(state_root / f"gludd-tool-streak-test-{os.getpid()}-{_tmp_counter}.json")
    hot_prefix = state_root / f"gludd-hot-{os.getpid()}-{_tmp_counter}-"
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".ts",
        dir=str(state_root),
        prefix=f"hook_test_{_tmp_counter}_",
        delete=False,
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = "0"  # parent process; ignore stale PID markers
        # Hermetic disengage path: the live watchdog (check_plugin_hashes.py)
        # can rewrite /tmp/gludd-watchdog-disengage.json mid-test, flipping
        # isDisengaged() to true and turning expected denies into allows.
        # Point plugins at a per-process nonexistent path unless a test
        # explicitly overrides it.
        env["GLUDD_DISENGAGE_PATH"] = str(state_root / f"gludd-disengage-hermetic-{os.getpid()}.json")
        env["GLUDD_FALSE_DONE_BLOCKS_FILE"] = false_done_path
        env["GLUDD_DISPATCH_OUTCOMES_FILE"] = dispatch_outcomes_path
        env["GLUDD_STREAK_FILE"] = streak_path
        env["GLUDD_HOT_MODULE_PREFIX"] = str(hot_prefix)
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd if cwd is not None else ROOT),
            env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        if not stdout:
            return None
        lines = stdout.split("\n")
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                return None if parsed is None else parsed
            except json.JSONDecodeError:
                continue
        return None
    finally:
        for path in (tmp, false_done_path, dispatch_outcomes_path, streak_path):
            with contextlib.suppress(OSError):
                os.unlink(path)
        for artifact_path in state_root.glob(f"{hot_prefix.name}*"):
            with contextlib.suppress(OSError):
                artifact_path.unlink()

def _factory_plugin_code(plugin_rel_path: str, hook_name: str, call_code: str) -> str:
    """Generate TS code that loads an async-factory plugin and calls a hook.

    For plugins that use: export default (async ({}) => { return { hook: fn } })
    """
    abs_path = str(PLUGIN_DIR / plugin_rel_path)
    return f"""\
const mod = await import('{abs_path}')
const plugin = await mod.default({{}})
const result = await {call_code}
console.log(JSON.stringify(result ?? null))
"""


def _pluginapi_code(plugin_rel_path: str, call_code: str) -> str:
    """Generate TS code for PluginAPI-style plugins.

    For plugins that use: export default function plugin(api: PluginAPI): void { api.tool.execute.before(fn) }
    """
    abs_path = str(PLUGIN_DIR / plugin_rel_path)
    return f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{abs_path}')
mod.default(api)
const result = {call_code}
console.log(JSON.stringify(result ?? null))
"""
