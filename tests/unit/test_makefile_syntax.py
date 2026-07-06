"""Makefile syntax guardrail.

Runs ``make -n gate`` (dry-run — no commands actually execute) and asserts it
parses cleanly. This catches the class of Makefile corruption where a recipe
line is turned from a tab to spaces (or any other syntactic breakage) before
it can land a broken commit that wedges every gate / test-and-commit /
release-cut target in the repository.

The check is fast (<1s), hermetic (``-n`` prints commands without running
them), and runs as part of the normal unit-test suite so a syntax break is
surfaced at ``make collect-check`` / ``make gate`` time, not at release time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


class TestMakefileSyntax:
    def test_makefile_exists(self) -> None:
        assert MAKEFILE.exists(), "Makefile must exist at repo root"

    def test_make_dry_run_gate_parses(self) -> None:
        """``make -n gate`` must exit 0 (Makefile parses with no syntax error).

        ``-n`` (--dry-run --just-print) reads and expands the Makefile without
        executing any recipe, so a non-zero exit here is purely a syntax /
        parse error — not a flaky test, not an environment issue. The most
        common root cause is a recipe line whose leading TAB was replaced by
        SPACES (the editor-silent space/tab corruption that prompted this
        test), which make reports as "missing separator".
        """
        result = subprocess.run(
            ["make", "-n", "gate"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            "Makefile syntax broken: `make -n gate` exited "
            f"{result.returncode}.\n"
            "--- stdout ---\n"
            f"{result.stdout}\n"
            "--- stderr ---\n"
            f"{result.stderr}\n"
            "Common cause: a recipe line uses spaces instead of a TAB. "
            "Open the Makefile, find the line make complains about, and "
            "restore the leading TAB character."
        )
