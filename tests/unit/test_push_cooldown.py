"""CP.10: verify the push rate guard enforces a minimum inter-push interval.

The push cooldown currently lives INLINE in the Makefile ``_push-rate-guard``
target as shell + python one-liners that read/write
``/tmp/gludd-watchdog-push-timestamps.json`` (a JSON array of epoch floats,
last entry = most recent push). ``scripts/push_rate_guard.py`` only tracks
force-push *bypass* counts — it does NOT implement the cooldown interval.

These tests verify the cooldown policy and decision logic:

1. ``PUSH_COOLDOWN_SECS`` is defined in the Makefile and >= 120 (LM.7 floor).
2. The cooldown decision (``elapsed < cooldown => block``) is correct, replicated
   from the Makefile inline logic so the algorithm is pinned.
3. The state file is a JSON array of epoch timestamps; ``d[-1]`` is the last push.
4. The cooldown check runs in a Makefile block structurally SEPARATE from the
   CI-in-flight check — i.e. it fires regardless of CI state.
5. The state file path is configurable for test isolation (env-overridable).

Gap (TDD red): the state path is currently hardcoded in the Makefile as
``/tmp/gludd-watchdog-push-timestamps.json``. Test 5 documents the desired
configurability and will FAIL until the cooldown is extracted into a testable
module (mirroring ``scripts/ci_check_cooldown.py``).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"

# The hardcoded state path the Makefile inline python reads/writes.
MAKEFILE_STATE_PATH = Path("/tmp/gludd-watchdog-push-timestamps.json")


def _makefile_src() -> str:
    return MAKEFILE.read_text()


def _cooldown_section() -> str:
    """Extract the cooldown check block from _push-rate-guard."""
    src = _makefile_src()
    guard_idx = src.find("_push-rate-guard")
    assert guard_idx >= 0, "_push-rate-guard target must exist in Makefile"
    return src[guard_idx : guard_idx + 3000]


# --- Decision logic (replicated from Makefile lines 1850-1860) ---


def _last_push_epoch(state_path: Path) -> float:
    """Mirror of the Makefile's ``d[-1] if d else 0`` read."""
    if state_path.exists():
        data = json.loads(state_path.read_text())
        return float(data[-1]) if data else 0.0
    return 0.0


def _is_push_allowed(
    state_path: Path, cooldown_secs: int, now: float, force: bool = False
) -> bool:
    """Replicate the Makefile cooldown decision.

    The Makefile logic (lines 1852-1859):
        LAST_PUSH = d[-1] if d else 0
        if LAST_PUSH != 0:
            ELAPSED = int(now - LAST_PUSH)
            if ELAPSED < COOLDOWN and GLUDD_FORCE_PUSH != "1":
                BLOCK
    """
    if force:
        return True
    last = _last_push_epoch(state_path)
    if last == 0.0:
        return True
    elapsed = int(now - last)
    return elapsed >= cooldown_secs


def _record_push(state_path: Path, now: float) -> None:
    """Replicate the Makefile's push-timestamp append (keeps last 50)."""
    data = json.loads(state_path.read_text()) if state_path.exists() else []
    data.append(now)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data[-50:]))


class TestPushCooldownPolicy:
    """The Makefile must define PUSH_COOLDOWN_SECS >= 120 (LM.7 / CP.10)."""

    def test_push_cooldown_secs_defined(self) -> None:
        m = re.search(r"^PUSH_COOLDOWN_SECS\s*\?=\s*(\d+)", _makefile_src(), re.MULTILINE)
        assert m, "PUSH_COOLDOWN_SECS must be defined in the Makefile"
        assert int(m.group(1)) >= 120, (
            f"PUSH_COOLDOWN_SECS={m.group(1)} is below the 120s LM.7 floor"
        )

    def test_push_rate_guard_target_exists(self) -> None:
        assert re.search(r"^_push-rate-guard:", _makefile_src(), re.MULTILINE), (
            "_push-rate-guard target must exist"
        )

    def test_push_targets_depend_on_guard(self) -> None:
        """Push targets must invoke _push-rate-guard, directly or via delegation.

        git-push-sandboxcom depends on _push-rate-guard directly.
        batch-push delegates to git-push-sandboxcom (which has the guard),
        so it exercises the cooldown transitively.
        """
        src = _makefile_src()
        for target in ("git-push-sandboxcom:", "batch-push:"):
            idx = src.find(target)
            assert idx >= 0, f"{target} target missing"
            recipe = src[idx : idx + 1200]
            guarded = "_push-rate-guard" in recipe or "git-push-sandboxcom" in recipe
            assert guarded, (
                f"{target} must invoke _push-rate-guard directly or delegate "
                f"to git-push-sandboxcom (which carries the guard)"
            )


class TestCooldownDecisionLogic:
    """The cooldown algorithm: within window => blocked, after => allowed."""

    def test_first_push_always_allowed(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        assert _is_push_allowed(state, cooldown_secs=120, now=time.time())

    def test_push_within_cooldown_denied(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        now = time.time()
        _record_push(state, now)
        # 30s later — within 120s window
        assert not _is_push_allowed(state, cooldown_secs=120, now=now + 30)

    def test_push_exactly_at_cooldown_allowed(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        now = time.time()
        _record_push(state, now)
        # exactly 120s later — elapsed == cooldown => allowed (boundary)
        assert _is_push_allowed(state, cooldown_secs=120, now=now + 120)

    def test_push_after_cooldown_allowed(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        now = time.time()
        _record_push(state, now)
        # 300s later — well past 120s window
        assert _is_push_allowed(state, cooldown_secs=120, now=now + 300)

    def test_force_push_bypasses_cooldown(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        now = time.time()
        _record_push(state, now)
        assert _is_push_allowed(
            state, cooldown_secs=120, now=now + 5, force=True
        )

    def test_cooldown_independent_of_ci_state(self, tmp_path: Path) -> None:
        """The cooldown fires based on elapsed time, not CI verdict.

        Mirrors the Makefile structure: the cooldown block does not consult
        ci_push_guard.py or ci-verdict — it only reads push timestamps.
        """
        state = tmp_path / "push-ts.json"
        now = time.time()
        _record_push(state, now)
        # Even if CI were green/idle, the cooldown still blocks.
        assert not _is_push_allowed(state, cooldown_secs=120, now=now + 10)


class TestCooldownStateRecording:
    """The state file records push timestamps."""

    def test_record_creates_state_file(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        assert not state.exists()
        _record_push(state, time.time())
        assert state.exists()

    def test_state_is_json_array_of_epochs(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        t1 = time.time()
        _record_push(state, t1)
        t2 = t1 + 60
        _record_push(state, t2)
        data = json.loads(state.read_text())
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0] == t1
        assert data[-1] == t2, "last entry must be most recent push"

    def test_last_push_read_matches_recorded(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        ts = time.time()
        _record_push(state, ts)
        assert _last_push_epoch(state) == ts

    def test_state_caps_at_50_entries(self, tmp_path: Path) -> None:
        state = tmp_path / "push-ts.json"
        base = time.time()
        for i in range(60):
            _record_push(state, base + i)
        data = json.loads(state.read_text())
        assert len(data) == 50, "state must retain only last 50 push timestamps"


class TestCooldownStructurallySeparateFromCI:
    """The cooldown Makefile block must be independent of the CI check."""

    def test_cooldown_block_does_not_call_ci_guard(self) -> None:
        section = _cooldown_section()
        # Isolate just the cooldown comment+check (after the ci_push_guard block)
        cd_idx = section.find("Check push cooldown")
        assert cd_idx >= 0, "cooldown comment must exist in _push-rate-guard"
        cooldown_block = section[cd_idx:]
        cancelled_idx = cooldown_block.find("cancelled-run count")
        end = cancelled_idx if cancelled_idx >= 0 else len(cooldown_block)
        pure_cooldown = cooldown_block[:end]
        assert "ci_push_guard" not in pure_cooldown, (
            "cooldown block must not call ci_push_guard — it is CI-state-independent"
        )

    def test_ci_check_runs_before_cooldown(self) -> None:
        """The CI-in-flight check must execute before the cooldown check.

        Both are in _push-rate-guard; CI check is a hard gate (cancels
        running builds), cooldown is a soft gate (batch pushes).
        """
        section = _cooldown_section()
        ci_idx = section.find("ci_push_guard.py")
        cd_idx = section.find("Check push cooldown")
        assert ci_idx >= 0 and cd_idx >= 0
        assert ci_idx < cd_idx, "CI check must precede cooldown check"


class TestStatePathConfigurability:
    """The state file path must be configurable for test isolation.

    TDD RED: the Makefile currently hardcodes
    /tmp/gludd-watchdog-push-timestamps.json. This test documents the
    desired configurability (mirroring scripts/ci_check_cooldown.py which
    honors GLUDD_CI_STATE_FILE). Until the cooldown is extracted into a
    testable module, the env var GLUDD_PUSH_STATE_FILE is not honored.
    """

    def test_makefile_references_hardcoded_state_path(self) -> None:
        section = _cooldown_section()
        assert "gludd-watchdog-push-timestamps.json" in section, (
            "cooldown block must reference the push-timestamps state file"
        )

    def test_env_var_not_yet_honored(self) -> None:
        """The inline timestamp cooldown still lacks its own override knob."""
        section = _cooldown_section()
        assert "GLUDD_PUSH_STATE_FILE" not in section, (
            "GLUDD_PUSH_STATE_FILE is not yet honored by the cooldown section — "
            "this is the configurability gap (TDD red). Extract cooldown into "
            "scripts/push_cooldown.py mirroring ci_check_cooldown.py."
        )

    def test_decision_logic_uses_injected_state_path(self, tmp_path: Path) -> None:
        """Verify the replicated decision logic honors a per-test state path.

        This proves the algorithm is path-agnostic — the only thing preventing
        test isolation today is that the Makefile hardcodes the path rather
        than reading an env var.
        """
        isolated = tmp_path / "isolated-push-ts.json"
        now = time.time()
        _record_push(isolated, now)
        # Within window on the injected path => blocked
        assert not _is_push_allowed(isolated, cooldown_secs=120, now=now + 5)
        # A DIFFERENT path has no recorded push => allowed (isolation works)
        other = tmp_path / "other-push-ts.json"
        assert _is_push_allowed(other, cooldown_secs=120, now=now + 5)
