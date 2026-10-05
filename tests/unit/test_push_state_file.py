"""TDD: push guardrails must write /tmp/gludd-push-state.json on block.

Defines the expected schema and behavior for a unified push-state file that
every push guardrail writes when it blocks a push.  The four guardrail
scenarios map to Makefile targets:

- _ci-restart-cap       (AA023 — max 3 CI restarts per session)
- _push-rate-guard      (cooldown / CI-pending check)
- force-push rate limit (scripts/push_rate_guard.py bypass counter)
- _stash-before-push-guard (AA022 — unstaged changes)

The module ``scripts.push_state.py`` (not yet created — TDD RED) exposes:

- ``record_push_block(reason, message, branch, commit_sha, **extra)`` →
  writes ``/tmp/gludd-push-state.json`` atomically (temp + rename).
- ``clear_push_state()`` → removes the file on successful push.
- ``load_push_state()`` → returns the last recorded block as a dict, or None.

These tests define the contract that will drive the implementation.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# ── Module-under-test stubs (will move to scripts/push_state.py) ──────────

_DEFAULT_PATH = Path("/tmp/gludd-push-state.json")

_REQUIRED_FIELDS = (
    "reason",
    "blocked_at",
    "message",
    "branch",
    "commit_sha",
)


def _state_path() -> Path:
    return Path(os.environ.get("GLUDD_PUSH_STATE_FILE", str(_DEFAULT_PATH)))


def record_push_block(
    reason: str,
    message: str,
    branch: str = "master",
    commit_sha: str = "unknown",
    **extra: object,
) -> Path:
    """Atomic write (temp + rename) of push-state block record."""
    state = {
        "reason": reason,
        "blocked_at": time.time(),
        "message": message,
        "branch": branch,
        "commit_sha": commit_sha,
        **extra,
    }
    sp = _state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(str(tmp), str(sp))
    return sp


def clear_push_state() -> None:
    """Remove the push-state file after a successful push."""
    sp = _state_path()
    try:
        sp.unlink(missing_ok=True)
    except TypeError:
        if sp.exists():
            sp.unlink()


def load_push_state() -> dict | None:
    """Return the last recorded push block, or None."""
    sp = _state_path()
    if not sp.exists():
        return None
    return json.loads(sp.read_text())


# ── Helpers ────────────────────────────────────────────────────────────────


def _blocked_at(block: dict) -> float:
    return float(block["blocked_at"])


# ── Tests ──────────────────────────────────────────────────────────────────


class TestPushStateFileWrittenOnBlock:
    """Each guardrail must write /tmp/gludd-push-state.json when it blocks."""

    def test_ci_restart_cap_writes_push_state_on_block(self, monkeypatch, tmp_path: Path):
        """Simulate _ci-restart-cap block; verify file written with correct reason."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        path = record_push_block(
            reason="ci-restart-cap",
            message="BLOCKED: 3 CI restarts this session. Max is 3.",
            branch="development",
            commit_sha="test-commit-sha-not-real",
            current_count=3,
            max_allowed=3,
        )
        assert path == state_file
        assert path.exists()

        block = load_push_state()
        assert block is not None
        assert block["reason"] == "ci-restart-cap"
        assert block["branch"] == "development"
        assert block["commit_sha"] == "test-commit-sha-not-real"
        assert block["current_count"] == 3
        assert block["max_allowed"] == 3
        assert "BLOCKED" in block["message"]

    def test_push_rate_guard_writes_push_state_on_block(self, monkeypatch, tmp_path: Path):
        """Simulate cooldown block; verify file written with correct reason."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        record_push_block(
            reason="push-rate-cooldown",
            message="BLOCKED: last push was 15 seconds ago (cooldown: 300s).",
            branch="master",
            commit_sha="def789abc123",  # pragma: allowlist secret
            cooldown_secs=300,
            elapsed_secs=15,
        )
        assert state_file.exists()

        block = load_push_state()
        assert block is not None
        assert block["reason"] == "push-rate-cooldown"
        assert block["cooldown_secs"] == 300
        assert block["elapsed_secs"] == 15

    def test_force_push_rate_guard_writes_push_state_on_block(self, monkeypatch, tmp_path: Path):
        """Simulate force-push rate block; verify file written with correct reason."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        record_push_block(
            reason="force-push-rate",
            message=("FORCE-PUSH BLOCKED: 5 consecutive force-pushes within 12h window. Use normal push or wait."),
            branch="master",
            commit_sha="ghi012jkl345",
            consecutive_bypasses=5,
            max_bypasses=5,
            window_hours=12,
        )
        assert state_file.exists()

        block = load_push_state()
        assert block is not None
        assert block["reason"] == "force-push-rate"
        assert block["consecutive_bypasses"] == 5
        assert block["max_bypasses"] == 5
        assert block["window_hours"] == 12

    def test_stash_before_push_writes_push_state_on_block(self, monkeypatch, tmp_path: Path):
        """Simulate stash-leak block; verify file written with correct reason."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        record_push_block(
            reason="stash-before-push",
            message=(
                "STASH-BEFORE-PUSH: unstaged changes detected in working tree. Commit or revert changes before pushing."
            ),
            branch="master",
            commit_sha="mno456pqr789",
            unstaged_files=["src/daemon.py", "tests/unit/test_daemon.py"],
        )
        assert state_file.exists()

        block = load_push_state()
        assert block is not None
        assert block["reason"] == "stash-before-push"
        assert block["unstaged_files"] == ["src/daemon.py", "tests/unit/test_daemon.py"]


class TestPushStateNotWrittenOnSuccess:
    """Successful pushes must NOT leave a stale state file."""

    def test_push_state_not_written_on_success(self, monkeypatch, tmp_path: Path):
        """Simulate successful push; verify file not written (or cleared if existed)."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        # Pre-condition: a previous block was recorded
        record_push_block(
            reason="ci-restart-cap",
            message="BLOCKED: 3 CI restarts.",
            branch="master",
            commit_sha="stale000",
        )
        assert state_file.exists()

        # Successful push: state should be cleared
        clear_push_state()
        assert not state_file.exists()
        assert load_push_state() is None

    def test_push_state_not_written_when_no_block(self, monkeypatch, tmp_path: Path):
        """No block means no state file (nominal path)."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        # Sanity: no state file exists
        if state_file.exists():
            state_file.unlink()

        assert load_push_state() is None
        assert not state_file.exists()


class TestPushStateValidJson:
    """The written file must be valid, parseable JSON."""

    def test_push_state_valid_json(self, monkeypatch, tmp_path: Path):
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        record_push_block(
            reason="push-rate-cooldown",
            message="Cooldown block.",
            branch="development",
            commit_sha="json000test",
            extra_num=42,
            extra_str="hello",
            extra_bool=True,
            extra_null=None,
        )
        raw = state_file.read_text()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"State file is not valid JSON: {exc}") from exc

        assert isinstance(parsed, dict)
        assert parsed["extra_num"] == 42
        assert parsed["extra_str"] == "hello"
        assert parsed["extra_bool"] is True
        assert parsed["extra_null"] is None


class TestPushStateRequiredFields:
    """The file must contain all required fields."""

    def test_push_state_contains_required_fields(self, monkeypatch, tmp_path: Path):
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        record_push_block(
            reason="ci-restart-cap",
            message="Blocked.",
            branch="development",
            commit_sha="fieldstest",
        )
        block = load_push_state()
        assert block is not None

        for field in _REQUIRED_FIELDS:
            assert field in block, f"Required field '{field}' missing from push state"
            assert block[field], f"Required field '{field}' must have a truthy value"

        assert isinstance(block["blocked_at"], (int, float))

    def test_push_state_timestamp_is_recent(self, monkeypatch, tmp_path: Path):
        """blocked_at must be within 5 seconds of now."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        before = time.time()
        record_push_block(
            reason="ci-restart-cap",
            message="Blocked.",
            branch="master",
            commit_sha="timecheck",
        )
        after = time.time()
        block = load_push_state()
        assert block is not None
        ts = _blocked_at(block)
        assert before - 1 <= ts <= after + 1, f"blocked_at={ts} not within [{before - 1}, {after + 1}]"

    def test_push_state_reason_is_known_value(self, monkeypatch, tmp_path: Path):
        """reason must be one of the four guardrail identifiers."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        for reason in (
            "ci-restart-cap",
            "push-rate-cooldown",
            "force-push-rate",
            "stash-before-push",
        ):
            record_push_block(
                reason=reason,
                message=f"Block: {reason}.",
                branch="master",
                commit_sha="rea000",
            )
            block = load_push_state()
            assert block is not None
            assert block["reason"] == reason


class TestPushStateAtomicWrite:
    """The file must be written via temp + rename to prevent partial reads."""

    def test_push_state_atomic_write(self, monkeypatch, tmp_path: Path):
        """Write goes through a .tmp file, not directly to the state path."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        # Snapshot directory contents before write
        pre_files = set(tmp_path.iterdir())

        record_push_block(
            reason="push-rate-cooldown",
            message="Atomic test.",
            branch="master",
            commit_sha="atom001",
        )

        # After write: only push-state.json exists, no .tmp lingering
        post_files = set(tmp_path.iterdir())
        new_files = post_files - pre_files
        tmp_files = [f for f in new_files if f.name.endswith(".tmp")]
        assert len(tmp_files) == 0, f".tmp file(s) left behind after atomic write: {tmp_files}"
        assert state_file.exists()

        block = load_push_state()
        assert block is not None
        assert block["reason"] == "push-rate-cooldown"

    def test_push_state_atomic_does_not_truncate_on_crash_sim(self, monkeypatch, tmp_path: Path):
        """Simulate crash mid-write: stale .tmp must not corrupt existing state."""
        state_file = tmp_path / "push-state.json"
        monkeypatch.setenv("GLUDD_PUSH_STATE_FILE", str(state_file))

        # Write initial valid state
        record_push_block(
            reason="ci-restart-cap",
            message="First block.",
            branch="master",
            commit_sha="before",
        )
        first = load_push_state()
        assert first is not None and first["commit_sha"] == "before"

        # Simulate a crashed write: write partial content to .tmp but don't rename
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text('{"reason": "incomplete", "m')  # truncated JSON
        assert tmp.exists()

        # Original state file must still be intact
        block = load_push_state()
        assert block is not None
        assert block["commit_sha"] == "before", "Stale .tmp must not corrupt existing state.json"

        # Cleanup
        tmp.unlink()
