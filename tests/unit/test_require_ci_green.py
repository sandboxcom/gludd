"""
Unit tests for scripts/require_ci_green.py — verdict_for() pure function.

No real gh calls are made. All tests exercise the decision logic only.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, cast

# ---------------------------------------------------------------------------
# Import the module under test by path (it lives in scripts/, not a package)
# ---------------------------------------------------------------------------


def _load_module():
    script_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "require_ci_green.py"
    )
    spec = importlib.util.spec_from_file_location("require_ci_green", script_path)
    mod = cast(Any, importlib.util).module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


require_ci_green = _load_module()
verdict_for = require_ci_green.verdict_for
verdict_from_runs = require_ci_green.verdict_from_runs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    db_id: int | str,
    sha: str,
    status: str,
    conclusion: str | None = None,
    workflow: str = "Build and Release",
) -> dict[str, Any]:
    return {
        "databaseId": db_id,
        "headSha": sha,
        "status": status,
        "conclusion": conclusion,
        "workflowName": workflow,
        "displayTitle": f"Run {db_id}",
    }


SHA = "abc123def456"  # pragma: allowlist secret  (dummy test fixture, not a real secret)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCiGreen:
    def test_success_returns_0(self):
        runs = [_run(1, SHA, "completed", "success")]
        code, msg = verdict_for(runs, SHA)
        assert code == 0
        assert "CI GREEN" in msg
        assert SHA in msg
        assert "1" in msg  # run id

    def test_failure_returns_1(self):
        runs = [_run(2, SHA, "completed", "failure")]
        code, msg = verdict_for(runs, SHA)
        assert code == 1
        assert "CI RED" in msg
        assert "failure" in msg

    def test_cancelled_returns_1(self):
        runs = [_run(3, SHA, "completed", "cancelled")]
        code, msg = verdict_for(runs, SHA)
        assert code == 1
        assert "CI RED" in msg

    def test_timed_out_returns_1(self):
        runs = [_run(4, SHA, "completed", "timed_out")]
        code, msg = verdict_for(runs, SHA)
        assert code == 1
        assert "CI RED" in msg

    def test_in_progress_returns_2(self):
        runs = [_run(5, SHA, "in_progress")]
        code, msg = verdict_for(runs, SHA)
        assert code == 2
        assert "CI PENDING" in msg
        assert "in_progress" in msg

    def test_queued_returns_2(self):
        runs = [_run(6, SHA, "queued")]
        code, msg = verdict_for(runs, SHA)
        assert code == 2
        assert "CI PENDING" in msg

    def test_waiting_returns_2(self):
        runs = [_run(7, SHA, "waiting")]
        code, msg = verdict_for(runs, SHA)
        assert code == 2
        assert "CI PENDING" in msg

    def test_no_matching_run_returns_1(self):
        # All runs have a different SHA
        runs = [_run(8, "ffffffff", "completed", "success")]
        code, msg = verdict_for(runs, SHA)
        assert code == 1
        assert "no run found" in msg
        assert SHA in msg

    def test_empty_runs_returns_1(self):
        code, msg = verdict_for([], SHA)
        assert code == 1
        assert "no run found" in msg

    def test_multiple_runs_picks_latest(self):
        """When multiple runs match the SHA, the one with the highest databaseId wins."""
        runs = [
            _run(10, SHA, "completed", "failure"),   # older
            _run(20, SHA, "completed", "success"),   # newest — should win
        ]
        code, msg = verdict_for(runs, SHA)
        assert code == 0
        assert "CI GREEN" in msg
        assert "20" in msg

    def test_multiple_runs_latest_is_failure(self):
        runs = [
            _run(10, SHA, "completed", "success"),  # older
            _run(20, SHA, "completed", "failure"),  # newest — should win
        ]
        code, msg = verdict_for(runs, SHA)
        assert code == 1
        assert "CI RED" in msg

    def test_prefers_build_and_release_workflow(self):
        """If both a 'Build and Release' run and another workflow run exist,
        the 'Build and Release' run wins regardless of databaseId order."""
        runs = [
            _run(100, SHA, "completed", "failure", workflow="Other Workflow"),
            _run(5,   SHA, "completed", "success", workflow="Build and Release"),
        ]
        # Without preference: id=100 would win (failure). With preference: id=5 wins (success).
        code, msg = verdict_for(runs, SHA)
        assert code == 0
        assert "CI GREEN" in msg

    def test_sha_prefix_does_not_match_exact_head(self):
        """A successful run for a different full SHA cannot satisfy readiness."""
        full_sha = SHA + "0" * (40 - len(SHA))
        runs = [_run(1, full_sha, "completed", "success")]
        code, msg = verdict_from_runs(runs, SHA)
        assert code == 1
        assert "no run found" in msg

    def test_cancelled_and_skipped_runs_are_red(self):
        for conclusion in ("cancelled", "skipped"):
            code, msg = verdict_from_runs(
                [_run(1, SHA, "completed", conclusion)], SHA
            )
            assert code == 1
            assert "CI RED" in msg

    def test_stale_success_for_other_sha_is_red(self):
        code, msg = verdict_from_runs(
            [_run(1, "stale-sha", "completed", "success")], SHA
        )
        assert code == 1
        assert "no run found" in msg

    def test_unknown_status_fails_closed(self):
        runs = [_run(9, SHA, "some_unknown_status", None)]
        code, msg = verdict_for(runs, SHA)
        assert code == 1
        assert "CI RED" in msg
        assert "fail-closed" in msg

    def test_message_includes_run_id(self):
        runs = [_run(42, SHA, "completed", "success")]
        _code, msg = verdict_for(runs, SHA)
        assert "42" in msg

    def test_success_message_format(self):
        runs = [_run(99, SHA, "completed", "success")]
        _code, msg = verdict_for(runs, SHA)
        assert msg == f"CI GREEN: {SHA} run 99"

    def test_pending_message_includes_status(self):
        runs = [_run(77, SHA, "queued")]
        _code, msg = verdict_for(runs, SHA)
        assert "queued" in msg

    def test_non_numeric_run_id_is_tolerated_deterministically(self):
        run = _run("not-a-number", SHA, "completed", "success")
        code, message = verdict_for([run], SHA)
        assert code == 0
        assert "not-a-number" in message

    def test_unknown_conclusion_remains_red(self):
        run = _run(78, SHA, "completed", "neutral")
        code, message = verdict_for([run], SHA)
        assert code == 1
        assert "fail-closed" in message
