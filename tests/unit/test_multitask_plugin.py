"""Behavior pin for the enforce-multitask plugin.

Per AGENTS.md "Message-shape mechanical rule" and user directive (2026-07-09):
every assistant response MUST contain either zero or >=10 dispatches per wave.
1-9 dispatches is DENIED. This test extracts exported constants from the
TypeScript source and validates them against the spec.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from unittest import mock

import pytest

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_export_value(src: str, name: str) -> str:
    """Extract the value of `export const X = <value>;`"""
    pat = re.compile(rf"export\s+const\s+{name}\s*=\s*(.+?);", re.DOTALL)
    m = pat.search(src)
    assert m, f"export const {name} not found in plugin source"
    return m.group(1).strip()


def _extract_string_value(src: str, name: str) -> str:
    raw = _extract_export_value(src, name)
    m = re.match(r'"(.+?)"\s*(?:\+\s*\n\s*"(.+?)")*', raw, re.DOTALL)
    if m:
        parts = [m.group(1)]
        if m.group(2):
            parts.append(m.group(2))
        return " ".join(parts)
    m = re.match(r'"(.+)"', raw, re.DOTALL)
    if m:
        return m.group(1)
    m = re.match(r"'(.+)'", raw, re.DOTALL)
    if m:
        return m.group(1)
    return raw


def _extract_env_default(src: str, env_var: str) -> int:
    pat = re.compile(rf"process\.env\.{env_var}\s*\|\|\s*\"(\d+)\"")
    m = pat.search(src)
    if m:
        return int(m.group(1))
    altpat = re.compile(rf"parseInt\(process\.env\.{env_var}\s*\|\|\s*\"(\d+)\"")
    altm = altpat.search(src)
    if altm:
        return int(altm.group(1))
    raise AssertionError(f"env var {env_var} default not found in source")


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-multitask.ts" in oc, "Plugin not registered in opencode.json"

    def test_exports_min_dispatch_constants(self):
        src = _plugin_source()
        assert "MIN_DISPATCHES" in src, "MIN_DISPATCHES export missing"
        assert "MAX_ZERO_STREAK" in src, "MAX_ZERO_STREAK export missing"

    def test_exports_deny_messages(self):
        src = _plugin_source()
        assert "DENY_PREFIX" in src, "DENY_PREFIX export missing"
        assert "ZERO_STREAK_DENY_PREFIX" in src, "ZERO_STREAK_DENY_PREFIX export missing"
        assert "STOP_GUARD_PREFIX" in src, "STOP_GUARD_PREFIX export missing"

    def test_exports_dispatch_tools(self):
        src = _plugin_source()
        assert "DISPATCH_TOOLS" in src, "DISPATCH_TOOLS export missing"

    def test_exports_state_file_path(self):
        src = _plugin_source()
        assert "MULTITASK_STATE_FILE" in src, "MULTITASK_STATE_FILE export missing"


class TestMinDispatchesDefault:
    def test_default_is_10(self):
        default = _extract_env_default(_plugin_source(), "GLUDD_MULTITASK_MIN_DISPATCHES")
        assert default == 10, f"MIN_DISPATCHES default should be 10, got {default}"

    def test_string_value_matches_default(self):
        raw = _extract_export_value(_plugin_source(), "MIN_DISPATCHES")
        assert "10" in raw


class TestMaxZeroStreak:
    def test_default_is_2(self):
        src = _plugin_source()
        m = re.search(r"MAX_ZERO_STREAK\s*=\s*(\d+)", src)
        assert m, "MAX_ZERO_STREAK assignment not found"
        assert int(m.group(1)) == 2

    def test_used_in_zero_streak_check(self):
        src = _plugin_source()
        assert "_state.zeroStreak >= MAX_ZERO_STREAK" in src, (
            "MAX_ZERO_STREAK not used in streak limit check"
        )


class TestDispatchTools:
    def test_contains_task_agent_workflow(self):
        src = _plugin_source()
        assert '"task"' in src
        assert '"agent"' in src
        assert '"workflow"' in src

    def test_is_frozen(self):
        src = _plugin_source()
        assert "Object.freeze" in src, "DISPATCH_TOOLS not frozen"


class TestHooksRegistered:
    def test_tool_execute_before_hook(self):
        assert "tool.execute.before" in _plugin_source()

    def test_text_complete_hook(self):
        assert "experimental.text.complete" in _plugin_source()

    def test_session_idle_hook(self):
        assert "session.idle" in _plugin_source()


class TestFailOpen:
    def test_try_catch_present(self):
        src = _plugin_source()
        assert "catch" in src, "No try/catch fail-open block found"

    def test_fail_open_comment_present(self):
        src = _plugin_source()
        assert "fail-open" in src.lower(), "No fail-open comment found"

    def test_catch_returns_or_continues(self):
        src = _plugin_source()
        assert src.count("catch") >= 3, f"Expected ≥3 catch blocks for fail-open, found {src.count('catch')}"


class TestEnvVarDisable:
    def test_enforce_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in src, "env-var disable switch missing"

    def test_disabled_when_set_to_zero(self):
        src = _plugin_source()
        assert 'GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"' in src, (
            "Should check !== '0' to disable when set to 0"
        )

    def test_min_dispatch_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_MIN_DISPATCHES" in src


class TestStateFilePath:
    def test_state_file_is_in_tmp(self):
        raw = _extract_string_value(_plugin_source(), "MULTITASK_STATE_FILE")
        assert raw == "/tmp/gludd-multitask-state.json", f"Wrong state file path: {raw}"


class TestDenyMessageContent:
    def test_deny_prefix_mentions_multitasking(self):
        raw = _extract_string_value(_plugin_source(), "DENY_PREFIX")
        assert "MULTITASKING" in raw, "DENY_PREFIX should mention MULTITASKING"

    def test_deny_prefix_mentions_batch_wider(self):
        raw = _extract_string_value(_plugin_source(), "DENY_PREFIX")
        assert "batch" in raw.lower() or "Batch" in raw, "DENY_PREFIX should mention batching"

    def test_deny_prefix_mentions_env_disable(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in src, "Plugin should name env var"

    def test_zero_streak_prefix_mentions_consecutive(self):
        src = _plugin_source()
        assert "consecutive" in src.lower(), "ZERO_STREAK_DENY_PREFIX should mention consecutive"

    def test_stop_guard_prefix_mentions_unchecked(self):
        raw = _extract_string_value(_plugin_source(), "STOP_GUARD_PREFIX")
        assert "unchecked" in raw.lower(), "STOP_GUARD_PREFIX should mention unchecked items"


class TestResultMarkers:
    def test_has_result_markers(self):
        src = _plugin_source()
        assert "task result" in src
        assert "completed" in src
        assert "subagent result" in src


class TestTasksHasUnchecked:
    def test_tasks_has_unchecked_function_present(self):
        src = _plugin_source()
        assert "tasksHasUnchecked" in src or "tasks.md" in src.lower()

    def test_checks_markdown_checkbox(self):
        src = _plugin_source()
        assert "[-*]\\s+\\[\\s*\\]" in src or "/\\[\\s*\\]" in src, (
            "Should check for unchecked markdown checkboxes"
        )
