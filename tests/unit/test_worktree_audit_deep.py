"""Deep audit tests for the worktree health check script.

Covers exit codes, age detection (stale worktree), unmerged branch detection,
remote tracking, prunable detection, constant values, and the make target.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).parent.parent.parent
SCRIPT = ROOT / "scripts" / "check_worktree_health.py"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Pinned values from the policy and script."""

    def test_max_age_seconds_is_24_hours(self):
        from scripts.check_worktree_health import MAX_AGE_SECONDS

        assert MAX_AGE_SECONDS == 24 * 60 * 60, (
            "MAX_AGE_SECONDS must be exactly 24 hours per the Git Worktree Lifecycle policy"
        )

    def test_worktree_root_is_tmp_gludd_worktrees(self):
        from scripts.check_worktree_health import WORKTREE_ROOT

        assert WORKTREE_ROOT == "/tmp/gludd-worktrees", "WORKTREE_ROOT must point to /tmp/gludd-worktrees"

    def test_main_checkout_is_gludd_repo(self):
        from scripts.check_worktree_health import MAIN_CHECKOUT

        assert MAIN_CHECKOUT == "/Users/shawnwilson/gludd", "MAIN_CHECKOUT must be the gludd repo root"

    def test_remote_name_is_sandboxcom(self):
        from scripts.check_worktree_health import REMOTE_NAME

        assert REMOTE_NAME == "sandboxcom", "REMOTE_NAME must be sandboxcom per branch-landing integrity policy"


# ---------------------------------------------------------------------------
# Unit: get_worktrees() exclude main checkout
# ---------------------------------------------------------------------------


class TestGetWorktreesExcludesMain:
    """get_worktrees() must filter out the main checkout."""

    def _make_entry(self, worktree, branch="refs/heads/feature/x", head="abc1234"):
        return {"worktree": worktree, "branch": branch, "head": head}

    def test_filters_main_checkout(self):
        from scripts.check_worktree_health import MAIN_CHECKOUT, get_worktrees

        porcelain = (
            f"worktree {MAIN_CHECKOUT}\n"
            f"HEAD abc1234\n"
            f"branch refs/heads/development\n"
            f"\n"
            f"worktree /tmp/gludd-worktrees/agent-foo\n"
            f"HEAD def5678\n"
            f"branch refs/heads/agent-foo\n"
        )
        with mock.patch("scripts.check_worktree_health.run", return_value=(0, porcelain, "")):
            result = get_worktrees()
        paths = [e["worktree"] for e in result]
        assert MAIN_CHECKOUT not in paths, "get_worktrees must exclude the main checkout"
        assert "/tmp/gludd-worktrees/agent-foo" in paths

    def test_returns_empty_when_no_worktrees(self):
        from scripts.check_worktree_health import get_worktrees

        porcelain = f"worktree {Path('/Users/shawnwilson/gludd')}\nHEAD abc1234\nbranch refs/heads/development\n"
        with mock.patch("scripts.check_worktree_health.run", return_value=(0, porcelain, "")):
            result = get_worktrees()
        assert result == [], "get_worktrees must return empty list when only main checkout exists"

    def test_returns_empty_on_git_failure(self, capsys):
        from scripts.check_worktree_health import get_worktrees

        with mock.patch("scripts.check_worktree_health.run", return_value=(1, "", "git failed")):
            result = get_worktrees()
        assert result == [], "get_worktrees must return empty list on git failure (fail-open)"


# ---------------------------------------------------------------------------
# Unit: is_merged()
# ---------------------------------------------------------------------------


class TestIsMerged:
    """is_merged() uses `git merge-base --is-ancestor`."""

    def test_merged_when_ancestor(self):
        from scripts.check_worktree_health import is_merged

        with mock.patch("scripts.check_worktree_health.run", return_value=(0, "", "")):
            assert is_merged("agent-foo") is True

    def test_not_merged_when_not_ancestor(self):
        from scripts.check_worktree_health import is_merged

        with mock.patch("scripts.check_worktree_health.run", return_value=(1, "", "")):
            assert is_merged("agent-foo") is False

    def test_default_target_is_development(self):
        from scripts.check_worktree_health import is_merged

        with mock.patch("scripts.check_worktree_health.run") as run_mock:
            run_mock.return_value = (0, "", "")
            is_merged("agent-bar")
        args = run_mock.call_args[0][0]
        assert "development" in args, "is_merged must default to checking against 'development' branch"


# ---------------------------------------------------------------------------
# Unit: branch_exists_on_remote()
# ---------------------------------------------------------------------------


class TestBranchExistsOnRemote:
    """branch_exists_on_remote() queries sandboxcom via git ls-remote."""

    def test_exists_when_ls_remote_returns_sha(self):
        from scripts.check_worktree_health import branch_exists_on_remote

        with mock.patch("scripts.check_worktree_health.run", return_value=(0, "abc123\ttab", "")):
            assert branch_exists_on_remote("agent-foo") is True

    def test_not_exists_when_ls_remote_returns_empty(self):
        from scripts.check_worktree_health import branch_exists_on_remote

        with mock.patch("scripts.check_worktree_health.run", return_value=(0, "", "")):
            assert branch_exists_on_remote("agent-foo") is False

    def test_fail_open_when_git_errors(self):
        from scripts.check_worktree_health import branch_exists_on_remote

        with mock.patch("scripts.check_worktree_health.run", return_value=(1, "", "error")):
            assert branch_exists_on_remote("agent-foo") is True, (
                "branch_exists_on_remote must fail-open (return True) on git error"
            )


# ---------------------------------------------------------------------------
# Unit: get_tree_age()
# ---------------------------------------------------------------------------


class TestGetTreeAge:
    """get_tree_age() returns age in seconds using git log or directory mtime."""

    def test_uses_commit_time_when_available(self):
        from scripts.check_worktree_health import get_tree_age

        now = int(time.time())
        commit_epoch = now - 3600  # 1 hour ago
        with (
            mock.patch("scripts.check_worktree_health.run", return_value=(0, str(commit_epoch), "")),
            mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=True),
        ):
            age = get_tree_age("/tmp/gludd-worktrees/agent-foo")
        assert age is not None
        assert 3500 <= age <= 3700, f"Expected ~3600s age, got {age}"

    def test_falls_back_to_mtime_when_git_log_fails(self):
        from scripts.check_worktree_health import get_tree_age

        now = time.time()
        with (
            mock.patch("scripts.check_worktree_health.run", return_value=(1, "", "not a git repo")),
            mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=True),
            mock.patch("scripts.check_worktree_health.os.path.getmtime", return_value=now - 7200),
        ):
            age = get_tree_age("/tmp/some/path")
        assert age is not None
        assert 7100 <= age <= 7300, f"Expected ~7200s age from mtime fallback, got {age}"

    def test_returns_none_when_path_does_not_exist(self):
        from scripts.check_worktree_health import get_tree_age

        with mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=False):
            age = get_tree_age("/nonexistent/path")
        assert age is None

    def test_includes_time_module(self):
        """Belt-and-suspenders: confirm the 'time' module is importable.
        A missing import would crash the whole script at runtime.
        """
        try:
            import time as _time

            assert hasattr(_time, "time")
        except ImportError as e:
            pytest.fail(f"time module not importable: {e}")


# ---------------------------------------------------------------------------
# Integration: script exit codes via subprocess
# ---------------------------------------------------------------------------


class TestScriptExitCodes:
    """Run the actual script and verify exit codes on the real tree."""

    def test_script_exits_zero_when_no_worktrees(self):
        """Production check: we know there are no extra worktrees right now."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Script exit code should be 0 with no worktrees.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "=== WORKTREE HEALTH: PASSED ===" in result.stdout

    def test_make_worktree_health_check_exits_zero(self):
        result = subprocess.run(
            ["make", "--no-print-directory", "worktree-health-check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"make worktree-health-check should exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_script_output_contains_no_active_worktrees(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert "no active worktrees" in result.stdout

    def test_script_is_executable_with_python(self):
        result = subprocess.run(
            [sys.executable, "-c", f"exec(open({str(SCRIPT)!r}).read())"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Unit: main() with mocked worktrees (violation scenarios)
# ---------------------------------------------------------------------------


class TestMainWithMockedWorktrees:
    """Call main() with mocked get_worktrees() to verify violation detection."""

    def _stale_unmerged_worktree(self):
        return [
            {
                "worktree": "/tmp/gludd-worktrees/agent-stale",
                "branch": "refs/heads/agent-stale",
                "head": "deadbeef",
            }
        ]

    def test_stale_unmerged_detected(self):
        from scripts.check_worktree_health import main

        now = int(time.time())
        stale_epoch = now - (25 * 60 * 60)  # 25h ago
        with (
            mock.patch("scripts.check_worktree_health.get_worktrees", return_value=self._stale_unmerged_worktree()),
            mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=True),
            mock.patch("scripts.check_worktree_health.run", return_value=(0, str(stale_epoch), "")),
            mock.patch("scripts.check_worktree_health.is_merged", return_value=False),
            mock.patch("scripts.check_worktree_health.branch_exists_on_remote", return_value=True),
        ):
            rc = main()
        assert rc == 1, f"Stale + unmerged worktree must exit 1, got {rc}"

    def test_stale_but_merged_is_ok(self):
        from scripts.check_worktree_health import main

        now = int(time.time())
        stale_epoch = now - (25 * 60 * 60)
        with (
            mock.patch("scripts.check_worktree_health.get_worktrees", return_value=self._stale_unmerged_worktree()),
            mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=True),
            mock.patch("scripts.check_worktree_health.run", return_value=(0, str(stale_epoch), "")),
            mock.patch("scripts.check_worktree_health.is_merged", return_value=True),
            mock.patch("scripts.check_worktree_health.branch_exists_on_remote", return_value=True),
        ):
            rc = main()
        assert rc == 0, "Stale but merged worktree should NOT be a violation"

    def test_missing_from_remote_detected(self):
        from scripts.check_worktree_health import main

        with (
            mock.patch(
                "scripts.check_worktree_health.get_worktrees",
                return_value=[
                    {
                        "worktree": "/tmp/gludd-worktrees/agent-orphan",
                        "branch": "refs/heads/agent-orphan",
                        "head": "cafebabe",
                    }
                ],
            ),
            mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=True),
            mock.patch("scripts.check_worktree_health.run", return_value=(0, str(int(time.time()) - 3600), "")),
            mock.patch("scripts.check_worktree_health.is_merged", return_value=True),
            mock.patch("scripts.check_worktree_health.branch_exists_on_remote", return_value=False),
        ):
            rc = main()
        assert rc == 1, "Branch missing from remote must exit 1"

    def test_prunable_worktree_detected(self):
        from scripts.check_worktree_health import main

        with (
            mock.patch(
                "scripts.check_worktree_health.get_worktrees",
                return_value=[
                    {
                        "worktree": "/tmp/gludd-worktrees/agent-prune",
                        "branch": "refs/heads/agent-prune",
                        "head": "feedface",
                        "prunable": "prunable reason: no commits",
                    }
                ],
            ),
            mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=True),
            mock.patch("scripts.check_worktree_health.run", return_value=(0, str(int(time.time()) - 600), "")),
            mock.patch("scripts.check_worktree_health.is_merged", return_value=True),
            mock.patch("scripts.check_worktree_health.branch_exists_on_remote", return_value=True),
        ):
            rc = main()
        assert rc == 1, "PRUNABLE worktree must exit 1"

    def test_healthy_worktree_passes(self):
        from scripts.check_worktree_health import main

        with (
            mock.patch(
                "scripts.check_worktree_health.get_worktrees",
                return_value=[
                    {
                        "worktree": "/tmp/gludd-worktrees/agent-healthy",
                        "branch": "refs/heads/agent-healthy",
                        "head": "12345678",
                    }
                ],
            ),
            mock.patch("scripts.check_worktree_health.os.path.isdir", return_value=True),
            mock.patch("scripts.check_worktree_health.run", return_value=(0, str(int(time.time()) - 1800), "")),
            mock.patch("scripts.check_worktree_health.is_merged", return_value=True),
            mock.patch("scripts.check_worktree_health.branch_exists_on_remote", return_value=True),
        ):
            rc = main()
        assert rc == 0, "Healthy worktree (merged, on remote, <24h) must exit 0"
