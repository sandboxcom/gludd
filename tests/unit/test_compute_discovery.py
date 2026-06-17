"""Offline tests for compute resource discovery + budget-aware auto-select.

Every test runs OFFLINE: no real network, no cloud SDK installed. The local
provider uses a mocked ``scrape_system_load``; cloud providers are exercised
with their SDK absent (asserting ``provider_unavailable`` / static-table
fallback, never an ImportError at module load).
"""

from __future__ import annotations

import builtins

import pytest

from general_ludd.infra.compute import GPUType
from general_ludd.infra.discovery import (
    DiscoveredResource,
    DiscoveryResult,
    DiscoverySource,
    DiscoveryStatus,
    WorkSpec,
    build_default_registry,
    select_resource,
)
from general_ludd.infra.discovery.base import auth_env, guarded_call
from general_ludd.infra.discovery.providers.local import LocalProvider
from general_ludd.infra.discovery.selector import register_discovered
from general_ludd.infra.discovery.service import DiscoveryService


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_snapshot(load_1m: float = 0.1, cpu: int = 8, mem_avail: float = 80.0):
    from general_ludd.controllers.load_scrape import LoadSnapshot

    return LoadSnapshot(
        loadavg_1m=load_1m,
        loadavg_5m=load_1m,
        loadavg_10m=load_1m,
        logical_cpu_count=cpu,
        cpu_percent=10.0,
        memory_available_percent=mem_avail,
        disk_free_percent=50.0,
        active_jobs=0,
    )


def _res(**kw) -> DiscoveredResource:
    base = dict(
        source=DiscoverySource.AWS,
        id="r",
        vcpu=8,
        mem_gb=32.0,
        cost_per_hour=1.0,
        available=True,
    )
    base.update(kw)
    return DiscoveredResource(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Package import is SDK-free
# --------------------------------------------------------------------------- #
def test_package_import_has_no_sdk_at_module_top() -> None:
    # Importing the discovery package + every provider module must succeed with
    # ZERO cloud SDKs installed (the sandbox norm).
    import general_ludd.infra.discovery
    import general_ludd.infra.discovery.providers.aws
    import general_ludd.infra.discovery.providers.azure
    import general_ludd.infra.discovery.providers.gcp
    import general_ludd.infra.discovery.providers.kubernetes
    import general_ludd.infra.discovery.providers.vmware  # noqa: F401


# --------------------------------------------------------------------------- #
# LOCAL provider
# --------------------------------------------------------------------------- #
async def test_local_provider_discovers_cpu_resource(monkeypatch) -> None:
    monkeypatch.setattr(
        "general_ludd.controllers.load_scrape.scrape_system_load",
        lambda: _make_snapshot(),
    )
    monkeypatch.setattr(
        "general_ludd.infra.discovery.providers.local._detect_gpus", lambda: (0, None)
    )
    res = await LocalProvider().discover()
    assert res.status == DiscoveryStatus.OK
    assert len(res.resources) == 1
    r = res.resources[0]
    assert r.source == DiscoverySource.LOCAL
    assert r.vcpu >= 1
    assert r.cost_per_hour == 0.0
    assert r.available is True


async def test_local_provider_gpu_optional_degrades(monkeypatch) -> None:
    monkeypatch.setattr(
        "general_ludd.controllers.load_scrape.scrape_system_load",
        lambda: _make_snapshot(),
    )
    # pynvml absent -> gpu_count 0, NOT provider_unavailable.
    monkeypatch.setattr(
        "general_ludd.infra.discovery.providers.local._detect_gpus", lambda: (0, None)
    )
    res = await LocalProvider().discover()
    assert res.status == DiscoveryStatus.OK
    assert res.resources[0].gpu_count == 0


async def test_local_provider_drops_ssrf_internal_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "general_ludd.controllers.load_scrape.scrape_system_load",
        lambda: _make_snapshot(),
    )
    monkeypatch.setattr(
        "general_ludd.infra.discovery.providers.local._detect_gpus", lambda: (0, None)
    )
    # An internal/metadata URL must be rejected and cleared.
    monkeypatch.setenv("GLUDD_LOCAL_INFERENCE_URL", "http://169.254.169.254/latest")
    res = await LocalProvider().discover()
    assert res.resources[0].endpoint_url is None


# --------------------------------------------------------------------------- #
# Absent-SDK -> provider_unavailable / static-table, never ImportError
# --------------------------------------------------------------------------- #
async def test_kubernetes_absent_sdk_is_provider_unavailable(monkeypatch) -> None:
    real_import = builtins.__import__

    def _no_k8s(name, *a, **k):
        if name == "kubernetes" or name.startswith("kubernetes."):
            raise ImportError("no kubernetes")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_k8s)
    from general_ludd.infra.discovery.providers.kubernetes import KubernetesProvider

    res = await KubernetesProvider().discover()
    assert res.status == DiscoveryStatus.PROVIDER_UNAVAILABLE


async def test_aws_absent_sdk_falls_back_to_static_table(monkeypatch) -> None:
    real_import = builtins.__import__

    def _no_boto3(name, *a, **k):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_boto3)
    from general_ludd.infra.discovery.providers.aws import AWSProvider

    res = await AWSProvider().discover()
    # AWS degrades to PARTIAL with the static table, not provider_unavailable.
    assert res.status == DiscoveryStatus.PARTIAL
    assert len(res.resources) > 0
    assert all(r.labels.get("price_source") == "static_table" for r in res.resources)


# --------------------------------------------------------------------------- #
# auth_env: transient inject + restore + fail-closed
# --------------------------------------------------------------------------- #
def test_auth_env_injects_and_restores(monkeypatch) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)

    class _Resolver:
        def resolve(self, alias: str) -> str | None:
            return "SECRET" if alias == "aws_key" else None

    import os

    with auth_env({"AWS_ACCESS_KEY_ID": "aws_key"}, _Resolver()):
        assert os.environ["AWS_ACCESS_KEY_ID"] == "SECRET"
    # Restored (removed) after the block.
    assert "AWS_ACCESS_KEY_ID" not in os.environ


def test_auth_env_fails_closed_on_unresolvable_alias() -> None:
    class _Resolver:
        def resolve(self, alias: str) -> str | None:
            return None

    with pytest.raises(RuntimeError), auth_env({"AWS_ACCESS_KEY_ID": "missing"}, _Resolver()):
        pass  # pragma: no cover - never reached


def test_auth_env_restores_even_on_exception(monkeypatch) -> None:
    import os

    monkeypatch.setenv("FOO", "orig")

    class _Resolver:
        def resolve(self, alias: str) -> str | None:
            return "new"

    with pytest.raises(ValueError), auth_env({"FOO": "a"}, _Resolver()):
        assert os.environ["FOO"] == "new"
        raise ValueError("boom")
    assert os.environ["FOO"] == "orig"


# --------------------------------------------------------------------------- #
# guarded_call: offline/timeout returns structured (raises to caller, no hang)
# --------------------------------------------------------------------------- #
async def test_guarded_call_times_out_without_hanging() -> None:
    import time

    def _slow() -> int:
        time.sleep(5)
        return 1

    with pytest.raises((TimeoutError, Exception)):
        await guarded_call(_slow, timeout_s=0.05, max_retries=0)


async def test_guarded_call_returns_value() -> None:
    assert await guarded_call(lambda: 42, timeout_s=1.0) == 42


# --------------------------------------------------------------------------- #
# Selector: never over-budget, fail-closed
# --------------------------------------------------------------------------- #
def test_selector_returns_none_when_nothing_fits_need() -> None:
    spec = WorkSpec(needs_gpu=True, gpu_count=2)
    cpu_only = _res(gpu_count=0)
    assert select_resource(spec, [cpu_only], headroom=1000.0) is None


def test_selector_never_picks_over_budget() -> None:
    spec = WorkSpec(vcpu=4, mem_gb=8.0, max_cost_usd=0.5)
    cheap = _res(id="cheap", cost_per_hour=0.4)
    pricey = _res(id="pricey", cost_per_hour=5.0)
    pick = select_resource(spec, [pricey, cheap], headroom=1000.0)
    assert pick is not None
    assert pick.id == "cheap"


def test_selector_fails_closed_when_all_over_budget() -> None:
    spec = WorkSpec(vcpu=4, mem_gb=8.0, max_cost_usd=0.1)
    a = _res(id="a", cost_per_hour=5.0)
    b = _res(id="b", cost_per_hour=9.0)
    assert select_resource(spec, [a, b], headroom=1000.0) is None


def test_selector_unknown_cost_rejected_under_cap() -> None:
    spec = WorkSpec(vcpu=4, mem_gb=8.0, max_cost_usd=10.0)
    unknown = _res(id="u", cost_per_hour=None)
    assert select_resource(spec, [unknown], headroom=1000.0) is None


def test_selector_unknown_cost_allowed_without_cap() -> None:
    spec = WorkSpec(vcpu=4, mem_gb=8.0, max_cost_usd=None)
    unknown = _res(id="u", cost_per_hour=None)
    pick = select_resource(spec, [unknown], headroom=float("inf"))
    assert pick is not None and pick.id == "u"


def test_selector_headroom_gates_pick() -> None:
    spec = WorkSpec(vcpu=4, mem_gb=8.0)  # no per-pick cap
    pricey = _res(id="p", cost_per_hour=5.0)
    # headroom below the projected cost -> fail closed.
    assert select_resource(spec, [pricey], headroom=1.0) is None


def test_selector_atomic_reserve_drop_and_retry() -> None:
    spec = WorkSpec(vcpu=4, mem_gb=8.0, max_cost_usd=10.0)
    cheap = _res(id="cheap", cost_per_hour=1.0)
    cheaper = _res(id="cheaper", cost_per_hour=0.5)

    class _Limiter:
        def __init__(self) -> None:
            self.calls: list[float | None] = []

        def try_charge(self, cost, **kw) -> bool:
            self.calls.append(cost)
            # Refuse the first (best-scored) candidate, accept the next.
            return len(self.calls) > 1

    limiter = _Limiter()
    pick = select_resource(
        spec, [cheap, cheaper], headroom=1000.0, spend_limiter=limiter
    )
    assert pick is not None
    assert len(limiter.calls) >= 2


def test_selector_drops_ssrf_internal_endpoint() -> None:
    spec = WorkSpec(vcpu=4, mem_gb=8.0)
    bad = _res(id="bad", endpoint_url="http://169.254.169.254/")
    assert select_resource(spec, [bad], headroom=1000.0) is None


# --------------------------------------------------------------------------- #
# register_discovered: dedupe + needs_deploy
# --------------------------------------------------------------------------- #
def test_register_needs_deploy_when_no_endpoint() -> None:
    from general_ludd.infra.utilization import UtilizationTracker

    tracker = UtilizationTracker()
    pick = _res(id="x", endpoint_url=None, source=DiscoverySource.AWS)
    out = register_discovered(tracker, pick, WorkSpec(model="m"))
    assert out["registered"] is False
    assert out["needs_deploy"] is True
    assert tracker.list_endpoints() == []


def test_register_endpoint_and_dedupe() -> None:
    from general_ludd.infra.utilization import UtilizationTracker

    tracker = UtilizationTracker()
    pick = _res(
        id="x",
        endpoint_url="https://gpu.example.com:8000",
        source=DiscoverySource.AWS,
        gpu_count=2,
        gpu_type=GPUType.A10G,
    )
    out = register_discovered(tracker, pick, WorkSpec(model="m"))
    assert out["registered"] is True
    ep_id = out["endpoint_id"]
    ep = tracker.get_endpoint(ep_id)
    assert ep is not None
    # Simulate load, then re-register: dedupe must preserve current_load.
    ep.current_load = 3
    out2 = register_discovered(tracker, pick, WorkSpec(model="m"))
    assert out2.get("deduped") is True
    assert tracker.get_endpoint(ep_id).current_load == 3


def test_register_drops_ssrf_internal_endpoint() -> None:
    from general_ludd.infra.utilization import UtilizationTracker

    tracker = UtilizationTracker()
    pick = _res(id="x", endpoint_url="http://10.0.0.5:8000", source=DiscoverySource.AWS)
    out = register_discovered(tracker, pick, WorkSpec(model="m"))
    assert out["registered"] is False
    assert tracker.list_endpoints() == []


# --------------------------------------------------------------------------- #
# DiscoveryService: cache + breaker degrade gracefully
# --------------------------------------------------------------------------- #
async def test_service_caches_last_good_and_serves_on_outage() -> None:
    class _Provider:
        source = DiscoverySource.AWS

        def __init__(self) -> None:
            self.calls = 0

        async def discover(self) -> DiscoveryResult:
            self.calls += 1
            if self.calls == 1:
                return DiscoveryResult(
                    source=DiscoverySource.AWS,
                    status=DiscoveryStatus.OK,
                    resources=(_res(id="good"),),
                )
            return DiscoveryResult(
                source=DiscoverySource.AWS, status=DiscoveryStatus.OFFLINE
            )

    svc = DiscoveryService(registry={DiscoverySource.AWS: _Provider()})  # type: ignore[dict-item]
    first = await svc.discover(DiscoverySource.AWS)
    assert first.status == DiscoveryStatus.OK and not first.from_cache
    # Second call goes offline -> served from cache.
    second = await svc.discover(DiscoverySource.AWS)
    assert second.from_cache is True
    assert second.resources[0].id == "good"


async def test_service_discover_all_never_raises() -> None:
    class _Boom:
        source = DiscoverySource.AWS

        async def discover(self) -> DiscoveryResult:
            raise RuntimeError("provider blew up")

    svc = DiscoveryService(registry={DiscoverySource.AWS: _Boom()})  # type: ignore[dict-item]
    results = await svc.discover_all()
    assert DiscoverySource.AWS in results
    assert results[DiscoverySource.AWS].status in (
        DiscoveryStatus.ERROR,
        DiscoveryStatus.OFFLINE,
    )


def test_build_default_registry_has_all_sources() -> None:
    reg = build_default_registry(secrets_resolver=None)
    assert set(reg) == set(DiscoverySource)


async def test_service_auto_register_routes_through_tracker() -> None:
    from general_ludd.infra.utilization import UtilizationTracker

    tracker = UtilizationTracker()
    svc = DiscoveryService(registry={}, tracker=tracker)
    pick = _res(
        id="x", endpoint_url="https://gpu.example.com:8000", source=DiscoverySource.AWS
    )
    out = svc.auto_register(pick, WorkSpec(model="m"))
    assert out["registered"] is True
    # The existing route_task() now sees the registered endpoint.
    routing = tracker.route_task("task-1", model="m")
    assert routing is not None
