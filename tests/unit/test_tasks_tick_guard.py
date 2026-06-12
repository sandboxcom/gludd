"""Tests for the TASKS.md tick guard in prelight."""

import textwrap
from pathlib import Path

import pytest

from general_ludd.quality.preflight import check_tasks_ticks

GOOD_TASKS = textwrap.dedent("""\
    # TASKS.md
    - [x] W0.1 — thing done | evidence: make gate "ALL PASSED" abc1234
    - [x] W0.2 — other done | evidence: tests/unit/test_foo.py 3 passed deadbeef
    - [ ] W1.1 — pending task
""")

BAD_TASKS = {
    "missing_evidence": (
        "- [x] W0.1 — thing done | no proof here abc1234\n",
        "evidence:",
    ),
    "missing_test_ref": (
        "- [x] W0.1 — thing done | evidence: some words abc1234\n",
        "make ",
    ),
    "missing_commit": (
        "- [x] W0.1 — thing done | evidence: make gate ALL PASSED\n",
        "hex commit",
    ),
    "short_commit": (
        "- [x] W0.1 — thing done | evidence: make gate ALL PASSED abc12\n",
        "7-40 char hex",
    ),
    "pending_word": (
        "- [x] W0.1 — thing done | evidence: make gate pending abc1234\n",
        '"pending"',
    ),
    "partial_word": (
        "- [x] W0.1 — thing done | evidence: make gate partial abc1234\n",
        '"partial"',
    ),
    "groundwork_word": (
        "- [x] W0.1 — thing done | evidence: make gate groundwork abc1234\n",
        '"groundwork"',
    ),
}


class TestCheckTasksTicks:
    def test_good_tasks_passes(self):
        result = check_tasks_ticks(GOOD_TASKS.splitlines())
        assert result["passed"], f"Expected pass, got violations: {result.get('violations', [])}"

    @pytest.mark.parametrize(
        "key",
        list(BAD_TASKS.keys()),
    )
    def test_bad_tick_fails(self, key):
        bad_line, expected_in_msg = BAD_TASKS[key]
        lines = [bad_line]
        result = check_tasks_ticks(lines)
        assert not result["passed"], f"Expected failure for {key}"
        assert len(result["violations"]) >= 1
        msg = result["violations"][0]
        assert expected_in_msg in msg or expected_in_msg.strip('"') in msg, (
            f"Violation for {key} should mention {expected_in_msg}: {msg}"
        )

    def test_unticked_lines_are_ignored(self):
        lines = [
            "# heading\n",
            "- [ ] W1.1 — still pending\n",
            "some prose text\n",
        ]
        result = check_tasks_ticks(lines)
        assert result["passed"]

    def test_tests_path_satisfies_test_ref(self):
        lines = [
            "- [x] X1 — done | evidence: tests/unit/test_bar.py 5 passed beef1234\n",
        ]
        result = check_tasks_ticks(lines)
        assert result["passed"]

    def test_real_tasks_file_passes(self):
        tasks_path = Path(__file__).parent.parent.parent / "TASKS.md"
        if not tasks_path.exists():
            pytest.skip("TASKS.md not found")
        lines = tasks_path.read_text().splitlines()
        result = check_tasks_ticks(lines)
        assert result["passed"], (
            "TASKS.md tick violations:\n" + "\n".join(result.get("violations", []))
        )
