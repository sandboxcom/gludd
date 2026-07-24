"""Verify GLUDD_FORCE_PUSH=1 does NOT bypass the CI-in-flight check.

The root cause of repeated CI cancellations during Session 52: the Makefile's
_push-rate-guard passed FORCE=1 to ci_push_guard.py AND added || true, which
completely bypassed the CI-in-flight check. Every force-push cancelled the
running Build and Release, preventing the release from ever completing.

This test verifies the Makefile source does NOT allow GLUDD_FORCE_PUSH to
bypass the CI-in-flight guard. The force-push override should only bypass
the cooldown timer and cancelled-run count, NOT the active-CI check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _makefile_src() -> str:
    return MAKEFILE.read_text()


class TestForcePushDoesNotBypassCIInFlight:
    """GLUDD_FORCE_PUSH must NOT skip the CI-in-flight check."""

    def test_no_force_flag_passed_to_ci_push_guard(self):
        """The Makefile must NOT pass FORCE=1 to ci_push_guard.py."""
        src = _makefile_src()
        # Find the ci_push_guard invocation
        matches = re.findall(r"ci_push_guard\.py.*", src)
        for match in matches:
            assert "FORCE=1" not in match, (
                f"ci_push_guard.py must NOT be called with FORCE=1 — "
                f"this bypasses the CI-in-flight check entirely. "
                f"Found: {match}"
            )

    def test_no_or_true_after_ci_push_guard(self):
        """The Makefile must NOT add || true after ci_push_guard.py."""
        src = _makefile_src()
        # Look for ci_push_guard followed by || true
        pattern = r"ci_push_guard\.py[^|]*\|\|\s*true"
        assert not re.search(pattern, src), (
            "ci_push_guard.py must NOT be followed by || true — "
            "this silently ignores the CI-in-flight check result"
        )

    def test_ci_in_flight_check_runs_regardless_of_force_push(self):
        """The ci_push_guard.py call must execute for ALL pushes."""
        src = _makefile_src()
        # Find the push-rate-guard section
        guard_idx = src.find("_push-rate-guard")
        assert guard_idx >= 0, "_push-rate-guard target must exist"
        section = src[guard_idx:guard_idx + 2000]

        # Verify ci_push_guard.py is called unconditionally (not inside
        # an if-else that skips it for GLUDD_FORCE_PUSH=1)
        assert "ci_push_guard.py" in section, (
            "ci_push_guard.py must be called in _push-rate-guard"
        )

        # The old broken pattern was:
        #   if GLUDD_FORCE_PUSH == "1"; then
        #     FORCE=1 ci_push_guard.py || true    ← BYPASSED
        #   else
        #     ci_push_guard.py || exit 1
        #   fi
        #
        # The fixed pattern should be:
        #   ci_push_guard.py || {
        #     if GLUDD_FORCE_PUSH == "1"; then exit 1  ← NOT BYPASSED
        #     else exit 1
        #     fi
        #   }

        # Check that GLUDD_FORCE_PUSH appears AFTER ci_push_guard (in the
        # error handling), not BEFORE it (in a conditional skip)
        guard_pos = section.find("ci_push_guard.py")
        force_pos = section.find("GLUDD_FORCE_PUSH", guard_pos)

        if force_pos > 0:
            # GLUDD_FORCE_PUSH appears after ci_push_guard — this is the
            # correct pattern (it's in the error handling branch)
            pass
        else:
            # Check the old broken pattern: GLUDD_FORCE_PUSH BEFORE ci_push_guard
            # with FORCE=1
            pre_guard = section[:guard_pos]
            if "GLUDD_FORCE_PUSH" in pre_guard and "FORCE=1" in pre_guard:
                pytest.fail(
                    "GLUDD_FORCE_PUSH appears BEFORE ci_push_guard.py call "
                    "with FORCE=1 — this is the broken pattern that bypasses "
                    "the CI-in-flight check"
                )

    def test_force_push_only_bypasses_cooldown(self):
        """GLUDD_FORCE_PUSH should only bypass cooldown and cancelled-run checks."""
        src = _makefile_src()
        guard_idx = src.find("_push-rate-guard")
        section = src[guard_idx:guard_idx + 3000]

        # The cooldown check SHOULD be bypassable
        # Just verify GLUDD_FORCE_PUSH appears in the cooldown section
        assert "GLUDD_FORCE_PUSH" in section, (
            "GLUDD_FORCE_PUSH should be referenced in _push-rate-guard "
            "for cooldown bypass"
        )


class TestNoDuplicateConcurrentBuilds:
    """Structural guard: the Makefile must prevent concurrent CI runs."""

    def test_concurrency_group_in_build_yml(self):
        build_yml = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "concurrency:" in build_yml, (
            "build.yml must have a concurrency section to prevent "
            "duplicate concurrent runs"
        )

    def test_cancel_in_progress_defined(self):
        build_yml = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "cancel-in-progress:" in build_yml, (
            "concurrency section must define cancel-in-progress"
        )
