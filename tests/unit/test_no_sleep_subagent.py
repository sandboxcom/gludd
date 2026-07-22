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

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

ALLOWLIST = frozenset({
    # Daemon/watchdog infrastructure — legitimate sleep-in-loop for background polling
    "agent_watchdog.py",
    "task_watchdog.py",
    "agent_liveness.py",
    # Background test runner — legitimate polling for test completion
    "heavy_sem.py",
    "run_test_background.sh",
    "token_window_monitor.py",
    # CI polling helpers: top-level make-driven operators, not subagent tasks.
    "ci_annotations_poll.py",
    "ci_await.py",
    "ci_poll.py",
    "ci_push_and_verify.sh",
    # Batch generation and runtime hook harnesses deliberately throttle loops.
    "generate_specs_expansion.py",
    "generate_specs_to_4000.py",
    "test_hook_runtime.py",
})


def _has_sleep_in_loop(file_path: Path) -> bool:
    """Return True if the file contains both a sleep call and a loop construct."""
    text = file_path.read_text()
    has_sleep = "sleep" in text.lower()
    if not has_sleep:
        return False
    has_loop = bool(re.search(r"\b(while|for)\b", text))
    return has_loop


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
