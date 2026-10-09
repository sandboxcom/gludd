"""TDD contract for gate attestations bound to the tested repository state."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import gate_status_attestation as attestation
from scripts.gate_status_attestation import (
    repository_state_id,
    sign_status,
    sign_terminal_status,
    verify_status,
    verify_terminal_status,
)
from scripts.makefile_layout import compose_makefile

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
        ).replace("lint PASS 0", "preflights FAIL 1"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="passed"):
        sign_status(status, state_id=_STATE, key=_KEY)


def test_failed_gate_can_be_authenticated_without_becoming_green(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8").replace(
            "=== GATE: PASSED ===",
            "=== GATE: FAILED ===",
        ).replace("lint PASS 0", "preflights FAIL 1"),
        encoding="utf-8",
    )

    sign_terminal_status(status, state_id=_STATE, key=_KEY, now=1_000)
    terminal = verify_terminal_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=1_001,
        freshness_seconds=60,
    )
    result = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=1_001,
        freshness_seconds=60,
    )

    assert terminal.ok
    assert not result.ok
    assert "uniquely completed passed gate" in result.reason
    assert "attestation-signature" in status.read_text(encoding="utf-8")


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


def test_uninitialized_submodule_uses_the_pinned_index_gitlink(tmp_path: Path) -> None:
    submodule = tmp_path / "submodule"
    parent = tmp_path / "parent"
    checkout = tmp_path / "checkout"
    submodule.mkdir()
    parent.mkdir()

    def git(repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    for repo in (submodule, parent):
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "gate-test@example.invalid")
        git(repo, "config", "user.name", "Gate Test")

    (submodule / "tracked.txt").write_text("submodule\n", encoding="utf-8")
    git(submodule, "add", "tracked.txt")
    git(submodule, "commit", "-q", "-m", "initial")
    git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(submodule),
        "external/example",
    )
    git(parent, "commit", "-q", "-am", "add submodule")
    subprocess.run(
        ["git", "clone", "-q", "--no-recurse-submodules", str(parent), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (checkout / "external" / "example").is_dir()
    assert repository_state_id(checkout) == repository_state_id(
        checkout,
        source="index",
    )


def test_makefile_signs_final_gate_and_checks_before_commit() -> None:
    makefile = compose_makefile(Path(__file__).parents[2] / "Makefile")

    assert "scripts/gate_status_attestation.py sign .gate-status" in makefile
    assert "scripts/gate_status_attestation.py verify .gate-status" in makefile
    assert "@echo run python scripts/gate_fresh_check.py check" not in makefile
    commit_recipe = makefile.split("\ngit-commit:", 1)[1].split("\n\n", 1)[0]
    assert "check-gate-fresh" in commit_recipe


def test_cli_sign_and_verify_use_one_private_key_and_exact_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = tmp_path / ".gate-status"
    key_path = tmp_path / "keys" / "gate.key"
    _passed_status(status)
    monkeypatch.setattr(attestation, "repository_state_id", lambda *_args, **_kwargs: _STATE)

    common = [str(status), "--repo-root", str(tmp_path), "--key-path", str(key_path)]
    assert attestation.main(["sign", *common]) == 0
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert attestation.main(["verify", *common, "--freshness-seconds", "60"]) == 0

    output = capsys.readouterr().out
    assert "gate attestation signed" in output
    assert "gate attestation valid" in output


def test_cli_authenticates_terminal_failure_without_admitting_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = tmp_path / ".gate-status"
    key_path = tmp_path / "gate.key"
    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8")
        .replace("lint PASS 0", "preflights FAIL 1")
        .replace("=== GATE: PASSED ===", "=== GATE: FAILED ==="),
        encoding="utf-8",
    )
    monkeypatch.setattr(attestation, "repository_state_id", lambda *_args, **_kwargs: _STATE)
    common = [str(status), "--repo-root", str(tmp_path), "--key-path", str(key_path)]

    assert attestation.main(["sign-terminal", *common]) == 0
    assert attestation.main(["verify", *common]) == 1
    captured = capsys.readouterr()
    assert "terminal gate attestation signed" in captured.out
    assert "uniquely completed passed gate" in captured.err


def test_cli_fails_closed_on_index_drift_and_invalid_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = tmp_path / ".gate-status"
    key_path = tmp_path / "gate.key"
    _passed_status(status)
    key_path.write_text(_KEY.hex(), encoding="ascii")
    sign_status(status, state_id=_STATE, key=_KEY)
    monkeypatch.setattr(
        attestation,
        "repository_state_id",
        lambda *_args, source="worktree": _STATE if source == "worktree" else "other",
    )
    common = [str(status), "--repo-root", str(tmp_path), "--key-path", str(key_path)]

    assert attestation.main(["verify", *common]) == 1
    assert "staged index does not match" in capsys.readouterr().err
    key_path.write_text("00", encoding="ascii")
    assert attestation.main(["verify", *common]) == 2
    assert "must contain 32 bytes" in capsys.readouterr().err


def test_verifier_rejects_missing_version_and_epoch_evidence(tmp_path: Path) -> None:
    missing = verify_status(tmp_path / "missing", state_id=_STATE, key=_KEY)
    assert not missing.ok and "missing" in missing.reason

    status = tmp_path / ".gate-status"
    _passed_status(status)
    sign_status(status, state_id=_STATE, key=_KEY, now=1_000)
    signed = status.read_text(encoding="utf-8")
    status.write_text(
        signed.replace("attestation-version 1", "attestation-version 2"),
        encoding="utf-8",
    )
    version = verify_status(status, state_id=_STATE, key=_KEY, now=1_001)
    assert not version.ok and "version" in version.reason

    status.write_text(
        signed.replace("attestation-epoch 1000", "attestation-epoch invalid"),
        encoding="utf-8",
    )
    epoch = verify_status(status, state_id=_STATE, key=_KEY, now=1_001)
    assert not epoch.ok and "integer" in epoch.reason


def test_signers_reject_malformed_or_incomplete_terminal_bodies(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    status.write_text("attestation-unknown value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        sign_status(status, state_id=_STATE, key=_KEY)

    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8").replace("smoke PASS", "smoke PENDING"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="required gate phase"):
        sign_status(status, state_id=_STATE, key=_KEY)

    status.write_text("=== GATE: FAILED ===\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no failed phase"):
        sign_terminal_status(status, state_id=_STATE, key=_KEY)


def test_git_helpers_reject_command_and_merge_stage_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        attestation.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"fatal",
        ),
    )
    with pytest.raises(RuntimeError, match="fatal"):
        attestation._run_git(tmp_path, "status")

    monkeypatch.setattr(
        attestation,
        "_run_git",
        lambda *_args: b"100644 " + b"a" * 40 + b" 2\tconflict.py\0",
    )
    with pytest.raises(RuntimeError, match="unresolved merge"):
        attestation._index_entries(tmp_path)
