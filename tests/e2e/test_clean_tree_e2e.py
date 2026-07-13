"""E2e test for enforce-clean-tree.ts: dirty-tree dispatch blocking.

Invokes the actual TypeScript plugin via node --experimental-strip-types
in isolated temp git repos, verifying the full deny/allow/enable/cleanup cycle.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-clean-tree.ts"

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
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"clean_tree_e2e_{_ts_counter}_"))
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
        try:
            tmp.unlink()
        except OSError:
            pass


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@e2e.test"], cwd=path, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "E2E Test"], cwd=path, capture_output=True
    )
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=path, capture_output=True
    )


# ─── Dirty tree blocks dispatch ────────────────────────────────────────────


def test_dirty_tree_blocks_task_dispatch(tmp_path):
    """Create dirty file -> hook blocks 'task' dispatch."""
    repo = tmp_path / "dirty-task"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is not None, "Expected deny result for dirty tree"
    assert result.get("permissionDecision") == "deny", (
        f"Expected deny, got: {result}"
    )
    assert "DIRTY TREE" in result.get("message", "")


def test_dirty_tree_blocks_agent_dispatch(tmp_path):
    """Create dirty file -> hook blocks 'agent' dispatch."""
    repo = tmp_path / "dirty-agent"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is not None, "Expected deny result for dirty tree"
    assert result.get("permissionDecision") == "deny"
    assert "DIRTY TREE" in result.get("message", "")


def test_dirty_tree_blocks_workflow_dispatch(tmp_path):
    """Create dirty file -> hook blocks 'workflow' dispatch."""
    repo = tmp_path / "dirty-workflow"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'workflow'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is not None, "Expected deny result for dirty tree"
    assert result.get("permissionDecision") == "deny"
    assert "DIRTY TREE" in result.get("message", "")


# ─── Non-dispatch tools not blocked ─────────────────────────────────────────


def test_dirty_tree_allows_read_tool(tmp_path):
    """Dirty tree does NOT block non-dispatch tools (read/bash/edit)."""
    repo = tmp_path / "dirty-read"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Dirty tree should not block read tool, got: {result}"
    )


def test_dirty_tree_allows_bash_tool(tmp_path):
    """Dirty tree does NOT block bash tool."""
    repo = tmp_path / "dirty-bash"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Dirty tree should not block bash tool, got: {result}"
    )


# ─── Clean tree allows dispatch ─────────────────────────────────────────────


def test_clean_tree_allows_task_dispatch(tmp_path):
    """Clean tree (committed) -> hook allows dispatch (returns void)."""
    repo = tmp_path / "clean-repo"
    repo.mkdir()
    _init_git_repo(repo)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Clean tree should allow dispatch, got: {result}"
    )


def test_clean_tree_allows_agent_dispatch(tmp_path):
    """Clean tree -> agent dispatch allowed."""
    repo = tmp_path / "clean-agent"
    repo.mkdir()
    _init_git_repo(repo)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny"


# ─── Env var disable ────────────────────────────────────────────────────────


def test_env_var_disables_dirty_tree_block(tmp_path):
    """GLUDD_CLEAN_TREE_ENFORCE=0 skips check even on dirty tree."""
    repo = tmp_path / "env-off"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"GLUDD_CLEAN_TREE_ENFORCE": "0"}, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable should allow dispatch on dirty tree, got: {result}"
    )


# ─── Subagent guard ─────────────────────────────────────────────────────────


def test_subagent_skips_dirty_tree_check(tmp_path):
    """OPENCODE_SUBAGENT=1 bypasses dirty-tree check entirely."""
    repo = tmp_path / "subagent-repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"OPENCODE_SUBAGENT": "1"}, cwd=str(repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip check, got: {result}"
    )


# ─── Fail-open: not a git repo ──────────────────────────────────────────────


def test_non_git_repo_fails_open_allows_dispatch(tmp_path):
    """Non-git directory should fail-open (allow dispatch)."""
    non_repo = tmp_path / "non-repo"
    non_repo.mkdir()

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(non_repo))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Non-git dir should fail-open, got: {result}"
    )


# ─── Full cycle: dirty -> commit -> clean ───────────────────────────────────


def test_full_cycle_dirty_commit_clean(tmp_path):
    """Dirty blocks dispatch; commit cleans; after cleanup dispatch allowed."""
    repo = tmp_path / "full-cycle"
    repo.mkdir()
    _init_git_repo(repo)

    # 1. Dirty -> blocked
    (repo / "work.txt").write_text("uncommitted")
    code_dirty = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    r1 = _run_plugin(code_dirty, cwd=str(repo))
    assert r1 is not None and r1.get("permissionDecision") == "deny", (
        f"Step 1: dirty tree must block dispatch, got: {r1}"
    )
    assert "DIRTY TREE" in r1.get("message", ""), (
        f"Deny message must include DIRTY TREE, got: {r1.get('message')}"
    )

    # 2. Commit -> clean
    subprocess.run(["git", "add", "work.txt"], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "commit work"], cwd=repo, capture_output=True
    )

    # 3. Clean -> allowed
    r2 = _run_plugin(code_dirty, cwd=str(repo))
    assert r2 is None or r2.get("permissionDecision") != "deny", (
        f"Step 3: clean tree must allow dispatch, got: {r2}"
    )


# ─── Deny message contents ──────────────────────────────────────────────────


def test_deny_message_includes_file_count(tmp_path):
    """Deny message reports correct count of uncommitted files."""
    repo = tmp_path / "multi-dirty"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "a.txt").write_text("a")
    (repo / "b.txt").write_text("b")
    (repo / "c.txt").write_text("c")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
console.log(JSON.stringify(result ?? {{}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert "DIRTY TREE" in result.get("message", "")
    assert "3" in result.get("message", ""), (
        f"Expected count 3 in deny message: {result.get('message')}"
    )


def test_deny_message_mentions_git_stash_and_commit(tmp_path):
    """Deny message directs user to commit or stash."""
    repo = tmp_path / "msg-test"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "f.txt").write_text("x")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    msg = result.get("message", "")
    assert "git-stash" in msg
    assert "ship-commit" in msg
    assert "GLUDD_CLEAN_TREE_ENFORCE=0" in msg


# ─── Edge cases ─────────────────────────────────────────────────────────────


def test_multiple_dirty_files_with_mixed_status(tmp_path):
    """Mixed status (modified, staged, untracked) all count as dirty."""
    repo = tmp_path / "mixed-repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "modified.txt").write_text("mod")
    (repo / "new.txt").write_text("new")
    (repo / ".gitkeep").write_text("staged-change")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, capture_output=True)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'workflow'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(repo))
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"Mixed status should block dispatch, got: {result}"
    )
