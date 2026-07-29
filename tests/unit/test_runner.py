"""Import-boundary regressions for the cross-platform TUI runner."""

from __future__ import annotations

import general_ludd.tui.runner as runner


def test_runner_keeps_posix_terminal_modules_lazy() -> None:
    """Portable commands must import the runner without loading POSIX modules."""
    assert callable(runner.run_tui)
    assert "termios" not in runner.__dict__
    assert "tty" not in runner.__dict__
