"""Regression tests for confirmed data-integrity bugs (#80).

Four bugs, each with a failing-first regression test:

1. Aliasing in RunHistoryRecorder — events were stored/returned by reference,
   so a recorded history entry was mutable after the fact.
2. Substring match in get_summary — ``todo_id in job_id`` aggregated
   ``TODO-12`` events under ``TODO-1``.
3. Broken dedup — the GitHub issue ingestor kept ``_seen_ids`` per-instance,
   but the maintenance router built a fresh ingestor per request, so dedup
   never persisted and every poll re-emitted all issues.
4. URL/query injection — request-supplied owner/repo/label were interpolated
   UNESCAPED into the GitHub API URL.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor
from general_ludd.observability.run_history import RunHistoryRecorder


class TestBug1Aliasing:
    """Mutating a returned record must not change stored history."""

    def test_mutating_returned_event_data_does_not_change_stored(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("job-1", "model_call", {"tokens": 100})

        out = rec.get_timeline("job-1")
        out[0]["event_type"] = "TAMPERED"
        out[0]["data"]["tokens"] = 999

        fresh = rec.get_timeline("job-1")
        assert fresh[0]["event_type"] == "model_call"
        assert fresh[0]["data"]["tokens"] == 100

    def test_mutating_caller_dict_after_record_does_not_change_stored(self) -> None:
        rec = RunHistoryRecorder()
        payload: dict[str, Any] = {"tokens": 100, "nested": {"k": "v"}}
        rec.record_event("job-1", "model_call", payload)

        # Caller keeps a reference and mutates it after recording.
        payload["tokens"] = 999
        payload["nested"]["k"] = "TAMPERED"

        stored = rec.get_timeline("job-1")[0]["data"]
        assert stored["tokens"] == 100
        assert stored["nested"]["k"] == "v"

    def test_summary_events_are_also_deep_copied(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("TODO-1:job-a", "e1", {"n": 1})
        summary = rec.get_summary("TODO-1")
        summary["events"][0]["data"]["n"] = 777
        assert rec.get_timeline("TODO-1:job-a")[0]["data"]["n"] == 1


class TestBug2SubstringMatch:
    """get_summary('TODO-1') must NOT aggregate 'TODO-12' events."""

    def test_todo_1_does_not_match_todo_12(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("TODO-1:job-a", "for-1", {})
        rec.record_event("TODO-12:job-b", "for-12", {})

        summary = rec.get_summary("TODO-1")
        assert summary["event_count"] == 1
        assert [e["event_type"] for e in summary["events"]] == ["for-1"]

    def test_exact_job_id_match_included(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("TODO-1", "bare", {})
        summary = rec.get_summary("TODO-1")
        assert summary["event_count"] == 1

    def test_prefix_boundary_still_aggregates_subjobs(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("TODO-42:job-a", "e1", {})
        rec.record_event("TODO-42:job-b", "e2", {})
        rec.record_event("TODO-420:job-c", "e3", {})  # must NOT match
        summary = rec.get_summary("TODO-42")
        assert summary["event_count"] == 2
        assert sorted(e["event_type"] for e in summary["events"]) == ["e1", "e2"]


class TestBug3BrokenDedup:
    """A second poll over the same issues must persist dedup across requests."""

    @pytest.mark.asyncio
    async def test_seen_ids_persist_across_ingestor_instances(self) -> None:
        fake_issues = [
            {"id": 1, "number": 1, "title": "A", "body": "", "labels": []},
            {"id": 2, "number": 2, "title": "B", "body": "", "labels": []},
        ]

        async def _fake_fetch() -> list[dict[str, Any]]:
            return list(fake_issues)

        shared: set[int] = set()

        first = GitHubIssueIngestor(owner="o", repo="r", seen_ids=shared)
        with patch.object(first, "_fetch_labeled_issues", _fake_fetch):
            todos1 = await first.poll_issues()
        assert len(todos1) == 2

        # Fresh ingestor instance (as the router builds per request), but
        # sharing the same seen-id store -> second poll emits nothing new.
        second = GitHubIssueIngestor(owner="o", repo="r", seen_ids=shared)
        with patch.object(second, "_fetch_labeled_issues", _fake_fetch):
            todos2 = await second.poll_issues()
        assert todos2 == []

    @pytest.mark.asyncio
    async def test_router_persists_dedup_across_two_poll_requests(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers.maintenance import register

        fake_issues = [
            {"id": 1, "number": 1, "title": "A", "body": "", "labels": []},
        ]

        async def _fake_fetch(self: GitHubIssueIngestor) -> list[dict[str, Any]]:
            return list(fake_issues)

        app = FastAPI()
        register(app, {})
        client = TestClient(app)

        with patch.object(
            GitHubIssueIngestor, "_fetch_labeled_issues", _fake_fetch
        ):
            r1 = client.post("/admin/issues/poll", json={"owner": "o", "repo": "r"})
            r2 = client.post("/admin/issues/poll", json={"owner": "o", "repo": "r"})

        assert r1.json()["count"] == 1
        assert r2.json()["count"] == 0


class TestBug4UrlInjection:
    """Request-supplied owner/repo/label must be escaped into the URL."""

    @pytest.mark.asyncio
    async def test_label_cannot_smuggle_query_params(self) -> None:
        captured: dict[str, str] = {}

        ingestor = GitHubIssueIngestor(
            owner="o",
            repo="r",
            label="a&state=closed",
        )

        def _fake_urlopen(req: Any, timeout: int = 30) -> Any:
            captured["url"] = req.full_url

            class _Resp:
                def __enter__(self_inner: Any) -> Any:
                    return self_inner

                def __exit__(self_inner: Any, *a: Any) -> None:
                    return None

                def read(self_inner: Any) -> bytes:
                    return b"[]"

            return _Resp()

        with patch("urllib.request.urlopen", _fake_urlopen):
            await ingestor._fetch_labeled_issues()

        url = captured["url"]
        parts = urlsplit(url)
        qs = parse_qs(parts.query)
        # The injected &state=closed must NOT override the real state=open.
        assert qs["state"] == ["open"]
        # The malicious label survives only as a single escaped value.
        assert qs["labels"] == ["a&state=closed"]

    @pytest.mark.asyncio
    async def test_owner_repo_path_is_escaped(self) -> None:
        captured: dict[str, str] = {}

        ingestor = GitHubIssueIngestor(owner="o/../../evil", repo="r r")

        def _fake_urlopen(req: Any, timeout: int = 30) -> Any:
            captured["url"] = req.full_url

            class _Resp:
                def __enter__(self_inner: Any) -> Any:
                    return self_inner

                def __exit__(self_inner: Any, *a: Any) -> None:
                    return None

                def read(self_inner: Any) -> bytes:
                    return b"[]"

            return _Resp()

        with patch("urllib.request.urlopen", _fake_urlopen):
            await ingestor._fetch_labeled_issues()

        parts = urlsplit(captured["url"])
        # Path-traversal slashes / spaces must be percent-encoded, not raw.
        assert "/../" not in parts.path
        assert " " not in parts.path


class TestFetchOffloadsBlockingIO:
    """_fetch_labeled_issues must not block the event loop.

    It is ``async def`` but previously called ``urlopen(..., timeout=30)``
    directly on the calling coroutine — a blocking network call. It runs on
    the daemon's tick path (``EventLoop`` awaits ``poll_issues`` each cycle),
    so a slow/hanging GitHub response would freeze the ENTIRE daemon loop,
    not just this one poll. The fix wraps the blocking call in
    ``asyncio.to_thread`` so the loop stays free for other coroutines while
    the network call is in flight.
    """

    @pytest.mark.asyncio
    async def test_slow_urlopen_does_not_starve_a_concurrent_task(self) -> None:
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        ticks: list[float] = []

        def _slow_urlopen(req: Any, timeout: int = 30) -> Any:
            # Blocking sleep on the calling thread — if this ran inline on
            # the event loop, no other coroutine could run for 0.3s.
            time.sleep(0.3)

            class _Resp:
                def __enter__(self_inner: Any) -> Any:
                    return self_inner

                def __exit__(self_inner: Any, *a: Any) -> None:
                    return None

                def read(self_inner: Any) -> bytes:
                    return b"[]"

            return _Resp()

        async def _ticker() -> None:
            # A concurrent coroutine that should keep making progress on
            # ~0.05s ticks throughout the "slow network call" window.
            for _ in range(6):
                await asyncio.sleep(0.05)
                ticks.append(time.monotonic())

        with patch("urllib.request.urlopen", _slow_urlopen):
            await asyncio.gather(ingestor._fetch_labeled_issues(), _ticker())

        # If the blocking urlopen ran directly on the event loop, the ticker
        # would be starved for the ~0.3s sleep and accumulate far fewer than
        # 6 ticks (likely 0-1, all bunched up after the sleep finally
        # returns). Offloaded to a worker thread, the loop stays free and
        # the ticker completes its full run right on schedule.
        assert len(ticks) == 6, f"ticker starved: only {len(ticks)}/6 ticks landed"

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread(self) -> None:
        """Directly assert the offload mechanism is ``asyncio.to_thread``."""
        ingestor = GitHubIssueIngestor(owner="o", repo="r")
        captured: dict[str, Any] = {}

        async def _fake_to_thread(func: Any, *a: Any, **kw: Any) -> Any:
            captured["called"] = True
            captured["func"] = func
            return func(*a, **kw)

        def _fake_urlopen(req: Any, timeout: int = 30) -> Any:
            class _Resp:
                def __enter__(self_inner: Any) -> Any:
                    return self_inner

                def __exit__(self_inner: Any, *a: Any) -> None:
                    return None

                def read(self_inner: Any) -> bytes:
                    return b"[]"

            return _Resp()

        with (
            patch("urllib.request.urlopen", _fake_urlopen),
            patch("asyncio.to_thread", _fake_to_thread),
        ):
            result = await ingestor._fetch_labeled_issues()

        assert captured.get("called") is True
        assert result == []
