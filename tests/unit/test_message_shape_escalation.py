"""MT.2: single-dispatch wave escalation behavior pin.

After 3 consecutive waves with exactly 1 dispatch, inject
"MESSAGE SHAPE VIOLATION: 3 consecutive single-dispatch waves.
Batch wider — 2+ dispatches per message."
Reset counter when a wave has 2+ dispatches.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


class TestMessageShapeEscalationStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_single_dispatch_waves_field_in_interface(self):
        src = _plugin_source()
        assert "singleDispatchWaves" in src, "singleDispatchWaves field missing from MultitaskState"
        assert re.search(r"singleDispatchWaves\s*:\s*number", src), (
            "singleDispatchWaves must be typed as number in MultitaskState"
        )

    def test_single_dispatch_waves_in_fresh_state(self):
        src = _plugin_source()
        # The freshState() function must initialize to 0
        m = re.search(r"function\s+freshState.*?\{.*?\}", src, re.DOTALL)
        assert m, "freshState() not found"
        body = m.group(0)
        assert "singleDispatchWaves" in body, "singleDispatchWaves missing from freshState()"

    def test_single_dispatch_waves_reset_in_init_block(self):
        src = _plugin_source()
        # The IIFE init block must reset to 0
        assert src.count("singleDispatchWaves = 0") >= 2, (
            "singleDispatchWaves must be reset to 0 in both freshState() "
            "AND the _state init block"
        )

    def test_escalation_message_in_source(self):
        src = _plugin_source()
        assert "MESSAGE SHAPE VIOLATION" in src, (
            "MESSAGE SHAPE VIOLATION escalation message missing"
        )
        assert "3 consecutive single-dispatch waves" in src, (
            "escalation message must reference '3 consecutive single-dispatch waves'"
        )
        assert "Batch wider" in src, (
            "escalation message must say 'Batch wider'"
        )
        assert "2+ dispatches per message" in src, (
            "escalation message must say '2+ dispatches per message'"
        )

    def test_handle_message_boundary_increments_single_dispatch(self):
        src = _plugin_source()
        # handleMessageBoundary must contain prevMessageDispatches === 1 → increment
        assert re.search(r"prevMessageDispatches\s*===\s*1", src), (
            "handleMessageBoundary must check prevMessageDispatches === 1"
        )
        assert re.search(r"singleDispatchWaves\s*\+\+", src), (
            "handleMessageBoundary must increment singleDispatchWaves when prev === 1"
        )

    def test_handle_message_boundary_resets_on_2plus(self):
        src = _plugin_source()
        # handleMessageBoundary must contain prevMessageDispatches >= 2 → reset
        assert re.search(r"prevMessageDispatches\s*>=\s*2", src), (
            "handleMessageBoundary must check prevMessageDispatches >= 2"
        )
        assert re.search(r"singleDispatchWaves\s*=\s*0", src), (
            "handleMessageBoundary must reset singleDispatchWaves to 0 when prev >= 2"
        )

    def test_escalation_fires_at_3(self):
        src = _plugin_source()
        assert re.search(r"singleDispatchWaves\s*>=\s*3", src), (
            "Escalation must fire when singleDispatchWaves >= 3"
        )

    def test_proxy_delegates_escalation_to_hot_or_default_impl(self):
        src = _plugin_source()
        # Enforcement lives once in defaultImpl/handleTextComplete. The lean
        # proxy selects the hot implementation or that compiled-in fallback.
        default_section = src.split("defaultImpl")[1].split("PROXY PLUGIN")[0]
        proxy_section = src.split("PROXY PLUGIN")[1]

        assert "MESSAGE SHAPE VIOLATION" in default_section, (
            "MT.2 escalation missing from defaultImpl"
        )
        assert 'loadHotModule("multitask", defaultImpl)' in proxy_section
        assert 'impl["experimental.text.complete"]' in proxy_section

    def test_escalation_appears_in_thin_wave_block_and_post_boundary(self):
        src = _plugin_source()
        # Count occurrences of the MESSAGE SHAPE VIOLATION phrase.
        # It appears in both behavior paths: the thin-wave block and the
        # post-boundary warning. The proxy delegates instead of duplicating it.
        count = src.count("MESSAGE SHAPE VIOLATION")
        assert count >= 2, (
            "MESSAGE SHAPE VIOLATION must appear in both behavior paths "
            "(thin-wave + post-boundary), "
            f"found {count}"
        )

    def test_single_dispatch_waves_not_incremented_on_zero(self):
        src = _plugin_source()
        # The code must NOT increment singleDispatchWaves when prev === 0
        # (that would conflate zero-dispatch with single-dispatch violations).
        handle_body = src.split("handleMessageBoundary")[1].split("\n  }")[0]
        # After incrementing on === 1, the next branch must be >= 2 (not >= 0 or > 0)
        # The zero case falls through with no action — verify that.
        inc_line = re.search(r"if\s*\(.*prevMessageDispatches\s*===\s*1.*\)\s*\{", handle_body)
        reset_line = re.search(r"\}\s*else\s+if\s*\(.*prevMessageDispatches\s*>=\s*2.*\)", handle_body)
        assert inc_line, "prevMessageDispatches === 1 check must exist"
        assert reset_line, "prevMessageDispatches >= 2 check must exist"
        # Verify there's no check for === 0 that increments singleDispatchWaves
        zero_inc = re.search(
            r"prevMessageDispatches\s*===\s*0[^}]*singleDispatchWaves\s*\+\+",
            handle_body,
        )
        assert not zero_inc, (
            "singleDispatchWaves must NOT be incremented when prev === 0"
        )
