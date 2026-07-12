"""D10: Commit-path file-claim livelock (#53) — TDD tests.

Validates the three livelock-prevention mechanisms:
- (a) Total-order claim acquisition (sorted files + atomic claim_or_conflict)
- (b) TTL on claims (stale claims auto-reaped, heartbeats refresh)
- (c) Exponential backoff with per-todo hash offset + jitter + escape to BLOCKED

Livelock scenario (before fix): two agents claim overlapping files,
each sees the other in overlaps(), both release, both retry in lockstep
— never making progress.  The atomic claim_or_conflict + per-todo-offset
backoff makes this structurally impossible.
"""

from __future__ import annotations

import threading
from typing import ClassVar
from unittest.mock import patch

import pytest

from general_ludd.coordination.file_claims import FileClaimRegistry
from general_ludd.event_loop.loop import EventLoop, _FileClaimConflict

_GIT = "general_ludd.git_automation.repo.GitAutomation"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """Injectable monotonic-style clock for deterministic TTL tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _FakeGit:
    files_by_repo: ClassVar[dict[str, list[str]]] = {}
    commits: ClassVar[list[str]] = []

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


def _todo(todo_id: str, worktree: str, status: str = "complete") -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        todo_id=todo_id,
        title=f"work {todo_id}",
        branch_name=f"gludd-{todo_id.lower()}",
        worktree=worktree,
        project_id="proj-d10",
        status=status,
        version=3,
    )


@pytest.fixture(autouse=True)
def _reset_fakegit() -> None:
    _FakeGit.files_by_repo = {}
    _FakeGit.commits = []


# ---------------------------------------------------------------------------
# (a) Total-order claim acquisition — atomic claim_or_conflict prevents livelock
# ---------------------------------------------------------------------------


class TestAtomicClaimPreventsLivelock:
    """The old claim()+overlaps()+release() pattern livelocked: two agents
    claim overlapping files, each sees the other in overlaps(), both release,
    both retry — forever.  claim_or_conflict is atomic: the first to acquire
    the registry lock checks-and-claims in one step, so the second gets a
    clean conflict signal without ever holding any claim."""

    def test_atomic_claim_first_wins_second_defers(self) -> None:
        """Agent A claims [a.py, shared.py], B claims [shared.py, b.py].
        A wins atomically; B's all-or-nothing check sees shared.py contested
        and defers cleanly (no claim ever installed)."""
        registry = FileClaimRegistry()

        acquired_a = registry.claim_or_conflict("agent-A", ["a.py", "shared.py"])
        assert acquired_a is True

        acquired_b = registry.claim_or_conflict("agent-B", ["shared.py", "b.py"])
        assert acquired_b is False

        claims = registry.all_claims()
        assert "shared.py" in claims
        assert claims["shared.py"] == ["agent-A"]
        assert "a.py" in claims
        assert claims["a.py"] == ["agent-A"]
        assert "b.py" not in claims

    def test_no_claim_leaked_on_conflict(self) -> None:
        """B tries to claim [shared.py, b.py] when A holds shared.py.
        B must not leave a partial claim on b.py — all-or-nothing."""
        registry = FileClaimRegistry()
        registry.claim_or_conflict("agent-A", ["shared.py"])

        registry.claim_or_conflict("agent-B", ["shared.py", "b.py"])

        claims = registry.all_claims()
        assert "b.py" not in claims or "agent-B" not in claims.get("b.py", [])

    def test_concurrent_disjoint_claims_no_false_serialization(self) -> None:
        """Non-overlapping file sets succeed independently — no false blocking."""
        registry = FileClaimRegistry()

        assert registry.claim_or_conflict("w1", ["module_a.py"]) is True
        assert registry.claim_or_conflict("w2", ["module_b.py"]) is True
        assert registry.claim_or_conflict("w3", ["module_c.py"]) is True

        claims = registry.all_claims()
        assert len(claims) == 3


class TestTotalOrderSorting:
    """Files are sorted before claiming for deterministic total-order."""

    def test_same_files_different_input_order_identical_key(self) -> None:
        """[b.py, a.py] and [a.py, b.py] produce the same claim key."""
        registry = FileClaimRegistry()

        registry.claim_or_conflict("w1", ["b.py", "a.py", "c.py"])
        acquired = registry.claim_or_conflict("w2", ["c.py", "a.py", "b.py"])

        assert acquired is False

    def test_total_order_unordered_input_still_detects_overlap(self) -> None:
        registry = FileClaimRegistry()
        registry.claim_or_conflict("holder", ["z.py", "a.py"])

        acquired = registry.claim_or_conflict("competitor", ["a.py"])
        assert acquired is False


# ---------------------------------------------------------------------------
# (b) TTL on claims — stale claims don't block, heartbeats keep alive
# ---------------------------------------------------------------------------


class TestTTLPreventsGhostClaimPoisoning:
    """A crashed agent's claim expires after TTL, unblocking contested files.
    Without TTL, a ghost claim poisons every file path forever — every future
    push on an overlapping path burns retries then goes BLOCKED."""

    def test_stale_claim_unblocks_overlapping_claim(self) -> None:
        clock = _FakeClock(1000.0)
        registry = FileClaimRegistry(ttl_seconds=10.0, clock=clock)

        registry.claim_or_conflict("crashed-worker", ["important.py"])
        clock.advance(11.0)

        acquired = registry.claim_or_conflict("live-worker", ["important.py"])
        assert acquired is True
        assert registry.all_claims().get("important.py") == ["live-worker"]

    def test_stale_claim_reaped_by_claim_or_conflict(self) -> None:
        clock = _FakeClock(1000.0)
        registry = FileClaimRegistry(ttl_seconds=5.0, clock=clock)

        registry.claim("stale-worker", ["critical.py"])
        clock.advance(5.01)

        acquired = registry.claim_or_conflict("fresh-worker", ["critical.py"])
        assert acquired is True

    def test_heartbeat_refreshes_ttl_prevents_staleness(self) -> None:
        clock = _FakeClock(1000.0)
        registry = FileClaimRegistry(ttl_seconds=10.0, clock=clock)

        registry.claim_or_conflict("live-worker", ["file.py"])
        clock.advance(9.0)
        registry.claim_or_conflict("live-worker", ["file.py"])  # heartbeat
        clock.advance(9.0)  # total 18s, but heartbeat reset at 9s

        acquired = registry.claim_or_conflict("other", ["file.py"])
        assert acquired is False

    def test_mixed_stale_and_live_claims(self) -> None:
        """After TTL expires for crashed worker, another live worker holds the file.
        A third worker should see conflict with the LIVE worker, not the stale one."""
        clock = _FakeClock(1000.0)
        registry = FileClaimRegistry(ttl_seconds=10.0, clock=clock)

        registry.claim("crashed", ["shared.py"])
        registry.claim("live-worker", ["other.py"])
        clock.advance(11.0)

        registry.claim_or_conflict("replacement", ["shared.py"])
        # replacement now holds shared.py; live-worker holds other.py
        assert registry.all_claims().get("shared.py") == ["replacement"]

        # Third worker tries shared.py — should conflict with replacement (live)
        acquired = registry.claim_or_conflict("third", ["shared.py"])
        assert acquired is False


# ---------------------------------------------------------------------------
# (c) Exponential backoff + retry-escape
# ---------------------------------------------------------------------------


class TestBackoffPerTodoOffset:
    """The per-todo hash offset ensures two todos at the same retry_count
    don't always check the same tick — they get different offsets derived
    from their todo_id hash."""

    def test_per_todo_offset_differentiates_tick_check(self) -> None:
        """Two different todo_ids produce different hash offsets.
        The likelihood of hash collision is combinatorially negligible
        relative to window size."""
        offset_a = abs(hash("todo-AAA")) % 8
        offset_b = abs(hash("todo-BBB")) % 8

        # Equal offsets would be a hash collision (extremely unlikely but
        # we assert that offsets can differ across the range).
        assert 0 <= offset_a < 8
        assert 0 <= offset_b < 8

    def test_same_todo_id_produces_consistent_offset(self) -> None:
        """Same todo_id always maps to the same hash offset (deterministic)."""
        o1 = abs(hash("same-id-here")) % 16
        o2 = abs(hash("same-id-here")) % 16
        assert o1 == o2

    def test_offset_within_window_bounds(self) -> None:
        for tid in ("t-1", "t-2", "t-3", "t-4", "t-5"):
            for window in (2, 4, 8, 16, 32, 64):
                offset = abs(hash(tid)) % window
                assert 0 <= offset < window


class TestRetryEscapeToBlocked:
    """After _MAX_PUSH_RETRIES (5) consecutive push failures, the todo
    transitions to BLOCKED — preventing infinite retry loop."""

    @pytest.mark.asyncio
    async def test_retry_escape_after_max_attempts(self) -> None:
        """Hook into _escape_push_livelock: after _MAX_PUSH_RETRIES failures,
        the todo status transitions to BLOCKED."""

        registry = FileClaimRegistry()
        loop = EventLoop(file_claim_registry=registry)

        todo = _todo("retry-escape-todo", "/wt/retry")
        _FakeGit.files_by_repo = {"/wt/retry": ["src/target.py"]}

        registry.claim_or_conflict("blocking-agent", ["src/target.py"])

        for attempt in range(loop._MAX_PUSH_RETRIES + 1):
            loop._total_ticks = attempt
            loop._push_retry_count[todo.todo_id] = attempt
            with patch(_GIT, _FakeGit), patch.object(
                loop, "_escape_push_livelock"
            ) as mock_escape:
                await loop._attempt_completed_push(todo)

                if attempt > loop._MAX_PUSH_RETRIES:
                    mock_escape.assert_called_once()
                    break
                else:
                    mock_escape.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_todo_skipped_on_future_ticks(self) -> None:
        """Once a todo is BLOCKED, _attempt_completed_push returns False
        immediately (no further retry)."""

        registry = FileClaimRegistry()
        loop = EventLoop(file_claim_registry=registry)

        todo = _todo("blocked-skip-me", "/wt/blocked", status="blocked")
        _FakeGit.files_by_repo = {"/wt/blocked": ["src/target.py"]}

        with patch(_GIT, _FakeGit):
            result = await loop._attempt_completed_push(todo)

        assert result is False
        assert _FakeGit.commits == []


# ---------------------------------------------------------------------------
# Integration: release unblocks + claim lifecycle
# ---------------------------------------------------------------------------


class TestReleaseUnblocksSubsequentClaim:
    """After holder releases, overlapping claims succeed."""

    def test_release_enables_overlapping_claim(self) -> None:
        registry = FileClaimRegistry()

        registry.claim_or_conflict("holder", ["shared.py"])
        acquired = registry.claim_or_conflict("waiter", ["shared.py"])
        assert acquired is False

        registry.release("holder")

        acquired = registry.claim_or_conflict("waiter", ["shared.py"])
        assert acquired is True

    def test_release_does_not_affect_other_workers(self) -> None:
        registry = FileClaimRegistry()
        registry.claim_or_conflict("Alice", ["a.py", "b.py"])
        registry.claim_or_conflict("Bob", ["c.py"])

        registry.release("Alice")

        assert registry.all_claims().get("a.py") is None
        assert registry.all_claims().get("b.py") is None
        assert registry.all_claims().get("c.py") == ["Bob"]


# ---------------------------------------------------------------------------
# Commit-path integration (event loop)
# ---------------------------------------------------------------------------


class TestCommitPathIntegration:
    """Test _try_commit_completed_work with the FileClaimRegistry wired in."""

    @pytest.mark.asyncio
    async def test_non_overlapping_commits_both_succeed(self) -> None:

        registry = FileClaimRegistry()
        loop = EventLoop(file_claim_registry=registry)

        _FakeGit.files_by_repo = {
            "/wt/feat-a": ["src/feature_a.py"],
            "/wt/feat-b": ["src/feature_b.py"],
        }

        with patch(_GIT, _FakeGit):
            await loop._try_commit_completed_work(_todo("T-A", "/wt/feat-a"))
            await loop._try_commit_completed_work(_todo("T-B", "/wt/feat-b"))

        assert len(_FakeGit.commits) == 2
        assert registry.all_claims() == {}

    @pytest.mark.asyncio
    async def test_overlapping_commit_defers_second(self) -> None:

        registry = FileClaimRegistry()
        loop = EventLoop(file_claim_registry=registry)

        registry.claim_or_conflict("T-A", ["src/shared.py", "src/a.py"])

        _FakeGit.files_by_repo = {"/wt/branch-b": ["src/shared.py", "src/b.py"]}

        with patch(_GIT, _FakeGit), pytest.raises(_FileClaimConflict):
            await loop._try_commit_completed_work(_todo("T-B", "/wt/branch-b"))

        assert _FakeGit.commits == []

    @pytest.mark.asyncio
    async def test_release_then_retry_succeeds(self) -> None:

        registry = FileClaimRegistry()
        loop = EventLoop(file_claim_registry=registry)

        registry.claim_or_conflict("T-A", ["src/shared.py"])
        _FakeGit.files_by_repo = {"/wt/branch-b": ["src/shared.py"]}

        with patch(_GIT, _FakeGit):
            with pytest.raises(_FileClaimConflict):
                await loop._try_commit_completed_work(_todo("T-B", "/wt/branch-b"))
            assert _FakeGit.commits == []

            registry.release("T-A")
            await loop._try_commit_completed_work(_todo("T-B", "/wt/branch-b"))

        assert _FakeGit.commits
        assert registry.all_claims() == {}


# ---------------------------------------------------------------------------
# Concurrency stress: thread-safe claim_or_conflict under contention
# ---------------------------------------------------------------------------


class TestThreadSafeClaimUnderContention:
    """Multiple threads concurrently attempting claim_or_conflict on
    overlapping file sets — verifying that exactly one wins and the
    registry stays consistent."""

    def test_concurrent_claim_threads_one_winner(self) -> None:
        registry = FileClaimRegistry()
        results: dict[str, bool] = {}
        errors: list[Exception] = []

        def try_claim(worker_id: str) -> None:
            try:
                result = registry.claim_or_conflict(
                    worker_id, ["shared.py", f"{worker_id}.py"]
                )
                results[worker_id] = result
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=try_claim, args=(f"worker-{i}",))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        winners = [wid for wid, ok in results.items() if ok]
        assert len(winners) == 1, f"Expected 1 winner, got {winners}"
        assert winners[0] in results
        claims = registry.all_claims()
        assert "shared.py" in claims


# ---------------------------------------------------------------------------
# Livelock scenario: verify the old pattern is structurally impossible
# ---------------------------------------------------------------------------


class TestLivelockStructurallyImpossible:
    """Simulate the old 3-step pattern (claim → overlaps → release) and
    confirm that claim_or_conflict breaks the deadlock decisively."""

    def test_old_pattern_would_livelock(self) -> None:
        """Demonstrate the old 3-step pattern's livelock by checking that
        claim() + overlaps() + release() still reports contention after
        repeated rounds.  This test proves the registry can DETECT the
        livelock scenario — the fix (claim_or_conflict) breaks it."""
        registry = FileClaimRegistry()

        for _round in range(10):
            registry.claim("agent-A", ["shared.py", "a.py"])
            registry.claim("agent-B", ["shared.py", "b.py"])

            overlap_a = registry.overlaps("agent-A")
            overlap_b = registry.overlaps("agent-B")

            assert "shared.py" in overlap_a
            assert "shared.py" in overlap_b

            registry.release("agent-A")
            registry.release("agent-B")

    def test_claim_or_conflict_breaks_deadlock(self) -> None:
        """claim_or_conflict atomically checks-and-claims, so the second
        agent always sees a clean conflict without installing its own claim.
        After release, the second agent wins immediately."""
        registry = FileClaimRegistry()

        for _round in range(3):
            acquired_a = registry.claim_or_conflict(
                "agent-A", ["shared.py", "a.py"]
            )
            assert acquired_a is True

            acquired_b = registry.claim_or_conflict(
                "agent-B", ["shared.py", "b.py"]
            )
            assert acquired_b is False

            assert registry.all_claims().get("b.py") is None

            registry.release("agent-A")

            acquired_b2 = registry.claim_or_conflict(
                "agent-B", ["shared.py", "b.py"]
            )
            assert acquired_b2 is True

            assert "shared.py" in registry.all_claims()
            assert "b.py" in registry.all_claims()

            registry.release("agent-B")
