#!/usr/bin/env python3
"""
parse_verify_state.py — parse `make verify-state` output and exit non-zero if
any check is RED. For use in pre-commit hooks.

Usage:
    make verify-state | python3 scripts/parse_verify_state.py

Exit codes:
    0  — CLEAN + SYNCED + CI GREEN
    1  — tree DIRTY: commit or stash first
    2  — remote DIVERGED: push first
    3  — CI RED: fix CI first
    4  — CI NO RUN: push to trigger CI

Checks are applied in priority order (highest to lowest): DIRTY, DIVERGED,
CI RED, CI NO RUN. Only the highest-priority failure is reported and exited on.
"""

from __future__ import annotations

import re
import sys


SECTION_PAT = re.compile(r"^--- (.+) ---$")
END_REPORT = "=== END STATE REPORT ==="


class StateReport:
    """Parsed fields from `make verify-state` output."""

    _tree: str | None = None
    _remote: str | None = None
    _ci: str | None = None

    @staticmethod
    def from_stdin() -> StateReport:
        report = StateReport()
        current_section: str | None = None

        for line in sys.stdin:
            line = line.rstrip("\n")

            if line == END_REPORT:
                break

            section_match = SECTION_PAT.match(line)
            if section_match:
                current_section = section_match.group(1)
                continue

            stripped = line.strip()
            if not stripped:
                continue

            if current_section == "Working Tree" and report._tree is None:
                report._tree = stripped
            elif current_section == "Remote" and report._remote is None:
                report._remote = stripped
            elif current_section == "CI" and report._ci is None:
                report._ci = stripped

        return report

    @property
    def tree_clean(self) -> bool:
        return self._tree == "CLEAN"

    @property
    def remote_synced(self) -> bool:
        return self._remote is not None and self._remote.startswith("SYNCED")

    @property
    def ci_green(self) -> bool:
        return self._ci is not None and self._ci.startswith("GREEN:")

    @property
    def ci_no_run(self) -> bool:
        return self._ci is not None and self._ci.startswith("NO RUN")

    @property
    def ci_red(self) -> bool:
        return self._ci is not None and self._ci.startswith("RED:")

    def check(self) -> tuple[int, str]:
        """Return (exit_code, message) for highest-priority failure, or (0,'')."""
        if not self.tree_clean:
            return 1, "verify-state: FAILED — Working Tree DIRTY → commit or stash first"
        if not self.remote_synced:
            return 2, "verify-state: FAILED — Remote DIVERGED → push first"
        if self.ci_red:
            return 3, "verify-state: FAILED — CI RED → fix CI first"
        if self.ci_no_run:
            return 4, "verify-state: FAILED — CI NO RUN → push to trigger CI"
        return 0, "verify-state: PASSED — CLEAN + SYNCED + CI GREEN"


def main() -> int:
    report = StateReport.from_stdin()
    code, msg = report.check()
    print(msg, file=sys.stderr if code != 0 else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
