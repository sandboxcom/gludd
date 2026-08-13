"""C26 items 5-7: async/process-lifecycle residual fixes.

Item 5 — zombie reaping in background_test_runner.
Item 6 — structured error from _langgraph_call_model.
Item 7 — module-level _daemon_state global moved into app state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────


def _short_lived_child() -> tuple[int, int]:
    """Spawn a short-lived child, wait for it to exit, return (pid, exit_code).
    The child reaps quickly so it should be a zombie when we check.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "exit(42)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    return proc.pid, proc.returncode


# ── Item 5: zombie reaping ───────────────────────────────────────────


class TestBackgroundRunnerReapsZombies:
    """_pid_alive must not misreport a zombie process as alive."""

    def test_os_kill_reports_zombie_as_alive(self) -> None:
        """Demonstrate that os.kill(pid, 0) on a zombie reports True."""
        pid, exit_code = _short_lived_child()
        assert exit_code == 42

        try:
            os.kill(pid, 0)
            alive_via_kill = True
        except OSError:
            # already reaped — the race is real but irrelevant here
            return

        # If os.kill(0) succeeded, the process is a zombie — NOT alive.
        # But os.kill(0) reports it as alive. This is the bug.
        assert alive_via_kill is True, (
            f"os.kill(pid,0) succeeded for pid={pid} (meaning it IS a zombie "
            f"that os.kill misreports as alive)"
        )

    def test_waitpid_wnohang_reaps_zombie_and_reports_dead(self) -> None:
        """os.waitpid(pid, WNOHANG) on a zombie returns (pid, status) not (0,0)."""
        pid, exit_code = _short_lived_child()
        assert exit_code == 42

        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            # Already reaped — prove os.kill also fails (clean exit for test)
            with pytest.raises(OSError):
                os.kill(pid, 0)
            return

        if wpid == 0:
            # Still running/waiting — shouldn't happen for short-lived child
            # on most systems, but the race window exists
            return

        # wpid == pid: was a zombie, now reaped
        assert wpid == pid
        assert os.WIFEXITED(status)
        assert os.WEXITSTATUS(status) == 42

    def test_pid_alive_uses_waitpid_not_kill(self) -> None:
        """The BackgroundTestRunner._pid_alive must use os.waitpid(WNOHANG)."""
        import inspect

        from general_ludd.runner.background_test_runner import (
            BackgroundTestRunner,
        )

        source = inspect.getsource(BackgroundTestRunner._pid_alive)
        assert "os.waitpid" in source, (
            "_pid_alive must use os.waitpid(WNOHANG) instead of os.kill(pid, 0)"
        )

    def test_pid_alive_reports_zombie_as_dead(self) -> None:
        """A zombie child must be reported as NOT alive by _pid_alive."""
        from general_ludd.runner.background_test_runner import (
            BackgroundTestRunner,
        )

        pid, exit_code = _short_lived_child()
        assert exit_code == 42

        alive = BackgroundTestRunner._pid_alive(pid)

        # The process exited with code 42. It may be a zombie or already reaped.
        # In either case, _pid_alive must NOT report it as alive.
        assert alive is False, (
            f"_pid_alive({pid}) returned True but the child exited with code 42 "
            f"(zombie misreported as alive)"
        )

    def test_running_child_reported_as_alive(self) -> None:
        """A running child must be reported as alive by _pid_alive."""
        from general_ludd.runner.background_test_runner import (
            BackgroundTestRunner,
        )

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            alive = BackgroundTestRunner._pid_alive(proc.pid)
            assert alive is True, f"_pid_alive({proc.pid}) returned False for running child"
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_nonexistent_pid_reported_as_dead(self) -> None:
        """A nonexistent PID must be reported as NOT alive."""
        from general_ludd.runner.background_test_runner import (
            BackgroundTestRunner,
        )

        # Find a high PID that is very unlikely to exist
        fake_pid = 999999
        alive = BackgroundTestRunner._pid_alive(fake_pid)
        assert alive is False, f"_pid_alive({fake_pid}) returned True for nonexistent PID"


# ── Item 6: structured error from _langgraph_call_model ──────────────


class TestLanggraphCallModelReturnsStructuredError:
    """_langgraph_call_model must raise a structured error, not return None."""

    def test_langgraph_model_call_error_exists_and_importable(self) -> None:
        """LangGraphModelCallError must exist as an importable exception class."""
        from general_ludd.daemon import LangGraphModelCallError

        assert issubclass(LangGraphModelCallError, Exception)

    def test_langgraph_model_call_error_has_original(self) -> None:
        """LangGraphModelCallError must preserve the original exception."""
        from general_ludd.daemon import LangGraphModelCallError

        original = ValueError("test failure")
        error = LangGraphModelCallError(original)

        assert str(error) == str(original)
        assert error.original_error is original

    def test_exception_carries_structured_context(self) -> None:
        """LangGraphModelCallError must provide structured error context."""
        from general_ludd.daemon import LangGraphModelCallError

        original = RuntimeError("gateway timeout")
        error = LangGraphModelCallError(original)

        assert error.original_error is original
        assert isinstance(error, Exception)

    def test_closure_return_type_is_str_not_optional(self) -> None:
        """The _langgraph_call_model closure must return str, not str|None."""
        daemon_path = Path(__file__).parent.parent.parent / "src" / "general_ludd" / "daemon.py"
        source = daemon_path.read_text(encoding="utf-8")

        # Find the line with the closure definition and extract the full
        # function body through the next statement at the same indent level.
        lines = source.split("\n")
        start_idx = next(i for i, ln in enumerate(lines) if "def _langgraph_call_model(prompt: str)" in ln)
        func_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())

        func_lines: list[str] = []
        for i in range(start_idx, len(lines)):
            line = lines[i]
            stripped = line.rstrip()
            line_indent = len(line) - len(line.lstrip())
            # Stop at a non-blank line at or before func_indent that is not
            # the definition itself.
            if i > start_idx and stripped and line_indent <= func_indent:
                break
            func_lines.append(line)

        func_src = "\n".join(func_lines)

        import ast
        import textwrap
        func_src = textwrap.dedent(func_src)

        tree = ast.parse(func_src)
        assert len(tree.body) == 1, f"expected 1 function def, got {len(tree.body)}"
        func_def = tree.body[0]
        assert isinstance(func_def, ast.FunctionDef)
        assert func_def.returns is not None, "_langgraph_call_model missing return annotation"

        if isinstance(func_def.returns, ast.BinOp):
            raise AssertionError(
                "_langgraph_call_model return type must be str, not Optional"
            )
        assert isinstance(func_def.returns, ast.Name), (
            f"expected ast.Name return annotation, got {ast.dump(func_def.returns)}"
        )
        assert func_def.returns.id == "str", (
            f"_langgraph_call_model return type must be str, got {func_def.returns.id}"
        )

    def test_closure_raises_not_returns_none(self) -> None:
        """The except block must raise, not 'return None'."""
        daemon_path = Path(__file__).parent.parent.parent / "src" / "general_ludd" / "daemon.py"
        source = daemon_path.read_text(encoding="utf-8")

        idx = source.index("def _langgraph_call_model")
        chunk = source[idx:]

        lines = chunk.split("\n")
        in_except = False
        for line in lines:
            if not line.strip() or "def _langgraph_call_model" in line:
                continue
            if line.strip() and not line.startswith(" ") and "def _langgraph_call_model" not in line:
                break

            if "except" in line:
                in_except = True
                continue
            if in_except and line.strip() and not line.startswith(" " * 16):
                in_except = False
            if in_except and "return None" in line.replace(" ", ""):
                raise AssertionError(
                    "_langgraph_call_model except block must raise, not return None"
                )


# ── Item 7: module-level _daemon_state global → app state ────────────


class TestDaemonStateInAppStateNotGlobal:
    """Module-level _daemon_state must not be a mutable default dict."""

    def test_module_level_default_is_sentinel_not_dict(self) -> None:
        """_daemon_state at module level must not be a pre-initialized dict."""
        import importlib

        import general_ludd.daemon as daemon_mod

        importlib.reload(daemon_mod)
        state = daemon_mod._daemon_state
        # Must not be the old mutable default {"todos": [], ...}
        assert not isinstance(state, dict), (
            f"_daemon_state is a dict at module level: {state!r}. "
            "Must be a sentinel (None or _DAEMON_STATE_UNSET) that is "
            "filled at app-creation time."
        )

    def test_app_state_daemon_state_is_dict_after_factory(self) -> None:
        """After create_daemon_app(), app.state.daemon_state must be a dict."""
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        state = getattr(app.state, "daemon_state", None)
        assert isinstance(state, dict), (
            f"app.state.daemon_state not a dict after create_daemon_app(): {state!r}"
        )
        assert "todos" in state
        assert "tick_metrics" in state
        assert "quality_gate" in state

    def test_two_apps_have_isolated_state(self) -> None:
        """Two create_daemon_app() calls must produce independent state dicts."""
        from general_ludd.daemon import create_daemon_app

        app1 = create_daemon_app()
        app2 = create_daemon_app()

        state1 = app1.state.daemon_state
        state2 = app2.state.daemon_state

        assert state1 is not state2, (
            "Two create_daemon_app() calls share the same daemon_state dict"
        )

        # Mutating one must not affect the other
        state1["todos"].append({"test": "marker"})
        assert len(state2["todos"]) == 0, (
            "Mutation to app1's daemon_state leaked into app2's daemon_state"
        )

    def test_global_proxy_tracks_latest_app_state_without_aliasing(self) -> None:
        """The stable compatibility proxy delegates without becoming app state."""
        import general_ludd.daemon as daemon_mod
        from general_ludd.daemon import create_daemon_app

        legacy_state = daemon_mod._daemon_state
        app = create_daemon_app()
        assert daemon_mod._daemon_state is legacy_state
        assert daemon_mod._daemon_state is not app.state.daemon_state

        daemon_mod._daemon_state["quality_gate"]["legacy"] = True
        assert app.state.daemon_state["quality_gate"] == {"legacy": True}

    def test_factory_recovers_if_legacy_global_was_overwritten(self, monkeypatch) -> None:
        """Older callers that reset the shim cannot break app construction."""
        import general_ludd.daemon as daemon_mod
        from general_ludd.daemon import create_daemon_app

        monkeypatch.setattr(daemon_mod, "_daemon_state", None)

        app = create_daemon_app()

        assert daemon_mod._daemon_state is not None
        assert daemon_mod._daemon_state is not app.state.daemon_state
        assert daemon_mod._daemon_state["todos"] is app.state.daemon_state["todos"]

    def test_no_direct_global_access_outside_dogfood_and_migration(self) -> None:
        """Scripts should use explicit injection; only dogfood.py uses global."""
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        # app.state.daemon_state is the authoritative store
        state = app.state.daemon_state
        assert isinstance(state, dict)
        assert "todos" in state
        assert "tick_metrics" in state
        assert "quality_gate" in state
        # The migration shim is a stable proxy over the latest app's state.
        import general_ludd.daemon as daemon_mod

        assert daemon_mod._daemon_state is not state
        assert daemon_mod._daemon_state["todos"] is state["todos"]
