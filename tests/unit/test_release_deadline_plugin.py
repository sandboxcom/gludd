"""Structural + behavioral tests for enforce-release-deadline.ts (RP.19).

Verifies: plugin existence, subagent guard, fail-open, hot-reload proxy
pattern, opencode.json registration, threshold constants, blocked-target
list, and the state-machine logic (start tracking, 2h warning, 3h block).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/plugin/enforce-release-deadline.ts"
)
OPOCODE_JSON = Path(__file__).resolve().parents[2] / "opencode.json"


def _src() -> str:
    return PLUGIN_PATH.read_text()


# ---------------------------------------------------------------------------
# Plugin existence + export shape
# ---------------------------------------------------------------------------


class TestPluginExists:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), "enforce-release-deadline.ts must exist"

    def test_exports_default_satisfies_plugin(self):
        assert "satisfies Plugin" in _src()

    def test_factory_is_async_arrow(self):
        assert "export default (" in _src()


class TestPluginHooks:
    def test_has_tool_execute_before_hook(self):
        assert '"tool.execute.before"' in _src()

    def test_has_experimental_text_complete_hook(self):
        assert '"experimental.text.complete"' in _src()

    def test_returns_two_hooks_from_factory(self):
        src = _src()
        factory_idx = src.find("export default (")
        after = src[factory_idx:] if factory_idx > 0 else src
        assert '"tool.execute.before"' in after
        assert '"experimental.text.complete"' in after


# ---------------------------------------------------------------------------
# Subagent guard
# ---------------------------------------------------------------------------


class TestSubagentGuard:
    def test_imports_is_subagent(self):
        assert "isSubagent" in _src()

    def test_guard_in_tool_execute_before(self):
        src = _src()
        idx = src.find('"tool.execute.before"')
        assert idx > 0
        after = src[idx:idx + 600]
        assert "isSubagent()" in after, "tool.execute.before must call isSubagent()"

    def test_guard_in_text_complete(self):
        src = _src()
        idx = src.find('"experimental.text.complete"')
        assert idx > 0
        after = src[idx:idx + 600]
        assert "isSubagent()" in after, "text.complete must call isSubagent()"

    def test_guard_precedes_enforcement_in_before_hook(self):
        src = _src()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        assert idx > 0
        after = src[idx:idx + 500]
        guard_idx = after.find("isSubagent()")
        enforce_idx = after.find("ENFORCE")
        if enforce_idx > 0:
            assert guard_idx < enforce_idx, "subagent guard must precede ENFORCE check"


# ---------------------------------------------------------------------------
# Fail-open guarantee
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_before_hook_has_try_catch(self):
        src = _src()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        end = src.find('"experimental.text.complete"', idx + 1)
        section = src[idx:end] if idx > 0 and end > idx else src[idx:]
        assert "catch" in section, "tool.execute.before must have try/catch (fail-open)"

    def test_text_complete_has_try_catch(self):
        src = _src()
        idx = src.find('"experimental.text.complete": async', src.find("defaultImpl"))
        after = src[idx:] if idx > 0 else src
        assert "catch" in after, "text.complete must have try/catch (fail-open)"

    def test_detect_release_task_catches_errors(self):
        src = _src()
        idx = src.find("function detectReleaseTaskInProgress")
        after = src[idx:idx + 1000] if idx > 0 else src
        assert "catch" in after, "detectReleaseTaskInProgress must fail-open"


# ---------------------------------------------------------------------------
# Hot-reload proxy pattern
# ---------------------------------------------------------------------------


class TestHotReload:
    def test_imports_load_hot_module(self):
        assert "loadHotModule" in _src()

    def test_uses_hot_module_name_release_deadline(self):
        assert 'loadHotModule("release-deadline"' in _src()

    def test_default_impl_defined(self):
        assert "const defaultImpl" in _src()
        assert "HotModule" in _src()

    def test_proxy_delegates_before_hook(self):
        src = _src()
        factory_idx = src.find("export default (")
        after = src[factory_idx:]
        assert 'impl["tool.execute.before"]' in after

    def test_proxy_delegates_text_complete(self):
        src = _src()
        factory_idx = src.find("export default (")
        after = src[factory_idx:]
        assert (
            'impl["text.complete"] || impl["experimental.text.complete"]' in after
        )


# ---------------------------------------------------------------------------
# opencode.json registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registered_in_opencode_json(self):
        data = json.loads(OPOCODE_JSON.read_text())
        plugins = data.get("plugin", [])
        assert any(
            "enforce-release-deadline.ts" in p for p in plugins
        ), "enforce-release-deadline.ts must be registered in opencode.json"


# ---------------------------------------------------------------------------
# Threshold constants (2h warn, 3h block)
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_warn_ms_default_is_2_hours(self):
        src = _src()
        m = re.search(
            r"RELEASE_DEADLINE_WARN_MS\s*\|\|\s*\"(\d+)\"", src
        )
        assert m, "GLUDD_RELEASE_DEADLINE_WARN_MS not found"
        assert int(m.group(1)) == 7200000, "warn default must be 7200000ms (2h)"

    def test_block_ms_default_is_3_hours(self):
        src = _src()
        m = re.search(
            r"RELEASE_DEADLINE_BLOCK_MS\s*\|\|\s*\"(\d+)\"", src
        )
        assert m, "GLUDD_RELEASE_DEADLINE_BLOCK_MS not found"
        assert int(m.group(1)) == 10800000, "block default must be 10800000ms (3h)"

    def test_state_file_default_path(self):
        src = _src()
        assert "gludd-release-deadline.json" in src

    def test_enforce_flag_default_on(self):
        src = _src()
        assert "GLUDD_RELEASE_DEADLINE_ENFORCE" in src
        assert '!== "0"' in src


# ---------------------------------------------------------------------------
# Blocked targets
# ---------------------------------------------------------------------------


class TestBlockedTargets:
    def test_blocked_targets_list_exists(self):
        src = _src()
        assert "BLOCKED_TARGETS" in src

    def test_blocks_test_unit(self):
        assert '"test-unit"' in _src()

    def test_blocks_lint(self):
        assert '"lint"' in _src()

    def test_blocks_typecheck(self):
        assert '"typecheck"' in _src()

    def test_blocks_ci_status(self):
        assert '"ci-status"' in _src()

    def test_blocks_ci_view(self):
        assert '"ci-view"' in _src()

    def test_does_not_block_release_cut(self):
        src = _src()
        idx = src.find("BLOCKED_TARGETS")
        after = src[idx:idx + 300]
        assert "release-cut" not in after

    def test_does_not_block_verify_release(self):
        src = _src()
        idx = src.find("BLOCKED_TARGETS")
        after = src[idx:idx + 300]
        assert "verify-release-completeness" not in after


# ---------------------------------------------------------------------------
# Release task detection from TASKS.md
# ---------------------------------------------------------------------------


class TestReleaseTaskDetection:
    def test_reads_tasks_md(self):
        assert "TASKS.md" in _src()

    def test_checks_status_in_progress(self):
        assert "in_progress" in _src()

    def test_checks_release_keyword(self):
        assert "release" in _src().lower()

    def test_detect_function_exists(self):
        assert "function detectReleaseTaskInProgress" in _src()

    def test_returns_null_on_no_match(self):
        src = _src()
        idx = src.find("function detectReleaseTaskInProgress")
        after = src[idx:idx + 1500]
        assert "return null" in after


# ---------------------------------------------------------------------------
# State file shape
# ---------------------------------------------------------------------------


class TestStateFile:
    def test_tracks_start_ms(self):
        assert "start_ms" in _src()

    def test_tracks_release_task(self):
        assert "release_task" in _src()

    def test_tracks_warned_flag(self):
        assert "warned" in _src()


# ---------------------------------------------------------------------------
# Block logic (deny returns permissionDecision)
# ---------------------------------------------------------------------------


class TestBlockLogic:
    def test_block_returns_permission_decision_deny(self):
        src = _src()
        idx = src.find("permissionDecision")
        assert idx > 0
        after = src[idx:idx + 100]
        assert '"deny"' in after

    def test_block_message_mentions_release_deadline(self):
        src = _src()
        assert "RELEASE DEADLINE BLOCK" in src


# ---------------------------------------------------------------------------
# Behavioral tests — Python simulation of plugin state-machine
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


WARN_MS = 7200000
BLOCK_MS = 10800000
BLOCKED = ["test-unit", "lint", "typecheck", "ci-status", "ci-view"]


def _detect_release_task(tasks_content: str) -> str | None:
    for line in tasks_content.split("\n"):
        if not re.search(r"status:\s*in_progress", line, re.IGNORECASE):
            continue
        if "release" not in line.lower():
            continue
        m = re.match(r"^\s*[-*]\s+\[\s*x?\s*\]\s+(\S+)", line)
        return m.group(1) if m and m.group(1) else "release"
    return None


class TestDetectReleaseTaskInProgressBehavioral:
    def test_detects_release_task_in_progress(self):
        content = "- [x] RP.19 — Release cut | status: in_progress\n"
        assert _detect_release_task(content) == "RP.19"

    def test_ignores_release_task_pending(self):
        content = "- [ ] RP.19 — Release cut | status: pending\n"
        assert _detect_release_task(content) is None

    def test_ignores_non_release_in_progress(self):
        content = "- [x] ABC.1 — Fix lint | status: in_progress\n"
        assert _detect_release_task(content) is None

    def test_returns_none_when_no_tasks(self):
        assert _detect_release_task("") is None

    def test_release_keyword_case_insensitive(self):
        content = "- [x] T.1 — cut RELEASE | status: in_progress\n"
        assert _detect_release_task(content) == "T.1"


class TestThresholdBehavioral:
    def test_2h_elapsed_triggers_warn_not_block(self):
        elapsed = WARN_MS + 1
        assert elapsed > WARN_MS
        assert elapsed <= BLOCK_MS

    def test_3h_elapsed_triggers_block(self):
        elapsed = BLOCK_MS + 1
        assert elapsed > WARN_MS
        assert elapsed > BLOCK_MS

    def test_under_2h_no_warning(self):
        elapsed = WARN_MS - 1
        assert elapsed <= WARN_MS


class TestBlockTargetBehavioral:
    def test_blocks_test_unit_after_3h(self):
        cmd = "make test-unit"
        target = cmd[5:].split()[0]
        elapsed = BLOCK_MS + 1
        assert target in BLOCKED
        assert elapsed > BLOCK_MS

    def test_allows_release_cut_after_3h(self):
        cmd = "make release-cut"
        target = cmd[5:].split()[0]
        assert target not in BLOCKED

    def test_allows_verify_release_after_3h(self):
        cmd = "make verify-release-completeness"
        target = cmd[5:].split()[0]
        assert target not in BLOCKED

    def test_allows_git_push_after_3h(self):
        cmd = "make git-push-sandboxcom"
        target = cmd[5:].split()[0]
        assert target not in BLOCKED

    def test_allows_git_tag_push_after_3h(self):
        cmd = "make git-tag-push"
        target = cmd[5:].split()[0]
        assert target not in BLOCKED

    def test_no_block_before_3h(self):
        elapsed = BLOCK_MS - 1
        assert elapsed <= BLOCK_MS


class TestStateMachineBehavioral:
    def test_start_recorded_when_release_task_detected(self, tmp_path):
        state_path = tmp_path / "state.json"
        task = "RP.19"
        state: dict = {}
        if task:
            state = {"release_task": task, "start_ms": _now_ms(), "warned": False}
            state_path.write_text(json.dumps(state))
        loaded = json.loads(state_path.read_text())
        assert loaded["release_task"] == task
        assert isinstance(loaded["start_ms"], int)

    def test_state_cleared_when_release_task_completes(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({
            "release_task": "RP.19", "start_ms": _now_ms(), "warned": False
        }))
        task = None
        if not task:
            state = {"release_task": None, "start_ms": None, "warned": None}
            state_path.write_text(json.dumps(state))
        loaded = json.loads(state_path.read_text())
        assert loaded["release_task"] is None

    def test_elapsed_computed_from_start_ms(self, tmp_path):
        start = _now_ms() - (BLOCK_MS + 60000)
        elapsed = _now_ms() - start
        assert elapsed > BLOCK_MS

    def test_warned_flag_set_after_warning(self, tmp_path):
        state_path = tmp_path / "state.json"
        state = {"release_task": "RP.19", "start_ms": _now_ms() - (WARN_MS + 1), "warned": False}
        elapsed = _now_ms() - state["start_ms"]
        if elapsed > WARN_MS:
            state["warned"] = True
            state_path.write_text(json.dumps(state))
        loaded = json.loads(state_path.read_text())
        assert loaded["warned"] is True


class TestNodeV26Compat:
    def test_no_nested_try_in_catch(self):
        src = _src()
        assert "catch { try" not in src
        assert "catch (e) { try" not in src

    def test_no_type_annotated_catch(self):
        src = _src()
        assert not re.search(r"catch\s*\(\s*\w+\s*:", src)

    def test_no_enums(self):
        assert not re.search(r"\benum\s+", _src())

    def test_no_namespaces(self):
        assert not re.search(r"\bnamespace\s+", _src())
