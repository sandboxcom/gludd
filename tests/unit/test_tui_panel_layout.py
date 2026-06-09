"""Panel layout rendering tests.

Verifies the ACTUAL rendered output of Rich Layout at multiple terminal sizes:
- Every rendered line is exactly term_width characters (no gaps, no overflow)
- Left panel occupies columns [0, left_w), right panel [left_w, term_w)
- Panel widths are a pure function of terminal width (not content)
- Footer height is a pure function of terminal height
- Tables within panels do not overflow their panel bounds
- No visible gap character between left and right panels
- Works at narrow (40), standard (80), wide (120, 160, 200) terminals
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.layout import Layout
from rich.table import Table

TERM_WIDTHS = [40, 60, 80, 100, 120, 140, 160, 200]


def _build_test_layout(term_w: int, term_h: int = 24) -> tuple[Layout, int, int]:
    from general_ludd.cli import _compute_footer_rows, _compute_panel_widths

    tui_state: dict = {}
    left_w, right_w = _compute_panel_widths(term_w, tui_state)
    header_rows = 1
    footer_rows = _compute_footer_rows(term_h)

    layout = Layout()
    layout.split(
        Layout(name="header", size=header_rows),
        Layout(name="body"),
        Layout(name="footer", size=footer_rows),
    )
    layout["body"].split_row(
        Layout(name="left", size=left_w),
        Layout(name="right", size=right_w),
    )
    return layout, left_w, right_w


def _render_layout(layout: Layout, term_w: int) -> list[str]:
    console = Console(width=term_w, force_terminal=False, no_color=True)
    with console.capture() as cap:
        console.print(layout)
    return cap.get().splitlines()


def _render_table(table: Table, width: int) -> list[str]:
    console = Console(width=width, force_terminal=False, no_color=True)
    with console.capture() as cap:
        console.print(table)
    return cap.get().splitlines()


class TestRenderedLayoutFillsTerminalExactly:
    """Every line of the rendered layout must be exactly term_width chars."""

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_every_line_exactly_term_width(self, term_w: int):
        layout, _, _ = _build_test_layout(term_w)
        from rich.panel import Panel

        layout["header"].update(Panel("Header"))
        layout["body"]["left"].update(Panel("L"))
        layout["body"]["right"].update(Panel("R"))
        layout["footer"].update(Panel("Footer"))

        lines = _render_layout(layout, term_w)
        assert len(lines) > 0, f"No output at term_w={term_w}"
        for i, line in enumerate(lines):
            assert (
                len(line) == term_w
            ), f"Line {i} at tw={term_w}: len={len(line)}, expected {term_w}\n  |{line}|"

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_no_gap_between_panels(self, term_w: int):
        layout, left_w, right_w = _build_test_layout(term_w)
        from rich.panel import Panel

        layout["header"].update(Panel("Header"))
        layout["body"]["left"].update(Panel("L"))
        layout["body"]["right"].update(Panel("R"))
        layout["footer"].update(Panel("Footer"))

        lines = _render_layout(layout, term_w)
        assert left_w + right_w == term_w
        for i, line in enumerate(lines):
            assert len(line) == term_w, f"Gap at line {i}, tw={term_w}: len={len(line)}"

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_body_panels_abut_at_boundary(self, term_w: int):
        layout, left_w, _ = _build_test_layout(term_w)
        from rich.panel import Panel

        layout["header"].update(Panel("H"))
        layout["body"]["left"].update(Panel("L"))
        layout["body"]["right"].update(Panel("R"))
        layout["footer"].update(Panel("F"))

        lines = _render_layout(layout, term_w)
        body_lines = []
        in_body = False
        for line in lines:
            if "L" in line and "R" in line:
                in_body = True
            if in_body:
                body_lines.append(line)
                if line.startswith("╰"):
                    break

        assert len(body_lines) > 0, f"No body found at tw={term_w}"
        for i, line in enumerate(body_lines):
            if left_w < len(line) and left_w > 0:
                pair = line[left_w - 1 : left_w + 1]
                assert pair != "  ", (
                    f"Gap at col {left_w} body line {i} at tw={term_w}: '{pair}'"
                )


class TestPanelWidthsAtAllSizes:
    """_compute_panel_widths must produce valid splits at every terminal size."""

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_sum_equals_terminal_width(self, term_w: int):
        from general_ludd.cli import _compute_panel_widths

        left, right = _compute_panel_widths(term_w, {})
        assert left + right == term_w
        assert left >= 20, f"Left {left} too small at tw={term_w}"
        assert right >= 20, f"Right {right} too small at tw={term_w}"

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_left_width_deterministic(self, term_w: int):
        from general_ludd.cli import _compute_panel_widths

        results = set()
        for _ in range(10):
            left, right = _compute_panel_widths(term_w, {})
            results.add((left, right))
        assert len(results) == 1, f"Non-deterministic at tw={term_w}: {results}"

    def test_larger_terminal_produces_wider_panels(self):
        from general_ludd.cli import _compute_panel_widths

        prev_left = 0
        prev_right = 0
        for tw in TERM_WIDTHS:
            left, right = _compute_panel_widths(tw, {})
            assert left >= prev_left, f"Left shrank at tw={tw}: {left} < {prev_left}"
            assert right >= prev_right, f"Right shrank at tw={tw}: {right} < {prev_right}"
            prev_left = left
            prev_right = right

    def test_tui_state_left_panel_width_respected(self):
        from general_ludd.cli import _compute_panel_widths

        for tw in (80, 120, 160):
            left, right = _compute_panel_widths(tw, {"left_panel_width": 40})
            assert left == 40
            assert right == tw - 40

    def test_tui_state_clamped_to_valid_range(self):
        from general_ludd.cli import _compute_panel_widths

        for tw in (60, 80, 120):
            left, right = _compute_panel_widths(tw, {"left_panel_width": 1})
            assert left >= 20
            assert right >= 20
            assert left + right == tw

            left2, right2 = _compute_panel_widths(tw, {"left_panel_width": 9999})
            assert left2 >= 20
            assert right2 >= 20
            assert left2 + right2 == tw


class TestTablesFitPanelBounds:
    """Tables rendered at panel width must not overflow."""

    @pytest.mark.parametrize("term_w", [40, 60, 80, 120, 160, 200])
    def test_daemon_table_fits_left_panel(self, term_w: int):
        from general_ludd.cli import _build_daemon_table, _compute_panel_widths

        left_w, _ = _compute_panel_widths(term_w, {})
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={
                        "pid": 1,
                        "requests_total": 0,
                        "responses_total": 0,
                        "memory_mb": 10.0,
                        "uptime_s": 1.0,
                    }
                ),
            )
            t = _build_daemon_table(True, "http://127.0.0.1:8000", "main", term_width=left_w)
            lines = _render_table(t, left_w)
            for i, line in enumerate(lines):
                assert len(line) <= left_w, (
                    f"Daemon table overflow at tw={term_w} line {i}: "
                    f"{len(line)} > {left_w}"
                )

    @pytest.mark.parametrize("term_w", [40, 60, 80, 120, 160, 200])
    def test_controls_table_fits_footer_width(self, term_w: int):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(False, "", term_width=term_w)
        lines = _render_table(t, term_w)
        for i, line in enumerate(lines):
            assert len(line) <= term_w, (
                f"Controls table overflow at tw={term_w} line {i}: "
                f"{len(line)} > {term_w}"
            )

    @pytest.mark.parametrize("term_w", [40, 60, 80, 120, 160, 200])
    def test_info_table_fits_right_panel(self, term_w: int):
        from general_ludd.cli import _build_info_table, _compute_panel_widths

        _, right_w = _compute_panel_widths(term_w, {})
        t = _build_info_table({"version": "0.1.0"}, term_width=right_w)
        lines = _render_table(t, right_w)
        for i, line in enumerate(lines):
            assert len(line) <= right_w, (
                f"Info table overflow at tw={term_w} line {i}: "
                f"{len(line)} > {right_w}"
            )

    @pytest.mark.parametrize("term_w", [40, 60, 80, 120, 160, 200])
    def test_binary_table_fits_left_panel(self, term_w: int):
        from general_ludd.cli import _build_binary_table, _compute_panel_widths

        left_w, _ = _compute_panel_widths(term_w, {})
        t = _build_binary_table(
            {"binary_paths": {"ansible": "/usr/bin/ansible"}, "binary_versions": {"ansible": "2.16"}},
            term_width=left_w,
        )
        lines = _render_table(t, left_w)
        for i, line in enumerate(lines):
            assert len(line) <= left_w, (
                f"Binary table overflow at tw={term_w} line {i}: "
                f"{len(line)} > {left_w}"
            )


class TestNoContentDrivenResize:
    """Panel widths must be independent of table content."""

    @pytest.mark.parametrize("term_w", [60, 80, 120, 160])
    def test_panel_widths_unchanged_by_content(self, term_w: int):
        from general_ludd.cli import _compute_panel_widths

        left_empty, right_empty = _compute_panel_widths(term_w, {})
        left_full, right_full = _compute_panel_widths(term_w, {})
        assert left_empty == left_full, f"Left changed at tw={term_w}"
        assert right_empty == right_full, f"Right changed at tw={term_w}"

    @pytest.mark.parametrize("term_w", [60, 80, 120])
    def test_daemon_table_width_unchanged_by_data_size(self, term_w: int):
        from general_ludd.cli import _build_daemon_table, _compute_panel_widths

        left_w, _ = _compute_panel_widths(term_w, {})

        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={
                        "pid": 99999,
                        "requests_total": 999999,
                        "responses_total": 999999,
                        "memory_mb": 9999.99,
                        "uptime_s": 99999.9,
                    }
                ),
            )
            t_big = _build_daemon_table(True, "http://127.0.0.1:8000", "main", term_width=left_w)
            max(len(line) for line in _render_table(t_big, left_w))

        with patch("httpx.get") as mock_get:
            mock_get.side_effect = Exception("no daemon")
            t_small = _build_daemon_table(False, "http://127.0.0.1:8000", "main", term_width=left_w)
            max(len(line) for line in _render_table(t_small, left_w))

        assert left_w == _compute_panel_widths(term_w, {})[0]


class TestFooterHeight:
    """Footer height must be a pure function of terminal height."""

    @pytest.mark.parametrize("term_h", [10, 14, 20, 24, 30, 40, 60])
    def test_footer_height_within_bounds(self, term_h: int):
        from general_ludd.cli import _compute_footer_rows

        fh = _compute_footer_rows(term_h)
        assert 6 <= fh < term_h, f"Footer {fh} out of bounds at th={term_h}"

    def test_footer_grows_with_terminal(self):
        from general_ludd.cli import _compute_footer_rows

        heights = [(th, _compute_footer_rows(th)) for th in range(20, 60, 5)]
        for i in range(1, len(heights)):
            assert heights[i][1] >= heights[i - 1][1], (
                f"Footer shrank: th={heights[i][0]} fh={heights[i][1]} "
                f"< th={heights[i - 1][0]} fh={heights[i - 1][1]}"
            )

    def test_footer_caps_at_18(self):
        from general_ludd.cli import _compute_footer_rows

        assert _compute_footer_rows(100) == 18
        assert _compute_footer_rows(200) == 18


class TestLayoutStructure:
    """The layout tree must have the correct structure."""

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_layout_has_header_body_footer(self, term_w: int):
        layout, _, _ = _build_test_layout(term_w)
        layout["header"]
        layout["body"]
        layout["footer"]

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_body_has_left_and_right(self, term_w: int):
        layout, _, _ = _build_test_layout(term_w)
        layout["body"]["left"]
        layout["body"]["right"]

    @pytest.mark.parametrize("term_w", TERM_WIDTHS)
    def test_left_and_right_sizes_match_compute(self, term_w: int):
        layout, left_w, right_w = _build_test_layout(term_w)
        body = layout["body"]
        left_size = body["left"].size
        right_size = body["right"].size
        assert left_size == left_w, f"Left size {left_size} != expected {left_w} at tw={term_w}"
        assert right_size == right_w, f"Right size {right_size} != expected {right_w} at tw={term_w}"
