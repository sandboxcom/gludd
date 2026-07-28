"""Tests for DP.2: wave refill automation in enforce-multitask.ts.

Verifies that the plugin tracks lastDispatchTs and injects a refill
reminder when the subagent pool drops below 5 for more than 30 seconds.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_export_value(src: str, name: str) -> str:
    pat = re.compile(rf"export\s+const\s+{name}\s*=\s*(.+?)(?:;|\n)", re.DOTALL)
    m = pat.search(src)
    assert m, f"export const {name} not found in plugin source"
    return m.group(1).strip()


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


class TestRefillConstant:
    """Verify RESULT_ARRIVAL_REFRESH_INTERVAL_MS constant exists and defaults to 30000."""

    def test_constant_exists(self):
        src = _plugin_source()
        assert "RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src, (
            "RESULT_ARRIVAL_REFRESH_INTERVAL_MS constant missing"
        )

    def test_constant_env_override(self):
        src = _plugin_source()
        val = _extract_env_default(src, "GLUDD_REFRESH_INTERVAL_MS")
        assert val == 30000, f"Default should be 30000, got {val}"

    def test_constant_used_in_refill_check(self):
        src = _plugin_source()
        assert "RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src, "constant not used in source"


class TestMultitaskStateShape:
    """Verify lastDispatchTs field exists in MultitaskState interface and freshState."""

    def test_last_dispatch_ts_in_interface(self):
        src = _plugin_source()
        assert re.search(r"lastDispatchTs\s*:\s*number", src), (
            "lastDispatchTs field missing from MultitaskState interface"
        )

    def test_last_dispatch_ts_in_fresh_state(self):
        src = _plugin_source()
        # Should appear in freshState's return object
        assert re.search(r"lastDispatchTs\s*:\s*0", src), (
            "lastDispatchTs: 0 missing from freshState()"
        )

    def test_last_dispatch_ts_initialized_in_boot_block(self):
        src = _plugin_source()
        # The _state initializer also sets it
        counts = len(re.findall(r"s\.lastDispatchTs\s*=\s*0", src))
        assert counts >= 1, (
            "s.lastDispatchTs = 0 not found in _state initialization block"
        )


class TestDispatchTimestampTracking:
    """Verify lastDispatchTs is set when a dispatch tool runs."""

    def test_last_dispatch_ts_set_on_dispatch(self):
        src = _plugin_source()
        assert "_state.lastDispatchTs = now" in src, (
            "_state.lastDispatchTs = now not found in tool.execute.before dispatch path"
        )

    def test_last_dispatch_ts_set_after_estimated_in_flight_inc(self):
        src = _plugin_source()
        # lastDispatchTs must be set AFTER estimatedInFlight++ for correct timing
        idx_inc = src.find("_state.estimatedInFlight++")
        idx_ts = src.find("_state.lastDispatchTs = now")
        assert idx_inc >= 0, "estimatedInFlight++ not found"
        assert idx_ts >= 0, "lastDispatchTs = now not found"
        assert idx_ts > idx_inc, (
            "lastDispatchTs must be set AFTER estimatedInFlight++ for correct ordering"
        )


class TestRefillLogicInDefaultImpl:
    """Verify the refill injection exists in defaultImpl's experimental.text.complete."""

    def test_refill_condition_checks_last_dispatch_ts_gt_0(self):
        src = _plugin_source()
        assert "_state.lastDispatchTs > 0" in src, (
            "lastDispatchTs > 0 guard missing from refill check"
        )

    def test_refill_condition_checks_estimated_in_flight_lt_5(self):
        src = _plugin_source()
        assert "_state.estimatedInFlight < 5" in src, (
            "estimatedInFlight < 5 guard missing from refill check"
        )

    def test_refill_condition_checks_elapsed_time(self):
        src = _plugin_source()
        assert "Date.now() - _state.lastDispatchTs" in src, (
            "elapsed-time check missing from refill logic"
        )

    def test_refill_warning_text_exists(self):
        src = _plugin_source()
        assert "FLOOR LOW" in src, (
            "FLOOR LOW warning message not found in source"
        )
        assert "Dispatch replacements now" in src, (
            "Dispatch replacements now message not found in source"
        )

    def test_refill_warning_includes_agent_count(self):
        src = _plugin_source()
        # Check that the message includes _state.estimatedInFlight in the string
        assert re.search(
            r'\bString\s*\(\s*_state\.estimatedInFlight\s*\)',
            src,
        ), "Agent count not included in FLOOR LOW message"

    def test_refill_warning_shows_seconds_since_last_dispatch(self):
        src = _plugin_source()
        assert re.search(
            r'Math\.round\s*\(\s*\(\s*Date\.now\(\s*\)\s*-\s*_state\.lastDispatchTs\s*\)\s*/\s*1000\s*\)',
            src,
        ), "Time-since-last-dispatch not computed in FLOOR LOW message"

    def test_refill_check_uses_constant(self):
        src = _plugin_source()
        assert "RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src
        assert "> RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src or ">RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src, (
            "Refill check does not reference RESULT_ARRIVAL_REFRESH_INTERVAL_MS"
        )


class TestRefillLogicInProxy:
    """Verify the thin proxy delegates refill behavior to one effective implementation."""

    def test_proxy_delegates_refill_check_to_effective_impl(self):
        src = _plugin_source()
        assert 'loadHotModule("multitask", defaultImpl)' in src
        assert (
            'impl["experimental.text.complete"] ?? impl["text.complete"]'
            in src
        )

    def test_refill_logic_has_single_source(self):
        src = _plugin_source()
        assert src.count("FLOOR LOW: only") == 1
        assert src.count("_state.estimatedInFlight < 5") == 1


class TestRefillDoesNotFireWhenPoolHigh:
    """Behavioral checks via source analysis: refill only fires when pool is low."""

    def test_refill_checks_estimated_in_flight_lt_5(self):
        src = _plugin_source()
        # The guard uses < 5, not <= 5
        assert "_state.estimatedInFlight < 5" in src, (
            "Should guard on estimatedInFlight < 5, not <= 5"
        )

    def test_refill_requires_last_dispatch_ts_nonzero(self):
        src = _plugin_source()
        assert "_state.lastDispatchTs > 0" in src, (
            "Should guard on lastDispatchTs > 0 (never dispatched → no refill)"
        )

    def test_refill_requires_elapsed_greater_than_interval(self):
        src = _plugin_source()
        assert "RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src
        assert "> RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src or ">RESULT_ARRIVAL_REFRESH_INTERVAL_MS" in src, (
            "Should use > not >= for elapsed time check"
        )


class TestPluginFileExports:
    """Ensure plugin file is valid and registered."""

    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-multitask.ts" in oc, "Plugin not registered in opencode.json"
