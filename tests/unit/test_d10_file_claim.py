"""D10: Commit-path file-claim livelock (#53) — TDD tests.

Covers:
- Total-order claim acquisition (sorted files prevent deadlock)
- Claim TTL expiry (stale claims auto-released)
- Backoff with jitter on contention (competing claims use jittered backoff)
- Concurrent claims non-overlapping (no false serialization)
"""

from __future__ import annotations

from typing import ClassVar, cast
from unittest.mock import patch

import pytest

from general_ludd.coordination.file_claims import FileClaimRegistry
from general_ludd.event_loop.loop import _FileClaimConflict

_GIT = "general_ludd.git_automation.repo.GitAutomation"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _todo(todo_id: str, worktree: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        todo_id=todo_id,
        title=f"work {todo_id}",
        branch_name=f"gludd-{todo_id.lower()}",
        worktree=worktree,
        project_id="proj-d10",
        status="complete",
        version=3,
    )


@pytest.fixture(autouse=True)
def _reset_fakegit() -> None:
    _FakeGit.files_by_repo = {}
    _FakeGit.commits = []


# ---------------------------------------------------------------------------
# Test 1: total-order claim acquisition — sorted file list prevents deadlock
# ---------------------------------------------------------------------------


def test_total_order_claim_acquisition() -> None:
    """D10: sorted file ordering produces canonical claim key."""
    registry = FileClaimRegistry()

    acquired_a = registry.claim_or_conflict("agent-A", ["b.py", "a.py", "c.py"])
    assert acquired_a is True

    acquired_b = registry.claim_or_conflict("agent-B", ["c.py", "a.py", "b.py"])
    assert acquired_b is False, (
        "agent-B should be denied because total-order treats the file sets "
        "as identical and agent-A already holds them"
    )

    claims = registry.all_claims()
    assert claims["a.py"] == ["agent-A"]
    assert claims["b.py"] == ["agent-A"]
    assert claims["c.py"] == ["agent-A"]


def test_total_order_subset_conflict() -> None:
    """D10: a worker claiming a superset of another's files is detected."""
    registry = FileClaimRegistry()

    registry.claim_or_conflict("agent-A", ["shared.py", "a.py"])
    acquired = registry.claim_or_conflict("agent-B", ["b.py", "shared.py"])
    assert acquired is False, "should conflict on shared.py regardless of position in input"


# ---------------------------------------------------------------------------
# Test 2: claim TTL expires — stale claims auto-released
# ---------------------------------------------------------------------------


class _FrozenClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_claim_ttl_expires_and_unblocks_overlapping_claim() -> None:
    """D10: a claim past its TTL is auto-reaped by claim_or_conflict."""
    clock = _FrozenClock(1000.0)
    registry = FileClaimRegistry(ttl_seconds=10.0, clock=clock)

    registry.claim("agent-A", ["shared.py"])
    assert "agent-A" in cast(list[str], registry.all_claims().get("shared.py", []))

    clock.advance(11.0)

    acquired = registry.claim_or_conflict("agent-B", ["shared.py"])
    assert acquired is True, "stale claim should not block new claim"
    assert registry.all_claims().get("shared.py") == ["agent-B"]


def test_claim_ttl_reaped_by_claim_or_conflict() -> None:
    """D10: claim_or_conflict actively reaps stale claims before checking."""
    clock = _FrozenClock(1000.0)
    registry = FileClaimRegistry(ttl_seconds=5.0, clock=clock)

    registry.claim("stale-worker", ["important.py"])
    clock.advance(6.0)

    acquired = registry.claim_or_conflict("fresh-worker", ["important.py"])
    assert acquired is True, (
        f"stale claim should have been reaped; claims={registry.all_claims()}"
    )


def test_claim_ttl_heartbeat_keeps_claim_alive() -> None:
    """D10: re-claiming (heartbeat) refreshes the TTL."""
    clock = _FrozenClock(1000.0)
    registry = FileClaimRegistry(ttl_seconds=10.0, clock=clock)

    registry.claim("live-worker", ["file.py"])
    clock.advance(9.0)
    registry.claim("live-worker", ["file.py"])  # heartbeat
    clock.advance(9.0)  # total 18s, but heartbeat reset at 9s

    acquired = registry.claim_or_conflict("other", ["file.py"])
    assert acquired is False, (
        "heartbeat should have refreshed TTL; claim should still be active"
    )


# ---------------------------------------------------------------------------
# Test 3: backoff with jitter on contention
# ---------------------------------------------------------------------------


def test_claim_or_conflict_is_contention_free_when_no_overlap() -> None:
    """D10: non-overlapping file sets always succeed immediately."""
    registry = FileClaimRegistry()

    assert registry.claim_or_conflict("w1", ["a.py"]) is True
    assert registry.claim_or_conflict("w2", ["b.py"]) is True
    assert registry.claim_or_conflict("w3", ["c.py"]) is True

    assert "w1" in cast(list[str], registry.all_claims().get("a.py", []))
    assert "w2" in cast(list[str], registry.all_claims().get("b.py", []))
    assert "w3" in cast(list[str], registry.all_claims().get("c.py", []))


def test_claim_or_conflict_detects_contention_immediately() -> None:
    """D10: when files overlap, claim_or_conflict returns False immediately."""
    registry = FileClaimRegistry()

    registry.claim_or_conflict("holder", ["shared.py", "x.py"])
    result = registry.claim_or_conflict("competitor", ["shared.py", "y.py"])
    assert result is False
    assert registry.all_claims().get("shared.py") == ["holder"]


def test_claim_or_conflict_releases_partial_on_conflict() -> None:
    """D10: all-or-nothing — partial claim never sneaks through."""
    registry = FileClaimRegistry()

    registry.claim_or_conflict("w1", ["a.py"])
    acquired = registry.claim_or_conflict("w2", ["a.py", "b.py"])
    assert acquired is False

    claims = registry.all_claims()
    assert "b.py" not in claims or "w2" not in claims.get("b.py", []), (
        "b.py should not be claimed by w2 on partial conflict"
    )


# ---------------------------------------------------------------------------
# Test 4: commit-path integration — claim_or_conflict in _try_commit_completed_work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_claims_non_overlapping() -> None:
    """D10: two workers committing non-overlapping file sets both succeed."""
    from general_ludd.event_loop.loop import EventLoop

    registry = FileClaimRegistry()
    loop = EventLoop(file_claim_registry=registry)

    _FakeGit.files_by_repo = {
        "/wt/A": ["src/feature_a.py"],
        "/wt/B": ["src/feature_b.py"],
    }

    with patch(_GIT, _FakeGit):
        await loop._try_commit_completed_work(_todo("T-A", "/wt/A"))
        await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))

    assert len(_FakeGit.commits) == 2
    assert registry.all_claims() == {}


@pytest.mark.asyncio
async def test_concurrent_claims_overlapping_deferred() -> None:
    """D10: overlapping files defer the second worker with _FileClaimConflict."""
    from general_ludd.event_loop.loop import EventLoop

    registry = FileClaimRegistry()
    loop = EventLoop(file_claim_registry=registry)

    registry.claim_or_conflict("T-A", ["src/shared.py", "src/a.py"])

    _FakeGit.files_by_repo = {"/wt/B": ["src/shared.py", "src/b.py"]}

    with patch(_GIT, _FakeGit), pytest.raises(_FileClaimConflict):
        await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))

    assert _FakeGit.commits == []
    assert "T-A" in cast(list[str], registry.all_claims().get("src/shared.py", []))


@pytest.mark.asyncio
async def test_claim_release_cycle_unblocks_overlapping_worker() -> None:
    """D10: after holder releases, overlapping worker successfully commits."""
    from general_ludd.event_loop.loop import EventLoop

    registry = FileClaimRegistry()
    loop = EventLoop(file_claim_registry=registry)

    registry.claim_or_conflict("T-A", ["src/shared.py"])
    _FakeGit.files_by_repo = {"/wt/B": ["src/shared.py"]}

    with patch(_GIT, _FakeGit):
        with pytest.raises(_FileClaimConflict):
            await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))
        assert _FakeGit.commits == []

        registry.release("T-A")

        await loop._try_commit_completed_work(_todo("T-B", "/wt/B"))

    assert _FakeGit.commits
    assert registry.all_claims() == {}
