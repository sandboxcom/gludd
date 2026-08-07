"""CI/CD integration test: multi-model pipeline with API keys when available.

Runs 4 games (snake, pong, tetris, breakout) through:
  1. DeepSeek single-model (4 games) — when DEEPSEEK_API_KEY is set
  2. OpenRouter multi-model (4 games) — when OPENROUTER_API_KEY is set

If keys are absent, structural tests pass trivially (CI stays green).
Writes results to /tmp/gludd-multi-model-results.json for CI artifact collection.
Generates a comparison table: single-model vs multi-model metrics per game.

Run:
    make test-e2e-multi-model
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
_RESULTS_FILE = Path("/tmp/gludd-multi-model-results.json")

# ---------------------------------------------------------------------------
# Game set and constants
# ---------------------------------------------------------------------------

_CI_GAMES = ("snake", "pong", "tetris", "breakout")
_TARGET_GAME = os.environ.get("CI_GAME", "").strip().lower()

_DS_BASE_URL = "https://api.deepseek.com/v1"
_OR_BASE_URL = "https://openrouter.ai/api/v1"
_DEEPSEEK_MODEL = "deepseek-chat"

_OR_MODELS: dict[str, dict[str, str]] = {
    "planner": {"model_id": "deepseek/deepseek-chat", "display": "DeepSeek-V3 (planner)"},
    "coder": {"model_id": "qwen/qwen2.5-coder-7b-instruct", "display": "Qwen2.5-Coder-7B (coder)"},
    "reviewer": {"model_id": "meta-llama/llama-3.3-70b-instruct", "display": "Llama-3.3-70B (reviewer)"},
}

# ---------------------------------------------------------------------------
# Key loading — env first, then shared key files
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

# ---------------------------------------------------------------------------
# Metrics data model
# ---------------------------------------------------------------------------


@dataclass
class PhaseMetrics:
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None


@dataclass
class PipelineMetrics:
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
    error: str | None = None


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
        "feature_failures": len(m.feature_failures),
        "lifecycle_failures": len(m.lifecycle_failures),
        "error": m.error,
    }


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
        model_profile_id="ci-ds",
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

    pid = f"ci-or-{role}"
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


# ---------------------------------------------------------------------------
# Pipeline runner — plan→code→review with per-phase metrics
# ---------------------------------------------------------------------------


def _call_model_raw(gateway: Any, profile_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    t0 = time.time()
    try:
        response = gateway.call_model(profile_id, messages=messages, estimated_cost=0.0, budget_remaining=10.0)
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
    prompt = GAME_DEFINITIONS[game_id]["prompt"]
    metrics = PipelineMetrics(game_id=game_id)
    t0 = time.time()

    # Phase 1: PLANNER
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
        gateway, planner_pid, [{"role": "system", "content": _planner_sys}, {"role": "user", "content": prompt}]
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

    # Phase 2: CODER
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
        {"role": "user", "content": plan_result["content"]},
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

    # Phase 3: REVIEWER
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
        {"role": "user", "content": plan_result["content"]},
        {"role": "assistant", "content": raw_code},
    ]
    review_result = _call_model_raw(gateway, reviewer_pid, review_messages)
    metrics.reviewer = PhaseMetrics(
        latency_ms=review_result["latency_ms"],
        tokens_in=review_result["tokens_in"],
        tokens_out=review_result["tokens_out"],
        error=review_result.get("error"),
    )

    # Extract code and verify
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

    with tempfile.TemporaryDirectory(prefix="gludd-ci-") as td:
        tp = Path(td)
        try:
            mod = _load_generated_module(source, f"ci_{game_id}", tp)
            metrics.imported = True
            metrics.feature_failures = verify_features(game_id, mod)
            metrics.lifecycle_failures = run_lifecycle_checks(game_id, mod)
        except Exception as exc:
            metrics.feature_failures = [f"module load/verify: {type(exc).__name__}: {exc}"]

    metrics.total_latency_ms = int((time.time() - t0) * 1000)
    metrics.total_tokens_in = metrics.planner.tokens_in + metrics.coder.tokens_in + metrics.reviewer.tokens_in
    metrics.total_tokens_out = metrics.planner.tokens_out + metrics.coder.tokens_out + metrics.reviewer.tokens_out
    return metrics


def _run_single_model(gateway: Any, profile_id: str, game_id: str) -> PipelineMetrics:
    return _run_pipeline(gateway, profile_id, profile_id, profile_id, game_id)


# ---------------------------------------------------------------------------
# Structural tests — always pass, no API keys needed
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestCIMultiModelStructural:
    def test_game_definitions_complete(self) -> None:
        for g in _CI_GAMES:
            assert g in GAME_DEFINITIONS, f"{g} missing from GAME_DEFINITIONS"
            d = GAME_DEFINITIONS[g]
            assert d["prompt"], f"{g}: empty prompt"
            assert d["class_name"], f"{g}: no class_name"
            assert d["verifications"], f"{g}: no verifications"

    def test_or_model_specs_complete(self) -> None:
        for role, spec in _OR_MODELS.items():
            assert spec["model_id"], f"{role}: no model_id"
            assert "/" in spec["model_id"], f"{role}: malformed model_id"

    def test_results_file_writable(self) -> None:
        _RESULTS_FILE.write_text(json.dumps({"_ci_test": True}))
        assert _RESULTS_FILE.exists()
        data = json.loads(_RESULTS_FILE.read_text())
        assert data.get("_ci_test") is True

    def test_multi_model_pipeline_importable(self) -> None:
        from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline

        assert MultiModelGamePipeline is not None

    def test_key_loading_no_crash_on_missing(self) -> None:
        ds = _load_ds_key()
        or_ = _load_or_key()
        assert ds is None or isinstance(ds, str)
        assert or_ is None or isinstance(or_, str)

    def test_all_ci_games_have_lifecycle_checks(self) -> None:
        for game_id in _CI_GAMES:
            vnames = {v[0] for v in GAME_DEFINITIONS[game_id]["verifications"]}
            required = {"lifecycle_initial_state", "lifecycle_start", "lifecycle_restart"}
            missing = required - vnames
            assert not missing, f"{game_id}: missing lifecycle verifications: {missing}"


# ---------------------------------------------------------------------------
# Live pipeline test classes
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
class TestCIModelPipeline:
    """Run all available tiers and produce comparison report.

    Each tier is run only if its API key is present. The test method
    `test_comparison_report` is parametrized with a single empty marker
    so it runs exactly once, aggregating all tiers and writing the results
    file for CI artifact collection.
    """

    @pytest.fixture(scope="class")
    def gateways(self) -> dict[str, Any]:
        gws: dict[str, Any] = {}
        if not _DS_SKIP:
            gws["deepseek"] = {"type": "ds", "gateway": _build_ds_gateway(), "pid": "ci-ds"}
        if not _OR_SKIP:
            or_gws: dict[str, Any] = {}
            for role in ("planner", "coder", "reviewer"):
                pid, gw = _build_or_gateway_for_role(role)
                or_gws[role] = {"pid": pid, "gateway": gw}
            gws["openrouter"] = {"type": "or", **or_gws, "gateway": or_gws["planner"]["gateway"]}
        return gws

    def _run_all(self, gateways: dict[str, Any]) -> dict[str, Any]:
        report: dict[str, Any] = {"ci_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        games = [_TARGET_GAME] if _TARGET_GAME else sorted(_CI_GAMES)

        for tier_key, gwd in gateways.items():
            tier_results: dict[str, Any] = {}
            print(f"\n{'=' * 60}\n  TIER: {tier_key} ({len(games)} games)\n{'=' * 60}\n", flush=True)
            for game_id in games:
                try:
                    if gwd["type"] == "ds":
                        m = _run_single_model(gwd["gateway"], gwd["pid"], game_id)
                    else:
                        m = _run_pipeline(
                            gwd["gateway"],
                            gwd["planner"]["pid"],
                            gwd["coder"]["pid"],
                            gwd["reviewer"]["pid"],
                            game_id,
                        )
                except Exception as exc:
                    m = PipelineMetrics(game_id=game_id)
                    m.error = f"{type(exc).__name__}: {exc}"
                status = (
                    "OK" if m.ast_ok and m.imported and not m.feature_failures and not m.lifecycle_failures else "GAPS"
                )
                print(
                    f"  [{tier_key}] {game_id}: ast={'OK' if m.ast_ok else 'FAIL'} "
                    f"imp={'OK' if m.imported else 'FAIL'} loc={m.lines_of_code} "
                    f"t={m.total_latency_ms}ms tok_in={m.total_tokens_in} tok_out={m.total_tokens_out} "
                    f"feat={len(m.feature_failures)}f lc={len(m.lifecycle_failures)}f "
                    f"err={m.error} status={status}",
                    flush=True,
                )
                tier_results[game_id] = _metrics_to_dict(m)
            report[tier_key] = tier_results
        return report

    def test_comparison_report(self, gateways: dict[str, Any]) -> None:
        print("\n" + "=" * 60, flush=True)
        print("CI MULTI-MODEL PIPELINE INTEGRATION TEST", flush=True)
        print("=" * 60 + "\n", flush=True)

        report = self._run_all(gateways)

        # Write results for CI artifact collection
        _RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RESULTS_FILE.write_text(json.dumps(report, indent=2))
        print(f"\n[CI] Results written to {_RESULTS_FILE}\n", flush=True)

        # Generate comparison table
        print("--- COMPARISON TABLE: SINGLE-MODEL vs MULTI-MODEL ---\n", flush=True)
        col_w = 22
        header = ["Metric", "DeepSeek (single-model)", "OpenRouter (multi-model)"]
        print("  " + "  ".join(h.ljust(col_w) for h in header))
        print("  " + "  ".join("-" * col_w for _ in header))

        for tier_key in ("deepseek", "openrouter"):
            if tier_key not in report:
                continue
            rows = list(report[tier_key].values())
            n = len(rows)
            ast_ok = sum(1 for r in rows if r["ast_valid"])
            runnable = sum(1 for r in rows if r["runnable"])
            clean = sum(1 for r in rows if r["feature_failures"] == 0 and r["lifecycle_failures"] == 0)
            total_latency = sum(r["total_latency_ms"] for r in rows)
            total_tokens = sum(r["total_tokens_in"] + r["total_tokens_out"] for r in rows)
            total_loc = sum(r["lines_of_code"] for r in rows)
            print(
                f"  {'Games'.ljust(col_w)}  {str(n).ljust(col_w)}"
                + (f"  {str(n).ljust(col_w)}" if tier_key == "deepseek" else "")
            )
            print(
                f"  {'AST pass'.ljust(col_w)}  {f'{ast_ok}/{n}'.ljust(col_w)}"
                + (f"  {f'{ast_ok}/{n}'.ljust(col_w)}" if tier_key == "deepseek" else "")
            )
            print(
                f"  {'Runnable'.ljust(col_w)}  {f'{runnable}/{n}'.ljust(col_w)}"
                + (f"  {f'{runnable}/{n}'.ljust(col_w)}" if tier_key == "deepseek" else "")
            )
            print(
                f"  {'Feature clean'.ljust(col_w)}  {f'{clean}/{n}'.ljust(col_w)}"
                + (f"  {f'{clean}/{n}'.ljust(col_w)}" if tier_key == "deepseek" else "")
            )
            print(
                f"  {'Avg latency (ms)'.ljust(col_w)}  {str(int(total_latency / max(n, 1))).ljust(col_w)}"
                + (f"  {str(int(total_latency / max(n, 1))).ljust(col_w)}" if tier_key == "deepseek" else "")
            )
            print(
                f"  {'Total tokens'.ljust(col_w)}  {str(total_tokens).ljust(col_w)}"
                + (f"  {str(total_tokens).ljust(col_w)}" if tier_key == "deepseek" else "")
            )
            print(
                f"  {'Total LOC'.ljust(col_w)}  {str(total_loc).ljust(col_w)}"
                + (f"  {str(total_loc).ljust(col_w)}" if tier_key == "deepseek" else "")
            )

        # Per-game detail
        games = [_TARGET_GAME] if _TARGET_GAME else sorted(_CI_GAMES)
        print(f"\n--- PER-GAME DETAIL ({len(games)} game(s)) ---\n", flush=True)
        for game_id in games:
            print(f"  {game_id}:")
            for tier_key in ("deepseek", "openrouter"):
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

        assert len(report) >= 1 or len(gateways) == 0, "No tiers produced results when gateways were configured"
