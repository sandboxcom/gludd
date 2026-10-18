"""Behavior pin for the no-idle-monitoring enforcement plugin.

Per AGENTS.md "No Idle Monitoring While Work Remains": when a background
operation is running, the main thread must interleave status checks with
concrete independent deliverables. Repeated monitoring calls without
productive work between them are denied.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import HookEnv, hook_plugin_env_impl

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-no-idle-monitoring.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_monitoring_patterns(src: str) -> list[re.Pattern[str]]:
    block_match = re.search(r"MONITORING_PATTERNS[^=]*=\s*Object\.freeze\(\[(.*?)\]\)", src, re.DOTALL)
    assert block_match, "MONITORING_PATTERNS export not found in plugin source"
    block = block_match.group(1)
    return [re.compile(lit.strip().strip("/")) for lit in re.findall(r"/(.*?)/", block)]


class TestPluginStructure:
    def test_plugin_file_exists(self) -> None:
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self) -> None:
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-no-idle-monitoring.ts" in oc, "Plugin not registered in opencode.json"

    def test_exports_monitoring_patterns(self) -> None:
        src = _plugin_source()
        assert "MONITORING_PATTERNS" in src, "MONITORING_PATTERNS export missing"
        assert "DENY_MESSAGE" in src, "DENY_MESSAGE export missing"

    def test_tool_execute_before_hook_registered(self) -> None:
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_fail_open_present(self) -> None:
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable_present(self) -> None:
        src = _plugin_source()
        assert "GLUDD_NO_IDLE_MONITORING_ENFORCE" in src, "Env-var disable switch missing"

    def test_threshold_present(self) -> None:
        src = _plugin_source()
        assert "THRESHOLD" in src, "Threshold constant missing"

    def test_state_file_present(self) -> None:
        src = _plugin_source()
        assert "gludd-idle-monitoring.json" in src, "State file path missing"


class TestMonitoringPatternMatcher:
    @pytest.fixture(scope="class")
    @staticmethod
    def patterns() -> list[re.Pattern[str]]:
        return _extract_monitoring_patterns(_plugin_source())

    @pytest.mark.parametrize(
        "cmd",
        [
            "make gate-status-check",
            "make gate-progress",
            "make gate-logs",
            "make gate-tail",
            "make gate-wait-report",
            "make ci-status",
            "make ci-verdict-fast",
            "make ci-cooldown-status",
            "make git-status",
            "make agent-worktree-list",
            "make worktree-health-check",
        ],
    )
    def test_deny_on_monitoring_patterns(self, patterns: list[re.Pattern[str]], cmd: str) -> None:
        assert any(p.search(cmd) for p in patterns), f"Should count as monitoring: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "make gate-background",
            "make lint",
            "make test-unit",
            "make ship-commit MSG='fix'",
            "make agent-worktree BRANCH=agent-fix",
            "make clean-tmp",
        ],
    )
    def test_allow_on_productive_make(self, patterns: list[re.Pattern[str]], cmd: str) -> None:
        assert not any(p.search(cmd) for p in patterns), f"Should not count as monitoring: {cmd!r}"


@pytest.fixture
def hook_plugin_env(tmp_path: Path) -> Generator[HookEnv, None, None]:
    for environment in hook_plugin_env_impl(tmp_path):
        environment.env["OPENCODE_SUBAGENT"] = "0"
        yield environment


def _seed_idle_counter(hook_plugin_env: HookEnv, last_ts: int | None = None) -> bytes:
    state_file = hook_plugin_env.state_path("GLUDD_IDLE_MONITORING_STATE_FILE")
    state_file.write_text(
        json.dumps({"count": 2, "last_ts": int(time.time() * 1000) if last_ts is None else last_ts}),
        encoding="utf-8",
    )
    return state_file.read_bytes()


def _install_idle_hot_hook(hook_plugin_env: HookEnv, body: str) -> None:
    prefix = hook_plugin_env.env["GLUDD_HOT_MODULE_PREFIX"]
    Path(f"{prefix}enforce-no-idle-monitoring.js").write_text(
        'module.exports = {"tool.execute.before": () => {' + body + "}};\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("tool", ["read", "grep", "glob"])
def test_read_tools_bypass_loaded_hot_override_without_resetting_counter(
    hook_plugin_env: HookEnv, tool: str,
) -> None:
    """A live hot hook cannot deny onboarding reads or hide a counter reset."""
    before = _seed_idle_counter(hook_plugin_env)
    _install_idle_hot_hook(
        hook_plugin_env,
        'return {permissionDecision: "deny", message: "loaded-hot-sentinel"};',
    )
    read = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before", input={"tool": tool}, repeat=6,
    )
    assert read.returncode == 0, read.stderr
    assert json.loads(read.stdout) is None
    assert hook_plugin_env.state_path("GLUDD_IDLE_MONITORING_STATE_FILE").read_bytes() == before

    monitoring = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before",
        input={"tool": "bash", "args": {"command": "make gate-status-check"}},
    )
    assert monitoring.returncode == 0, monitoring.stderr
    assert json.loads(monitoring.stdout) == {
        "permissionDecision": "deny", "message": "loaded-hot-sentinel",
    }


@pytest.mark.parametrize(
    "env_override",
    [{"OPENCODE_SUBAGENT": "1"}, {"GLUDD_NO_IDLE_MONITORING_ENFORCE": "0"}],
    ids=["subagent", "disabled"],
)
def test_subagent_and_disable_guards_precede_loaded_hot_override(
    hook_plugin_env: HookEnv, env_override: dict[str, str],
) -> None:
    before = _seed_idle_counter(hook_plugin_env)
    _install_idle_hot_hook(
        hook_plugin_env,
        'return {permissionDecision: "deny", message: "guard-hot-sentinel"};',
    )
    result = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before",
        input={"tool": "bash", "args": {"command": "make gate-status-check"}},
        env_overrides=env_override, repeat=3,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is None
    assert hook_plugin_env.state_path("GLUDD_IDLE_MONITORING_STATE_FILE").read_bytes() == before


def test_loaded_hot_hook_exception_fails_open(hook_plugin_env: HookEnv) -> None:
    before = _seed_idle_counter(hook_plugin_env)
    _install_idle_hot_hook(hook_plugin_env, 'throw new Error("broken hot hook");')
    result = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before",
        input={"tool": "bash", "args": {"command": "make gate-status-check"}},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is None
    assert hook_plugin_env.state_path("GLUDD_IDLE_MONITORING_STATE_FILE").read_bytes() == before


@pytest.mark.parametrize("tool", ["edit", "write", "task", "agent", "workflow"])
def test_productive_tools_reset_counter_and_preserve_third_check_denial(
    hook_plugin_env: HookEnv, tool: str,
) -> None:
    _seed_idle_counter(hook_plugin_env)
    reset = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before", input={"tool": tool},
    )
    assert reset.returncode == 0, reset.stderr
    assert json.loads(reset.stdout) is None
    reset_state = hook_plugin_env.read_json("GLUDD_IDLE_MONITORING_STATE_FILE")
    assert isinstance(reset_state, dict), reset_state
    assert reset_state["count"] == 0

    monitoring = {"tool": "bash", "args": {"command": "make gate-status-check"}}
    allowed = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before", input=monitoring, repeat=2,
    )
    assert allowed.returncode == 0, allowed.stderr
    assert json.loads(allowed.stdout) is None
    blocked = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before", input=monitoring,
    )
    assert blocked.returncode == 0, blocked.stderr
    assert json.loads(blocked.stdout)["permissionDecision"] == "deny"
    blocked_state = hook_plugin_env.read_json("GLUDD_IDLE_MONITORING_STATE_FILE")
    assert isinstance(blocked_state, dict), blocked_state
    assert blocked_state["count"] == 2


def test_stale_monitoring_counter_resets_before_counting(hook_plugin_env: HookEnv) -> None:
    _seed_idle_counter(hook_plugin_env, last_ts=0)
    result = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before",
        input={"tool": "bash", "args": {"command": "make gate-status-check"}},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is None
    state = hook_plugin_env.read_json("GLUDD_IDLE_MONITORING_STATE_FILE")
    assert isinstance(state, dict), state
    assert state["count"] == 1


@pytest.mark.parametrize("state_kind", ["invalid-json", "directory"])
def test_idle_state_io_errors_fail_open(hook_plugin_env: HookEnv, state_kind: str) -> None:
    state_file = hook_plugin_env.state_path("GLUDD_IDLE_MONITORING_STATE_FILE")
    if state_kind == "invalid-json":
        state_file.write_text("{invalid-json", encoding="utf-8")
    else:
        state_file.mkdir()
    result = hook_plugin_env.invoke(
        PLUGIN_PATH.name, "tool.execute.before",
        input={"tool": "bash", "args": {"command": "make gate-status-check"}},
        repeat=1 if state_kind == "invalid-json" else 3,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is None
    if state_kind == "invalid-json":
        state = hook_plugin_env.read_json("GLUDD_IDLE_MONITORING_STATE_FILE")
        assert isinstance(state, dict), state
        assert state["count"] == 1
    else:
        assert state_file.is_dir()


def test_shared_idle_handler_preserves_subagent_counter(hook_plugin_env: HookEnv) -> None:
    """The fallback exported by the hot builder must keep its own isolation."""
    before = _seed_idle_counter(hook_plugin_env)
    helper_path = PLUGIN_PATH.parent.parent / "lib" / "idle_monitoring.ts"
    probe = hook_plugin_env.cwd / "idle-helper-probe.ts"
    probe.write_text(
        f"import {{ idleMonitoringToolExecuteBefore }} from {json.dumps(str(helper_path))};\n"
        'export default () => ({"tool.execute.before": (input, output) => '
        "idleMonitoringToolExecuteBefore(input, output, {"
        "stateFile: process.env.GLUDD_IDLE_MONITORING_STATE_FILE, "
        'threshold: 3, staleMs: 120000, patterns: [/make\\s+gate-status-check/], '
        'productiveTools: ["edit"], envDisableVar: "GLUDD_NO_IDLE_MONITORING_ENFORCE", '
        'pluginName: "enforce-no-idle-monitoring"}, "probe deny")});\n',
        encoding="utf-8",
    )
    subagent = hook_plugin_env.invoke(
        str(probe), "tool.execute.before", input={"tool": "edit"},
        env_overrides={"OPENCODE_SUBAGENT": "1"},
    )
    assert subagent.returncode == 0, subagent.stderr
    assert json.loads(subagent.stdout) is None
    assert hook_plugin_env.state_path("GLUDD_IDLE_MONITORING_STATE_FILE").read_bytes() == before

    parent = hook_plugin_env.invoke(
        str(probe), "tool.execute.before", input={"tool": "edit"},
        env_overrides={"OPENCODE_SUBAGENT": "0"},
    )
    assert parent.returncode == 0, parent.stderr
    assert json.loads(parent.stdout) is None
    state = hook_plugin_env.read_json("GLUDD_IDLE_MONITORING_STATE_FILE")
    assert isinstance(state, dict), state
    assert state["count"] == 0
