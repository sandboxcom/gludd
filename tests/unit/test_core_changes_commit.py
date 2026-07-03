"""Tests for the ``gludd core-changes commit`` CLI (FIM change-export SLICE B).

Seed a :class:`ChangeRecordStore`, then drive ``_cmd_core_changes_commit``
against a REAL temporary git repo, using a trivial ``["true"]``/``["false"]``
gate command so no nested ``make gate`` ever runs. Covers: successful
materialise + gated commit + record pruning, a failing gate (fail-closed: no
sha, record left unpruned), the default core-only selection skipping user /
excluded records, and a path that escapes ``--repo`` raising ``ValueError``.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from general_ludd.cli_core_changes import _cmd_core_changes_commit
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.integrity.change_log import ChangeRecordStore


def _args(store_dir: str, repo: str, **overrides: object) -> argparse.Namespace:
    ns = argparse.Namespace(
        store_dir=store_dir,
        repo=repo,
        gate="true",  # trivial success gate — NEVER "make gate"
        message="",
        all=False,
        id=None,
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    GitAutomation(str(path)).init_repo()
    return str(path)


def _run(args: argparse.Namespace) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _cmd_core_changes_commit(args)
    return rc, buf.getvalue()


def test_success_materialises_and_marks_committed(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store_dir = tmp_path / "store"
    repo = _init_repo(tmp_path / "repo")

    store = ChangeRecordStore(store_dir=str(store_dir))
    rec = store.record(
        "/orig/worktree/src/general_ludd/routers/foo.py",
        "config",
        "tidy foo",
        old_content="x = 0\n",
        new_content="x = 1\n",
    )

    rc, out = _run(_args(str(store_dir), repo))

    assert rc == 0
    # File materialised at the re-anchored src/general_ludd location.
    written = Path(repo) / "src" / "general_ludd" / "routers" / "foo.py"
    assert written.read_text() == "x = 1\n"
    # A commit sha was printed.
    assert out.strip()
    # The record is marked committed (no longer unpruned).
    remaining = ChangeRecordStore(store_dir=str(store_dir)).list_records(
        unpruned_only=True
    )
    assert remaining == []
    assert rec.id == 0  # sanity: first record


def test_message_defaults_to_record_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store_dir = tmp_path / "store"
    repo = _init_repo(tmp_path / "repo")

    store = ChangeRecordStore(store_dir=str(store_dir))
    store.record(
        "/w/src/general_ludd/bar.py",
        "config",
        "the meaningful reason",
        new_content="y = 2\n",
    )

    rc, _out = _run(_args(str(store_dir), repo))
    assert rc == 0

    # The HEAD commit message is the record's reason (no --message given).
    result = GitAutomation(repo)._run_git("log", "-1", "--pretty=%s")
    assert result.stdout.strip() == "the meaningful reason"


def test_gate_failure_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store_dir = tmp_path / "store"
    repo = _init_repo(tmp_path / "repo")

    store = ChangeRecordStore(store_dir=str(store_dir))
    store.record(
        "/w/src/general_ludd/baz.py",
        "config",
        "should not commit",
        new_content="z = 3\n",
    )

    rc, out = _run(_args(str(store_dir), repo, gate="false"))

    assert rc == 1
    assert "commit failed" in out
    # Record left UNPRUNED so it can be retried.
    remaining = ChangeRecordStore(store_dir=str(store_dir)).list_records(
        unpruned_only=True
    )
    assert len(remaining) == 1


def test_default_selection_skips_user_and_excluded(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store_dir = tmp_path / "store"
    repo = _init_repo(tmp_path / "repo")

    store = ChangeRecordStore(store_dir=str(store_dir))
    # A user-classified change (outside gludd) — not selected by default.
    store.record(
        "/home/op/project/app.py",
        "config",
        "user project change",
        new_content="a = 1\n",
    )

    rc, out = _run(_args(str(store_dir), repo))

    assert rc == 0
    assert "no matching" in out
    # Untouched: still unpruned.
    remaining = ChangeRecordStore(store_dir=str(store_dir)).list_records(
        unpruned_only=True
    )
    assert len(remaining) == 1


def test_excluded_record_dropped_even_with_all(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store_dir = tmp_path / "store"
    repo = _init_repo(tmp_path / "repo")

    store = ChangeRecordStore(store_dir=str(store_dir))
    # A compiled artefact — excluded, dropped before selection even with --all.
    store.record(
        "/w/src/general_ludd/foo.pyc",
        "config",
        "compiled artefact",
        new_content="ignored\n",
    )

    rc, out = _run(_args(str(store_dir), repo, all=True))

    assert rc == 0
    assert "no matching" in out
    remaining = ChangeRecordStore(store_dir=str(store_dir)).list_records(
        unpruned_only=True
    )
    assert len(remaining) == 1


def test_path_escaping_repo_raises_and_leaves_record(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store_dir = tmp_path / "store"
    repo = _init_repo(tmp_path / "repo")

    store = ChangeRecordStore(store_dir=str(store_dir))
    # An absolute path with no src/general_ludd marker -> re-anchoring leaves it
    # absolute -> AtomicSafeWriter refuses it (escapes the repo root).
    rec = store.record(
        "/etc/evil_target.conf",
        "config",
        "escape attempt",
        new_content="pwned\n",
    )

    with pytest.raises(ValueError):
        _run(_args(str(store_dir), repo, id=rec.id))

    # Fail-closed: the record was never marked committed.
    remaining = ChangeRecordStore(store_dir=str(store_dir)).list_records(
        unpruned_only=True
    )
    assert len(remaining) == 1
