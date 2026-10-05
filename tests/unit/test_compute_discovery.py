"""Offline contracts for bounded, budget-aware compute discovery."""

from __future__ import annotations

import asyncio
import math
from dataclasses import FrozenInstanceError

import pytest

from general_ludd.infra.compute_discovery import (
    DiscoveryResult,
    DiscoveryService,
    DiscoveryStatus,
    WorkSpec,
    register_discovered,
    select_resource,
)
from general_ludd.infra.discovery import DiscoveredResource
from general_ludd.infra.utilization import UtilizationTracker


def _resource(**overrides: object) -> DiscoveredResource:
    values: dict[str, object] = {
        "provider": "aws",
        "kind": "g5.xlarge",
        "id": "instance-1",
        "cpu": 8.0,
        "mem_gb": 32.0,
        "gpu": "a10g",
        "gpu_count": 1,
        "cost_per_hour": 1.0,
        "available": True,
    }
    values.update(overrides)
    return DiscoveredResource(**values)  # type: ignore[arg-type]


class _Probe:
    def __init__(
        self,
        resources: list[DiscoveredResource] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.resources = resources or []
        self.error = error
        self.calls = 0

    def probe(self) -> list[DiscoveredResource]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.resources


def test_resource_candidate_fields_are_frozen_and_isolated() -> None:
    first = _resource(labels={"zone": "a"})
    second = _resource(id="instance-2")

    assert first.id == "instance-1"
    assert first.region is None
    assert first.endpoint_url is None
    assert first.labels == {"zone": "a"}
    assert second.labels == {}
    with pytest.raises(FrozenInstanceError):
        first.available = False  # type: ignore[misc]


def test_selector_filters_capacity_gpu_availability_and_endpoint_safety() -> None:
    spec = WorkSpec(needs_gpu=True, gpu="a10g", gpu_count=1, cpu=4.0, mem_gb=8.0)
    candidates = [
        _resource(id="offline", available=False),
        _resource(id="small", cpu=2.0),
        _resource(id="wrong-gpu", gpu="t4"),
        _resource(id="metadata", endpoint_url="http://169.254.169.254/latest"),
        _resource(id="fit", cost_per_hour=0.5),
    ]

    assert select_resource(spec, candidates, headroom=10.0).id == "fit"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("cost", "cap"),
    [
        (None, 10.0),
        (math.nan, 10.0),
        (math.inf, 10.0),
        (-1.0, 10.0),
        (2.0, 1.0),
    ],
)
def test_selector_fails_closed_for_unknown_invalid_or_over_cap_cost(
    cost: float | None,
    cap: float,
) -> None:
    spec = WorkSpec(cpu=1.0, max_cost_usd=cap)

    assert select_resource(spec, [_resource(cost_per_hour=cost)], headroom=100.0) is None


def test_selector_allows_unknown_cost_only_without_any_finite_cap() -> None:
    pick = select_resource(
        WorkSpec(cpu=1.0),
        [_resource(cost_per_hour=None)],
        headroom=math.inf,
    )

    assert pick is not None


def test_selector_uses_cheapest_fit_when_high_quality_choice_exceeds_headroom() -> None:
    spec = WorkSpec(cpu=4.0, mem_gb=8.0, max_cost_usd=20.0)
    oversized = _resource(id="oversized", cpu=64.0, mem_gb=512.0, cost_per_hour=9.0)
    exact = _resource(id="exact", cpu=4.0, mem_gb=8.0, cost_per_hour=0.4)

    pick = select_resource(spec, [oversized, exact], headroom=0.5)

    assert pick is exact


def test_selector_atomically_drops_refused_candidate_and_retries() -> None:
    class _Limiter:
        def __init__(self) -> None:
            self.costs: list[float | None] = []

        def try_charge(self, cost_usd: float | None, **_kwargs: object) -> bool:
            self.costs.append(cost_usd)
            return len(self.costs) == 2

    limiter = _Limiter()
    candidates = [
        _resource(id="first", cost_per_hour=0.1),
        _resource(id="second", cost_per_hour=0.2),
    ]

    pick = select_resource(
        WorkSpec(model="model", project_id="project", max_cost_usd=1.0),
        candidates,
        headroom=1.0,
        spend_limiter=limiter,
    )

    assert pick is not None
    assert pick.id == "second"
    assert limiter.costs == [0.1, 0.2]


def test_selector_rejects_non_positive_or_non_finite_runtime() -> None:
    candidate = _resource()

    assert select_resource(WorkSpec(), [candidate], headroom=1.0, runtime_hours=0.0) is None
    assert select_resource(WorkSpec(), [candidate], headroom=1.0, runtime_hours=math.inf) is None


def test_selector_returns_none_when_capacity_pool_is_empty() -> None:
    assert select_resource(WorkSpec(cpu=100.0), [_resource(cpu=2.0)], headroom=10.0) is None


def test_selector_handles_free_capacity_and_unknown_task_type() -> None:
    candidate = _resource(cost_per_hour=0.0, cpu=0.0, mem_gb=0.0)

    pick = select_resource(
        WorkSpec(task_type=object()),
        [candidate],
        headroom=math.inf,
    )

    assert pick is candidate


def test_selector_returns_none_when_atomic_limiter_refuses_every_fit() -> None:
    class _RefuseAll:
        def try_charge(
            self,
            cost_usd: float | None,
            *,
            kind: str,
            model: str | None = None,
            project_id: str | None = None,
        ) -> bool:
            del cost_usd, kind, model, project_id
            return False

    assert (
        select_resource(
            WorkSpec(),
            [_resource()],
            headroom=10.0,
            spend_limiter=_RefuseAll(),
        )
        is None
    )


def test_register_discovered_requires_public_endpoint() -> None:
    tracker = UtilizationTracker()

    missing = register_discovered(tracker, _resource(endpoint_url=None), WorkSpec(model="m"))
    internal = register_discovered(
        tracker,
        _resource(endpoint_url="http://10.0.0.5:8000"),
        WorkSpec(model="m"),
    )

    assert missing == {
        "registered": False,
        "needs_deploy": True,
        "resource_id": "instance-1",
        "provider": "aws",
        "reason": "no SSRF-safe endpoint_url (deploy required before routing)",
    }
    assert internal["registered"] is False
    assert tracker.list_endpoints() == []


def test_register_discovered_deduplicates_without_resetting_load() -> None:
    tracker = UtilizationTracker()
    candidate = _resource(endpoint_url="https://gpu.example.com:8000", gpu_count=2)

    first = register_discovered(tracker, candidate, WorkSpec(model="m"))
    endpoint = tracker.get_endpoint(str(first["endpoint_id"]))
    assert endpoint is not None
    endpoint.current_load = 3
    second = register_discovered(tracker, candidate, WorkSpec(model="m"))

    assert first["registered"] is True
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert tracker.get_endpoint(str(first["endpoint_id"])).current_load == 3  # type: ignore[union-attr]


def test_register_cpu_candidate_derives_slots_from_cpu() -> None:
    tracker = UtilizationTracker()
    candidate = _resource(
        provider="local",
        gpu="",
        gpu_count=0,
        cpu=6.0,
        endpoint_url="https://cpu.example.com",
    )

    result = register_discovered(tracker, candidate, WorkSpec())
    endpoint = tracker.get_endpoint(str(result["endpoint_id"]))

    assert endpoint is not None
    assert endpoint.max_concurrent == 3


def test_discovery_result_ok_contract() -> None:
    ok = DiscoveryResult("local", DiscoveryStatus.OK, (_resource(),))
    partial = DiscoveryResult("aws", DiscoveryStatus.PARTIAL)
    failed = DiscoveryResult("gcp", DiscoveryStatus.ERROR, error="RuntimeError")

    assert ok.ok is True
    assert partial.ok is True
    assert failed.ok is False


async def test_service_returns_structured_success_and_caches_last_good() -> None:
    probe = _Probe([_resource()])
    service = DiscoveryService(registry={"aws": probe}, timeout_s=0.5)

    first = await service.discover("aws")
    probe.error = RuntimeError("secret-value-must-not-leak")
    second = await service.discover("aws")

    assert first.status == DiscoveryStatus.OK
    assert second.from_cache is True
    assert second.resources == first.resources
    assert "secret-value" not in str(second)


async def test_service_cache_expires_instead_of_routing_ghost_resource() -> None:
    now = [10.0]
    probe = _Probe([_resource()])
    service = DiscoveryService(
        registry={"aws": probe},
        cache_ttl_s=5.0,
        timeout_s=0.5,
        clock=lambda: now[0],
    )
    await service.discover("aws")
    now[0] = 16.0
    probe.error = RuntimeError("offline")

    result = await service.discover("aws")

    assert result.status == DiscoveryStatus.ERROR
    assert result.from_cache is False
    assert result.resources == ()


async def test_service_timeout_is_bounded_and_structured() -> None:
    class _SlowProbe:
        def probe(self) -> list[DiscoveredResource]:
            import time

            time.sleep(0.05)
            return [_resource()]

    service = DiscoveryService(registry={"slow": _SlowProbe()}, timeout_s=0.01)

    result = await service.discover("slow")

    assert result.status == DiscoveryStatus.OFFLINE
    assert result.error == "probe timeout"


async def test_service_circuit_breaker_skips_repeated_failure() -> None:
    probe = _Probe(error=RuntimeError("boom"))
    service = DiscoveryService(
        registry={"aws": probe},
        breaker_threshold=1,
        breaker_cooldown_s=60.0,
    )

    first = await service.discover("aws")
    second = await service.discover("aws")

    assert first.status == DiscoveryStatus.ERROR
    assert second.status == DiscoveryStatus.OFFLINE
    assert second.error == "circuit breaker open; no cached result"
    assert probe.calls == 1


async def test_service_circuit_breaker_allows_half_open_probe_after_cooldown() -> None:
    now = [0.0]
    probe = _Probe(error=RuntimeError("boom"))
    service = DiscoveryService(
        registry={"aws": probe},
        breaker_threshold=1,
        breaker_cooldown_s=5.0,
        clock=lambda: now[0],
    )
    await service.discover("aws")
    now[0] = 6.0
    probe.error = None
    probe.resources = [_resource()]

    result = await service.discover("aws")

    assert result.status == DiscoveryStatus.OK
    assert probe.calls == 2


async def test_service_discover_all_isolated_and_observable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = DiscoveryService(
        registry={
            "local": _Probe([_resource(provider="local")]),
            "broken": _Probe(error=ValueError("credential-secret")),
        }
    )

    with caplog.at_level("INFO"):
        results = await service.discover_all()

    assert results["local"].status == DiscoveryStatus.OK
    assert results["broken"].status == DiscoveryStatus.ERROR
    assert "credential-secret" not in caplog.text
    assert "compute discovery refresh tick" in caplog.text


async def test_service_unknown_provider_and_refresh_never_raise() -> None:
    service = DiscoveryService(registry={"aws": _Probe(error=RuntimeError("boom"))})

    missing = await service.discover("missing")
    await service.refresh_once()

    assert missing.status == DiscoveryStatus.ERROR
    assert missing.error == "no probe registered for provider"


def test_service_auto_register_requires_injected_tracker() -> None:
    candidate = _resource(endpoint_url="https://gpu.example.com")
    service = DiscoveryService(registry={})

    assert service.auto_register(candidate, WorkSpec()) == {
        "registered": False,
        "reason": "no utilization tracker",
    }


def test_service_cache_accessors_and_injected_tracker_registration() -> None:
    tracker = UtilizationTracker()
    service = DiscoveryService(registry={}, tracker=tracker)
    candidate = _resource(endpoint_url="https://gpu.example.com")

    assert service.providers == ()
    assert service.cached("missing") is None
    assert service.all_cached_resources() == []
    assert service.auto_register(candidate, WorkSpec())["registered"] is True


async def test_service_accepts_synchronous_discover_and_rejects_invalid_provider() -> None:
    class _SyncDiscover:
        def discover(self) -> list[DiscoveredResource]:
            return [_resource()]

    service = DiscoveryService(
        registry={"sync": _SyncDiscover(), "invalid": object()},
    )

    sync = await service.discover("sync")
    invalid = await service.discover("invalid")

    assert sync.status == DiscoveryStatus.OK
    assert invalid.status == DiscoveryStatus.ERROR
    assert invalid.error == "TypeError"


async def test_service_preserves_non_outage_status_without_cache_fallback() -> None:
    class _Unavailable:
        async def discover(self) -> DiscoveryResult:
            return DiscoveryResult(
                "ignored",
                DiscoveryStatus.PROVIDER_UNAVAILABLE,
                error="provider_unavailable",
            )

    service = DiscoveryService(registry={"cloud": _Unavailable()})

    result = await service.discover("cloud")

    assert result.provider == "cloud"
    assert result.status == DiscoveryStatus.PROVIDER_UNAVAILABLE


async def test_discover_all_runs_probes_concurrently() -> None:
    entered = 0
    release = asyncio.Event()

    class _AsyncProbe:
        async def discover(self) -> DiscoveryResult:
            nonlocal entered
            entered += 1
            if entered == 2:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=0.5)
            return DiscoveryResult("async", DiscoveryStatus.OK)

    service = DiscoveryService(registry={"one": _AsyncProbe(), "two": _AsyncProbe()})

    results = await service.discover_all()

    assert set(results) == {"one", "two"}
