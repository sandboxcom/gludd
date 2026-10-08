"""Lease-bound liveness checks before worker credentials leave the daemon."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from general_ludd.reload.worker_broadcast import WorkerBroadcaster, WorkerInfo


@dataclass
class ManualClock:
    """Controllable monotonic nanosecond clock for lease and budget tests."""

    now_ns: int = 1_000_000_000

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, seconds: float) -> None:
        self.now_ns += int(seconds * 1_000_000_000)


def test_expired_worker_is_publicly_probed_then_lease_is_renewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One successful public probe renews the lease before the PSK POST."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "do-not-leak")
    clock = ManualClock()
    probes: list[tuple[str, dict[str, object]]] = []
    broadcasts: list[tuple[str, dict[str, object]]] = []

    def get_transport(url: str, **kwargs: object) -> httpx.Response:
        probes.append((url, kwargs))
        return httpx.Response(200)

    def post_transport(url: str, **kwargs: object) -> httpx.Response:
        broadcasts.append((url, kwargs))
        return httpx.Response(200)

    broadcaster = WorkerBroadcaster(
        liveness_lease_seconds=5.0,
        monotonic_ns=clock,
        get=get_transport,
        post=post_transport,
    )
    assert broadcaster.register(WorkerInfo("worker-1", "https://worker.example.com"))
    clock.advance(6.0)

    first = broadcaster.broadcast_reload("ALL")
    second = broadcaster.broadcast_reload("ALL")

    assert first[0].success is True
    assert second[0].success is True
    assert [call[0] for call in probes] == ["https://worker.example.com/healthz"]
    assert "headers" not in probes[0][1]
    assert probes[0][1]["timeout"] == 1.0
    assert probes[0][1]["follow_redirects"] is False
    assert probes[0][1]["verify"] is True
    assert len(broadcasts) == 2
    assert broadcasts[0][1]["headers"] == {"Authorization": "Bearer do-not-leak"}


def test_unreachable_expired_worker_never_receives_psk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed probe produces a result without invoking the POST transport."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "do-not-leak")
    clock = ManualClock()
    posts: list[str] = []

    def get_transport(url: str, **kwargs: object) -> httpx.Response:
        del url, kwargs
        raise httpx.ConnectTimeout("worker did not answer")

    def post_transport(url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        posts.append(url)
        return httpx.Response(200)

    broadcaster = WorkerBroadcaster(
        liveness_lease_seconds=1.0,
        monotonic_ns=clock,
        get=get_transport,
        post=post_transport,
    )
    broadcaster.register(WorkerInfo("worker-1", "https://worker.example.com"))
    clock.advance(2.0)

    results = broadcaster.broadcast_model_update("add", "model-1", {})

    assert posts == []
    assert results[0].success is False
    assert results[0].error == "liveness check failed"


def test_liveness_enforcement_has_explicit_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented rollback bypasses probes and preserves legacy delivery."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "configured")
    monkeypatch.setenv("GLUDD_WORKER_LIVENESS_ENFORCE", "0")
    clock = ManualClock()
    probes: list[str] = []
    posts: list[str] = []

    def get_transport(url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        probes.append(url)
        return httpx.Response(503)

    def post_transport(url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        posts.append(url)
        return httpx.Response(200)

    broadcaster = WorkerBroadcaster(
        liveness_lease_seconds=1.0,
        monotonic_ns=clock,
        get=get_transport,
        post=post_transport,
    )
    broadcaster.register(WorkerInfo("worker-1", "https://worker.example.com"))
    clock.advance(2.0)

    assert broadcaster.broadcast_reload("ALL")[0].success is True
    assert probes == []
    assert posts == ["https://worker.example.com/admin/reload"]


def test_successful_ping_renews_expired_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator ping is valid lease evidence and avoids a duplicate probe."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "configured")
    clock = ManualClock()
    probes: list[str] = []

    def get_transport(url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        probes.append(url)
        return httpx.Response(200)

    broadcaster = WorkerBroadcaster(
        liveness_lease_seconds=1.0,
        monotonic_ns=clock,
        get=get_transport,
        post=lambda *args, **kwargs: httpx.Response(200),
    )
    broadcaster.register(WorkerInfo("worker-1", "https://worker.example.com"))
    clock.advance(2.0)

    assert broadcaster.ping_all() == {"worker-1": True}
    assert broadcaster.broadcast_reload("ALL")[0].success is True
    assert probes == ["https://worker.example.com/healthz"]


def test_probe_revalidates_address_before_transport_or_psk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An injected unsafe address reaches neither public nor credentialed I/O."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "configured")
    clock = ManualClock()
    calls: list[str] = []

    def transport(url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        calls.append(url)
        return httpx.Response(200)

    broadcaster = WorkerBroadcaster(
        liveness_lease_seconds=1.0,
        monotonic_ns=clock,
        get=transport,
        post=transport,
    )
    broadcaster._workers["unsafe"] = WorkerInfo("unsafe", "http://169.254.169.254")
    clock.advance(2.0)

    results = broadcaster.broadcast_reload("ALL")

    assert calls == []
    assert results[0].error == "unsafe address"


def test_registry_cap_allows_rolling_reregistration() -> None:
    """The 64-worker cap rejects only new identities, not lease renewal."""
    clock = ManualClock()
    broadcaster = WorkerBroadcaster(monotonic_ns=clock)
    for index in range(64):
        assert broadcaster.register(
            WorkerInfo(f"worker-{index}", f"https://worker-{index}.example.com")
        )

    clock.advance(1.0)
    assert broadcaster.register(
        WorkerInfo("worker-0", "https://worker-0-new.example.com")
    )
    assert not broadcaster.register(
        WorkerInfo("worker-64", "https://worker-64.example.com")
    )
    workers = broadcaster.list_workers()
    assert len(workers) == 64
    assert workers[0].address == "https://worker-0-new.example.com"


def test_expired_probe_fanout_has_one_second_and_ten_second_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial probes stop at the shared deadline without spawning workers."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "configured")
    clock = ManualClock()
    timeouts: list[float] = []
    posts: list[str] = []

    def get_transport(url: str, **kwargs: object) -> httpx.Response:
        del url
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        timeouts.append(float(timeout))
        clock.advance(1.0)
        return httpx.Response(503)

    def post_transport(url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        posts.append(url)
        return httpx.Response(200)

    broadcaster = WorkerBroadcaster(
        liveness_lease_seconds=1.0,
        monotonic_ns=clock,
        get=get_transport,
        post=post_transport,
    )
    for index in range(64):
        broadcaster.register(
            WorkerInfo(f"worker-{index}", f"https://worker-{index}.example.com")
        )
    clock.advance(2.0)

    results = broadcaster.broadcast_reload("ALL")

    assert len(results) == 64
    assert len(timeouts) == 10
    assert all(0.0 < timeout <= 1.0 for timeout in timeouts)
    assert posts == []
    assert all(result.error == "liveness check failed" for result in results)
