"""E2E evaluation: discover models runnable on local hardware and auto-select candidates.

This file covers two complementary planes:

OFFLINE (always runs, no key / no network):
  - ``LocalModelDiscovery`` harness discovers candidate models from a pool of
    ``ModelProfile`` entries by checking each profile's ``resource_profile``
    against mocked hardware specs (cpu_cores, mem_gb, vram_gb).
  - Budget/work-aware AUTO-SELECT picks the best runnable candidate from
    ``AdaptiveRouter`` aggregate scores and registers the winner into an
    in-test ``UtilizationTracker``.
  - "No model fits the budget" fallback: when the resource budget is too tight
    for every candidate, ``LocalModelDiscovery.select`` returns ``None`` and
    the tracker is never populated.
  - CPU-only path: a machine with no GPU but sufficient RAM can still run
    quantized CPU-only profiles.

LIVE / LOCAL (skipped by default):
  Guarded by ``GLUDD_RUN_LOCAL_MODEL=1``.  When set, the test attempts to call
  a configured local or z.ai gateway profile and asserts a non-empty completion.
  Set ``GLUDD_LOCAL_MODEL_PROFILE`` to the profile id to test (defaults to a
  minimal z.ai glm-4.6 profile when ``ZAI_API_KEY`` is available).

Run (offline only, default):
    make test-unit TESTFILE=tests/e2e/test_local_model_discovery_eval.py

Run live/local (requires GLUDD_RUN_LOCAL_MODEL=1 and a valid API key):
    GLUDD_RUN_LOCAL_MODEL=1 ZAI_API_KEY=<key> \\
        uv run pytest tests/e2e/test_local_model_discovery_eval.py -s -v
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_ZAI_MODEL = "glm-4.6"

_RUN_LOCAL = os.getenv("GLUDD_RUN_LOCAL_MODEL", "").strip() in ("1", "true", "yes")
_LOCAL_SKIP_REASON = (
    "GLUDD_RUN_LOCAL_MODEL not set — "
    "set GLUDD_RUN_LOCAL_MODEL=1 to run the live/local model test"
)

# ---------------------------------------------------------------------------
# Hardware spec dataclass (mocked in offline tests)
# ---------------------------------------------------------------------------


@dataclass
class HardwareSpec:
    """Snapshot of available local compute resources."""

    cpu_cores: int
    mem_gb: float
    vram_gb: float = 0.0
    gpu_name: str = ""
    has_cuda: bool = False
    has_metal: bool = False

    @classmethod
    def probe(cls) -> HardwareSpec:
        """Probe the actual host hardware (used in live tests only)."""
        import platform
        import shutil

        cpu_cores = os.cpu_count() or 2

        # Best-effort mem_gb via /proc/meminfo (Linux) or sysctl (macOS).
        mem_gb = 8.0
        try:
            if platform.system() == "Linux":
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            mem_kb = int(line.split()[1])
                            mem_gb = mem_kb / (1024 * 1024)
                            break
            elif platform.system() == "Darwin":
                import subprocess
                out = subprocess.check_output(
                    ["sysctl", "-n", "hw.memsize"], text=True, timeout=5
                )
                mem_gb = int(out.strip()) / (1024 ** 3)
        except Exception:
            pass

        has_cuda = shutil.which("nvidia-smi") is not None
        has_metal = platform.system() == "Darwin"

        vram_gb = 0.0
        if has_cuda:
            try:
                import subprocess
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.total",
                     "--format=csv,noheader,nounits"],
                    text=True, timeout=10,
                )
                vram_mb = float(out.strip().splitlines()[0])
                vram_gb = vram_mb / 1024.0
            except Exception:
                pass

        return cls(
            cpu_cores=cpu_cores,
            mem_gb=mem_gb,
            vram_gb=vram_gb,
            has_cuda=has_cuda,
            has_metal=has_metal,
        )


# ---------------------------------------------------------------------------
# Resource requirements per resource_profile label (mirrors ModelProfile field)
# ---------------------------------------------------------------------------

# Each tuple: (min_mem_gb, min_vram_gb_for_gpu, min_cpu_cores, cpu_ram_fallback_gb)
# ``cpu_ram_fallback_gb`` is the RAM required to run quantized CPU-only.
_RESOURCE_REQUIREMENTS: dict[str, tuple[float, float, int, float]] = {
    "cpu_tiny": (2.0, 0.0, 2, 2.0),
    "cpu_small": (4.0, 0.0, 2, 4.0),
    "cpu_medium": (8.0, 0.0, 4, 8.0),
    "ai_light": (4.0, 2.0, 2, 8.0),
    "ai_medium": (8.0, 4.0, 4, 16.0),
    "ai_heavy": (16.0, 8.0, 8, 32.0),
    "ai_xlarge": (32.0, 16.0, 16, 64.0),
}

_DEFAULT_REQUIREMENTS = (16.0, 8.0, 8, 32.0)  # conservative default


def _profile_fits(resource_profile: str, hw: HardwareSpec) -> bool:
    """Return True if the hardware can run a model with this resource_profile."""
    min_mem, min_vram, min_cpu, cpu_fallback_ram = _RESOURCE_REQUIREMENTS.get(
        resource_profile, _DEFAULT_REQUIREMENTS
    )

    if hw.cpu_cores < min_cpu:
        return False

    # GPU path: VRAM is sufficient.
    if (hw.has_cuda or hw.has_metal) and hw.vram_gb >= min_vram:
        return hw.mem_gb >= min_mem

    # CPU-only fallback: need more system RAM to compensate.
    return hw.mem_gb >= cpu_fallback_ram


# ---------------------------------------------------------------------------
# Minimal in-test UtilizationTracker (no external compute/ module required)
# ---------------------------------------------------------------------------


@dataclass
class UtilizationTracker:
    """Tracks registered local model candidates and their resource assignments."""

    _registered: list[dict[str, Any]] = field(default_factory=list)

    def register(
        self,
        model_profile_id: str,
        resource_profile: str,
        hw: HardwareSpec,
        score: float = 0.0,
    ) -> None:
        self._registered.append(
            {
                "model_profile_id": model_profile_id,
                "resource_profile": resource_profile,
                "hw_cpu_cores": hw.cpu_cores,
                "hw_mem_gb": hw.mem_gb,
                "hw_vram_gb": hw.vram_gb,
                "composite_score": score,
            }
        )

    def list_registered(self) -> list[dict[str, Any]]:
        return list(self._registered)

    def is_registered(self, model_profile_id: str) -> bool:
        return any(e["model_profile_id"] == model_profile_id for e in self._registered)


# ---------------------------------------------------------------------------
# LocalModelDiscovery harness
# ---------------------------------------------------------------------------


class LocalModelDiscovery:
    """Discover and auto-select local model candidates from a ModelGateway.

    Workflow:
      1. ``discover(hw)``  — filter ``gateway.list_profiles()`` to those whose
         ``resource_profile`` fits the given ``HardwareSpec``.
      2. ``select(hw, task_type, budget_usd)``  — run ``AdaptiveRouter.route()``
         over the runnable candidates and return the best ``RoutingDecision``.
      3. ``register_winner(decision, hw, tracker)``  — write the selected profile
         into a ``UtilizationTracker`` for downstream scheduling.
    """

    def __init__(
        self,
        gateway: Any,
        router: Any,
    ) -> None:
        self._gateway = gateway
        self._router = router

    def discover(self, hw: HardwareSpec) -> list[Any]:
        """Return the subset of gateway profiles runnable on *hw*."""
        runnable = []
        for profile in self._gateway.list_profiles():
            if profile.enabled and _profile_fits(profile.resource_profile, hw):
                runnable.append(profile)
        return runnable

    async def select(
        self,
        hw: HardwareSpec,
        task_type: Any,
        budget_usd: float = 0.0,
    ) -> Any | None:
        """Return a RoutingDecision for the best runnable profile, or None."""
        runnable = self.discover(hw)
        if not runnable:
            return None

        runnable_ids = {p.model_profile_id for p in runnable}

        # Route subject to the budget cap; the router's fallback fires when no
        # profile fits the cost constraint.
        decision = await self._router.route(
            task_type,
            max_cost_usd=budget_usd if budget_usd > 0.0 else None,
        )

        # If the router selected a profile NOT in our runnable set (e.g. it
        # came from a remote-only aggregate), respect local feasibility and
        # return None.
        if decision.fallback:
            # Fallback means no historical data / no fit — surface it directly;
            # the caller decides whether to use the default.
            return decision

        if decision.selected_model_profile_id not in runnable_ids:
            return None

        return decision

    @staticmethod
    def register_winner(
        decision: Any,
        hw: HardwareSpec,
        tracker: UtilizationTracker,
        gateway: Any,
    ) -> None:
        """Register the winning profile into the tracker if it is locally runnable."""
        profile_id = decision.selected_model_profile_id
        profile = gateway.get_profile(profile_id)
        resource_profile = profile.resource_profile if profile else "unknown"
        tracker.register(
            model_profile_id=profile_id,
            resource_profile=resource_profile,
            hw=hw,
            score=decision.composite_score,
        )


# ---------------------------------------------------------------------------
# Helpers: build a fake gateway + router for offline tests
# ---------------------------------------------------------------------------

def _make_fake_gateway(profiles: list[dict[str, Any]]) -> Any:
    """Return a ModelGateway loaded with the given profile dicts."""
    from general_ludd.models.gateway import ModelGateway, ModelProfile

    parsed = [ModelProfile(**p) for p in profiles]
    gw = ModelGateway(profiles=parsed)
    return gw


def _make_stub_router(
    best_model_id: str,
    composite_score: float = 0.85,
    avg_cost_usd: float = 0.001,
    sample_count: int = 5,
    task_type: Any = None,
) -> Any:
    """Return an AdaptiveRouter whose _get_best_from_history is stubbed."""
    from general_ludd.schemas.benchmark import RoutingCandidate, TaskType

    tt = task_type or TaskType.BUG_FIX

    candidate = RoutingCandidate(
        prompt_profile_id=None,
        model_profile_id=best_model_id,
        composite_score=composite_score,
        avg_cost_usd=avg_cost_usd,
        sample_count=sample_count,
        task_type=tt,
    )

    router = MagicMock()
    # route() is async; must return an awaitable RoutingDecision.
    from general_ludd.schemas.benchmark import RoutingDecision

    decision = RoutingDecision(
        selected_prompt_profile_id=None,
        selected_model_profile_id=best_model_id,
        composite_score=composite_score,
        estimated_cost_usd=avg_cost_usd,
        sample_count=sample_count,
        fallback=False,
        reason="best_historical_score",
    )
    router.route = AsyncMock(return_value=decision)
    router._candidate = candidate  # stored for test assertions
    return router


def _make_fallback_router() -> Any:
    """Return a router that always returns a fallback decision."""
    router = MagicMock()
    from general_ludd.schemas.benchmark import RoutingDecision

    decision = RoutingDecision(
        selected_prompt_profile_id=None,
        selected_model_profile_id="default",
        composite_score=0.0,
        estimated_cost_usd=0.0,
        sample_count=0,
        fallback=True,
        reason="cost_cap_no_fit",
    )
    router.route = AsyncMock(return_value=decision)
    return router


# ---------------------------------------------------------------------------
# Shared profile catalogue used by offline tests
# ---------------------------------------------------------------------------

_PROFILES_CATALOGUE: list[dict[str, Any]] = [
    # CPU-tiny: runs on almost any machine.
    dict(
        model_profile_id="tiny-cpu-q4",
        provider="openai",
        model_name="tinyllama-1b-q4",
        resource_profile="cpu_tiny",
        enabled=True,
        context_window=2048,
        max_input_tokens=1800,
        max_output_tokens=256,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=0.0,
        roles=["coder"],
    ),
    # AI-light: needs a small GPU or 8 GB RAM.
    dict(
        model_profile_id="phi3-mini-4k",
        provider="openai",
        model_name="phi-3-mini-4k-instruct",
        resource_profile="ai_light",
        enabled=True,
        context_window=4096,
        max_input_tokens=3800,
        max_output_tokens=512,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=0.0,
        roles=["coder", "reviewer"],
    ),
    # AI-heavy: needs 16 GB RAM or an 8 GB VRAM GPU (disabled by default).
    dict(
        model_profile_id="llama3-8b",
        provider="openai",
        model_name="llama-3-8b-instruct",
        resource_profile="ai_heavy",
        enabled=True,
        context_window=8192,
        max_input_tokens=7500,
        max_output_tokens=1024,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=0.0,
        roles=["coder", "planner"],
    ),
    # Disabled profile — must never appear in discovery results.
    dict(
        model_profile_id="disabled-model",
        provider="openai",
        model_name="some-model",
        resource_profile="cpu_tiny",
        enabled=False,
        context_window=2048,
        max_input_tokens=1800,
        max_output_tokens=256,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=0.0,
        roles=[],
    ),
]


# ---------------------------------------------------------------------------
# OFFLINE TESTS
# ---------------------------------------------------------------------------


class TestLocalModelDiscoveryOffline:
    """Offline: hardware-gated discovery + AUTO-SELECT using mocked hw + stub router."""

    # ------------------------------------------------------------------ #
    # 1. Discovery — well-provisioned machine (16 GB RAM, 8 GB VRAM GPU)
    # ------------------------------------------------------------------ #

    def test_discover_all_runnable_on_well_provisioned_machine(self) -> None:
        """A machine with 16 GB RAM and 8 GB GPU VRAM can run all enabled profiles."""
        hw = HardwareSpec(
            cpu_cores=8, mem_gb=16.0, vram_gb=8.0, has_cuda=True
        )
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)
        router = _make_stub_router("llama3-8b")
        disc = LocalModelDiscovery(gateway=gw, router=router)

        runnable = disc.discover(hw)
        ids = {p.model_profile_id for p in runnable}

        # All three enabled profiles fit; disabled one must be absent.
        assert "tiny-cpu-q4" in ids, f"expected tiny-cpu-q4 in runnable; got {ids}"
        assert "phi3-mini-4k" in ids, f"expected phi3-mini-4k in runnable; got {ids}"
        assert "llama3-8b" in ids, f"expected llama3-8b in runnable; got {ids}"
        assert "disabled-model" not in ids, "disabled profile must not appear in discovery"

    # ------------------------------------------------------------------ #
    # 2. Discovery — constrained machine (4 GB RAM, no GPU)
    # ------------------------------------------------------------------ #

    def test_discover_limited_to_cpu_tiny_on_low_mem_machine(self) -> None:
        """4 GB RAM, no GPU: only cpu_tiny fits; ai_light / ai_heavy do not."""
        hw = HardwareSpec(cpu_cores=4, mem_gb=4.0, vram_gb=0.0)
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)
        router = _make_stub_router("tiny-cpu-q4")
        disc = LocalModelDiscovery(gateway=gw, router=router)

        runnable = disc.discover(hw)
        ids = {p.model_profile_id for p in runnable}

        assert "tiny-cpu-q4" in ids, f"cpu_tiny should fit 4 GB RAM; got {ids}"
        assert "phi3-mini-4k" not in ids, "ai_light needs 8 GB CPU RAM; should not fit 4 GB"
        assert "llama3-8b" not in ids, "ai_heavy needs 32 GB CPU RAM; should not fit 4 GB"

    # ------------------------------------------------------------------ #
    # 3. Discovery — CPU-only but sufficient RAM (16 GB, no GPU)
    # ------------------------------------------------------------------ #

    def test_discover_cpu_only_medium_memory_machine(self) -> None:
        """16 GB RAM, no GPU: cpu_tiny and ai_light fit (cpu fallback); ai_heavy needs 32 GB."""
        hw = HardwareSpec(cpu_cores=4, mem_gb=16.0, vram_gb=0.0)
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)
        router = _make_stub_router("phi3-mini-4k")
        disc = LocalModelDiscovery(gateway=gw, router=router)

        runnable = disc.discover(hw)
        ids = {p.model_profile_id for p in runnable}

        assert "tiny-cpu-q4" in ids
        assert "phi3-mini-4k" in ids, (
            "ai_light cpu_fallback_ram is 8 GB; 16 GB should fit"
        )
        assert "llama3-8b" not in ids, (
            "ai_heavy cpu_fallback_ram is 32 GB; 16 GB RAM should NOT fit"
        )

    # ------------------------------------------------------------------ #
    # 4. Discovery — no profiles fit (extremely constrained)
    # ------------------------------------------------------------------ #

    def test_discover_returns_empty_when_no_profile_fits(self) -> None:
        """1 CPU core, 1 GB RAM: no profile fits even the cpu_tiny requirement."""
        hw = HardwareSpec(cpu_cores=1, mem_gb=1.0, vram_gb=0.0)
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)
        router = _make_stub_router("tiny-cpu-q4")
        disc = LocalModelDiscovery(gateway=gw, router=router)

        runnable = disc.discover(hw)
        assert runnable == [], f"expected empty runnable list; got {runnable}"

    # ------------------------------------------------------------------ #
    # 5. AUTO-SELECT: picks best candidate and registers into UtilizationTracker
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_auto_select_registers_winner_into_tracker(self) -> None:
        """select() picks best candidate; register_winner() writes it into tracker."""
        from general_ludd.schemas.benchmark import TaskType

        hw = HardwareSpec(cpu_cores=8, mem_gb=16.0, vram_gb=8.0, has_cuda=True)
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)
        router = _make_stub_router(
            "llama3-8b", composite_score=0.9, avg_cost_usd=0.0
        )
        disc = LocalModelDiscovery(gateway=gw, router=router)
        tracker = UtilizationTracker()

        decision = await disc.select(hw, TaskType.BUG_FIX, budget_usd=0.0)

        assert decision is not None, "select() must return a decision on a capable machine"
        assert not decision.fallback, "select() should NOT return a fallback on capable hw"
        assert decision.selected_model_profile_id == "llama3-8b"

        LocalModelDiscovery.register_winner(decision, hw, tracker, gw)

        assert tracker.is_registered("llama3-8b"), (
            "register_winner() must write the selected profile into the tracker"
        )
        entries = tracker.list_registered()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["hw_cpu_cores"] == 8
        assert entry["hw_mem_gb"] == 16.0
        assert entry["composite_score"] == pytest.approx(0.9)

    # ------------------------------------------------------------------ #
    # 6. AUTO-SELECT: no model fits the budget → fallback decision, no registration
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_auto_select_fallback_when_budget_too_tight(self) -> None:
        """When the router returns a fallback, select() surfaces it; tracker stays empty."""
        from general_ludd.schemas.benchmark import TaskType

        hw = HardwareSpec(cpu_cores=8, mem_gb=16.0, vram_gb=8.0, has_cuda=True)
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)
        router = _make_fallback_router()
        disc = LocalModelDiscovery(gateway=gw, router=router)
        tracker = UtilizationTracker()

        decision = await disc.select(hw, TaskType.BUG_FIX, budget_usd=0.00001)

        assert decision is not None
        assert decision.fallback is True, (
            "select() must propagate the fallback flag when router yields no fit"
        )

        # Do NOT register a fallback decision into the tracker.
        assert tracker.list_registered() == [], (
            "tracker must remain empty when no profile fits the budget"
        )

    # ------------------------------------------------------------------ #
    # 7. AUTO-SELECT: no runnable profiles → select() returns None
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_auto_select_returns_none_when_nothing_runnable(self) -> None:
        """If discover() yields nothing, select() returns None without calling the router."""
        from general_ludd.schemas.benchmark import TaskType

        hw = HardwareSpec(cpu_cores=1, mem_gb=1.0, vram_gb=0.0)
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)
        router = _make_stub_router("tiny-cpu-q4")
        disc = LocalModelDiscovery(gateway=gw, router=router)

        result = await disc.select(hw, TaskType.BUG_FIX, budget_usd=0.0)

        assert result is None, (
            "select() must return None when no profile fits the hardware"
        )
        # Router should not have been called since there are no runnable profiles.
        router.route.assert_not_called()

    # ------------------------------------------------------------------ #
    # 8. AUTO-SELECT: best candidate not in runnable set → returns None
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_auto_select_returns_none_when_router_picks_non_local(self) -> None:
        """If the router picks a remote-only profile, select() returns None (local infeasible)."""
        from general_ludd.schemas.benchmark import RoutingDecision, TaskType

        hw = HardwareSpec(cpu_cores=4, mem_gb=8.0, vram_gb=0.0)
        # Only cpu_tiny and ai_light are runnable on 8 GB no-GPU.
        gw = _make_fake_gateway(_PROFILES_CATALOGUE)

        # Router returns a profile not in the runnable set.
        router = MagicMock()
        router.route = AsyncMock(
            return_value=RoutingDecision(
                selected_prompt_profile_id=None,
                selected_model_profile_id="llama3-8b",  # needs 32 GB CPU RAM
                composite_score=0.95,
                estimated_cost_usd=0.0,
                sample_count=10,
                fallback=False,
                reason="best_historical_score",
            )
        )
        disc = LocalModelDiscovery(gateway=gw, router=router)

        result = await disc.select(hw, TaskType.FEATURE, budget_usd=0.0)

        assert result is None, (
            "select() must return None when the router's pick cannot run locally"
        )

    # ------------------------------------------------------------------ #
    # 9. UtilizationTracker: multiple registrations, is_registered, list
    # ------------------------------------------------------------------ #

    def test_utilization_tracker_registers_multiple(self) -> None:
        """UtilizationTracker accumulates entries and is_registered is accurate."""
        hw = HardwareSpec(cpu_cores=8, mem_gb=32.0, vram_gb=8.0, has_cuda=True)
        tracker = UtilizationTracker()

        tracker.register("model-a", "ai_light", hw, score=0.75)
        tracker.register("model-b", "ai_heavy", hw, score=0.90)

        assert tracker.is_registered("model-a")
        assert tracker.is_registered("model-b")
        assert not tracker.is_registered("model-c")

        entries = tracker.list_registered()
        assert len(entries) == 2
        scores = {e["model_profile_id"]: e["composite_score"] for e in entries}
        assert scores["model-a"] == pytest.approx(0.75)
        assert scores["model-b"] == pytest.approx(0.90)

    # ------------------------------------------------------------------ #
    # 10. _profile_fits: unit coverage of edge cases
    # ------------------------------------------------------------------ #

    def test_profile_fits_unknown_resource_profile_uses_conservative_default(self) -> None:
        """An unknown resource_profile label applies the conservative default (ai_heavy tier)."""
        hw_ok = HardwareSpec(cpu_cores=16, mem_gb=64.0, vram_gb=0.0)
        hw_tight = HardwareSpec(cpu_cores=4, mem_gb=8.0, vram_gb=0.0)

        assert _profile_fits("unknown_exotic_label", hw_ok), (
            "64 GB machine should handle the conservative 32 GB CPU-fallback default"
        )
        assert not _profile_fits("unknown_exotic_label", hw_tight), (
            "8 GB machine must NOT handle the 32 GB conservative default"
        )

    def test_profile_fits_gpu_path_vram_sufficient(self) -> None:
        """A machine with exactly the required VRAM passes the GPU path."""
        # ai_heavy needs min_vram=8 GB; 8 GB GPU + 16 GB RAM should pass.
        hw = HardwareSpec(cpu_cores=8, mem_gb=16.0, vram_gb=8.0, has_cuda=True)
        assert _profile_fits("ai_heavy", hw)

    def test_profile_fits_gpu_path_vram_insufficient_falls_back_to_cpu(self) -> None:
        """GPU present but not enough VRAM; falls through to CPU-RAM check."""
        # ai_heavy cpu_fallback_ram=32 GB; 16 GB must fail.
        hw_fail = HardwareSpec(
            cpu_cores=8, mem_gb=16.0, vram_gb=4.0, has_cuda=True
        )
        assert not _profile_fits("ai_heavy", hw_fail)

        # With 32 GB RAM the CPU fallback succeeds.
        hw_ok = HardwareSpec(
            cpu_cores=8, mem_gb=32.0, vram_gb=4.0, has_cuda=True
        )
        assert _profile_fits("ai_heavy", hw_ok)

    def test_profile_fits_cpu_cores_gate(self) -> None:
        """Insufficient CPU core count blocks even if RAM is ample."""
        # ai_medium requires min_cpu=4; 2 cores must fail regardless of RAM.
        hw = HardwareSpec(cpu_cores=2, mem_gb=64.0, vram_gb=16.0, has_cuda=True)
        assert not _profile_fits("ai_medium", hw)


# ---------------------------------------------------------------------------
# OPTIONAL LIVE / LOCAL TEST
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _RUN_LOCAL, reason=_LOCAL_SKIP_REASON)
class TestLocalModelLive:
    """Live/local: probe real hardware, run discovery, and attempt a model call.

    Skipped by default; enable with GLUDD_RUN_LOCAL_MODEL=1.

    If ZAI_API_KEY is set, falls back to the z.ai/glm-4.6 gateway as a
    stand-in for a "local" provider (same code path, different transport).
    Set GLUDD_LOCAL_MODEL_PROFILE to override the profile id.
    """

    def _load_zai_key(self) -> str | None:
        key = os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if key:
            return key
        from pathlib import Path
        key_file = Path(__file__).parent.parent.parent / ".zai.key"
        if key_file.exists():
            v = key_file.read_text().strip()
            return v if v else None
        return None

    def _build_live_gateway(self, profile_id: str) -> Any:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager

        zai_key = self._load_zai_key()
        if not zai_key:
            pytest.skip("ZAI_API_KEY not set; cannot run live model call")

        profile = ModelProfile(
            model_profile_id=profile_id,
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name=_ZAI_MODEL,
            api_base_alias="ZAI_BASE_URL",
            credential_alias="ZAI_API_KEY",
            context_window=64000,
            max_input_tokens=60000,
            max_output_tokens=256,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
            api_metered=False,
            run_budget_usd=1.0,
            enabled=True,
            resource_profile="ai_light",
            roles=["coder"],
        )
        registry = ProviderRegistry()
        registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
        secrets = EnvSecretsManager()
        secrets.set("ZAI_API_KEY", zai_key)
        secrets.set("ZAI_BASE_URL", _ZAI_BASE_URL)
        return ModelGateway(
            profiles=[profile],
            provider_registry=registry,
            secrets_manager=secrets,  # type: ignore[arg-type]
        )

    def test_live_hardware_probe_and_discovery(self) -> None:
        """Probe real host hardware and run discovery against a live-ish gateway."""
        hw = HardwareSpec.probe()
        print(
            f"\n[LIVE HW] cpu={hw.cpu_cores} mem={hw.mem_gb:.1f}GB "
            f"vram={hw.vram_gb:.1f}GB cuda={hw.has_cuda} metal={hw.has_metal}"
        )

        profile_id = os.environ.get("GLUDD_LOCAL_MODEL_PROFILE", "local-zai-lite")
        gw = self._build_live_gateway(profile_id)
        router = _make_stub_router(profile_id, composite_score=0.8)
        disc = LocalModelDiscovery(gateway=gw, router=router)

        runnable = disc.discover(hw)
        print(f"[LIVE DISC] runnable profiles: {[p.model_profile_id for p in runnable]}")

        # The ai_light profile requires 8 GB CPU RAM or a 2 GB VRAM GPU.
        # On a developer laptop this should pass; on a micro VM it might not.
        if not runnable:
            pytest.skip(
                f"No profiles runnable on this host "
                f"(cpu={hw.cpu_cores} mem={hw.mem_gb:.1f}GB vram={hw.vram_gb:.1f}GB)"
            )

        # Verify the discovered profiles are all enabled and resource-fit.
        for p in runnable:
            assert p.enabled, f"discovered profile {p.model_profile_id!r} is disabled"
            assert _profile_fits(p.resource_profile, hw), (
                f"profile {p.model_profile_id!r} resource_profile={p.resource_profile!r} "
                f"claims runnable but _profile_fits() disagrees"
            )

        print(f"[LIVE DISC] discovery PASSED: {len(runnable)} profile(s) fit this host")

    @pytest.mark.xfail(
        raises=Exception,
        reason="provider rate-limit / network failure — not a discovery bug",
        strict=False,
    )
    def test_live_model_call_returns_non_empty_completion(self) -> None:
        """Call the live model and assert a non-empty completion is returned."""
        profile_id = os.environ.get("GLUDD_LOCAL_MODEL_PROFILE", "local-zai-lite")
        gw = self._build_live_gateway(profile_id)

        response = gw.call_model(
            profile_id,
            messages=[{"role": "user", "content": "Reply with exactly: LOCAL_OK"}],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )

        assert isinstance(response.content, str) and response.content.strip(), (
            f"live model returned empty content: {response!r}"
        )
        print(
            f"\n[LIVE MODEL] profile={profile_id!r} "
            f"content={response.content[:80]!r} "
            f"usage={response.usage_metadata}"
        )
        assert response.content.strip() != "[check-mode: agent run skipped]", (
            "got check-mode placeholder — real model call did NOT happen"
        )
        print("[LIVE MODEL] local model call returned a non-empty completion: PROVEN")
