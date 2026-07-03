"""#31 (multi-agent safety): the event loop's git-delivery path claims a todo's
affected files in the shared FileClaimRegistry BEFORE committing, so two
concurrent todos cannot clobber+commit the same file simultaneously.

Integration point under test is ``EventLoop._try_commit_completed_work`` — the
ONLY place a todo's affected files become known (the worktree has been written,
so ``GitAutomation.changed_files()`` yields the real paths; at dispatch time the
model has not run and the set is empty/unknown).

Behaviours proven:
  * non-overlapping workers both commit (no false serialization);
  * a second worker whose files overlap a STILL-HELD claim is DEFERRED — it does
    not commit, and raises so the F3 retry path re-attempts on a later tick;
  * once the first worker RELEASES (delivery settled), the overlapping worker may
    proceed;
  * the coordination facet reflects the active claim while a delivery is held.

#53 (file-claim livelock): the push-retry loop must escape after a bounded
number of failures instead of retrying every tick without limit.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.coordination.file_claims import FileClaimRegistry
from general_ludd.event_loop.loop import EventLoop, _FileClaimConflict
from general_ludd.routers.coordination import _coordination_facet
from general_ludd.schemas.todo import TodoStatus

_GIT = "general_ludd.git_automation.repo.GitAutomation"


class _FakeGit:
    """Stand-in for GitAutomation used inside _try_commit_completed_work.

    Each repo_path maps to a fixed set of changed files so a test can wire two
    worktrees to overlapping / disjoint file sets.
    """

    # repo_path -> list of changed files (set per test before invocation).
    files_by_repo: dict[str, list[str]] = {}  # noqa: RUF012 — test fixture
    commits: list[str] = []  # noqa: RUF012 — test fixture tracking

    def __init__(self, repo_path: str = "") -> None:
        self.repo_path = repo_path

    def changed_files(self) -> list[str]:
        return list(_FakeGit.files_by_repo.get(self.repo_path, []))

    def commit(self, message: str) -> str:
        _FakeGit.commits.append(message)
        return "cafef00d" * 5

    def push(self, remote: str = "origin", branch: str = "main") -> bool:
        return True

    def lines_changed_in_commit(self, ref: str = "HEAD") -> int:
        return 1


def _todo(todo_id: str, worktree: str) -> SimpleNamespace:
    return SimpleNamespace(
        todo_id=todo_id,
        title=f"work {todo_id}",
        branch_name=f"gludd-{todo_id.lower()}",
        worktree=worktree,
        project_id="proj-31",
        status="complete",
        version=3,
    )


@pytest.fixture(autouse=True)
def _reset_fakegit() -> None:
    _FakeGit.files_by_repo = {}
    _FakeGit.commits = []


@pytest.mark.asyncio
async def test_non_overlapping_workers_both_commit() -> None:
    """Disjoint file sets -> no serialization; both deliveries commit."""
    registry = FileClaimRegistry()
    loop = EventLoop(file_claim_registry=registry)
    _FakeGit.files_by_repo = {
        "/wt/A": ["src/a.py"],
        "/wt/B": ["src/b.py"],
    }

    with patch(_GIT, _FakeGit):
        await loop._try_commit_completed_work(_todo("T-A", "/wt/A"))
        await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))

    assert len(_FakeGit.commits) == 2
    # Both released after their own delivery settled -> no lingering claims.
    assert registry.all_claims() == {}


@pytest.mark.asyncio
async def test_overlapping_second_worker_is_deferred_not_clobbered() -> None:
    """A held claim on src/shared.py blocks an overlapping worker's commit."""
    registry = FileClaimRegistry()
    loop = EventLoop(file_claim_registry=registry)

    # Worker A is "still running": pre-claim the shared file so its delivery is
    # in flight (mirrors A holding the file while B tries to deliver).
    registry.claim("T-A", ["src/shared.py", "src/a.py"])

    _FakeGit.files_by_repo = {"/wt/B": ["src/shared.py", "src/b.py"]}

    with patch(_GIT, _FakeGit), pytest.raises(_FileClaimConflict):
        await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))

    # B never committed (deferred, not clobbered).
    assert _FakeGit.commits == []
    # B released its transient claim on conflict; only A's claim remains.
    assert registry.all_claims() == {
        "src/shared.py": ["T-A"],
        "src/a.py": ["T-A"],
    }


@pytest.mark.asyncio
async def test_release_frees_claim_so_overlapping_worker_proceeds() -> None:
    """After the holder releases, the overlapping worker may deliver."""
    registry = FileClaimRegistry()
    loop = EventLoop(file_claim_registry=registry)

    registry.claim("T-A", ["src/shared.py"])
    _FakeGit.files_by_repo = {"/wt/B": ["src/shared.py"]}

    with patch(_GIT, _FakeGit):
        # First attempt: blocked by A's live claim.
        with pytest.raises(_FileClaimConflict):
            await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))
        assert _FakeGit.commits == []

        # A finishes -> releases its claim.
        registry.release("T-A")

        # Retry: now succeeds and releases its own claim afterward.
        await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))

    assert _FakeGit.commits  # B committed on retry
    assert registry.all_claims() == {}  # released after delivery


@pytest.mark.asyncio
async def test_facet_reflects_active_claim_during_delivery() -> None:
    """The coordination facet surfaces a claim held while a delivery is gated.

    The facet reads the SAME registry the event loop claims into, so an
    in-flight worker's reservation is observable via /api/facts.
    """
    registry = FileClaimRegistry()
    loop = EventLoop(file_claim_registry=registry)

    captured: dict[str, object] = {}

    class _CapturingGit(_FakeGit):
        def commit(self, message: str) -> str:
            # While B's commit "runs", A still holds src/shared.py — snapshot the
            # facet to prove the registry reflects the live claim mid-delivery.
            app = SimpleNamespace(state=SimpleNamespace(_file_claims=registry))
            captured.update(_coordination_facet(app))  # type: ignore[arg-type]
            return super().commit(message)

    # A holds an unrelated file the whole time.
    registry.claim("T-A", ["src/a.py"])
    _FakeGit.files_by_repo = {"/wt/B": ["src/b.py"]}

    with patch(_GIT, _CapturingGit):
        await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))

    claims = captured.get("claims", {})
    assert isinstance(claims, dict)
    # A's standing claim AND B's in-flight claim were both visible mid-delivery.
    assert claims.get("src/a.py") == ["T-A"]
    assert claims.get("src/b.py") == ["T-B"]


@pytest.mark.asyncio
async def test_none_registry_leaves_delivery_unchanged() -> None:
    """No registry wired -> commit path behaves exactly as before."""
    loop = EventLoop(file_claim_registry=None)
    _FakeGit.files_by_repo = {"/wt/A": ["src/a.py"]}

    with patch(_GIT, _FakeGit):
        await loop._try_commit_completed_work(_todo("T-A", "/wt/A"))

    assert _FakeGit.commits  # committed normally


# ── #53: file-claim livelock escape ───────────────────────────────────────


@pytest.mark.asyncio
async def test_push_livelock_escapes_after_max_retries() -> None:
    """#53: when a todo's completed-work push fails with a file-claim conflict
    every tick, the retry loop must escape after _MAX_PUSH_RETRIES instead of
    retrying forever.

    BEFORE fix: every call to _attempt_completed_push returns True (fail),
    the retry counter is never incremented, and no BLOCKED transition occurs
    — the todo livelocks indefinitely.

    AFTER fix: after 5 consecutive failures the 6th call transitions the todo
    to BLOCKED, clears the retry counter, and returns False so the reconcile
    loop does NOT count it as a push failure to retry.
    """
    registry = FileClaimRegistry()
    # Pre-claim the shared file so every push attempt hits a conflict.
    registry.claim("T-OTHER", ["src/shared.py"])

    mock_todo_repo = AsyncMock()

    loop = EventLoop(file_claim_registry=registry, todo_repo=mock_todo_repo)
    _FakeGit.files_by_repo = {"/wt/X": ["src/shared.py"]}
    todo = _todo("T-X", "/wt/X")

    with patch(_GIT, _FakeGit):
        results = []
        for _ in range(6):
            results.append(await loop._attempt_completed_push(todo))

    # First 5: all fail (retry budget exhausted on the 5th).
    assert results[:5] == [True] * 5, (
        f"Expected first 5 attempts to return True (failure), got {results[:5]}"
    )
    # 6th: escaped the livelock — todo was transitioned to BLOCKED.
    assert results[5] is False, (
        f"Expected 6th attempt to return False (livelock escaped), got {results[5]}"
    )

    mock_todo_repo.transition.assert_called_once()
    call_args = mock_todo_repo.transition.call_args
    assert call_args[0][0] == "T-X", f"Expected todo_id T-X, got {call_args[0][0]}"
    assert call_args[0][1] == TodoStatus.BLOCKED, (
        f"Expected status BLOCKED, got {call_args[0][1]}"
    )


@pytest.mark.asyncio
async def test_successful_push_resets_retry_counter() -> None:
    """#53: a successful push clears the retry count so subsequent failures
    get a fresh budget rather than carrying stale retry state."""
    registry = FileClaimRegistry()
    mock_todo_repo = AsyncMock()

    loop = EventLoop(file_claim_registry=registry, todo_repo=mock_todo_repo)

    # Two different todos, same file set.  First fails 3 times, then succeeds.
    # A second todo failing should NOT inherit the first's retry counter.
    todo_a = _todo("T-A", "/wt/A")
    todo_b = _todo("T-B", "/wt/B")

    # Fail todo-A 2 times with a conflict.
    registry.claim("T-OTHER", ["src/shared.py"])
    _FakeGit.files_by_repo = {"/wt/A": ["src/shared.py"]}
    with patch(_GIT, _FakeGit):
        await loop._attempt_completed_push(todo_a)
        await loop._attempt_completed_push(todo_a)

    # Now clear the conflict for A (but keep it for B).
    registry.release("T-OTHER")
    _FakeGit.files_by_repo = {"/wt/A": ["src/clean.py"]}
    with patch(_GIT, _FakeGit):
        result = await loop._attempt_completed_push(todo_a)
        assert result is False

    # B gets its own independent retry counter.
    registry.claim("T-OTHER", ["src/b.py"])
    _FakeGit.files_by_repo = {"/wt/B": ["src/b.py"]}
    with patch(_GIT, _FakeGit):
        # B should get a full budget of 5 retries, not 3 (leftover from A).
        for _ in range(5):
            result = await loop._attempt_completed_push(todo_b)
            assert result is True  # conflict → failure
        # 6th: escape.
        result = await loop._attempt_completed_push(todo_b)
        assert result is False

    # Verify B was transitioned to BLOCKED (its own count hit 5).
    transition_calls_after = mock_todo_repo.transition.call_count
    assert transition_calls_after == 1, (
        f"Expected exactly 1 BLOCKED transition (for B after its own 5 retries), "
        f"got {transition_calls_after}"
    )
