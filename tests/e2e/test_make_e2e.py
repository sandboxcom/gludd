"""E2e test for enforce-make.ts: bash make-only + metachar enforcement.

Invokes the actual TypeScript plugin via node --experimental-strip-types,
verifying the full deny/allow/disable/subagent cycle for bash commands.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-make.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"make_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env["GLUDD_GATE_PYTEST_RUNNING"] = "0"
        env["GLUDD_GATE_BASETEMP"] = "/tmp/gludd-gate-basetemp-non-existent"
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


def _bash_assert(code_template: str, env_override: dict | None = None) -> dict | None:
    """Run the enforce-make bash handler and return the parsed JSON result.
    Wraps the call in try/catch so thrown errors become JSON deny results.
    """
    code = code_template.replace("__PLUGIN_PATH__", str(PLUGIN_PATH))
    return _run_plugin(code, env_override=env_override)


# ─── Helper TS code templates ────────────────────────────────────────────────

_BASH_TRY_BLOCK = """\
const mod = await import('__PLUGIN_PATH__')
const plugin = await mod.default({})
try {
  const result = await plugin['tool.execute.before'](BASH_INPUT, undefined)
  console.log(JSON.stringify(result ?? {allowed: true}))
} catch (e) {
  console.log(JSON.stringify({permissionDecision: 'deny', message: e.message || String(e)}))
}
"""


def _bash_code(command: str) -> str:
    return _BASH_TRY_BLOCK.replace(
        "BASH_INPUT", json.dumps({"tool": "bash", "args": {"command": command}})
    )


# ─── Commands that should be blocked ─────────────────────────────────────────


def test_non_make_command_blocked():
    """Bare 'ls' (non-make) is blocked."""
    result = _bash_assert(_bash_code("ls"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "Command does not start with 'make'" in result.get("message", "")


def test_unknown_make_target_blocked():
    """A make prefix is insufficient when the target does not exist."""
    result = _bash_assert(_bash_code("make definitely-not-a-gludd-target"))
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "unknown Make target" in result.get("message", "")


def test_pipe_metachar_blocked():
    """Pipe '|' in command is blocked."""
    result = _bash_assert(_bash_code("echo foo | bar"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Shell metacharacter" in result.get("message", "")


def test_semicolon_metachar_blocked():
    """Semicolon ';' in command is blocked."""
    result = _bash_assert(_bash_code("make test; make lint"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Shell metacharacter" in result.get("message", "")


def test_and_and_metachar_blocked():
    """'&&' in command is blocked."""
    result = _bash_assert(_bash_code("make test && make lint"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Shell metacharacter" in result.get("message", "")


def test_dollar_subshell_blocked():
    """'$()' subshell expansion is blocked."""
    result = _bash_assert(_bash_code("echo $(date)"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Shell metacharacter" in result.get("message", "")


def test_redirect_metachar_blocked():
    """'2>&1' redirect (contains '&' metachar) is blocked."""
    result = _bash_assert(_bash_code("make lint 2>&1"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Shell metacharacter" in result.get("message", "")


def test_python_command_blocked():
    """'python3 script.py' is blocked as non-make."""
    result = _bash_assert(_bash_code("python3 -m pytest"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"


# ─── Commands that should be allowed ─────────────────────────────────────────


def test_make_lint_allowed():
    """Plain 'make lint' is allowed."""
    result = _bash_assert(_bash_code("make lint"))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"make lint should be allowed, got: {result}"
    )


def test_make_test_specific_allowed():
    """'make test TESTFILE=path' (targeted) is allowed."""
    result = _bash_assert(_bash_code("make test TESTFILE='tests/e2e/test_make_e2e.py'"))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"make test with TESTFILE should be allowed, got: {result}"
    )


def test_make_testcount_allowed():
    """'make test-count' is allowed (short-running)."""
    result = _bash_assert(_bash_code("make test-count"))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"make test-count should be allowed, got: {result}"
    )


def test_make_collect_check_allowed():
    """'make collect-check' is allowed."""
    result = _bash_assert(_bash_code("make collect-check"))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"make collect-check should be allowed, got: {result}"
    )


# ─── Long-running foreground commands blocked ────────────────────────────────


def test_make_gate_blocked():
    """'make gate' is blocked as long-running foreground."""
    result = _bash_assert(_bash_code("make gate"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Long-running foreground" in result.get("message", "")


def test_make_test_unit_blocked():
    """'make test-unit' is blocked as long-running foreground."""
    result = _bash_assert(_bash_code("make test-unit"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Long-running foreground" in result.get("message", "")


def test_make_bare_test_blocked():
    """Bare 'make test' (no TESTFILE) is blocked."""
    result = _bash_assert(_bash_code("make test"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"
    assert "Long-running foreground" in result.get("message", "")


def test_make_qa_blocked():
    """'make qa' is blocked as long-running."""
    result = _bash_assert(_bash_code("make qa"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"


def test_make_validate_blocked():
    """'make validate' is blocked as long-running."""
    result = _bash_assert(_bash_code("make validate"))
    assert result is not None
    assert result.get("permissionDecision") == "deny"


# ─── Env var disable (GLUDD_MAKE_ENFORCE=0) ──────────────────────────────────


def test_env_disable_allows_non_make():
    """GLUDD_MAKE_ENFORCE=0 allows non-make commands."""
    result = _bash_assert(
        _bash_code("ls"), env_override={"GLUDD_MAKE_ENFORCE": "0"}
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable should allow non-make, got: {result}"
    )


def test_env_disable_allows_metachar():
    """GLUDD_MAKE_ENFORCE=0 allows metachar commands (use non-gate target to avoid concurrency guard)."""
    result = _bash_assert(
        _bash_code("make lint && make lint"), env_override={"GLUDD_MAKE_ENFORCE": "0"}
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable should allow metachars, got: {result}"
    )


def test_env_disable_allows_bare_test():
    """GLUDD_MAKE_ENFORCE=0 allows non-make commands (long-running guard is separate)."""
    result = _bash_assert(
        _bash_code("python3 script.py"), env_override={"GLUDD_MAKE_ENFORCE": "0"}
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable should allow non-make, got: {result}"
    )


# ─── Subagent guard (OPENCODE_SUBAGENT=1) ────────────────────────────────────


def test_subagent_allows_non_make():
    """OPENCODE_SUBAGENT=1 skips all checks, even non-make."""
    result = _bash_assert(
        _bash_code("ls"), env_override={"OPENCODE_SUBAGENT": "1"}
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip check, got: {result}"
    )


def test_subagent_allows_metachar():
    """OPENCODE_SUBAGENT=1 skips metachar checks."""
    result = _bash_assert(
        _bash_code("echo foo | bar"), env_override={"OPENCODE_SUBAGENT": "1"}
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip metachar check, got: {result}"
    )


def test_subagent_allows_bare_gate():
    """OPENCODE_SUBAGENT=1 allows 'make gate' (skips long-running check)."""
    result = _bash_assert(
        _bash_code("make gate"), env_override={"OPENCODE_SUBAGENT": "1"}
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should allow make gate, got: {result}"
    )


# ─── Non-bash tools not blocked ──────────────────────────────────────────────


def test_read_tool_not_blocked():
    """Non-bash tools (read) are not blocked by make enforcement."""
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"read tool should not be blocked, got: {result}"
    )


def test_edit_tool_not_blocked():
    """Non-bash tools (edit) are not blocked."""
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"edit tool should not be blocked, got: {result}"
    )


# ─── Make with VAR=val args ──────────────────────────────────────────────────


def test_make_with_var_assignment_allowed():
    """'make lint FILES=...' with VAR=val is allowed."""
    result = _bash_assert(_bash_code("make test-specific TESTFILE='tests/unit/test_dummy.py' NO_XDIST=1"))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"make with VAR=val should be allowed, got: {result}"
    )


# ─── Deny message content ────────────────────────────────────────────────────


def test_non_make_deny_message_complete():
    """Deny message for non-make includes guidance."""
    result = _bash_assert(_bash_code("ls"))
    msg = result.get("message", "")
    assert "BLOCKED: Direct bash commands" in msg
    assert "What to do instead" in msg
    assert "Makefile target" in msg


def test_metachar_deny_message_complete():
    """Deny message for metachar includes guidance."""
    result = _bash_assert(_bash_code("echo foo | bar"))
    msg = result.get("message", "")
    assert "BLOCKED: Direct bash commands" in msg
    assert "Shell metacharacter" in msg
    assert "Create a Makefile target" in msg
