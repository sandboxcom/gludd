"""TDD contract for gate attestations bound to the tested repository state."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from scripts.gate_status_attestation import (
    repository_state_id,
    sign_status,
    verify_status,
)

_KEY = b"k" * 32
_STATE = "state-" + "a" * 64


def _passed_status(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "=== GATE 2026-08-12T00:00:00Z ===",
                "lint PASS 0",
                "typecheck PASS 0",
                "collect PASS 0",
                "test PASS 0",
                "smoke PASS",
                "---",
                "epoch 1786492800",
                "=== GATE: PASSED ===",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_signed_current_status_is_accepted(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    now = int(time.time())

    sign_status(status, state_id=_STATE, key=_KEY, now=now)

    result = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=now + 1,
        freshness_seconds=60,
    )
    assert result.ok
    assert result.age_seconds == 1


def test_handwritten_pass_without_attestation_is_rejected(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)

    result = verify_status(status, state_id=_STATE, key=_KEY)

    assert not result.ok
    assert "attestation" in result.reason.lower()


@pytest.mark.parametrize(
    ("mutation", "reason_fragment"),
    (
        ("status", "status digest"),
        ("state", "repository state"),
        ("signature", "signature"),
    ),
)
def test_tampering_is_rejected(
    tmp_path: Path,
    mutation: str,
    reason_fragment: str,
) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    now = int(time.time())
    sign_status(status, state_id=_STATE, key=_KEY, now=now)
    state = _STATE
    if mutation == "status":
        status.write_text(
            status.read_text(encoding="utf-8").replace("smoke PASS", "smoke FAIL"),
            encoding="utf-8",
        )
    elif mutation == "state":
        state = "state-" + "b" * 64
    else:
        status.write_text(
            status.read_text(encoding="utf-8").replace(
                "attestation-signature ",
                "attestation-signature 0",
            ),
            encoding="utf-8",
        )

    result = verify_status(
        status,
        state_id=state,
        key=_KEY,
        now=now,
        freshness_seconds=60,
    )

    assert not result.ok
    assert reason_fragment in result.reason.lower()


def test_stale_and_future_attestations_are_rejected(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    sign_status(status, state_id=_STATE, key=_KEY, now=1_000)

    stale = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=1_061,
        freshness_seconds=60,
    )
    future = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=900,
        freshness_seconds=60,
    )

    assert not stale.ok and "stale" in stale.reason.lower()
    assert not future.ok and "future" in future.reason.lower()


def test_failed_or_incomplete_gate_cannot_be_signed(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8").replace(
            "=== GATE: PASSED ===",
            "=== GATE: FAILED ===",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="passed"):
        sign_status(status, state_id=_STATE, key=_KEY)


def test_duplicate_attestation_field_is_rejected(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    sign_status(status, state_id=_STATE, key=_KEY)
    status.write_text(
        status.read_text(encoding="utf-8") + "attestation-version 1\n",
        encoding="utf-8",
    )

    result = verify_status(status, state_id=_STATE, key=_KEY)

    assert not result.ok
    assert "duplicate" in result.reason.lower()


def test_worktree_and_index_state_must_converge_before_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q")
    git("config", "user.email", "gate-test@example.invalid")
    git("config", "user.name", "Gate Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-q", "-m", "initial")
    assert repository_state_id(repo) == repository_state_id(repo, source="index")

    tracked.write_text("two\n", encoding="utf-8")
    assert repository_state_id(repo) != repository_state_id(repo, source="index")
    git("add", "tracked.txt")
    assert repository_state_id(repo) == repository_state_id(repo, source="index")

    added = repo / "added.txt"
    added.write_text("new\n", encoding="utf-8")
    assert repository_state_id(repo) != repository_state_id(repo, source="index")
    git("add", "added.txt")
    assert repository_state_id(repo) == repository_state_id(repo, source="index")


def test_makefile_signs_final_gate_and_checks_before_commit() -> None:
    makefile = (Path(__file__).parents[2] / "Makefile").read_text(encoding="utf-8")

    assert "scripts/gate_status_attestation.py sign .gate-status" in makefile
    assert "scripts/gate_status_attestation.py verify .gate-status" in makefile
    assert "@echo run python scripts/gate_fresh_check.py check" not in makefile
    commit_recipe = makefile.split("\ngit-commit:", 1)[1].split("\n\n", 1)[0]
    assert "check-gate-fresh" in commit_recipe
