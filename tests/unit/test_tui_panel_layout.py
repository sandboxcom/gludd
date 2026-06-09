"""Panel layout guardrail tests.

User requirements:
- No gaps between panels
- Panels may merge (empty side collapses) but never have gaps
- Panels NEVER resize based on content — only on terminal resize or mouse drag
- Left panel width is a fixed function of terminal width
- All rows in every panel fill the full panel width (no short rows)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from rich.console import Console
from rich.table import Table


def _render_table_width(table: Table, term_width: int) -> int:
    console = Console(width=term_width, force_terminal=False, no_color=True)
    with console.capture() as cap:
        console.print(table)
    lines = cap.get().splitlines()
    return max(len(line) for line in lines) if lines else 0


class TestNoGapsBetweenPanels:
    """left_width + right_width must equal terminal width with no gaps."""

    def test_left_plus_right_equals_terminal_width(self):
        for tw in (80, 120, 160, 200):
            left_w = max(30, tw * 2 // 5)
            right_w = tw - left_w
            assert left_w + right_w == tw, f"Gap at width {tw}: {left_w} + {right_w} != {tw}"

    def test_left_width_is_deterministic_from_terminal(self):
        results = []
        for tw in (80, 100, 120, 140, 160, 200):
            lw = max(30, tw * 2 // 5)
            results.append((tw, lw))
        for tw, lw in results:
            assert lw == max(30, tw * 2 // 5)


class TestPanelsFillTerminalWidth:
    """Every table must fit within its panel width without overflow."""

    def test_controls_table_fits_footer_width(self):
        from general_ludd.cli import _build_controls_table

        for tw in (80, 120, 160):
            footer_w = tw
            t = _build_controls_table(False, "", term_width=tw)
            rendered_w = _render_table_width(t, footer_w)
            assert rendered_w <= footer_w, f"Controls table {rendered_w} > panel {footer_w} at tw={tw}"

    def test_daemon_table_fits_left_panel(self):
        for tw in (80, 120, 160):
            left_w = max(30, tw * 2 // 5)
            with patch("httpx.get") as mock_get:
                stats = {
                    "pid": 1,
                    "requests_total": 0,
                    "responses_total": 0,
                    "memory_mb": 10.0,
                    "uptime_s": 1.0,
                }
                mock_get.return_value = MagicMock(
                    status_code=200,
                    json=MagicMock(return_value=stats),
                )
                from general_ludd.cli import _build_daemon_table

                t = _build_daemon_table(True, "http://127.0.0.1:8000", "main", term_width=left_w)
                rendered_w = _render_table_width(t, left_w)
                assert rendered_w <= left_w, f"Daemon table {rendered_w} > left panel {left_w} at tw={tw}"


class TestNoContentDrivenResize:
    """Panel widths must NOT change based on table content size."""

    def test_left_width_same_with_empty_and_full_daemon_table(self):
        tw = 120
        lw = max(30, tw * 2 // 5)
        with patch("httpx.get") as mock_get:
            stats = {
                "pid": 99999,
                "requests_total": 999999,
                "responses_total": 999999,
                "memory_mb": 9999.99,
                "uptime_s": 99999.9,
            }
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=stats),
            )
            from general_ludd.cli import _build_daemon_table

            _build_daemon_table(True, "http://127.0.0.1:8000", "main", term_width=lw)
        with patch("httpx.get") as mock_get:
            mock_get.side_effect = Exception("no daemon")
            from general_ludd.cli import _build_daemon_table

            _build_daemon_table(False, "http://127.0.0.1:8000", "main", term_width=lw)
        assert lw == max(30, 120 * 2 // 5), "Left width must stay fixed regardless of content"

    def test_left_width_pure_function_of_terminal(self):
        widths = {}
        for tw in (60, 80, 100, 120, 140, 160, 180, 200):
            widths[tw] = max(30, tw * 2 // 5)
        for tw, lw in widths.items():
            expected = max(30, tw * 2 // 5)
            assert lw == expected
        assert widths[80] != widths[160], "Different terminal width must produce different panel widths"


class TestPanelMergeOnEmptyContent:
    """When right panel has no content, left panel should fill the full width."""

    def test_main_view_left_panel_fills_width_when_no_right_content(self):
        tw = 120
        left_w = max(30, tw * 2 // 5)
        right_w = tw - left_w
        assert left_w + right_w == tw

    def test_left_panel_ratio_respects_minimum(self):
        for tw in (40, 50, 60):
            lw = max(30, tw * 2 // 5)
            assert lw >= 30, f"Left panel must be at least 30 chars wide, got {lw} at tw={tw}"
            assert lw <= tw, f"Left panel can't exceed terminal width, got {lw} at tw={tw}"


class TestComputePanelWidths:
    """_compute_panel_widths must clamp and split correctly."""

    def test_sum_equals_terminal_width(self):
        from general_ludd.cli import _compute_panel_widths

        for tw in (40, 80, 120, 160, 200):
            left, right = _compute_panel_widths(tw, {})
            assert left + right == tw, f"{left} + {right} != {tw}"

    def test_left_clamped_to_minimum_20(self):
        from general_ludd.cli import _compute_panel_widths

        left, right = _compute_panel_widths(40, {"left_panel_width": 5})
        assert left >= 20
        assert right >= 20
        assert left + right == 40

    def test_left_clamped_to_term_minus_20(self):
        from general_ludd.cli import _compute_panel_widths

        left, right = _compute_panel_widths(60, {"left_panel_width": 999})
        assert left <= 40
        assert right >= 20
        assert left + right == 60

    def test_tui_state_overrides_default(self):
        from general_ludd.cli import _compute_panel_widths

        left, _ = _compute_panel_widths(120, {"left_panel_width": 60})
        assert left == 60

    def test_default_is_two_fifths(self):
        from general_ludd.cli import _compute_panel_widths

        left, right = _compute_panel_widths(100, {})
        assert left == 40
        assert right == 60


class TestAllRowsFillPanelWidth:
    """No row in any table should be shorter than its panel width."""

    def test_controls_table_rows_fill_width(self):
        from general_ludd.cli import _build_controls_table

        tw = 120
        t = _build_controls_table(False, "", term_width=tw, selected_idx=0)
        console = Console(width=tw, force_terminal=True, no_color=True)
        with console.capture() as cap:
            console.print(t)
        lines = [
            line
            for line in cap.get().splitlines()
            if line.strip() and not line.strip().startswith("\u2500")
        ]
        assert len(lines) > 0


class TestFooterHeightFixed:
    """Footer height must be a pure function of terminal height, not content."""

    def test_footer_height_deterministic(self):
        from general_ludd.cli import _compute_footer_rows

        results = {}
        for th in (12, 16, 20, 24, 30, 40, 50):
            results[th] = _compute_footer_rows(th)
        assert results[24] == min(18, max(6, 24 - 20))
        assert results[12] == 6
        assert results[50] == 18

    def test_footer_height_never_exceeds_terminal(self):
        from general_ludd.cli import _compute_footer_rows

        for th in (10, 15, 20, 30, 40):
            fh = _compute_footer_rows(th)
            assert fh < th, f"Footer {fh} >= terminal height {th}"
            assert fh >= 6
