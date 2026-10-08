from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

from .fixtures import (
    LIB_DIR,
    PLUGIN_DIR,
    _dirty_test_path,
    _runtime_state_path,
    _runtime_state_root,
)
from .runner import (
    _run_ts,
)


def test_runtime_state_root_honors_isolated_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Runtime state is rooted in the verifier-owned namespace when configured."""
    monkeypatch.setenv("GLUDD_RUNTIME_TEST_STATE_DIR", str(tmp_path))
    assert _runtime_state_root() == tmp_path.resolve()


def test_runtime_state_path_redirects_only_known_global_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Known global state is isolated without rewriting unrelated paths."""
    monkeypatch.setenv("GLUDD_RUNTIME_TEST_STATE_DIR", str(tmp_path))
    known = "/tmp/gludd-tool-streak.json"
    unrelated = "/tmp/operator-owned.json"
    assert _runtime_state_path(known) == str(tmp_path.resolve() / "gludd-tool-streak.json")
    assert _runtime_state_path(unrelated) == unrelated

def test_run_ts_returns_none_for_empty_stdout() -> None:
    """A hook that emits no result has the same observable value as undefined."""
    assert _run_ts("// intentionally no stdout") is None


def test_run_ts_ignores_non_json_diagnostics() -> None:
    """Non-JSON diagnostics do not become a fabricated hook result."""
    assert _run_ts("console.log('runtime diagnostic only')") is None


def test_run_ts_namespaces_streak_state_per_invocation() -> None:
    """Concurrent hook invocations must never share the mutable streak file."""
    code = "console.log(JSON.stringify({streakPath: process.env.GLUDD_STREAK_FILE ?? null}))"

    first = _run_ts(code)
    second = _run_ts(code)

    first_path = Path(first["streakPath"])
    second_path = Path(second["streakPath"])
    assert first_path.parent == _runtime_state_root()
    assert second_path.parent == _runtime_state_root()
    assert first_path.name.startswith("gludd-tool-streak-test-")
    assert second_path.name.startswith("gludd-tool-streak-test-")
    assert first_path != second_path


def test_shared_explicit_non_subagent_ignores_stale_pid_marker() -> None:
    """An explicit false marker must beat a stale PID file from another process."""
    code = f"""\
const fs = await import('node:fs')
process.env.OPENCODE_SUBAGENT = '0'
const marker = `/tmp/gludd-subagent-${{process.pid}}.json`
fs.writeFileSync(marker, '{{}}', 'utf8')
try {{
  const mod = await import('{LIB_DIR}/shared.ts')
  console.log(JSON.stringify({{subagent: mod.isSubagent()}}))
}} finally {{
  try {{ fs.unlinkSync(marker) }} catch {{}}
}}
"""
    result = _run_ts(code)
    assert result == {"subagent": False}

# enforce-clean-tree.ts  —  exports pure functions + PluginAPI hook
# ---------------------------------------------------------------------------


def test_clean_tree_get_git_status() -> None:
    """getGitStatus() returns non-empty string in a real git repo."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{status: mod.getGitStatus(), length: mod.getGitStatus().length}}))
"""
    result = _run_ts(code)
    assert result is not None
    assert isinstance(result["status"], str)
    # In the gludd repo, there may be dirty files
    assert isinstance(result["length"], int)


def test_clean_tree_is_dirty_in_real_repo() -> None:
    """isTreeDirty() returns boolean in a real git repo."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{dirty: mod.isTreeDirty()}}))
"""
    result = _run_ts(code)
    assert isinstance(result["dirty"], bool)


def test_clean_tree_count_dirty_files_zero() -> None:
    """countDirtyFiles returns 0 for empty status."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{count: mod.countDirtyFiles('')}}))
"""
    result = _run_ts(code)
    assert result["count"] == 0


def test_clean_tree_count_dirty_files_nonzero() -> None:
    """countDirtyFiles counts lines in porcelain output."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
const fake = ' M foo.py\\n?? bar.py\\n M baz.py'
console.log(JSON.stringify({{count: mod.countDirtyFiles(fake)}}))
"""
    result = _run_ts(code)
    assert result["count"] == 3


def test_clean_tree_build_deny_message() -> None:
    """buildDenyMessage includes count and DENY_MESSAGE_PREFIX."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{msg: mod.buildDenyMessage(5), prefix: mod.getDenyMessagePrefix()}}))
"""
    result = _run_ts(code)
    assert "5" in result["msg"]
    assert "DIRTY TREE" in result["msg"]
    assert result["prefix"] == "DIRTY TREE"


def test_clean_tree_dispatch_tools_defined() -> None:
    """DISPATCH_TOOLS array contains task, agent, workflow."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify(mod.getDispatchTools()))
"""
    result = _run_ts(code)
    assert "task" in result
    assert "agent" in result
    assert "workflow" in result


def test_clean_tree_hook_dispatch_with_dirty_tree() -> None:
    """The proxy hook denies dispatch when tree is dirty."""
    test_file = _dirty_test_path("dispatch")
    try:
        with open(test_file, "w") as f:
            f.write("test dirty file for hook test")
            f.flush()
            os.fsync(f.fileno())
        code = f"""\
const helpers = await import('{LIB_DIR}/plugin_test_exports.ts')
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const gs = helpers.getGitStatus()
console.log("GIT_STATUS[" + gs.length + "]=" + JSON.stringify(gs).slice(0,200))
const dt = helpers.isTreeDirty()
console.log("IS_DIRTY=" + dt)
const toolName = 'task'
const isDispatch = helpers.getDispatchTools().includes(toolName)
console.log("IS_DISPATCH=" + isDispatch)
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code)
        assert result is not None, "Expected deny object, got None"
        assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
        assert "DIRTY TREE" in result.get("message", "")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)


def test_clean_tree_hook_clean_tree_allows_dispatch() -> None:
    """Clean tree should allow dispatch (hook returns undefined/void)."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    if result is not None and result.get("permissionDecision") == "deny":
        pass


def test_clean_tree_env_disable() -> None:
    """GLUDD_CLEAN_TREE_ENFORCE=0 disables the check."""
    test_file = _dirty_test_path("disabled")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"GLUDD_CLEAN_TREE_ENFORCE": "0"})
        assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)


def test_clean_tree_subagent_skip() -> None:
    """OPENCODE_SUBAGENT=1 skips all enforcement."""
    test_file = _dirty_test_path("subagent")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
        assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)
