"""Native commit input boundaries must refuse without starting a mutation child."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from general_ludd.git_automation import locking


@pytest.fixture
def transaction_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use real private Git metadata and preserve unrelated caller environment."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    (tmp_path / "Makefile").write_text(".PHONY: ship-commit\nship-commit:\n\t@exit 99\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAKEFLAGS", "")
    return tmp_path


def _transaction(
    observer_fn: locking._CommitObserver | None = None,
    identity_fn: Callable[[], str] | None = None,
) -> int:
    return locking._run_make_transaction(
        "ship-commit", "make", "Makefile", observed_root=".gate-logs/observed",
        heartbeat_seconds=2, quiet_seconds=0, max_seconds=30, retain_runs=4,
        observer_fn=observer_fn, identity_fn=identity_fn,
    )


def _unexpected_observer(command: Sequence[str], log_path: Path, **options: object) -> int:
    raise AssertionError("input validation started the observer")


@pytest.mark.parametrize(
    ("transaction_make_flags", "transaction_mode", "transaction_status"),
    [("--question", "query", 1), ("--dry-run", "dry-run", 0), ("--touch", "touch", 0)],
)
def test_inspection_preserves_checkout_without_resolving_or_starting_observer(
    transaction_checkout: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], transaction_make_flags: str,
    transaction_mode: str, transaction_status: int,
) -> None:
    initial_makefile = (transaction_checkout / "Makefile").read_bytes()
    monkeypatch.setenv("MAKEFLAGS", transaction_make_flags)
    monkeypatch.setattr(locking, "_default_observer", lambda: pytest.fail("inspection loaded an observer"))
    assert _transaction() == transaction_status
    guidance = capsys.readouterr().err
    assert f"mode={transaction_mode}" in guidance
    assert f"native_status={transaction_status}" in guidance
    assert "source_checkpoint=false" in guidance and "release_ready=false" in guidance
    assert (transaction_checkout / "Makefile").read_bytes() == initial_makefile
    assert not (transaction_checkout / ".gate-logs").exists()
    assert not (transaction_checkout / ".git" / "gludd-git.lock").exists()


@pytest.mark.parametrize("transaction_binding", ["observer-only", "identity-only", "non-function"])
def test_incomplete_or_non_native_observer_bindings_refuse_before_locking(
    transaction_checkout: Path, transaction_binding: str,
) -> None:
    class CallableObserver:
        def __call__(self, command: Sequence[str], log_path: Path, **options: object) -> int:
            raise AssertionError("non-native observer was executed")

    def unused_identity() -> str:
        raise AssertionError("input validation requested a run identity")

    observer_fn: locking._CommitObserver | None = _unexpected_observer
    identity_fn: Callable[[], str] | None = unused_identity
    if transaction_binding == "observer-only":
        identity_fn = None
    elif transaction_binding == "identity-only":
        observer_fn = None
    else:
        observer_fn = CallableObserver()
    message = "actual source function" if transaction_binding == "non-function" else "bound together"
    with pytest.raises(ValueError, match=message):
        _transaction(observer_fn, identity_fn)
    assert not (transaction_checkout / ".gate-logs").exists()
    assert not (transaction_checkout / ".git" / "gludd-git.lock").exists()


def test_native_jobserver_rejects_regular_files_without_closing_caller_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (tmp_path / "read").open("w") as first, (tmp_path / "write").open("w") as second:
        monkeypatch.setenv("MAKEFLAGS", f"--jobserver-auth={first.fileno()},{second.fileno()}")
        with pytest.raises(ValueError, match="not a pipe"):
            locking._native_make_options()
        first.write("still owned")
        second.write("still owned")


def test_native_jobserver_rejects_mismatched_real_pipe_pairs_and_preserves_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = os.pipe(), os.pipe()
    try:
        monkeypatch.setenv(
            "MAKEFLAGS",
            f"--jobserver-auth={first[0]},{first[1]} --jobserver-fds={second[0]},{second[1]}",
        )
        with pytest.raises(ValueError, match="mismatched"):
            locking._native_make_options()
        for read_fd, write_fd in (first, second):
            assert os.write(write_fd, b"owned") == 5
            assert os.read(read_fd, 5) == b"owned"
    finally:
        for fd in (*first, *second):
            os.close(fd)


def test_direct_cli_cannot_skip_the_native_stream_owner(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as failure:
        locking.main(["--make-target", "ship-commit"])
    assert failure.value.code == 2
    assert "existing stream_command Make owner" in capsys.readouterr().err
