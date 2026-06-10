"""Tests for Slurm TUI view and keybinding wiring."""
from __future__ import annotations

from typing import Any

from general_ludd.tui.keybindings import TUIKeyHandler


def _make_handler() -> tuple[TUIKeyHandler, dict[str, Any]]:
    state: dict[str, Any] = {
        "current_view": "main",
        "daemon_running": False,
        "status_msg": "",
        "daemon_url": "http://localhost:8000",
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "dispatch_mode": "active",
        "ansible_search_results": [],
    }
    return TUIKeyHandler(state), state


class TestSlurmViewKeybinding:
    def test_slurm_view_toggle(self):
        handler, state = _make_handler()
        handler.handle_key("L")
        assert state["current_view"] == "slurm"

    def test_slurm_view_exit(self):
        handler, state = _make_handler()
        state["current_view"] = "slurm"
        handler.handle_key("L")
        assert state["current_view"] == "main"


class TestSlurmTableBuilder:
    def test_build_slurm_table_empty(self):
        from general_ludd.cli import _build_slurm_table
        t = _build_slurm_table([])
        assert t is not None
        assert t.title == "Slurm Jobs"

    def test_build_slurm_table_with_jobs(self):
        from general_ludd.cli import _build_slurm_table
        jobs = [
            {"job_id": "12345", "state": "RUNNING", "exit_code": None},
            {"job_id": "12346", "state": "COMPLETED", "exit_code": 0},
        ]
        t = _build_slurm_table(jobs)
        assert t.row_count == 2

    def test_build_slurm_table_no_unbounded_columns(self):
        from general_ludd.cli import _build_slurm_table
        t = _build_slurm_table([])
        for col in t.columns:
            assert col.max_width is not None
