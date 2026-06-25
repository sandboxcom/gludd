"""C2 coverage: event-loop ``asyncio.to_thread`` offload + failure/timeout paths.

POST_COMMIT_BACKLOG_2026-06-24.md C2: the loop's async-offload paths and the
reviewer failure/timeout handling were under-tested. The loop offloads blocking
work to a worker thread so the event loop stays responsive:

  * ``_review_in_process`` offloads the BLOCKING ``reviewer.review_return`` via
    ``asyncio.to_thread`` (loop.py ~676) and the returned ``TaskDecision`` flows
    back into ``apply_decision``;
  * if the offloaded reviewer RAISES (a real timeout/hang surfaces here as an
    exception from the worker thread), the loop catches it (loop.py ~682) and
    converts it to a ``manual_hold`` decision — it never silently completes;
  * ``_try_commit_completed_work`` offloads blocking git (``commit`` / ``push``)
    through ``asyncio.to_thread`` (loop.py ~1806/1822) so a 60s git call can
    never freeze the loop.

The existing ``test_event_loop.py`` pins that ``review_return`` is *handed* to
``to_thread``; these tests additionally assert the OFFLOADED RESULT FLOWS BACK
(decision -> apply_decision) and that the FAILURE path is handled — neither was
covered before. Everything is hermetic: the gateway/reviewer/runner/git are
stubs and ``to_thread`` is patched with a stub that records its target and runs
it synchronously so no real threads spin.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision


def _recording_to_thread(calls: list[tuple[Any, tuple, dict]]) -> Any:
    """An async ``to_thread`` stub: record (fn, args, kwargs), then run fn.

    Running the (synchronous) target synchronously lets the real return value
    flow back exactly as a worker thread would deliver it, while ``calls`` makes
    the offload contract observable (the blocking fn must be offloaded, not
    called inline)."""

    async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((fn, args, kwargs))
        return fn(*args, **kwargs)

    return _run


def _review_tr() -> MagicMock:
    tr = MagicMock()
    tr.return_id = "RET-C2-001"
    tr.todo_id = "TODO-C2-001"
    tr.job_id = "JOB-C2-001"
    tr.playbook = "noop.yml"
    tr.queue = "model"
    tr.work_type = "review"
    tr.exit_code = 0
    tr.result_summary = "all good"
    return tr


class TestReviewerOffload:
    @pytest.mark.asyncio
    async def test_reviewer_offloaded_and_decision_flows_back(self) -> None:
        """The blocking reviewer is offloaded via to_thread AND its returned
        decision is delivered to apply_decision (result flows back off-thread)."""
        decision = TaskDecision(
            return_id="RET-C2-001",
            matched_todo_id="TODO-C2-001",
            decision="complete",
            confidence=0.99,
        )
        reviewer = MagicMock()
        reviewer.review_return.return_value = decision

        session = AsyncMock()
        session.flush = AsyncMock()
        loop = EventLoop(
            reviewer=reviewer,
            todo_repo=AsyncMock(),
            session=session,
        )

        calls: list[tuple[Any, tuple, dict]] = []
        apply_mock = AsyncMock()
        with (
            patch(
                "general_ludd.event_loop.loop.asyncio.to_thread",
                _recording_to_thread(calls),
            ),
            patch(
                "general_ludd.review.decision_applier.apply_decision",
                apply_mock,
            ),
        ):
            await loop._review_in_process(_review_tr())

        # The blocking reviewer was offloaded — not called inline on the loop.
        assert len(calls) == 1
        assert calls[0][0] is reviewer.review_return
        # The OFFLOADED RESULT flowed back: the exact decision drove application.
        apply_mock.assert_awaited_once()
        assert apply_mock.await_args.args[0] is decision

    @pytest.mark.asyncio
    async def test_reviewer_raises_is_caught_and_held(self) -> None:
        """If the offloaded reviewer raises (a hang/timeout surfaces here as an
        exception from the worker thread), the loop converts it into a
        manual_hold decision — it NEVER silently completes the work."""
        reviewer = MagicMock()
        reviewer.review_return.side_effect = TimeoutError("reviewer wedged")

        session = AsyncMock()
        session.flush = AsyncMock()
        loop = EventLoop(
            reviewer=reviewer,
            todo_repo=AsyncMock(),
            session=session,
        )

        calls: list[tuple[Any, tuple, dict]] = []
        apply_mock = AsyncMock()
        with (
            patch(
                "general_ludd.event_loop.loop.asyncio.to_thread",
                _recording_to_thread(calls),
            ),
            patch(
                "general_ludd.review.decision_applier.apply_decision",
                apply_mock,
            ),
        ):
            await loop._review_in_process(_review_tr())

        # The reviewer was still offloaded (the raise came from the thread).
        assert len(calls) == 1
        assert calls[0][0] is reviewer.review_return
        # The failure was caught and escalated, not swallowed: a manual_hold
        # decision (confidence 0.0) was applied rather than a silent complete.
        apply_mock.assert_awaited_once()
        applied: TaskDecision = apply_mock.await_args.args[0]
        assert applied.decision == "manual_hold"
        assert applied.confidence == 0.0
        assert any("reviewer error" in note.lower() for note in applied.audit_notes)


class TestCommitOffload:
    @pytest.mark.asyncio
    async def test_commit_and_push_offloaded_via_to_thread(self) -> None:
        """Blocking git commit/push are offloaded through asyncio.to_thread so a
        slow git call cannot freeze the event loop."""
        loop = EventLoop()

        todo = MagicMock()
        todo.todo_id = "TODO-C2-GIT"
        todo.title = "do the thing"
        todo.branch_name = None
        todo.worktree = "/tmp/wt-c2"
        todo.project_id = None

        repo = MagicMock()
        repo.commit.return_value = "abc123"
        repo.push.return_value = None

        calls: list[tuple[Any, tuple, dict]] = []
        with (
            patch(
                "general_ludd.event_loop.loop.asyncio.to_thread",
                _recording_to_thread(calls),
            ),
            patch(
                "general_ludd.git_automation.repo.GitAutomation",
                return_value=repo,
            ),
        ):
            await loop._try_commit_completed_work(todo)

        offloaded_fns = [c[0] for c in calls]
        # Both the blocking commit and push were offloaded to a worker thread.
        assert repo.commit in offloaded_fns
        assert repo.push in offloaded_fns
        # commit/push were never called inline on the loop thread.
        repo.commit.assert_called_once()
        repo.push.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_worktree_skips_git_entirely(self) -> None:
        """A todo with no worktree never touches git — no offload, no crash."""
        loop = EventLoop()
        todo = MagicMock()
        todo.todo_id = "TODO-C2-NOWT"
        todo.title = "no worktree"
        todo.branch_name = None
        todo.worktree = None

        calls: list[tuple[Any, tuple, dict]] = []
        with patch(
            "general_ludd.event_loop.loop.asyncio.to_thread",
            _recording_to_thread(calls),
        ):
            await loop._try_commit_completed_work(todo)

        assert calls == []
