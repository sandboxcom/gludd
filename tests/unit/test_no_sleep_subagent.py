"""Guard against dispatched subagents that waste floor slots with sleep-in-poll-loops.

Per AGENTS.md "CI-Poll Subagents Are Forbidden" (2026-07-08) and
"Background Operations NEVER Block Dispatch" (2026-07-06): a dispatched
subagent that loops with sleep (poll CI until terminal, poll gate-status
every N seconds, etc.) holds a subagent slot and the orchestrator's
attention for 30-40 minutes while producing zero value.

This test scans scripts/ for .py and .sh files that contain both ``sleep``
and a loop construct (``while`` / ``for``).  A file that matches and is
NOT in the ALLOWLIST causes a test failure — it is a potential
sleep-in-poll-loop script that should either be allowlisted (with
documented justification) or refactored to remove the sleep-in-loop.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

ALLOWLIST = frozenset({
    # Daemon/watchdog infrastructure — legitimate sleep-in-loop for background polling.
    "agent_watchdog.py",
    "task_watchdog.py",
    "azure_event_guard.sh",
    # Background test runners supervise child processes and emit visible heartbeats.
    "heavy_sem.py",
    "run_test_background.sh",
    "run_ci_shards_parallel.py",
    "token_window_monitor.py",
    # Bounded service-readiness loop; it never occupies a delegated agent slot.
    "smoke_daemon.py",
    # Release-only CI observers are bounded, emit heartbeats, and remain blocked
    # from delegated-agent prompts by enforce-no-wait.ts.
    "ci_annotations_poll.py",
    "ci_await.py",
    "ci_poll.py",
    "ci_push_and_verify.sh",
})


class _PythonSleepInLoopVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loop_depth = 0
        self.found = False

    def _visit_loop(self, node: ast.AST) -> None:
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        is_sleep = (
            isinstance(function, ast.Name) and function.id == "sleep"
        ) or (
            isinstance(function, ast.Attribute) and function.attr == "sleep"
        )
        if self.loop_depth and is_sleep:
            self.found = True
        self.generic_visit(node)


def _python_has_sleep_in_loop(text: str, file_path: Path) -> bool:
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        relative_path = file_path.relative_to(REPO_ROOT)
        raise AssertionError(
            f"Source file is not valid Python: {relative_path}"
        ) from exc
    visitor = _PythonSleepInLoopVisitor()
    visitor.visit(tree)
    return visitor.found


def _shell_has_sleep_in_loop(text: str) -> bool:
    loop_depth = 0
    pending_loop = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if not line:
            continue
        if re.match(r"^(for|while|until)\b", line):
            pending_loop = True
        if pending_loop and re.search(r"(?:^|;)\s*do(?:\s|;|$)", line):
            loop_depth += 1
            pending_loop = False
        if loop_depth and re.search(r"(?:^|[;&|()])\s*sleep(?:\s|$)", line):
            return True
        if loop_depth and re.match(r"^done(?:\s|;|$)", line):
            loop_depth -= 1
    return False


def _has_sleep_in_loop(file_path: Path) -> bool:
    """Return True only when an executable sleep call is nested in a loop."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        relative_path = file_path.relative_to(REPO_ROOT)
        raise AssertionError(
            f"Source file is not valid UTF-8: {relative_path}"
        ) from exc
    if file_path.suffix == ".py":
        return _python_has_sleep_in_loop(text, file_path)
    return _shell_has_sleep_in_loop(text)


def _collect_sleep_in_loop_files() -> frozenset[str]:
    result: set[str] = set()
    for ext in ("*.py", "*.sh"):
        for file_path in sorted(SCRIPTS_DIR.glob(ext)):
            if _has_sleep_in_loop(file_path):
                result.add(file_path.name)
    return frozenset(result)


def test_allowlisted_files_exist() -> None:
    """Every file in ALLOWLIST must exist in scripts/."""
    missing = [f for f in ALLOWLIST if not (SCRIPTS_DIR / f).is_file()]
    assert not missing, (
        f"ALLOWLIST entries do not exist in scripts/: {', '.join(sorted(missing))}"
    )


def test_no_unexpected_sleep_in_loop_files() -> None:
    """Only allowlisted files may contain sleep inside a loop construct."""
    found = _collect_sleep_in_loop_files()
    unexpected = found - ALLOWLIST
    assert not unexpected, (
        "Unexpected sleep-in-loop files in scripts/ (not in ALLOWLIST):\n"
        + "\n".join(f"  - {f}" for f in sorted(unexpected))
        + "\n\nEither add to ALLOWLIST with documented justification in the test file,"
        + "\nor refactor to remove the sleep-in-poll-loop anti-pattern."
    )


def test_allowlist_entries_are_still_valid() -> None:
    """Allowlisted files that exist but no longer match the pattern should be
    re-evaluated — the test flags them as stale."""
    found = _collect_sleep_in_loop_files()
    stale = ALLOWLIST - found
    assert not stale, (
        "ALLOWLIST entries no longer contain sleep-in-loop —"
        " remove them from ALLOWLIST:\n"
        + "\n".join(f"  - {f}" for f in sorted(stale))
    )


def test_python_scan_ignores_sleep_words_outside_loops(tmp_path: Path) -> None:
    script = tmp_path / "false_positive.py"
    script.write_text(
        'RULE = "never sleep"\nfor item in ():\n    print(item)\n',
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is False


def test_python_scan_detects_sleep_call_inside_loop(tmp_path: Path) -> None:
    script = tmp_path / "poller.py"
    script.write_text(
        "import time\nwhile True:\n    time.sleep(1)\n",
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is True


def test_shell_scan_ignores_sleep_outside_loop(tmp_path: Path) -> None:
    script = tmp_path / "false_positive.sh"
    script.write_text(
        'echo "sleep"\nfor item in one two; do\n  echo "$item"\ndone\n',
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is False


def test_shell_scan_detects_sleep_inside_loop(tmp_path: Path) -> None:
    script = tmp_path / "poller.sh"
    script.write_text(
        "while true; do\n  sleep 1\ndone\n",
        encoding="utf-8",
    )
    assert _has_sleep_in_loop(script) is True
