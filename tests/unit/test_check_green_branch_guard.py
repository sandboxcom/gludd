"""Behavioral coverage for the immutable green-branch push guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest


class GuardModule(Protocol):
    """Typed surface exercised from the standalone guard script."""

    def _run(self, cmd: list[str]) -> tuple[int, str, str]: ...

    def _remote_tip(self, branch: str, remote: str = "sandboxcom") -> str | None: ...

    def _ci_verdict(self, sha: str) -> str: ...

    def _local_head(self) -> str: ...

    def _commits_ahead(self, remote_sha: str, local_head: str) -> int: ...

    def main(self, argv: list[str]) -> int: ...


def _load_guard() -> GuardModule:
    """Load the standalone script as a typed module."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_green_branch_guard.py"
    spec = importlib.util.spec_from_file_location("check_green_branch_guard_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(GuardModule, module)


def test_run_returns_output_and_converts_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The subprocess seam normalizes success and failures without raising."""
    guard = _load_guard()

    class Result:
        returncode = 0
        stdout = "  ok\n"
        stderr = "  note\n"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: Result())
    assert guard._run(["git", "status"]) == (0, "ok", "note")

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("bounded")

    monkeypatch.setattr("subprocess.run", raise_timeout)
    assert guard._run(["git", "status"]) == (2, "", "bounded")


def test_remote_tip_handles_overrides_and_command_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remote lookup distinguishes new branches, failures, and existing tips."""
    guard = _load_guard()
    monkeypatch.setenv("GLUDD_GUARD_REMOTE_SHA_OVERRIDE", "")
    assert guard._remote_tip("release/test") is None
    monkeypatch.setenv("GLUDD_GUARD_REMOTE_SHA_OVERRIDE", "abc123")
    assert guard._remote_tip("release/test") == "abc123"
    monkeypatch.delenv("GLUDD_GUARD_REMOTE_SHA_OVERRIDE")
    monkeypatch.setattr(guard, "_run", lambda cmd: (1, "", "offline"))
    assert guard._remote_tip("release/test") is None
    monkeypatch.setattr(guard, "_run", lambda cmd: (0, "abc123 refs/heads/release/test", ""))
    assert guard._remote_tip("release/test") == "abc123"


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, "GREEN"), (2, "PENDING"), (1, "RED")],
)
def test_ci_verdict_maps_checker_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    expected: str,
) -> None:
    """Hosted verdict exit codes map to the guard's stable vocabulary."""
    guard = _load_guard()
    monkeypatch.setattr(guard, "_run", lambda cmd: (returncode, "", ""))
    assert guard._ci_verdict("abc123") == expected
    monkeypatch.setenv("GLUDD_GUARD_CI_VERDICT_OVERRIDE", "NONE")
    assert guard._ci_verdict("abc123") == "NONE"


def test_local_head_and_ahead_count_are_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Git lookup failures cannot invent commits beyond the remote tip."""
    guard = _load_guard()
    monkeypatch.setenv("GLUDD_GUARD_HEAD_SHA_OVERRIDE", "head123")
    assert guard._local_head() == "head123"
    monkeypatch.delenv("GLUDD_GUARD_HEAD_SHA_OVERRIDE")
    monkeypatch.setattr(guard, "_run", lambda cmd: (0, "head456", ""))
    assert guard._local_head() == "head456"

    monkeypatch.setenv("GLUDD_GUARD_AHEAD_OVERRIDE", "3")
    assert guard._commits_ahead("remote", "head") == 3
    monkeypatch.setenv("GLUDD_GUARD_AHEAD_OVERRIDE", "invalid")
    assert guard._commits_ahead("remote", "head") == 0
    monkeypatch.delenv("GLUDD_GUARD_AHEAD_OVERRIDE")
    assert guard._commits_ahead("", "head") == 0
    assert guard._commits_ahead("abcdef", "abc") == 0
    monkeypatch.setattr(guard, "_run", lambda cmd: (1, "", "failed"))
    assert guard._commits_ahead("remote", "head") == 0
    monkeypatch.setattr(guard, "_run", lambda cmd: (0, "invalid", ""))
    assert guard._commits_ahead("remote", "head") == 0
    monkeypatch.setattr(guard, "_run", lambda cmd: (0, "4", ""))
    assert guard._commits_ahead("remote", "head") == 4


@pytest.mark.parametrize(
    ("remote", "verdict", "ahead", "expected", "message"),
    [
        (None, "NONE", 0, 0, "no remote tip"),
        ("abcdef123", "RED", 1, 0, "is RED"),
        ("abcdef123", "GREEN", 0, 0, "adds no new commits"),
        ("abcdef123", "GREEN", 2, 1, "ff-only merge to master"),
    ],
)
def test_main_enforces_immutable_green_tip(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    remote: str | None,
    verdict: str,
    ahead: int,
    expected: int,
    message: str,
) -> None:
    """Only a green remote with additional local commits is blocked."""
    guard = _load_guard()
    monkeypatch.setattr(guard, "_remote_tip", lambda branch: remote)
    monkeypatch.setattr(guard, "_ci_verdict", lambda sha: verdict)
    monkeypatch.setattr(guard, "_local_head", lambda: "fedcba987")
    monkeypatch.setattr(guard, "_commits_ahead", lambda remote_sha, local_head: ahead)
    assert guard.main(["guard", "--branch", "release/test"]) == expected
    captured = capsys.readouterr()
    assert message in captured.out + captured.err


def test_main_requires_branch(capsys: pytest.CaptureFixture[str]) -> None:
    """Missing branch input fails closed as a usage error."""
    guard = _load_guard()
    assert guard.main(["guard"]) == 2
    assert "--branch <name> required" in capsys.readouterr().err
