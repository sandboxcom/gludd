"""E2E test for enforce-no-wait.ts: main-thread wait blocking + CI-poll dispatch blocking.

Invokes the actual TypeScript plugin via node --experimental-strip-types
verifying deny/allow/env-disable/subagent/fail-open behaviors.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-wait.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"no_wait_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
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


def _bash_invoke(cmd: str) -> str:
    return f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const hook = plugin['tool.execute.before']
try {{
  const result = await hook({{tool: 'bash', command: `{cmd}`}}, undefined)
  console.log(JSON.stringify(result ?? {{allowed: true}}))
}} catch (error) {{
  console.log(JSON.stringify({{
    permissionDecision: error?.permissionDecision ?? 'error',
    message: error?.message ?? String(error),
  }}))
}}
"""


def _bash_output_args_invoke(cmd: str) -> str:
    """Invoke the hook with OpenCode 1.18's real two-argument command shape."""
    return f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const hook = plugin['tool.execute.before']
try {{
  const result = await hook({{tool: 'bash'}}, {{args: {{command: `{cmd}`}}}})
  console.log(JSON.stringify(result ?? {{allowed: true}}))
}} catch (error) {{
  console.log(JSON.stringify({{
    permissionDecision: error?.permissionDecision ?? 'error',
    message: error?.message ?? String(error),
  }}))
}}
"""


def _dispatch_invoke(tool: str, prompt: str = "", description: str = "") -> str:
    return f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const hook = plugin['tool.execute.before']
try {{
  const result = await hook({{tool: '{tool}', prompt: `{prompt}`, description: `{description}`}}, undefined)
  console.log(JSON.stringify(result ?? {{allowed: true}}))
}} catch (error) {{
  console.log(JSON.stringify({{
    permissionDecision: error?.permissionDecision ?? 'error',
    message: error?.message ?? String(error),
  }}))
}}
"""


# ─── Bash wait-pattern tests ─────────────────────────────────────────────


def test_sleep_and_make_blocked():
    """sleep N && make gate-status-check is denied."""
    result = _run_plugin(_bash_invoke("sleep 30 && make gate-status-check"))
    assert result is not None, "Expected deny for sleep+N && make pattern"
    assert result.get("permissionDecision") == "deny", f"Got: {result}"
    assert "Main-thread wait forbidden" in result.get("message", "")


def test_output_args_command_shape_blocked():
    """OpenCode 1.18 supplies live TUI bash arguments through output.args."""
    result = _run_plugin(_bash_output_args_invoke("make gate-tail"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_naked_sleep_blocked():
    """sleep N (naked) is denied."""
    result = _run_plugin(_bash_invoke("sleep 60"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_gate_tail_blocked():
    """make gate-tail is denied (follows forever)."""
    result = _run_plugin(_bash_invoke("make gate-tail"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_gate_bg_check_blocked():
    """make gate-bg-check is denied."""
    result = _run_plugin(_bash_invoke("make gate-bg-check"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_gate_status_check_blocked():
    """make gate-status-check on main thread is denied."""
    result = _run_plugin(_bash_invoke("make gate-status-check"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


# ─── Non-matching bash commands allowed ──────────────────────────────────


def test_normal_make_allowed():
    """Plain make test-unit is NOT blocked."""
    result = _run_plugin(_bash_invoke("make test-unit"))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Normal make must be allowed, got: {result}"
    )


def test_empty_bash_allowed():
    """Empty bash command is not blocked."""
    result = _run_plugin(_bash_invoke(""))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Empty command must be allowed, got: {result}"
    )


# ─── CI-poll dispatch-prompt blocking ───────────────────────────────────


def test_ci_poll_task_dispatch_blocked():
    """Task dispatch with 'poll CI until' in prompt is denied."""
    result = _run_plugin(_dispatch_invoke(
        "task", prompt="poll CI until it turns green and report back"
    ))
    assert result is not None, "Expected deny for CI-poll dispatch"
    assert result.get("permissionDecision") == "deny", f"Got: {result}"
    assert "CI-poll dispatch forbidden" in result.get("message", "")


def test_ci_wait_for_green_blocked():
    """Dispatch with 'wait for CI to turn green' is denied."""
    result = _run_plugin(_dispatch_invoke(
        "task", prompt="please wait for CI to go green then push"
    ))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_ci_loop_verdict_blocked():
    """Dispatch with 'loop on make ci-verdict' is denied."""
    result = _run_plugin(_dispatch_invoke(
        "agent", prompt="loop on make ci-verdict until good and exit"
    ))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_ci_every_n_seconds_blocked():
    """Dispatch with 'every 30 seconds ... up to' is denied."""
    result = _run_plugin(_dispatch_invoke(
        "workflow", prompt="run ci-verdict every 30 seconds up to 60 iterations"
    ))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_ci_until_conclusion_success_blocked():
    """Dispatch with 'until conclusion is success' is denied."""
    result = _run_plugin(_dispatch_invoke(
        "task", prompt="check status until conclusion is success"
    ))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


def test_ci_poll_in_description_blocked():
    """CI-poll phrase in description field is also blocked."""
    result = _run_plugin(_dispatch_invoke(
        "task", description="poll CI until green"
    ))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"


# ─── Benign dispatch passes through ─────────────────────────────────────


def test_normal_task_dispatch_allowed():
    """Task dispatch without CI-poll language is allowed."""
    result = _run_plugin(_dispatch_invoke(
        "task", prompt="write a test for the health endpoint"
    ))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Normal dispatch must be allowed, got: {result}"
    )


def test_normal_agent_dispatch_allowed():
    """Agent dispatch with normal prompt is allowed."""
    result = _run_plugin(_dispatch_invoke(
        "agent", prompt="read the README and summarize"
    ))
    assert result is None or result.get("permissionDecision") != "deny"


def test_empty_dispatch_allowed():
    """Dispatch with empty prompt/description is allowed."""
    result = _run_plugin(_dispatch_invoke("task"))
    assert result is None or result.get("permissionDecision") != "deny"


# ─── Non-dispatch tools not blocked ─────────────────────────────────────


def test_read_tool_not_blocked():
    """Read tool is not blocked by CI-poll patterns."""
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny"


def test_edit_tool_not_blocked():
    """Edit tool is not blocked."""
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny"


def test_glob_tool_not_blocked():
    """Glob tool is not blocked."""
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'glob'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny"


# ─── Env var disable ─────────────────────────────────────────────────────


def test_env_disable_skips_bash_wait():
    """GLUDD_NO_WAIT_ENFORCE=0 skips bash wait blocking."""
    result = _run_plugin(
        _bash_invoke("sleep 30 && make gate-status-check"),
        env_override={"GLUDD_NO_WAIT_ENFORCE": "0"},
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable must allow sleep+make, got: {result}"
    )


def test_env_disable_skips_ci_poll():
    """GLUDD_NO_WAIT_ENFORCE=0 skips CI-poll dispatch blocking."""
    result = _run_plugin(
        _dispatch_invoke("task", prompt="poll CI until terminal"),
        env_override={"GLUDD_NO_WAIT_ENFORCE": "0"},
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable must allow CI-poll dispatch, got: {result}"
    )


# ─── Subagent guard ─────────────────────────────────────────────────────


def test_subagent_skips_bash_wait():
    """OPENCODE_SUBAGENT=1 skips bash wait check."""
    result = _run_plugin(
        _bash_invoke("sleep 60"),
        env_override={"OPENCODE_SUBAGENT": "1"},
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent must skip, got: {result}"
    )


def test_subagent_skips_ci_poll():
    """OPENCODE_SUBAGENT=1 skips CI-poll dispatch check."""
    result = _run_plugin(
        _dispatch_invoke("task", prompt="poll CI until terminal", description="wait for CI"),
        env_override={"OPENCODE_SUBAGENT": "1"},
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent must skip CI-poll, got: {result}"
    )


# ─── Deny message content ───────────────────────────────────────────────


def test_bash_deny_message_references_agents_md():
    """Bash deny message references AGENTS.md."""
    result = _run_plugin(_bash_invoke("make gate-tail"))
    assert "AGENTS.md" in result.get("message", "")


def test_ci_poll_deny_message_references_cooldown():
    """CI-poll deny message references cooldown and release-cut."""
    result = _run_plugin(_dispatch_invoke(
        "task", prompt="poll CI until green"
    ))
    msg = result.get("message", "")
    assert "CI-poll dispatch forbidden" in msg
    assert "ci-verdict-safe" in msg
    assert "release-cut" in msg


# ─── Fail-open ──────────────────────────────────────────────────────────


def test_corrupt_input_fails_open():
    """Missing command field on bash tool fails open (no crash)."""
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Missing command must fail-open, got: {result}"
    )


def test_null_params_fails_open():
    """Null/undefined params on dispatch fail open."""
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task', prompt: null, description: null}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Null params must fail-open, got: {result}"
    )


# ─── Boundary checks ────────────────────────────────────────────────────


def test_sleep_and_make_different_cmd_blocked():
    """sleep N && make gate-bg-check is also covered."""
    result = _run_plugin(_bash_invoke("sleep 10 && make gate-bg-check"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Got: {result}"
