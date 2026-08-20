"""
Unit tests for scripts/require_ci_green.py — _detect_branch().

Covers branch auto-detection (added commit 37b23f3d):
- success path (git rev-parse returns a branch name)
- failure fallback (git not available / not a repo)
- empty-output fallback
- whitespace stripping
- integration with verdict_for() flow when branch=None

No real git or gh calls are made. All subprocess I/O is mocked.
"""

from __future__ import annotations

import importlib.util
import os
from subprocess import CompletedProcess
from typing import Any, cast
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test by path (it lives in scripts/, not a package)
# ---------------------------------------------------------------------------


def _load_module():
    script_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "require_ci_green.py"
    )
    spec = importlib.util.spec_from_file_location("require_ci_green_detect_branch", script_path)
    mod = cast(Any, importlib.util).module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


require_ci_green = _load_module()
_detect_branch = require_ci_green._detect_branch
verdict_for = require_ci_green.verdict_for


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proc(stdout: str = "", returncode: int = 0) -> CompletedProcess[str]:
    """Build a minimal subprocess.CompletedProcess stand-in."""
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDetectBranchSuccess:
    def test_returns_current_branch_when_git_succeeds(self):
        """(1) _detect_branch() returns the current git branch on success."""
        with patch("subprocess.run", return_value=_proc("feature/rp-12\n")):
            assert _detect_branch() == "feature/rp-12"

    def test_strips_whitespace_from_output(self):
        """(4) _detect_branch() strips leading/trailing whitespace."""
        with patch("subprocess.run", return_value=_proc("   development\n  ")):
            assert _detect_branch() == "development"

    def test_returns_master_branch(self):
        with patch("subprocess.run", return_value=_proc("master\n")):
            assert _detect_branch() == "master"


class TestDetectBranchFallback:
    def test_returns_development_when_git_fails(self):
        """(2) _detect_branch() returns 'development' when git raises (e.g. not a git repo)."""
        with patch("subprocess.run", side_effect=FileNotFoundError("git not installed")):
            assert _detect_branch() == "development"

    def test_returns_development_on_subprocess_timeout(self):
        with patch("subprocess.run", side_effect=TimeoutError("timed out")):
            assert _detect_branch() == "development"

    def test_returns_development_on_generic_exception(self):
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            assert _detect_branch() == "development"

    def test_returns_development_when_output_empty(self):
        """(3) _detect_branch() returns 'development' when git output is empty."""
        with patch("subprocess.run", return_value=_proc("")):
            assert _detect_branch() == "development"

    def test_returns_development_when_output_only_whitespace(self):
        with patch("subprocess.run", return_value=_proc("   \n\t")):
            assert _detect_branch() == "development"

    def test_returns_development_when_detached_head(self):
        """rev-parse returns 'HEAD' in detached-HEAD state — fall back to development."""
        with patch("subprocess.run", return_value=_proc("HEAD\n")):
            assert _detect_branch() == "development"


class TestDetectBranchIntegration:
    def test_called_by_verdict_for_when_branch_is_none(self):
        """(5) Integration: verdict_for() invokes _detect_branch() when branch=None."""
        with patch.object(require_ci_green, "_detect_branch", return_value="development") as mock_det:
            # Force gh to fail fast so we only assert _detect_branch was called,
            # not on gh's result.
            with patch("subprocess.run", return_value=_proc("[]")):
                verdict_for("abc123", branch=None)
            mock_det.assert_called_once()

    def test_not_called_when_branch_explicit(self):
        """Sanity: explicit branch bypasses _detect_branch()."""
        with patch.object(require_ci_green, "_detect_branch", return_value="development") as mock_det:
            with patch("subprocess.run", return_value=_proc("[]")):
                verdict_for("abc123", branch="master")
            mock_det.assert_not_called()

    def test_detected_branch_passed_to_gh(self):
        """The branch returned by _detect_branch is passed through to the gh subprocess."""
        captured: dict[str, Any] = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            return _proc("[]")

        with patch.object(require_ci_green, "_detect_branch", return_value="feature/rp-12"), \
             patch("subprocess.run", side_effect=fake_run):
            verdict_for("deadbeef", branch=None)

        # The gh command should include the detected branch name
        assert "feature/rp-12" in captured["cmd"]

    def test_rejects_ambiguous_branch_arguments(self):
        with pytest.raises(TypeError, match="either positionally or by keyword"):
            verdict_for("deadbeef", "development", branch="master")

    def test_rejects_branch_for_supplied_run_data(self):
        with pytest.raises(TypeError, match="only valid when querying CI"):
            verdict_for([], branch="development")

    @pytest.mark.parametrize("stdout, returncode", [("", 1), ('{"runs": []}', 0)])
    def test_gh_failures_return_error_verdict(
        self,
        stdout: str,
        returncode: int,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("subprocess.run", return_value=_proc(stdout, returncode)):
            assert verdict_for("deadbeef", branch="development") == 2
        assert "CI ERROR:" in capsys.readouterr().out
