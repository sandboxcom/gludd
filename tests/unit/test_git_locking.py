"""Tests for per-repo git serialization (issue #63).

The product bug: gludd runs roles in parallel and many call git against the
same repo, with NO serialization, so concurrent roles race on
``.git/index.lock`` (git aborts "Another git process is running"), on HEAD, and
on commits (lost / interleaved). These tests pin the contract of
``git_automation.locking.git_repo_lock`` and its wiring into
``GitAutomation._run_git``.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

import pytest

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.git_automation.repo import GitAutomation


def _init_repo(path: str) -> GitAutomation:
    auto = GitAutomation(path)
    auto.init_repo(path)
    # Seed one commit so HEAD exists.
    readme = os.path.join(path, "README.md")
    with open(readme, "w") as fh:
        fh.write("seed\n")
    auto.commit("seed")
    return auto


# --- in-process serialization (the common in-daemon race) ----------------


def test_two_threads_mutating_same_repo_no_lock_error_no_lost_commit(tmp_path):
    """Two threads each performing an atomic mutating git op against the SAME
    repo must both succeed (no ``index.lock`` / "Another git process" error)
    and produce TWO distinct commits (none lost to the race).

    Each worker uses a SINGLE serialized git invocation (``commit --allow-empty``
    via ``_run_git``) so the test isolates the per-repo serialization guarantee
    — the bug #63 fix — from higher-level multi-step atomicity. Without the lock
    these collide on ``.git/index.lock`` / the ref update; with it they serialize
    and both commits land linearly."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    auto = _init_repo(repo)
    start = auto.get_current_commit()

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        try:
            barrier.wait()  # maximize the race window
            # One atomic, serialized git invocation per worker: an empty commit
            # is a HEAD-ref mutation that races on index.lock / ref-lock without
            # serialization, yet always has "something to commit" (so a benign
            # interleave can't turn into a spurious nothing-to-commit).
            auto._run_git("commit", "--allow-empty", "-m", f"commit {name}")
        except Exception as exc:  # record for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent commits raced / failed: {errors}"
    # Both commits landed on top of the seed -> linear history of 3 commits.
    log = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    assert int(log.stdout.strip()) == 3, "a commit was lost to the race"
    assert auto.get_current_commit() != start


def test_index_lock_never_observed_under_contention(tmp_path):
    """Under heavy contention, no git op fails with the 'Another git process'
    / index.lock error — the serializer prevents the collision entirely.

    We assert specifically on the index.lock / ref-lock collision signature
    (the bug #63 symptom), so the test pins the serialization guarantee and is
    not confused by unrelated git exit codes."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    auto = _init_repo(repo)

    collisions: list[str] = []

    def is_lock_collision(stderr: str) -> bool:
        return (
            "index.lock" in stderr
            or "Another git process" in stderr
            or "cannot lock ref" in stderr
        )

    def worker(i: int) -> None:
        for j in range(3):
            try:
                auto._run_git("commit", "--allow-empty", "-m", f"w{i}-{j}")
            except subprocess.CalledProcessError as exc:
                if exc.stderr and is_lock_collision(exc.stderr):
                    collisions.append(exc.stderr)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not collisions, f"git lock collision under contention: {collisions}"
    # All 12 serialized commits landed (4 workers x 3) on top of the seed.
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    assert int(count.stdout.strip()) == 1 + 4 * 3, "commits lost under contention"


# --- different repos do not block each other -----------------------------


def test_different_repos_do_not_block_each_other(tmp_path):
    """A lock held on repo A must NOT block work on repo B."""
    repo_a = str(tmp_path / "a")
    repo_b = str(tmp_path / "b")
    os.makedirs(repo_a)
    os.makedirs(repo_b)
    # Need .git dirs so the cross-process lock path is exercised, not skipped.
    GitAutomation(repo_a).init_repo(repo_a)
    GitAutomation(repo_b).init_repo(repo_b)

    held = threading.Event()
    release = threading.Event()

    def hold_a() -> None:
        with git_repo_lock(repo_a):
            held.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_a)
    holder.start()
    assert held.wait(timeout=5), "holder never acquired repo A lock"

    # While A is held, acquiring B must succeed promptly (not block on A).
    t0 = time.monotonic()
    with git_repo_lock(repo_b, timeout=2.0):
        acquired_b_in = time.monotonic() - t0
    assert acquired_b_in < 1.0, "repo B blocked on a lock held for repo A"

    release.set()
    holder.join(timeout=5)


# --- stale lock breaking --------------------------------------------------


def test_file_lock_breaks_stale_lock_after_timeout(tmp_path):
    """A leftover lock file from a crashed holder (old mtime, no live flock)
    must be broken so a fresh acquire still succeeds within the timeout."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    GitAutomation(repo).init_repo(repo)
    git_dir = os.path.join(repo, ".git")
    lock_file = os.path.join(git_dir, "gludd-git.lock")

    # Simulate a crashed holder: a stale lock file, no process holding flock.
    with open(lock_file, "w") as fh:
        fh.write("")
    old = time.time() - 10_000
    os.utime(lock_file, (old, old))

    # With a small stale_after, the fresh acquire breaks the stale file and
    # succeeds well within the acquire timeout.
    t0 = time.monotonic()
    with git_repo_lock(repo, timeout=5.0, stale_after=1.0):
        elapsed = time.monotonic() - t0
    assert elapsed < 5.0, "stale lock was not broken; acquire timed out"


def test_acquire_times_out_when_held_by_another_process(tmp_path):
    """When the cross-process flock is genuinely held (live, fresh), a second
    acquire with a short timeout raises TimeoutError rather than hanging."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    GitAutomation(repo).init_repo(repo)
    git_dir = os.path.join(repo, ".git")
    lock_file = os.path.join(git_dir, "gludd-git.lock")

    fcntl = pytest.importorskip("fcntl")
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    # Keep it fresh so stale-breaking does not kick in during the short window.
    os.utime(lock_file, None)
    try:
        # stale_after large so it cannot be broken; held fresh -> must time out.
        with pytest.raises(TimeoutError), git_repo_lock(
            repo, timeout=0.5, stale_after=10_000
        ):
            pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --- re-entrancy ----------------------------------------------------------


def test_reentrant_same_repo_same_thread_no_self_deadlock(tmp_path):
    """Nested ``git_repo_lock`` on the same repo in one thread must not
    self-deadlock (re-entrant)."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    GitAutomation(repo).init_repo(repo)

    done = threading.Event()

    def nested() -> None:
        # Intentionally nested (not combined) — that IS the re-entrancy under test.
        # SIM117 suppressed: combining the with-statements would defeat the test.
        with git_repo_lock(repo):  # noqa: SIM117
            with git_repo_lock(repo):
                with git_repo_lock(repo):
                    pass
        done.set()

    t = threading.Thread(target=nested)
    t.start()
    t.join(timeout=5)
    assert done.is_set(), "nested git_repo_lock self-deadlocked"


def test_nested_run_git_same_repo_does_not_deadlock(tmp_path):
    """``commit`` calls ``_run_git`` several times in sequence; with the lock
    wired into ``_run_git`` this must still work (the lock is re-entrant)."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    auto = _init_repo(repo)

    fpath = os.path.join(repo, "x.txt")
    with open(fpath, "w") as fh:
        fh.write("y")
    # commit() internally does add + commit + rev-parse — three _run_git calls
    # each taking the per-repo lock. Re-entrancy is what keeps this alive.
    sha = auto.commit("nested run_git")
    assert sha and auto.get_current_commit() == sha


# --- repo.py wiring -------------------------------------------------------


def test_run_git_holds_lock_for_duration(tmp_path, monkeypatch):
    """Every git invocation through ``_run_git`` must hold the per-repo lock for
    its duration — proven by observing the in-process lock is held when the
    subprocess actually runs."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    auto = _init_repo(repo)

    from general_ludd.git_automation import locking

    key = locking._normalize(repo)
    lock = locking._get_inprocess_lock(key)

    observed = {}
    real_run = subprocess.run

    def spy_run(*args, **kwargs):
        # If _run_git holds the RLock, a *different* thread cannot acquire it.
        acquired = lock.acquire(blocking=False)
        if acquired:
            # We got it -> re-entrant from the SAME thread (the lock is held by
            # us). Release the extra acquisition; held==True still holds.
            lock.release()
        # Confirm a foreign thread is blocked out while we run.
        result_holder = {}

        def foreign() -> None:
            result_holder["got"] = lock.acquire(blocking=False)
            if result_holder["got"]:
                lock.release()

        th = threading.Thread(target=foreign)
        th.start()
        th.join(timeout=2)
        observed["foreign_blocked"] = not result_holder.get("got", True)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy_run)
    auto._run_git("rev-parse", "HEAD")
    assert observed.get("foreign_blocked") is True, (
        "_run_git did not hold the per-repo lock while the subprocess ran"
    )
