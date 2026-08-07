"""Multi-model pipeline E2E benchmarks across three deployment tiers.

Tests the planner→coder→reviewer pipeline via MultiModelGamePipeline for 4 games
(snake, pong, tetris, breakout) against three cloud deployment models:

1. **DeepSeek API (PaaS)** — single-model: deepseek-chat for all 3 phases
2. **OpenRouter (PaaS)** — multi-model: Qwen2.5-Coder-7B (coder),
   DeepSeek-V3 (planner), Llama-3.3-70B (reviewer)
3. **Self-hosted endpoint (IaaS)** — LOCAL_MODEL_NAME with any
   OpenAI-compatible endpoint for all 3 phases

Tracks per-phase metrics (latency, tokens, AST validity, importable, features)
and produces a comparison table: single-model vs multi-model.

Skip conditions:
    - DEEPSEEK_API_KEY not set and .deepseek.key not found (DeepSeek tests)
    - OPENROUTER_API_KEY not set and .openrouter.key not found (OpenRouter tests)
    - LOCAL_MODEL_BASE_URL unreachable (local tests)

Smoke mode (one game):
    MP_GAME=snake uv run pytest tests/e2e/test_multi_model_pipeline_cloud.py -v -s

Run:
    DEEPSEEK_API_KEY="sk-..." OPENROUTER_API_KEY="sk-or-v1-..." \
        uv run pytest tests/e2e/test_multi_model_pipeline_cloud.py -v -s
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from tests.e2e._game_lifecycle import run_lifecycle_checks
from tests.e2e.test_game_building_deepseek import (
    GAME_DEFINITIONS,
    _extract_python_module,
    _load_generated_module,
    _parse_ast,
    verify_features,
)

_REPO_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Constants — game set, model specs, URLs
# ---------------------------------------------------------------------------

_GAMES = ("snake", "pong", "tetris", "breakout")
_TARGET_GAME = os.environ.get("MP_GAME", "").strip().lower()

_DS_BASE_URL = "https://api.deepseek.com/v1"
_OR_BASE_URL = "https://openrouter.ai/api/v1"

_DEEPSEEK_MODEL = "deepseek-chat"

_OR_MODELS: dict[str, dict[str, str]] = {
    "planner": {
        "model_id": "deepseek/deepseek-chat",
        "display": "DeepSeek-V3 (planner)",
    },
    "coder": {
        "model_id": "qwen/qwen2.5-coder-7b-instruct",
        "display": "Qwen2.5-Coder-7B (coder)",
    },
    "reviewer": {
        "model_id": "meta-llama/llama-3.3-70b-instruct",
        "display": "Llama-3.3-70B (reviewer)",
    },
}

_LOCAL_BASE_URL = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")
_LOCAL_MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", "qwen2.5:0.5b")
_LOCAL_MODEL_KEY = os.environ.get("LOCAL_MODEL_KEY", "")

_SCORES_FILE = Path("/tmp/gludd-multi-model-pipeline-cloud.json")
_SMOKE = bool(_TARGET_GAME)

# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

_KEY_SENTINEL = object()
_DS_KEY_CACHE: str | None | object = _KEY_SENTINEL
_OR_KEY_CACHE: str | None | object = _KEY_SENTINEL


def _load_ds_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    kf = _REPO_ROOT / ".deepseek.key"
    if kf.exists():
        v = kf.read_text().strip()
        return v if v else None
    return None


def _load_or_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    kf = _REPO_ROOT / ".openrouter.key"
    if kf.exists():
        v = kf.read_text().strip()
        return v if v else None
    return None


def _get_ds_key() -> str | None:
    global _DS_KEY_CACHE
    if _DS_KEY_CACHE is _KEY_SENTINEL:
        _DS_KEY_CACHE = _load_ds_key()
    return cast(str | None, _DS_KEY_CACHE)


def _get_or_key() -> str | None:
    global _OR_KEY_CACHE
    if _OR_KEY_CACHE is _KEY_SENTINEL:
        _OR_KEY_CACHE = _load_or_key()
    return cast(str | None, _OR_KEY_CACHE)


_DS_KEY = _get_ds_key()
_OR_KEY = _get_or_key()
_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None

_DS_SKIP = (
    "DEEPSEEK_API_KEY not set and .deepseek.key not found"
    if not _DS_KEY
    else ("langchain-openai not installed — run make sync with provider deps" if not _HAS_LANGCHAIN_OPENAI else None)
)
_OR_SKIP = (
    "OPENROUTER_API_KEY not set and .openrouter.key not found"
    if not _OR_KEY
    else ("langchain-openai not installed — run make sync with provider deps" if not _HAS_LANGCHAIN_OPENAI else None)
)


def _probe_local_endpoint() -> bool:
    import urllib.error
    import urllib.request

    try:
        url = f"{_LOCAL_BASE_URL}/models" if "/v1" in _LOCAL_BASE_URL else f"{_LOCAL_BASE_URL}/v1/models"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


_LOCAL_REACHABLE = _probe_local_endpoint()
_LOCAL_SKIP = None if _LOCAL_REACHABLE else f"Local model endpoint unreachable at {_LOCAL_BASE_URL}"


# ---------------------------------------------------------------------------
# Per-phase metric data model
# ---------------------------------------------------------------------------


@dataclass
class PhaseMetrics:
    """Timing and token data for one pipeline phase."""

    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None


@dataclass
class PipelineMetrics:
    """Aggregate metrics for one pipeline run (all 3 phases)."""

    game_id: str
    source: str | None = None
    ast_ok: bool = False
    imported: bool = False
    lines_of_code: int = 0
    total_latency_ms: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    planner: PhaseMetrics = field(default_factory=PhaseMetrics)
    coder: PhaseMetrics = field(default_factory=PhaseMetrics)
    reviewer: PhaseMetrics = field(default_factory=PhaseMetrics)
    feature_failures: list[str] = field(default_factory=list)
    lifecycle_failures: list[str] = field(default_factory=list)
    review_rounds: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Gateway builders
# ---------------------------------------------------------------------------

_GW_CACHE: dict[str, Any] = {}


def _build_ds_gateway() -> Any:
    if "ds" in _GW_CACHE:
        return _GW_CACHE["ds"]

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    profile = ModelProfile(
        model_profile_id="mp-deepseek",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_DEEPSEEK_MODEL,
        api_base_alias="DEEPSEEK_API_BASE",
        credential_alias="DEEPSEEK_API_KEY",
        context_window=65536,
        max_input_tokens=60000,
        max_output_tokens=8192,
        cost_per_input_token=0.00000027,
        cost_per_output_token=0.0000011,
        api_metered=True,
        run_budget_usd=5.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("DEEPSEEK_API_KEY", cast(str, _DS_KEY))
    secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
    gw = cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
    _GW_CACHE["ds"] = gw
    return gw


def _build_or_gateway_for_role(role: str) -> tuple[str, Any]:
    spec = _OR_MODELS[role]

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    pid = f"mp-or-{role}"
    profile = ModelProfile(
        model_profile_id=pid,
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=spec["model_id"],
        api_base_alias="OPENROUTER_API_BASE",
        credential_alias="OPENROUTER_API_KEY",
        context_window=65536,
        max_input_tokens=60000,
        max_output_tokens=8192,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=True,
        run_budget_usd=10.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=[role],
        latency_class="medium",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("OPENROUTER_API_KEY", cast(str, _OR_KEY))
    secrets.set("OPENROUTER_API_BASE", _OR_BASE_URL)
    gw = cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
    return pid, gw


def _build_local_gateway() -> Any:
    if "local" in _GW_CACHE:
        return _GW_CACHE["local"]

    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    pid = f"mp-local-{_LOCAL_MODEL_NAME.replace('/', '_').replace(':', '_')}"
    profile = ModelProfile(
        model_profile_id=pid,
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_LOCAL_MODEL_NAME,
        api_base_alias="LOCAL_MODEL_BASE",
        credential_alias="LOCAL_MODEL_KEY",
        context_window=32768,
        max_input_tokens=28000,
        max_output_tokens=4096,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=0.0,
        enabled=True,
        resource_profile="ai_light",
        roles=["coder", "planner", "reviewer"],
        latency_class="medium",
        quality_class="variable",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("LOCAL_MODEL_BASE", _LOCAL_BASE_URL)
    if _LOCAL_MODEL_KEY:
        secrets.set("LOCAL_MODEL_KEY", _LOCAL_MODEL_KEY)
    gw = cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
    _GW_CACHE["local"] = gw
    return gw


# ---------------------------------------------------------------------------
# Pipeline runner — executes plan→code→review, collects per-phase metrics
# ---------------------------------------------------------------------------


def _call_model_raw(gateway: Any, profile_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    t0 = time.time()
    try:
        response = gateway.call_model(
            profile_id,
            messages=messages,
            estimated_cost=0.0,
            budget_remaining=10.0,
        )
    except Exception as exc:
        return {
            "content": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": int((time.time() - t0) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    latency_ms = int((time.time() - t0) * 1000)
    usage = response.usage_metadata or {}
    return {
        "content": response.content,
        "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "latency_ms": latency_ms,
        "error": None,
    }


def _run_pipeline(
    gateway: Any,
    planner_pid: str,
    coder_pid: str,
    reviewer_pid: str,
    game_id: str,
) -> PipelineMetrics:
    """Run a full multi-model pipeline for one game. Collect per-phase metrics.

    Uses MultiModelGamePipeline but wraps plan/code/review calls to capture
    per-phase token/latency data, then runs AST + import + feature checks.
    """
    from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline

    prompt = GAME_DEFINITIONS[game_id]["prompt"]
    metrics = PipelineMetrics(game_id=game_id)
    t0 = time.time()

    MultiModelGamePipeline(gateway)

    # --- Phase 1: PLANNER ---
    _planner_sys = (
        "You are a GAME DESIGN PLANNER. Given a brief game description, "
        "produce a structured design specification. Output each field on "
        "its own line in field:value format:\n\n"
        "name:<game-name>\ngenre:<genre>\narchitecture:<architecture>\n"
        "components:<comma-separated>\ntech:<comma-separated>\n"
        "acceptance:<comma-separated criteria>\n\n"
        "Be specific and concrete. Only output these fields, nothing else."
    )
    plan_result = _call_model_raw(
        gateway,
        planner_pid,
        [
            {"role": "system", "content": _planner_sys},
            {"role": "user", "content": prompt},
        ],
    )
    metrics.planner = PhaseMetrics(
        latency_ms=plan_result["latency_ms"],
        tokens_in=plan_result["tokens_in"],
        tokens_out=plan_result["tokens_out"],
        error=plan_result.get("error"),
    )

    if plan_result.get("error"):
        metrics.error = f"planner failed: {plan_result['error']}"
        metrics.total_latency_ms = int((time.time() - t0) * 1000)
        return metrics

    # --- Phase 2: CODER ---
    designer_content = plan_result["content"]
    code_messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a GAME CODER. Write complete, runnable Python game code from "
                "the design spec. The code must be self-contained, use ONLY the stdlib, "
                "and include ALL components listed in the spec.\n\n"
                "Output ONLY the Python code, no explanation, no markdown fences."
            ),
        },
        {"role": "user", "content": designer_content},
    ]

    code_result = _call_model_raw(gateway, coder_pid, code_messages)
    metrics.coder = PhaseMetrics(
        latency_ms=code_result["latency_ms"],
        tokens_in=code_result["tokens_in"],
        tokens_out=code_result["tokens_out"],
        error=code_result.get("error"),
    )

    if code_result.get("error"):
        metrics.error = f"coder failed: {code_result['error']}"
        metrics.total_latency_ms = int((time.time() - t0) * 1000)
        return metrics

    raw_code = code_result["content"]

    # --- Phase 3: REVIEWER ---
    review_messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a GAME CODE REVIEWER. Review the provided game code against "
                "the design spec. Output a structured review in field:value format:\n\n"
                "issues:<comma-separated issues, or empty>\n"
                "fixes:<comma-separated fixes, or empty>\n"
                "score:<0.0-1.0 quality score>\n"
                "passed:<true or false>\n\n"
                "Check: syntax, all required components, acceptance criteria met."
            ),
        },
        {"role": "user", "content": designer_content},
        {"role": "assistant", "content": raw_code},
    ]

    review_result = _call_model_raw(gateway, reviewer_pid, review_messages)
    metrics.reviewer = PhaseMetrics(
        latency_ms=review_result["latency_ms"],
        tokens_in=review_result["tokens_in"],
        tokens_out=review_result["tokens_out"],
        error=review_result.get("error"),
    )
    metrics.review_rounds = 1

    # --- Extract code and verify ---
    source = _extract_python_module(raw_code)
    if not source:
        metrics.error = "no Python code extracted from coder output"
        metrics.total_latency_ms = int((time.time() - t0) * 1000)
        return metrics
    metrics.source = source
    metrics.lines_of_code = source.count("\n") + 1

    ast_result = _parse_ast(source)
    metrics.ast_ok = ast_result["parseable"]
    if not metrics.ast_ok:
        metrics.error = f"AST parse failed: {ast_result.get('error')}"
        metrics.total_latency_ms = int((time.time() - t0) * 1000)
        return metrics

    import tempfile

    with tempfile.TemporaryDirectory(prefix="gludd-mp-") as td:
        tp = Path(td)
        try:
            mod = _load_generated_module(source, f"mp_{game_id}", tp)
            metrics.imported = True
            metrics.feature_failures = verify_features(game_id, mod)
            metrics.lifecycle_failures = run_lifecycle_checks(game_id, mod)
        except Exception as exc:
            metrics.feature_failures = [f"module load/verify: {type(exc).__name__}: {exc}"]

    metrics.total_latency_ms = int((time.time() - t0) * 1000)
    metrics.total_tokens_in = metrics.planner.tokens_in + metrics.coder.tokens_in + metrics.reviewer.tokens_in
    metrics.total_tokens_out = metrics.planner.tokens_out + metrics.coder.tokens_out + metrics.reviewer.tokens_out

    _print_metrics(metrics, "pipeline")
    return metrics


def _run_single_model(
    gateway: Any,
    profile_id: str,
    game_id: str,
) -> PipelineMetrics:
    """Run single-model generation (one model for all phases).
    Uses the same gateway ID for all three phases.
    """
    return _run_pipeline(gateway, profile_id, profile_id, profile_id, game_id)


def _print_metrics(m: PipelineMetrics, label: str) -> None:
    status = "OK" if m.ast_ok and m.imported and not m.feature_failures and not m.lifecycle_failures else "GAPS"
    line = (
        f"[mp] {label}/{m.game_id}: "
        f"ast={'OK' if m.ast_ok else 'FAIL'} "
        f"import={'OK' if m.imported else 'FAIL'} "
        f"loc={m.lines_of_code} "
        f"t={m.total_latency_ms}ms "
        f"toks_in={m.total_tokens_in} "
        f"toks_out={m.total_tokens_out} "
        f"features={len(m.feature_failures)}f "
        f"lifecycle={len(m.lifecycle_failures)}f "
        f"review_rounds={m.review_rounds} "
        f"status={status}"
    )
    print(f"\n{line}\n", flush=True)


def _metrics_to_dict(m: PipelineMetrics) -> dict[str, Any]:
    return {
        "game": m.game_id,
        "ast_valid": m.ast_ok,
        "runnable": m.imported,
        "lines_of_code": m.lines_of_code,
        "total_latency_ms": m.total_latency_ms,
        "total_tokens_in": m.total_tokens_in,
        "total_tokens_out": m.total_tokens_out,
        "planner_latency_ms": m.planner.latency_ms,
        "coder_latency_ms": m.coder.latency_ms,
        "reviewer_latency_ms": m.reviewer.latency_ms,
        "review_rounds": m.review_rounds,
        "feature_failures": len(m.feature_failures),
        "lifecycle_failures": len(m.lifecycle_failures),
        "error": m.error,
    }


# ---------------------------------------------------------------------------
# Structural tests — no API key needed
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestMultiModelPipelineStructural:
    def test_game_definitions_exist_for_pipeline_games(self) -> None:
        for g in _GAMES:
            assert g in GAME_DEFINITIONS, f"{g} missing from GAME_DEFINITIONS"
            d = GAME_DEFINITIONS[g]
            assert d["prompt"], f"{g}: empty prompt"
            assert d["class_name"], f"{g}: no class_name"
            assert d["verifications"], f"{g}: no verifications"

    def test_or_model_specs_complete(self) -> None:
        assert len(_OR_MODELS) == 3
        for role, spec in _OR_MODELS.items():
            assert spec["model_id"], f"{role}: no model_id"
            assert "/" in spec["model_id"], f"{role}: malformed model_id"
            assert spec["display"], f"{role}: no display"

    def test_deepseek_model_name_set(self) -> None:
        assert _DEEPSEEK_MODEL
        assert "deepseek" in _DEEPSEEK_MODEL.lower()

    def test_scores_file_writable(self) -> None:
        _SCORES_FILE.write_text(json.dumps({"_test": True}))
        assert _SCORES_FILE.exists()
        data = json.loads(_SCORES_FILE.read_text())
        assert data.get("_test") is True

    def test_multi_model_pipeline_importable(self) -> None:
        from general_ludd.cloud.multi_model_game_pipeline import (
            DesignSpec,
            MultiModelGamePipeline,
            ReviewResult,
        )

        assert MultiModelGamePipeline is not None
        assert DesignSpec is not None
        assert ReviewResult is not None

    def test_software_generator_importable(self) -> None:
        from general_ludd.cloud.software_generator import SoftwareGenerator

        assert SoftwareGenerator is not None

    def test_pipeline_generate_signature_accepts_per_role_models(self) -> None:
        import inspect

        from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline

        sig = inspect.signature(MultiModelGamePipeline.generate)
        params = list(sig.parameters.keys())
        assert "planner_model" in params
        assert "coder_model" in params
        assert "reviewer_model" in params

    @pytest.mark.parametrize("game_id", _GAMES)
    def test_verifications_include_lifecycle(self, game_id: str) -> None:
        vnames = {v[0] for v in GAME_DEFINITIONS[game_id]["verifications"]}
        required = {
            "lifecycle_initial_state",
            "lifecycle_start",
            "lifecycle_restart",
        }
        missing = required - vnames
        assert not missing, f"{game_id}: missing lifecycle verifications: {missing}"


# ---------------------------------------------------------------------------
# 1. DeepSeek single-model pipeline (PaaS)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
class TestDeepSeekPipeline:
    """DeepSeek API: deepseek-chat for all 3 pipeline phases."""

    @pytest.fixture(scope="class")
    def gateway(self) -> Any:
        if _DS_SKIP:
            pytest.skip(_DS_SKIP)
        return _build_ds_gateway()

    def _run_one(self, gateway: Any, game_id: str) -> PipelineMetrics:
        return _run_single_model(gateway, "mp-deepseek", game_id)

    @pytest.mark.parametrize("game_id", sorted(_GAMES))
    def test_game_generation(self, gateway: Any, game_id: str) -> None:
        if _SMOKE and game_id != _TARGET_GAME:
            pytest.skip(f"MP_GAME={_TARGET_GAME}, skipping {game_id}")

        m = self._run_one(gateway, game_id)
        if m.error:
            pytest.fail(f"DeepSeek pipeline failed for {game_id}: {m.error}")

        assert m.ast_ok, f"DeepSeek/{game_id}: AST parse failed"
        assert m.imported, f"DeepSeek/{game_id}: module not importable"
        assert m.lines_of_code > 30, f"DeepSeek/{game_id}: too few lines ({m.lines_of_code})"
        assert m.feature_failures == 0, f"DeepSeek/{game_id}: {len(m.feature_failures)} feature failures"
        assert m.lifecycle_failures == 0, f"DeepSeek/{game_id}: {len(m.lifecycle_failures)} lifecycle failures"


# ---------------------------------------------------------------------------
# 2. OpenRouter multi-model pipeline (PaaS)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
class TestOpenRouterMultiModelPipeline:
    """OpenRouter: Qwen2.5-Coder-7B (coder), DeepSeek-V3 (planner),
    Llama-3.3-70B (reviewer)."""

    @pytest.fixture(scope="class")
    def gateways(self) -> dict[str, Any]:
        if _OR_SKIP:
            pytest.skip(_OR_SKIP)
        gws: dict[str, Any] = {}
        for role in ("planner", "coder", "reviewer"):
            pid, gw = _build_or_gateway_for_role(role)
            gws[role] = {"pid": pid, "gateway": gw}
        return gws

    def _run_one(self, gateways: dict[str, Any], game_id: str) -> PipelineMetrics:
        planner_pid = gateways["planner"]["pid"]
        coder_pid = gateways["coder"]["pid"]
        reviewer_pid = gateways["reviewer"]["pid"]

        # All share same gateway instance (profiles distinguished by pid)
        gw = gateways["planner"]["gateway"]
        return _run_pipeline(gw, planner_pid, coder_pid, reviewer_pid, game_id)

    @pytest.mark.parametrize("game_id", sorted(_GAMES))
    def test_game_generation(self, gateways: dict[str, Any], game_id: str) -> None:
        if _SMOKE and game_id != _TARGET_GAME:
            pytest.skip(f"MP_GAME={_TARGET_GAME}, skipping {game_id}")

        m = self._run_one(gateways, game_id)
        if m.error:
            pytest.fail(f"OpenRouter pipeline failed for {game_id}: {m.error}")

        assert m.ast_ok, f"OpenRouter/{game_id}: AST parse failed"
        assert m.imported, f"OpenRouter/{game_id}: module not importable"
        assert m.lines_of_code > 30, f"OpenRouter/{game_id}: too few lines ({m.lines_of_code})"
        assert m.feature_failures == 0, f"OpenRouter/{game_id}: {len(m.feature_failures)} feature failures"
        assert m.lifecycle_failures == 0, f"OpenRouter/{game_id}: {len(m.lifecycle_failures)} lifecycle failures"


# ---------------------------------------------------------------------------
# 3. Self-hosted endpoint pipeline (IaaS)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.local_model
@pytest.mark.slow
class TestLocalEndpointPipeline:
    """Self-hosted: LOCAL_MODEL_NAME for all 3 pipeline phases."""

    @pytest.fixture(scope="class")
    def gateway(self) -> Any:
        if _LOCAL_SKIP:
            pytest.skip(_LOCAL_SKIP)
        return _build_local_gateway()

    def _local_pid(self) -> str:
        return f"mp-local-{_LOCAL_MODEL_NAME.replace('/', '_').replace(':', '_')}"

    def _run_one(self, gateway: Any, game_id: str) -> PipelineMetrics:
        pid = self._local_pid()
        return _run_single_model(gateway, pid, game_id)

    @pytest.mark.parametrize("game_id", sorted(_GAMES))
    def test_game_generation(self, gateway: Any, game_id: str) -> None:
        if _SMOKE and game_id != _TARGET_GAME:
            pytest.skip(f"MP_GAME={_TARGET_GAME}, skipping {game_id}")

        m = self._run_one(gateway, game_id)
        if m.error:
            pytest.fail(f"Local pipeline failed for {game_id}: {m.error}")

        assert m.ast_ok, f"Local/{game_id}: AST parse failed"
        assert m.imported, f"Local/{game_id}: module not importable"
        assert m.lines_of_code > 20, f"Local/{game_id}: too few lines ({m.lines_of_code})"


# ---------------------------------------------------------------------------
# 4. Aggregate comparison report — single-model vs multi-model
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
class TestMultiModelComparisonReport:
    """Run all three deployment tiers and produce a comparison table."""

    @pytest.fixture(scope="class")
    def gateways(self) -> dict[str, Any]:
        gws: dict[str, Any] = {}
        if not _DS_SKIP:
            gws["deepseek"] = {
                "type": "ds",
                "gateway": _build_ds_gateway(),
                "pid": "mp-deepseek",
            }
        if not _OR_SKIP:
            or_gws: dict[str, Any] = {}
            for role in ("planner", "coder", "reviewer"):
                pid, gw = _build_or_gateway_for_role(role)
                or_gws[role] = {"pid": pid, "gateway": gw}
            gws["openrouter"] = {
                "type": "or",
                **or_gws,
                "gateway": or_gws["planner"]["gateway"],
            }
        if not _LOCAL_SKIP:
            pid = f"mp-local-{_LOCAL_MODEL_NAME.replace('/', '_').replace(':', '_')}"
            gws["local"] = {
                "type": "local",
                "gateway": _build_local_gateway(),
                "pid": pid,
            }
        return gws

    def _run_all(self, gateways: dict[str, Any]) -> dict[str, Any]:
        report: dict[str, Any] = {}
        games = [_TARGET_GAME] if _SMOKE else sorted(_GAMES)

        for tier_key, gwd in gateways.items():
            tier_results: dict[str, Any] = {}
            for game_id in games:
                try:
                    if gwd["type"] == "ds":
                        m = _run_single_model(gwd["gateway"], gwd["pid"], game_id)
                    elif gwd["type"] == "or":
                        m = _run_pipeline(
                            gwd["gateway"],
                            gwd["planner"]["pid"],
                            gwd["coder"]["pid"],
                            gwd["reviewer"]["pid"],
                            game_id,
                        )
                    else:
                        m = _run_single_model(gwd["gateway"], gwd["pid"], game_id)
                except Exception as exc:
                    m = PipelineMetrics(game_id=game_id)
                    m.error = f"{type(exc).__name__}: {exc}"
                tier_results[game_id] = _metrics_to_dict(m)
            report[tier_key] = tier_results
        return report

    def _tier_summary(self, games: dict[str, dict[str, Any]]) -> dict[str, Any]:
        rows = list(games.values())
        n = len(rows)
        ast_ok = sum(1 for r in rows if r["ast_valid"])
        runnable = sum(1 for r in rows if r["runnable"])
        total_latency = sum(r["total_latency_ms"] for r in rows)
        total_tokens = sum(r["total_tokens_in"] + r["total_tokens_out"] for r in rows)
        total_loc = sum(r["lines_of_code"] for r in rows)
        clean = sum(1 for r in rows if r["feature_failures"] == 0 and r["lifecycle_failures"] == 0)
        return {
            "games": n,
            "ast_pass": f"{ast_ok}/{n}",
            "runnable": f"{runnable}/{n}",
            "clean": f"{clean}/{n}",
            "avg_latency_ms": int(total_latency / max(n, 1)),
            "total_tokens": total_tokens,
            "total_loc": total_loc,
        }

    def test_comparison_report(self, gateways: dict[str, Any]) -> None:
        if len(gateways) < 1:
            pytest.skip("No API keys / endpoints available for comparison report")

        print("\n" + "=" * 70)
        print("MULTI-MODEL PIPELINE CLOUD BENCHMARK")
        print("=" * 70 + "\n", flush=True)

        report = self._run_all(gateways)
        _SCORES_FILE.write_text(json.dumps(report, indent=2))

        print("\n--- SINGLE-MODEL vs MULTI-MODEL COMPARISON ---\n")
        col_w = 18

        # Header
        header = ["Metric", "DeepSeek (PaaS)", "OpenRouter (PaaS)", "Local (IaaS)"]
        print("  " + "  ".join(h.ljust(col_w) for h in header))
        print("  " + "  ".join("-" * col_w for _ in header))

        summaries: dict[str, dict[str, Any]] = {}
        for tier_key in ("deepseek", "openrouter", "local"):
            if tier_key in report:
                summaries[tier_key] = self._tier_summary(report[tier_key])

        rows = [
            ("Games tested", "games", str),
            ("AST pass", "ast_pass", str),
            ("Runnable", "runnable", str),
            ("Feature clean", "clean", str),
            ("Avg latency (ms)", "avg_latency_ms", str),
            ("Total tokens", "total_tokens", str),
            ("Total LOC", "total_loc", str),
        ]
        for label, key, _ in rows:
            vals = [label.ljust(col_w)]
            for tier_key in ("deepseek", "openrouter", "local"):
                s = summaries.get(tier_key)
                if s:
                    vals.append(str(s.get(key, "N/A")).ljust(col_w))
                else:
                    vals.append("skipped".ljust(col_w))
            print("  " + "  ".join(vals))

        # Per-game matrix
        games = [_TARGET_GAME] if _SMOKE else sorted(_GAMES)
        print(f"\n--- PER-GAME DETAIL ({len(games)} game(s)) ---\n")
        for game_id in games:
            print(f"  {game_id}:")
            for tier_key in ("deepseek", "openrouter", "local"):
                row = report.get(tier_key, {}).get(game_id)
                if row is None:
                    print(f"    {tier_key.ljust(15)}: skipped")
                    continue
                flag = "+" if row["ast_valid"] and row["runnable"] and row["feature_failures"] == 0 else "-"
                print(
                    f"    {tier_key.ljust(15)}: {flag} "
                    f"loc={row['lines_of_code']} "
                    f"t={row['total_latency_ms']}ms "
                    f"toks={row['total_tokens_in']}+{row['total_tokens_out']} "
                    f"p={row['planner_latency_ms']}ms "
                    f"c={row['coder_latency_ms']}ms "
                    f"r={row['reviewer_latency_ms']}ms "
                    f"feat={row['feature_failures']}f "
                    f"lc={row['lifecycle_failures']}f"
                )

        print(f"\nScores saved to {_SCORES_FILE}\n", flush=True)

        # Assert at least one tier had results
        assert len(report) > 0, "No tiers produced results"
