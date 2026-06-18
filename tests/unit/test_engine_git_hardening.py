"""Security hardening tests for the git helpers in execution/engine.py.

After the delegation refactor the four helpers (``_is_git_repo``,
``_git_create_branch``, ``_git_commit``, ``_git_current_branch``) delegate to
``GitAutomation`` in ``general_ludd.git_automation.repo``.  All hardening
properties are therefore inherited from ``GitAutomation._run_git``:

  * bounded ``timeout=`` so a hung remote / credential prompt cannot stall the
    daemon forever,
  * the non-interactive git env (``GIT_TERMINAL_PROMPT=0`` / ``GIT_ASKPASS=echo``)
    so git fails instead of blocking on a TTY prompt,
  * every invocation serialized under ``git_repo_lock`` so concurrent roles
    cannot race on ``.git/index.lock``, and
  * ``_git_create_branch`` must reject a dash-leading branch name BEFORE exec
    (option injection) and place ``--`` before the branch positional (delegated
    to ``GitAutomation.create_branch`` which already enforces both).

Tests patch the subprocess and lock at their actual call sites inside
``general_ludd.git_automation.repo`` / ``general_ludd.git_automation.locking``
rather than at engine-module level, because engine now delegates to those.
"""

from __future__ import annotations

import shutil
import subprocess
from contextlib import contextmanager
from unittest import mock

import pytest

import general_ludd.git_automation.repo as _repo_mod
from general_ludd.execution import engine
from general_ludd.git_automation.repo import _GIT_TIMEOUT_SECONDS, _NON_INTERACTIVE_GIT_ENV

# --- helpers ---------------------------------------------------------------


def _ok_run(*, returncode: int = 0, stdout: str = "") -> mock.Mock:
    cp = mock.Mock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = ""
    return cp


# --- timeout is passed (via GitAutomation._run_git) ------------------------


def test_is_git_repo_passes_timeout_and_env() -> None:
    with mock.patch.object(_repo_mod.subprocess, "run", return_value=_ok_run()) as run:
        engine._is_git_repo("/some/repo")
    assert run.call_count == 1
    kwargs = run.call_args.kwargs
    assert kwargs["timeout"] == _GIT_TIMEOUT_SECONDS
    for key, val in _NON_INTERACTIVE_GIT_ENV.items():
        assert kwargs["env"][key] == val


def test_create_branch_passes_timeout_and_env() -> None:
    with mock.patch.object(_repo_mod.subprocess, "run", return_value=_ok_run()) as run:
        assert engine._git_create_branch("/some/repo", "feature/x") is True
    kwargs = run.call_args.kwargs
    assert kwargs["timeout"] == _GIT_TIMEOUT_SECONDS
    for key, val in _NON_INTERACTIVE_GIT_ENV.items():
        assert kwargs["env"][key] == val


def test_current_branch_passes_timeout_and_env() -> None:
    with mock.patch.object(
        _repo_mod.subprocess, "run", return_value=_ok_run(stdout="main\n")
    ) as run:
        assert engine._git_current_branch("/some/repo") == "main"
    kwargs = run.call_args.kwargs
    assert kwargs["timeout"] == _GIT_TIMEOUT_SECONDS
    for key, val in _NON_INTERACTIVE_GIT_ENV.items():
        assert kwargs["env"][key] == val


def test_commit_passes_timeout_and_env_on_every_call() -> None:
    with mock.patch.object(
        _repo_mod.subprocess, "run", return_value=_ok_run(stdout="abcdef1234\n")
    ) as run:
        sha = engine._git_commit("/some/repo", "msg")
    assert sha == "abcdef12"
    # add, commit, rev-parse — all three carry the bound + env.
    assert run.call_count == 3
    for call in run.call_args_list:
        assert call.kwargs["timeout"] == _GIT_TIMEOUT_SECONDS
        for key, val in _NON_INTERACTIVE_GIT_ENV.items():
            assert call.kwargs["env"][key] == val


# --- lock is used (via git_automation.locking.git_repo_lock) ---------------


@contextmanager
def _tracking_lock(seen: list[str], path: str):
    seen.append(path)
    yield


def test_each_helper_acquires_the_repo_lock() -> None:
    for fn, args, min_lock_count in (
        (engine._is_git_repo, ("/repo-a",), 1),
        (engine._git_create_branch, ("/repo-b", "feature/y"), 1),
        # _git_commit: add + commit + rev-parse = 3 _run_git calls → 3 lock acquisitions
        (engine._git_commit, ("/repo-c", "m"), 3),
        (engine._git_current_branch, ("/repo-d",), 1),
    ):
        seen: list[str] = []
        with (
            mock.patch.object(
                _repo_mod, "git_repo_lock",
                side_effect=lambda p, _s=seen, **kw: _tracking_lock(_s, p),
            ),
            mock.patch.object(
                _repo_mod.subprocess, "run",
                return_value=_ok_run(stdout="deadbeef\n"),
            ),
        ):
            fn(*args)
        assert len(seen) >= min_lock_count, (
            f"{fn.__name__} did not acquire the repo lock (seen={seen!r})"
        )
        # Every lock acquisition must be for the expected repo path.
        assert all(p == args[0] for p in seen), (
            f"{fn.__name__} acquired lock for wrong path: {seen!r}"
        )


# --- dash-leading branch rejected (never reaches subprocess) ---------------


def test_create_branch_rejects_leading_dash_without_exec() -> None:
    with mock.patch.object(_repo_mod.subprocess, "run") as run:
        result = engine._git_create_branch("/repo", "--upload-pack=evil")
    assert result is False
    run.assert_not_called()  # refused BEFORE any git exec


def test_create_branch_adds_end_of_options_separator() -> None:
    with mock.patch.object(_repo_mod.subprocess, "run", return_value=_ok_run()) as run:
        engine._git_create_branch("/repo", "feature/x")
    argv = run.call_args.args[0]
    assert argv[:3] == ["git", "checkout", "-b"]
    # `--` must follow the branch so a name can never be reparsed as an option.
    assert "--" in argv
    assert argv.index("--") == argv.index("feature/x") + 1


# --- fail-closed on TimeoutExpired -----------------------------------------


def test_is_git_repo_fails_closed_on_timeout() -> None:
    with mock.patch.object(
        _repo_mod.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60),
    ):
        assert engine._is_git_repo("/repo") is False


def test_create_branch_fails_closed_on_timeout() -> None:
    with mock.patch.object(
        _repo_mod.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60),
    ):
        assert engine._git_create_branch("/repo", "feature/x") is False


def test_commit_fails_closed_on_timeout() -> None:
    with mock.patch.object(
        _repo_mod.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60),
    ):
        assert engine._git_commit("/repo", "msg") is None


def test_current_branch_fails_closed_on_timeout() -> None:
    with mock.patch.object(
        _repo_mod.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60),
    ):
        assert engine._git_current_branch("/repo") == "unknown"


# --- real-repo smoke -------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_real_repo_smoke(tmp_path) -> None:
    """End-to-end against a real on-disk git repo (no mocks)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.local"], cwd=repo,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo,
        capture_output=True, check=True,
    )

    assert engine._is_git_repo(str(repo)) is True
    assert engine._is_git_repo(str(tmp_path / "not-a-repo")) is False

    # Establish an initial commit first so HEAD points at a real branch (a fresh
    # `git init` checkout has an UNBORN branch — rev-parse --abbrev-ref HEAD
    # reports "HEAD" until the first commit lands, which is unrelated to our
    # hardening). This mirrors the engine: branch is cut, then a commit is made.
    (repo / "seed.txt").write_text("seed")
    seed_sha = engine._git_commit(str(repo), "seed commit")
    assert seed_sha is not None
    assert len(seed_sha) == 8

    assert engine._git_create_branch(str(repo), "gludd/feature-x") is True
    assert engine._git_current_branch(str(repo)) == "gludd/feature-x"

    (repo / "file.txt").write_text("hello")
    sha = engine._git_commit(str(repo), "feature commit")
    assert sha is not None
    assert len(sha) == 8

    # A dash-leading branch is refused fail-closed even on a real repo.
    assert engine._git_create_branch(str(repo), "-evil") is False
