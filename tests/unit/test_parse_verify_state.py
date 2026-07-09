"""TDD tests for scripts/parse_verify_state.py.

Parses `make verify-state` output and exits with specific codes so pre-commit
hooks can reject commits that would land on a dirty tree, diverged remote, red
CI, or missing CI run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "parse_verify_state.py"


def _run_with_stdin(stdin: str) -> tuple[int, str, str]:
    """Pipe `stdin` into parse_verify_state.py, return (code, stdout, stderr)."""
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


CLEAN_SYNCED_GREEN = """\
=== GLUDD STATE REPORT 2026-07-09T12:00:00Z ===

--- Working Tree ---
CLEAN

--- HEAD ---
Local:  abc123def456789012345678901234567890abcd
Branch: master

--- Remote ---
SYNCED: abc123def456789012345678901234567890abcd

--- Recent Commits ---
abc123d feat: something

--- CI ---
GREEN: run 1234567890

=== END STATE REPORT ===
"""

DIRTY = """\
=== GLUDD STATE REPORT 2026-07-09T12:00:00Z ===

--- Working Tree ---
DIRTY (2 files):
 M scripts/foo.py
?? scripts/bar.py

--- HEAD ---
Local:  abc123def456789012345678901234567890abcd
Branch: master

--- Remote ---
SYNCED: abc123def456789012345678901234567890abcd

--- Recent Commits ---
abc123d feat: something

--- CI ---
GREEN: run 1234567890

=== END STATE REPORT ===
"""

DIVERGED = """\
=== GLUDD STATE REPORT 2026-07-09T12:00:00Z ===

--- Working Tree ---
CLEAN

--- HEAD ---
Local:  abc123def456789012345678901234567890abcd
Branch: master

--- Remote ---
DIVERGED: local=abc123def456 remote=789012345678

--- Recent Commits ---
abc123d feat: something

--- CI ---
GREEN: run 1234567890

=== END STATE REPORT ===
"""

UNREACHABLE = """\
=== GLUDD STATE REPORT 2026-07-09T12:00:00Z ===

--- Working Tree ---
CLEAN

--- HEAD ---
Local:  abc123def456789012345678901234567890abcd
Branch: master

--- Remote ---
UNREACHABLE

--- Recent Commits ---
abc123d feat: something

--- CI ---
GREEN: run 1234567890

=== END STATE REPORT ===
"""

CI_RED = """\
=== GLUDD STATE REPORT 2026-07-09T12:00:00Z ===

--- Working Tree ---
CLEAN

--- HEAD ---
Local:  abc123def456789012345678901234567890abcd
Branch: master

--- Remote ---
SYNCED: abc123def456789012345678901234567890abcd

--- Recent Commits ---
abc123d feat: something

--- CI ---
RED: run 1234567891 conclusion=failure

=== END STATE REPORT ===
"""

CI_NO_RUN = """\
=== GLUDD STATE REPORT 2026-07-09T12:00:00Z ===

--- Working Tree ---
CLEAN

--- HEAD ---
Local:  abc123def456789012345678901234567890abcd
Branch: master

--- Remote ---
SYNCED: abc123def456789012345678901234567890abcd

--- Recent Commits ---
abc123d feat: something

--- CI ---
NO RUN for abc123def456

=== END STATE REPORT ===
"""

CI_PENDING = """\
=== GLUDD STATE REPORT 2026-07-09T12:00:00Z ===

--- Working Tree ---
CLEAN

--- HEAD ---
Local:  abc123def456789012345678901234567890abcd
Branch: master

--- Remote ---
SYNCED: abc123def456789012345678901234567890abcd

--- Recent Commits ---
abc123d feat: something

--- CI ---
PENDING: run 1234567892 status=in_progress

=== END STATE REPORT ===
"""


class TestParseVerifyState:
    def test_all_green_exits_0(self) -> None:
        code, stdout, stderr = _run_with_stdin(CLEAN_SYNCED_GREEN)
        assert code == 0, f"expected 0, got {code}\nstderr: {stderr}"
        assert "PASSED" in stdout
        assert "CLEAN" in stdout
        assert "SYNCED" in stdout
        assert "GREEN" in stdout

    def test_dirty_exits_1(self) -> None:
        code, stdout, stderr = _run_with_stdin(DIRTY)
        assert code == 1, f"expected 1, got {code}\nstderr: {stderr}"
        assert "DIRTY" in stderr
        assert "commit or stash" in stderr

    def test_diverged_exits_2(self) -> None:
        code, stdout, stderr = _run_with_stdin(DIVERGED)
        assert code == 2, f"expected 2, got {code}\nstderr: {stderr}"
        assert "DIVERGED" in stderr
        assert "push first" in stderr

    def test_unreachable_exits_2(self) -> None:
        code, stdout, stderr = _run_with_stdin(UNREACHABLE)
        assert code == 2, f"expected 2, got {code}\nstderr: {stderr}"
        assert "DIVERGED" in stderr

    def test_ci_red_exits_3(self) -> None:
        code, stdout, stderr = _run_with_stdin(CI_RED)
        assert code == 3, f"expected 3, got {code}\nstderr: {stderr}"
        assert "RED" in stderr
        assert "fix CI" in stderr

    def test_ci_no_run_exits_4(self) -> None:
        code, stdout, stderr = _run_with_stdin(CI_NO_RUN)
        assert code == 4, f"expected 4, got {code}\nstderr: {stderr}"
        assert "NO RUN" in stderr
        assert "trigger CI" in stderr

    def test_ci_pending_is_not_a_failure(self) -> None:
        code, stdout, stderr = _run_with_stdin(CI_PENDING)
        assert code == 0, f"expected 0 for PENDING, got {code}\nstderr: {stderr}"

    def test_priority_dirty_over_diverged(self) -> None:
        text = CLEAN_SYNCED_GREEN.replace(
            "CLEAN",
            "DIRTY (1 files):\n M scripts/foo.py",
        ).replace(
            "SYNCED: abc123def456789012345678901234567890abcd",
            "DIVERGED: local=abc123def456 remote=789012345678",
        )
        code, stdout, stderr = _run_with_stdin(text)
        assert code == 1, f"expected 1 (dirty wins), got {code}\nstderr: {stderr}"

    def test_empty_input_exits_1(self) -> None:
        code, stdout, stderr = _run_with_stdin("")
        assert code == 1, f"expected 1 for empty input, got {code}\nstderr: {stderr}"

    def test_script_exists(self) -> None:
        """Script must exist and contain a valid parser class."""
        assert SCRIPT.exists(), f"{SCRIPT} must exist"
        assert SCRIPT.read_text().startswith("#!/usr/bin/env python3"), (
            f"{SCRIPT} must start with a shebang"
        )
