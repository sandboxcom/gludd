"""E2e test for enforce-commit-lock.ts: commit serialization via O_EXCL lock.

Invokes the actual TypeScript plugin via node --experimental-strip-types,
awaits its hook map, and calls the returned OpenCode hooks directly.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-commit-lock.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"commit_lock_e2e_{_ts_counter}_"))
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


# ─── No lock allows commit ──────────────────────────────────────────────────


def test_no_lock_allows_commit(tmp_path):
    """No lock file exists -> acquire succeeds, commit allowed."""
    lock_path = tmp_path / "commit.lock"
    assert not lock_path.exists()

    code = f"""\
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)
const result = await hooks['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG="test"'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(tmp_path))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"No lock should allow commit, got: {result}"
    )
    assert lock_path.exists(), "Lock file should exist after acquire"


# ─── Fresh lock denies second commit ─────────────────────────────────────────


def test_fresh_lock_denies_second_commit(tmp_path):
    """Pre-existing lock -> second commit is denied."""
    lock_path = tmp_path / "commit.lock"
    lock_path.write_text("12345")
    assert lock_path.exists()

    code = f"""\
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)
const result = await hooks['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG="test"'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(tmp_path))
    assert result is not None, "Expected deny result for fresh lock"
    assert result.get("permissionDecision") == "deny", (
        f"Fresh lock should deny commit, got: {result}"
    )
    assert "COMMIT-LOCK" in result.get("message", ""), (
        f"Deny message should include COMMIT-LOCK: {result.get('message')}"
    )


# ─── Stale lock breaks and allows ────────────────────────────────────────────


def test_stale_lock_breaks_and_allows(tmp_path):
    """Lock older than 5 min -> stale-break, lock re-acquired."""
    lock_path = tmp_path / "commit.lock"
    lock_path.write_text("99999")
    stale_mtime = time.time() - 600
    os.utime(lock_path, (stale_mtime, stale_mtime))
    assert lock_path.exists()

    code = f"""\
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)
const result = await hooks['tool.execute.before']({{tool: 'bash', command: 'make git-commit MSG="fix"'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(tmp_path))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Stale lock should break and allow, got: {result}"
    )


# ─── Non-commit bash command not blocked ─────────────────────────────────────


def test_non_commit_bash_allowed(tmp_path):
    """Non-commit make target -> lock is not even checked."""
    lock_path = tmp_path / "commit.lock"
    lock_path.write_text("block-me")
    assert lock_path.exists()

    code = f"""\
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)
const result = await hooks['tool.execute.before']({{tool: 'bash', command: 'make test-unit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(tmp_path))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Non-commit bash should be allowed even with lock, got: {result}"
    )


# ─── Non-bash tool not blocked ───────────────────────────────────────────────


def test_non_bash_tool_not_blocked(tmp_path):
    """Edit tool is not affected by commit lock."""
    lock_path = tmp_path / "commit.lock"
    lock_path.write_text("77777")
    assert lock_path.exists()

    code = f"""\
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)
const result = await hooks['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(tmp_path))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Non-bash tool should not be blocked, got: {result}"
    )


# ─── Subagent guard skips enforcement ────────────────────────────────────────


def test_subagent_skips_commit_lock(tmp_path):
    """OPENCODE_SUBAGENT=1 bypasses commit-lock entirely."""
    lock_path = tmp_path / "commit.lock"
    lock_path.write_text("subagent-test")
    assert lock_path.exists()

    code = f"""\
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)
const result = await hooks['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG="x"'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code,
        env_override={"OPENCODE_SUBAGENT": "1"},
        cwd=str(tmp_path),
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip commit lock, got: {result}"
    )


# ─── Env var disable ─────────────────────────────────────────────────────────


def test_env_var_disables_commit_lock(tmp_path):
    """GLUDD_COMMIT_LOCK_ENFORCE=0 disables the lock check."""
    lock_path = tmp_path / "commit.lock"
    lock_path.write_text("should-not-matter")
    assert lock_path.exists()

    code = f"""\
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)
const result = await hooks['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG="x"'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code,
        env_override={"GLUDD_COMMIT_LOCK_ENFORCE": "0"},
        cwd=str(tmp_path),
    )
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable should allow commit, got: {result}"
    )


# ─── After-commit releases lock ──────────────────────────────────────────────


def test_after_commit_releases_lock(tmp_path):
    """tool.execute.after releases the lock file after a commit command."""
    lock_path = tmp_path / "commit.lock"

    code = f"""\
import * as fs from 'node:fs'
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)

await hooks['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG="x"'}}, undefined)
const afterAcquire = fs.existsSync('{lock_path}')

await hooks['tool.execute.after']({{tool: 'bash', command: 'make ship-commit MSG="x"'}}, undefined)
const afterRelease = fs.existsSync('{lock_path}')

console.log(JSON.stringify({{acquired: afterAcquire, released: !afterRelease}}))
"""
    result = _run_plugin(code, cwd=str(tmp_path))
    assert result is not None, "Expected JSON result from after-commit test"
    assert result.get("acquired") is True, f"Lock should be acquired: {result}"
    assert result.get("released") is True, f"Lock should be released: {result}"


# ─── Lock file contains PID ──────────────────────────────────────────────────


def test_lock_file_contains_pid(tmp_path):
    """Acquired lock file should contain the calling process PID."""
    lock_path = tmp_path / "commit.lock"

    code = f"""\
import * as fs from 'node:fs'
process.env.GLUDD_COMMIT_LOCK_PATH = '{lock_path}'
const mod = await import('{PLUGIN_PATH}')
const hooks = await mod.default(undefined, undefined)

await hooks['tool.execute.before']({{tool: 'bash', command: 'make test-and-commit'}}, undefined)
const content = fs.readFileSync('{lock_path}', 'utf8').trim()
console.log(JSON.stringify({{pid: Number(content)}}))
"""
    result = _run_plugin(code, cwd=str(tmp_path))
    assert isinstance(result["pid"], int), f"Lock file should contain PID: {result}"
    assert result["pid"] > 0, f"PID should be positive: {result['pid']}"
