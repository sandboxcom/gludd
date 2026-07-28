"""E2e test for enforce-delegate.ts: mainthread streak and force-delegate enforcement.

Invokes the actual TypeScript plugin via node --experimental-strip-types
in isolated state files, verifying deny/allow/disable/subagent-guard cycles.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"delegate_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd or str(ROOT), env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
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
            tmp.unlink()


def _write_streak_file(path: str, count: int) -> None:
    Path(path).write_text(json.dumps({"count": count, "ts": 1_700_000_000_000}))


# ─── Streak below threshold allows mutation ─────────────────────────────────


def test_streak_below_threshold_allows_edit(tmp_path):
    """streak=0, threshold=2 -> edit allowed."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 0)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Streak below threshold should allow, got: {result}"
    )


# ─── Streak at threshold denies mutation ────────────────────────────────────


def test_streak_at_threshold_denies_edit(tmp_path):
    """streak=2, threshold=2, live=0 -> edit denied."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 2)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_LIVE_AGENTS_COUNT": "0",
    })
    assert result is not None, "Expected deny for streak at threshold"
    assert result.get("permissionDecision") == "deny", (
        f"Expected deny, got: {result}"
    )
    assert "STREAK BLOCK" in result.get("message", "")


def test_streak_at_threshold_denies_write(tmp_path):
    """streak=2, threshold=2 -> write denied."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 2)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_LIVE_AGENTS_COUNT": "0",
    })
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"Expected deny for write, got: {result}"
    )


def test_streak_at_threshold_denies_mutating_bash(tmp_path):
    """streak=2 -> inline mutating bash (make format-python) denied."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 2)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'bash'}}, {{args: {{command: 'make format-python'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_LIVE_AGENTS_COUNT": "0",
    })
    assert result is not None and result.get("permissionDecision") == "deny"


def test_streak_at_threshold_allows_git_shipping(tmp_path):
    """Terminal shipping remains available so completed work can be committed."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 2)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'bash'}}, {{args: {{command: 'make git-commit MSG=test'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_LIVE_AGENTS_COUNT": "0",
    })
    assert result is not None and result.get("allowed") is True


# ─── Read tools exempt from mainthread streak ────────────────────────────────


def test_read_tools_exempt_from_streak(tmp_path):
    """read/grep/glob tools not blocked by mainthread streak."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 5)

    for tool in ["read", "grep", "glob"]:
        code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: '{tool}'}}, {{args: {{}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
        result = _run_plugin(code, env_override={
            "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
            "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
            "GLUDD_MAINTHREAD_THRESHOLD": "2",
            "GLUDD_LIVE_AGENTS_COUNT": "0",
        })
        assert result is None or result.get("permissionDecision") != "deny", (
            f"Read tool '{tool}' should be exempt from streak, got: {result}"
        )


# ─── Dispatch tools reset streak ─────────────────────────────────────────────


def test_dispatch_resets_streak(tmp_path):
    """task/agent/workflow dispatch always allowed (resets streak)."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 5)

    for tool in ["task", "agent", "workflow"]:
        code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: '{tool}'}}, {{args: {{prompt: 'do work'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
        result = _run_plugin(code, env_override={
            "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
            "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
            "GLUDD_MAINTHREAD_THRESHOLD": "2",
            "GLUDD_LIVE_AGENTS_COUNT": "0",
        })
        assert result is None or result.get("permissionDecision") != "deny", (
            f"Dispatch tool '{tool}' should be allowed, got: {result}"
        )


# ─── Env var disable ─────────────────────────────────────────────────────────


def test_env_var_disables_mainthread_streak(tmp_path):
    """GLUDD_MAINTHREAD_STREAK_ENFORCE=0 allows even at streak threshold."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 5)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_MAINTHREAD_STREAK_ENFORCE": "0",
        "GLUDD_LIVE_AGENTS_COUNT": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable should allow, got: {result}"
    )


# ─── Subagent guard ──────────────────────────────────────────────────────────


def test_subagent_skips_delegate(tmp_path):
    """OPENCODE_SUBAGENT=1 skips all enforcement entirely."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 5)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "OPENCODE_SUBAGENT": "1",
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_LIVE_AGENTS_COUNT": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip enforcement, got: {result}"
    )


# ─── Corrupt state fail-open ─────────────────────────────────────────────────


def test_corrupt_streak_file_fails_open(tmp_path):
    """Corrupt state file -> fail-open (allows edit)."""
    streak_file = str(tmp_path / "streak.json")
    Path(streak_file).write_text("NOT JSON {{{")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_LIVE_AGENTS_COUNT": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Corrupt state should fail-open, got: {result}"
    )


# ─── Force-delegate: opt-in grind guard ──────────────────────────────────────


def test_force_delegate_denies_when_enabled_and_over_grace(tmp_path):
    """GLUDD_FORCE_DELEGATE=1 + consecutive > GRACE + live=0 -> denies edit."""
    force_state = str(tmp_path / "force.json")
    Path(force_state).write_text(
        json.dumps({"consecutive_targeted": 4, "consecutive_denied": 0})
    )

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_FORCE_DELEGATE": "1",
        "GLUDD_FORCE_DELEGATE_STATE": force_state,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_LIVE_AGENTS_COUNT": "0",
        "GLUDD_FORCE_DELEGATE_GRACE": "3",
    })
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"Force-delegate should deny when over grace, got: {result}"
    )
    assert "FORCE-DELEGATE" in result.get("message", "")


# ─── High live-agent count bypasses streak ───────────────────────────────────


def test_high_live_agent_count_bypasses_streak(tmp_path):
    """live agents >= TARGET bypasses mainthread streak block."""
    streak_file = str(tmp_path / "streak.json")
    _write_streak_file(streak_file, 5)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{filePath: '/tmp/x'}}}})
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
    result = _run_plugin(code, env_override={
        "GLUDD_MAINTHREAD_STREAK_FILE": streak_file,
        "GLUDD_DISENGAGE_PATH": str(tmp_path / "disengage.json"),
        "GLUDD_MAINTHREAD_THRESHOLD": "2",
        "GLUDD_LIVE_AGENTS_COUNT": "999",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"High live count should bypass streak, got: {result}"
    )
