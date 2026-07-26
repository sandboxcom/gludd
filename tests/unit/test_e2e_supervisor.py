"""TDD coverage for durable, restartable E2E execution state."""

from pathlib import Path

from scripts.e2e_supervisor import ensure_state, heartbeat, pending_files, record_status


def test_completed_files_are_not_scheduled_again(tmp_path: Path) -> None:
    state = tmp_path / "e2e-state.json"
    ensure_state(state, revision="abc")
    record_status(state, "tests/e2e/test_a.py", "PASS")
    record_status(state, "tests/e2e/test_b.py", "SKIP")

    assert pending_files(state, ["tests/e2e/test_a.py", "tests/e2e/test_b.py", "tests/e2e/test_c.py"]) == [
        "tests/e2e/test_c.py"
    ]


def test_resume_starts_at_first_failed_file(tmp_path: Path) -> None:
    state = tmp_path / "e2e-state.json"
    ensure_state(state, revision="abc")
    record_status(state, "tests/e2e/test_a.py", "PASS")
    record_status(state, "tests/e2e/test_b.py", "FAIL")

    assert pending_files(state, ["tests/e2e/test_a.py", "tests/e2e/test_b.py", "tests/e2e/test_c.py"]) == [
        "tests/e2e/test_b.py",
        "tests/e2e/test_c.py",
    ]


def test_new_revision_resets_prior_completion_state(tmp_path: Path) -> None:
    state = tmp_path / "e2e-state.json"
    ensure_state(state, revision="abc")
    record_status(state, "tests/e2e/test_a.py", "PASS")

    reset = ensure_state(state, revision="def")

    assert reset["revision"] == "def"
    assert pending_files(state, ["tests/e2e/test_a.py"]) == ["tests/e2e/test_a.py"]


def test_heartbeat_is_persisted(tmp_path: Path) -> None:
    state = tmp_path / "e2e-state.json"
    ensure_state(state, revision="abc")

    updated = heartbeat(state)

    assert updated["last_heartbeat"] > 0
