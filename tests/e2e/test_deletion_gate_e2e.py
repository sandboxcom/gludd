"""E2e test for enforce-deletion-gate.ts: deletion threshold gating.

Invokes the actual TypeScript plugin via node --experimental-strip-types
in isolated temp dirs, verifying the full threshold/block/bypass cycle.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-deletion-gate.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> dict | None:
    """Write TS to temp file, run via node, return last JSON line of stdout."""
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"delgate_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env.pop("OPENCODE_SUBAGENT", None)
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
        try:
            tmp.unlink()
        except OSError:
            pass


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@e2e.test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "E2E Test"], cwd=path, capture_output=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True)


def _setup_test_file(path: Path, lines: int = 10) -> Path:
    f = path / "test.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, lines + 1)) + "\n")
    return f


# ─── Under threshold → allowed ──────────────────────────────────────────────


def test_edit_under_threshold_allowed(tmp_path):
    repo = tmp_path / "under"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt', old_string: 'line 1\\nline 2', new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Small edit should be allowed, got: {result}"
    )


def test_write_under_threshold_allowed(tmp_path):
    repo = tmp_path / "write-under"
    repo.mkdir()
    _init_git_repo(repo)
    f = _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'write', args: {{file_path: '{f}', content: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5\\n'}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Small write should be allowed, got: {result}"
    )


# ─── Over threshold without DELETION_REASON → blocked ───────────────────────


def test_edit_over_threshold_no_reason_blocked(tmp_path):
    repo = tmp_path / "over-edit"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5\\nline 6\\nline 7',
    new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is not None, "Expected deny result for large deletion"
    assert result.get("permissionDecision") == "deny", (
        f"Expected deny, got: {result}"
    )
    assert "exceeds threshold" in result.get("message", "")


def test_write_over_threshold_no_reason_blocked(tmp_path):
    repo = tmp_path / "over-write"
    repo.mkdir()
    _init_git_repo(repo)
    f = _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'write', args: {{file_path: '{f}', content: 'only one line'}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is not None, "Expected deny result for large write"
    assert result.get("permissionDecision") == "deny"
    assert "exceeds threshold" in result.get("message", "")


# ─── Over threshold WITH DELETION_REASON → allowed + audit log ──────────────


def test_edit_over_threshold_with_reason_allowed(tmp_path):
    repo = tmp_path / "with-reason"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5\\nline 6\\nline 7',
    new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code, env_override={"DELETION_REASON": "refactoring dead code"}, cwd=str(repo)
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Deletion with reason should be allowed, got: {result}"
    )
    audit_log = repo / ".deletion-audit.log"
    assert audit_log.exists(), "Expected .deletion-audit.log to be created"
    log_content = audit_log.read_text()
    assert "test.txt" in log_content
    assert "refactoring dead code" in log_content


def test_write_over_threshold_with_reason_allowed(tmp_path):
    repo = tmp_path / "write-reason"
    repo.mkdir()
    _init_git_repo(repo)
    f = _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'write', args: {{file_path: '{f}', content: 'one line'}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code, env_override={"DELETION_REASON": "rewrite module"}, cwd=str(repo)
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Write with reason should be allowed, got: {result}"
    )


# ─── Subagent guard ─────────────────────────────────────────────────────────


def test_subagent_skips_deletion_gate(tmp_path):
    repo = tmp_path / "subagent"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5\\nline 6\\nline 7',
    new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code, env_override={"OPENCODE_SUBAGENT": "1"}, cwd=str(repo)
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip gate, got: {result}"
    )


# ─── Threshold = 0 disables gate ────────────────────────────────────────────


def test_threshold_zero_disables_gate(tmp_path):
    repo = tmp_path / "threshold-zero"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5\\nline 6\\nline 7',
    new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code, env_override={"GLUDD_DELETION_GATE_THRESHOLD": "0"}, cwd=str(repo)
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Threshold 0 should disable gate, got: {result}"
    )


# ─── Non-edit/write tools → allowed ─────────────────────────────────────────


def test_read_tool_never_blocked(tmp_path):
    repo = tmp_path / "read-tool"
    repo.mkdir()
    _init_git_repo(repo)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'read', args: {{}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Read tool should always be allowed, got: {result}"
    )


def test_bash_tool_never_blocked(tmp_path):
    repo = tmp_path / "bash-tool"
    repo.mkdir()
    _init_git_repo(repo)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Bash tool should always be allowed, got: {result}"
    )


def test_task_tool_never_blocked(tmp_path):
    repo = tmp_path / "task-tool"
    repo.mkdir()
    _init_git_repo(repo)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Task tool should always be allowed, got: {result}"
    )


# ─── Custom threshold ───────────────────────────────────────────────────────


def test_custom_threshold_respected(tmp_path):
    repo = tmp_path / "custom-thresh"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code_under = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2', new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code_under, env_override={"GLUDD_DELETION_GATE_THRESHOLD": "3"}, cwd=str(repo)
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"2 lines under threshold 3 should be allowed, got: {result}"
    )

    code_over = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5',
    new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result2 = _run_plugin(
        code_over, env_override={"GLUDD_DELETION_GATE_THRESHOLD": "3"}, cwd=str(repo)
    )
    assert result2 is not None, "Expected deny result over threshold 3"
    assert result2.get("permissionDecision") == "deny"


# ─── Threshold negative → gate disabled ─────────────────────────────────────


def test_negative_threshold_disables_gate(tmp_path):
    repo = tmp_path / "neg-thresh"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5\\nline 6\\nline 7',
    new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code, env_override={"GLUDD_DELETION_GATE_THRESHOLD": "-1"}, cwd=str(repo)
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Negative threshold should disable gate, got: {result}"
    )


# ─── Empty DELETION_REASON still blocks ─────────────────────────────────────


def test_empty_deletion_reason_still_blocks(tmp_path):
    repo = tmp_path / "empty-reason"
    repo.mkdir()
    _init_git_repo(repo)
    _setup_test_file(repo, 10)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{file_path: 'test.txt',
    old_string: 'line 1\\nline 2\\nline 3\\nline 4\\nline 5\\nline 6\\nline 7',
    new_string: ''}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code, env_override={"DELETION_REASON": ""}, cwd=str(repo)
    )
    assert result is not None, "Expected deny for empty reason"
    assert result.get("permissionDecision") == "deny"


# ─── Fail-open: missing file in write → allowed ─────────────────────────────


def test_write_to_nonexistent_file_fails_open(tmp_path):
    repo = tmp_path / "no-file"
    repo.mkdir()
    _init_git_repo(repo)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'write', args: {{file_path: 'nonexistent.txt', content: 'new content'}}}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Write to missing file should fail-open, got: {result}"
    )


# ─── No args → allowed ──────────────────────────────────────────────────────


def test_missing_args_fails_open(tmp_path):
    repo = tmp_path / "no-args"
    repo.mkdir()
    _init_git_repo(repo)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit'}}, undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Missing args should fail-open, got: {result}"
    )
