"""TDD: the event loop records per-commit LOC deltas into the app.state ledger.

Covers the wiring requested in the loc_changed integration: after a git commit
inside ``_try_commit_completed_work``, the lines changed in that commit are
counted via ``GitAutomation.lines_changed_in_commit`` and accumulated on the
``LocLedger`` carried by the EventLoop (which the daemon stores on
``app.state._loc_ledger``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import pytest

from general_ludd.accounting.ledger import LocLedger
from general_ludd.event_loop.loop import EventLoop

_TICK = "general_ludd.git_automation.repo.GitAutomation"


class _FakeGit:
    """Stand-in for GitAutomation used inside _try_commit_completed_work."""

    instances: ClassVar[list[object]] = []

    def __init__(self, repo_path: str = "") -> None:
        self.repo_path = repo_path
        self.committed: list[str] = []
        _FakeGit.instances.append(self)

    def commit(self, message: str) -> str:
        self.committed.append(message)
        return "deadbeef" * 5

    def push(self, remote: str = "origin", branch: str = "main") -> bool:
        return True

    def lines_changed_in_commit(self, ref: str = "HEAD") -> int:
        return 12


@pytest.mark.asyncio
async def test_commit_records_loc_delta_into_ledger() -> None:
    ledger = LocLedger()
    loop = EventLoop(loc_ledger=ledger)
    todo = SimpleNamespace(
        todo_id="T-1",
        title="do thing",
        branch_name="gludd-t-1",
        worktree="/tmp/repo-T-1",
        project_id="proj-loc",
    )

    with patch(_TICK, _FakeGit):
        await loop._try_commit_completed_work(todo)

    assert ledger.total("proj-loc") == 12


@pytest.mark.asyncio
async def test_commit_without_ledger_does_not_raise() -> None:
    loop = EventLoop(loc_ledger=None)
    todo = SimpleNamespace(
        todo_id="T-2",
        title="do thing",
        branch_name="gludd-t-2",
        worktree="/tmp/repo-T-2",
        project_id="proj-nolegder",
    )

    with patch(_TICK, _FakeGit):
        await loop._try_commit_completed_work(todo)

    assert _FakeGit.instances[-1].committed  # commit ran


@pytest.mark.asyncio
async def test_loc_count_failure_does_not_abort_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = LocLedger()
    loop = EventLoop(loc_ledger=ledger)
    todo = SimpleNamespace(
        todo_id="T-3",
        title="do thing",
        branch_name="gludd-t-3",
        worktree="/tmp/repo-T-3",
        project_id="proj-fail",
    )

    class _BrokenLoc(_FakeGit):
        def lines_changed_in_commit(self, ref: str = "HEAD") -> int:
            raise RuntimeError("git show exploded")

    with patch(_TICK, _BrokenLoc):
        await loop._try_commit_completed_work(todo)

    assert ledger.total("proj-fail") == 0
