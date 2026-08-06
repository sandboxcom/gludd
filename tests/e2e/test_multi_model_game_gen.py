"""Multi-model game generation E2E benchmark: Qwen, DeepSeek, Llama, Phi.

Tests each model on 4 games (snake, pong, tetris, breakout) via OpenRouter
for uniform API access. Scores: AST-valid, runnable (importable), lines of
code, response time. Produces a per-model comparison matrix.

Skip conditions (any of these skip the live tests):
    - OPENROUTER_API_KEY not set in env and .openrouter.key not found
    - langchain-openai not installed

Run:
    OPENROUTER_API_KEY="sk-or-v1-..." \\
        uv run pytest tests/e2e/test_multi_model_game_gen.py -v -s
or smoke (one model, one game):
    MM_MODEL=deepseek MM_GAME=snake \\
        uv run pytest tests/e2e/test_multi_model_game_gen.py -v -s
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
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

_OR_BASE_URL = "https://openrouter.ai/api/v1"

_GAMES = ("snake", "pong", "tetris", "breakout")

_MODELS: dict[str, dict[str, str]] = {
    "qwen": {
        "model_id": "qwen/qwen2.5-coder-7b-instruct",
        "display": "Qwen2.5-Coder-7B",
    },
    "deepseek": {
        "model_id": "deepseek/deepseek-chat",
        "display": "DeepSeek-V3",
    },
    "llama": {
        "model_id": "meta-llama/llama-3.3-70b-instruct",
        "display": "Llama-3.3-70B",
    },
    "phi": {
        "model_id": "microsoft/phi-4",
        "display": "Phi-4",
    },
}

_TARGET_MODEL = os.environ.get("MM_MODEL", "").strip().lower()
_TARGET_GAME = os.environ.get("MM_GAME", "").strip().lower()


def _load_orb_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    kf = _REPO_ROOT / ".openrouter.key"
    if kf.exists():
        v = kf.read_text().strip()
        return v if v else None
    return None


_OR_KEY = _load_orb_key()
_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None

_SKIP_REASON: str | None = None
if not _OR_KEY:
    _SKIP_REASON = (
        "OPENROUTER_API_KEY not set and .openrouter.key not found — "
        "set OPENROUTER_API_KEY or place key in .openrouter.key"
    )
elif not _HAS_LANGCHAIN_OPENAI:
    _SKIP_REASON = "langchain-openai not installed — run make sync with provider deps"


_SCORES_FILE = Path("/tmp/gludd-multi-model-game-gen.json")


def _build_gateway(profile_id: str, model_id: str) -> Any:
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    profile = ModelProfile(
        model_profile_id=profile_id,
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=model_id,
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
        roles=["coder"],
        latency_class="medium",
        quality_class="high",
    )
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
    secrets = EnvSecretsManager()
    secrets.set("OPENROUTER_API_KEY", cast(str, _OR_KEY))
    secrets.set("OPENROUTER_API_BASE", _OR_BASE_URL)
    return cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)


def _call_model(gateway: Any, profile_id: str, prompt: str) -> dict[str, Any]:
    t0 = time.time()
    response = gateway.call_model(
        profile_id,
        messages=[{"role": "user", "content": prompt}],
        estimated_cost=0.0,
        budget_remaining=10.0,
    )
    latency_ms = int((time.time() - t0) * 1000)
    usage = response.usage_metadata or {}
    return {
        "content": response.content,
        "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "content_len": len(response.content),
        "latency_ms": latency_ms,
    }


def _save_scores(scores: dict[str, Any]) -> None:
    _SCORES_FILE.write_text(json.dumps(scores, indent=2))


def _load_scores() -> dict[str, Any]:
    if _SCORES_FILE.exists():
        return json.loads(_SCORES_FILE.read_text())
    return {}


def _game_score_row(
    game_id: str,
    source: str | None,
    ast_ok: bool,
    imported: bool,
    feature_fails: list[str],
    lifecycle_fails: list[str],
    elapsed_ms: int,
    tokens_in: int,
    tokens_out: int,
) -> dict[str, Any]:
    lines = source.count("\n") + 1 if source else 0
    return {
        "game": game_id,
        "ast_valid": ast_ok,
        "runnable": imported,
        "lines_of_code": lines,
        "response_time_ms": elapsed_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "feature_failures": len(feature_fails),
        "lifecycle_failures": len(lifecycle_fails),
        "feature_detail": feature_fails[:5],
        "lifecycle_detail": lifecycle_fails[:5],
    }


def _model_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    ast_ok = sum(1 for r in rows if r["ast_valid"])
    runnable = sum(1 for r in rows if r["runnable"])
    total_loc = sum(r["lines_of_code"] for r in rows)
    avg_latency = sum(r["response_time_ms"] for r in rows) / max(n, 1)
    return {
        "games_tested": n,
        "ast_pass_rate": f"{ast_ok}/{n}",
        "runnable_rate": f"{runnable}/{n}",
        "total_loc": total_loc,
        "avg_response_ms": int(avg_latency),
        "per_game": rows,
    }


# ---------------------------------------------------------------------------
# Structural tests (no API key needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestMultiModelGameGenStructural:
    def test_game_definitions_exist_for_target_games(self) -> None:
        for g in _GAMES:
            assert g in GAME_DEFINITIONS, f"{g} missing from GAME_DEFINITIONS"
            d = GAME_DEFINITIONS[g]
            assert d["prompt"], f"{g}: empty prompt"
            assert d["class_name"], f"{g}: no class_name"
            assert d["verifications"], f"{g}: no verifications"

    def test_model_definitions_complete(self) -> None:
        assert len(_MODELS) == 4
        for mk, md in _MODELS.items():
            assert md["model_id"], f"{mk}: no model_id"
            assert md["display"], f"{mk}: no display"

    def test_game_prompts_are_non_trivial(self) -> None:
        for g in _GAMES:
            prompt = GAME_DEFINITIONS[g]["prompt"]
            assert len(prompt) > 300, f"{g}: prompt too short ({len(prompt)} chars)"
            assert "class " in prompt.lower(), f"{g}: prompt missing class directive"

    def test_model_ids_are_valid_openrouter_paths(self) -> None:
        for mk, md in _MODELS.items():
            parts = md["model_id"].split("/")
            assert len(parts) == 2, f"{mk}: model_id must be 'provider/model' format, got {md['model_id']!r}"

    @pytest.mark.parametrize("model_key", list(_MODELS.keys()))
    def test_profile_id_generation(self, model_key: str) -> None:
        pid = f"mm-{model_key}"
        assert pid.startswith("mm-")
        assert len(pid) > 3

    @pytest.mark.parametrize("game_id", _GAMES)
    def test_verifications_include_lifecycle(self, game_id: str) -> None:
        vnames = {v[0] for v in GAME_DEFINITIONS[game_id]["verifications"]}
        required = {"lifecycle_initial_state", "lifecycle_start", "lifecycle_restart"}
        missing = required - vnames
        assert not missing, f"{game_id}: missing lifecycle verifications: {missing}"

    def test_scores_file_writable(self) -> None:
        _save_scores({"_test": True})
        assert _SCORES_FILE.exists()
        data = _load_scores()
        assert data.get("_test") is True


# ---------------------------------------------------------------------------
# Live model tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
class TestMultiModelGameGeneration:
    """Generate all 4 games against each of 4 models. Score and compare."""

    @pytest.fixture(scope="class")
    def gateways(self) -> dict[str, Any]:
        gw: dict[str, Any] = {}
        for mk, md in _MODELS.items():
            pid = f"mm-{mk}"
            try:
                gw[mk] = _build_gateway(pid, md["model_id"])
            except Exception as exc:
                gw[mk] = f"gateway build failed: {exc}"
        return gw

    def _run_model_game(
        self,
        gateway: Any,
        profile_id: str,
        game_id: str,
    ) -> dict[str, Any]:
        prompt = GAME_DEFINITIONS[game_id]["prompt"]
        t0 = time.time()

        try:
            raw = _call_model(gateway, profile_id, prompt)
        except Exception as exc:
            return _game_score_row(
                game_id,
                None,
                False,
                False,
                [],
                [],
                int((time.time() - t0) * 1000),
                0,
                0,
            ) | {"error": f"model call failed: {type(exc).__name__}: {exc}"}

        source = _extract_python_module(raw["content"])
        ast_ok = False
        if source:
            ast_result = _parse_ast(source)
            ast_ok = ast_result["parseable"]

        imported = False
        feature_fails: list[str] = []
        lifecycle_fails: list[str] = []

        if source and ast_ok:
            with __import__("tempfile").TemporaryDirectory(prefix="gludd-mm-") as td:
                tp = Path(td)
                try:
                    mod = _load_generated_module(source, f"mm_{game_id}", tp)
                    imported = True
                    feature_fails = verify_features(game_id, mod)
                    lifecycle_fails = run_lifecycle_checks(game_id, mod)
                except Exception as exc:
                    feature_fails = [f"load/verify: {type(exc).__name__}: {exc}"]

        elapsed = int((time.time() - t0) * 1000)
        row = _game_score_row(
            game_id,
            source,
            ast_ok,
            imported,
            feature_fails,
            lifecycle_fails,
            elapsed,
            raw["tokens_in"],
            raw["tokens_out"],
        )
        line_str = (
            f"[mm] {profile_id}/{game_id}: "
            f"ast={'OK' if ast_ok else 'FAIL'} "
            f"import={'OK' if imported else 'FAIL'} "
            f"loc={row['lines_of_code']} "
            f"t={elapsed}ms "
            f"features={len(feature_fails)}f "
            f"lifecycle={len(lifecycle_fails)}f"
        )
        print(f"\n{line_str}\n", flush=True)
        return row

    @pytest.mark.parametrize("model_key", sorted(_MODELS.keys()))
    @pytest.mark.parametrize("game_id", sorted(_GAMES))
    def test_model_game_generation(self, gateways: dict[str, Any], model_key: str, game_id: str) -> None:
        if _SKIP_REASON:
            pytest.skip(_SKIP_REASON)
        if _TARGET_MODEL and model_key != _TARGET_MODEL:
            pytest.skip(f"MM_MODEL={_TARGET_MODEL}, skipping {model_key}")
        if _TARGET_GAME and game_id != _TARGET_GAME:
            pytest.skip(f"MM_GAME={_TARGET_GAME}, skipping {game_id}")

        gateway = gateways[model_key]
        if isinstance(gateway, str):
            pytest.skip(f"Gateway build failed for {model_key}: {gateway}")

        profile_id = f"mm-{model_key}"
        row = self._run_model_game(gateway, profile_id, game_id)

        assert row["ast_valid"], f"{model_key}/{game_id}: AST parse failed"
        assert row["runnable"], f"{model_key}/{game_id}: module not importable"
        assert row["lines_of_code"] > 30, f"{model_key}/{game_id}: too few lines ({row['lines_of_code']})"

        assert row["feature_failures"] == 0, (
            f"{model_key}/{game_id}: {row['feature_failures']} feature failures: {row['feature_detail']}"
        )
        assert row["lifecycle_failures"] == 0, (
            f"{model_key}/{game_id}: {row['lifecycle_failures']} lifecycle failures: {row['lifecycle_detail']}"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestMultiModelScoreReport:
    """Aggregate scores across all models and games into a comparison report."""

    @pytest.fixture(scope="class")
    def gateways(self) -> dict[str, Any]:
        gw: dict[str, Any] = {}
        for mk, md in _MODELS.items():
            pid = f"mm-{mk}"
            try:
                gw[mk] = _build_gateway(pid, md["model_id"])
            except Exception as exc:
                gw[mk] = f"gateway build failed: {exc}"
        return gw

    def _score_all(self, gateways: dict[str, Any]) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for mk in sorted(_MODELS.keys()):
            if _TARGET_MODEL and mk != _TARGET_MODEL:
                continue
            gw = gateways[mk]
            if isinstance(gw, str):
                results[mk] = {"error": gw}
                continue
            pid = f"mm-{mk}"
            rows: list[dict[str, Any]] = []
            for g in sorted(_GAMES):
                if _TARGET_GAME and g != _TARGET_GAME:
                    continue
                try:
                    row = TestMultiModelGameGeneration()._run_model_game(gw, pid, g)
                except Exception as exc:
                    row = _game_score_row(g, None, False, False, [], [], 0, 0, 0) | {
                        "error": f"{type(exc).__name__}: {exc}"
                    }
                rows.append(row)
            results[mk] = _model_summary(rows)
        return results

    def test_aggregate_score_report(self, gateways: dict[str, Any]) -> None:
        if _SKIP_REASON:
            pytest.skip(_SKIP_REASON)

        print("\n" + "=" * 70)
        print("MULTI-MODEL GAME GENERATION BENCHMARK")
        print("=" * 70 + "\n", flush=True)

        report = self._score_all(gateways)
        _save_scores(report)

        print("\n--- PER-MODEL SUMMARY ---")
        for mk in sorted(report.keys()):
            s = report[mk]
            if "error" in s:
                print(f"  {mk}: ERROR — {s['error']}")
                continue
            print(
                f"  {mk} ({_MODELS[mk]['display']}): "
                f"ast={s['ast_pass_rate']} runnable={s['runnable_rate']} "
                f"loc={s['total_loc']} avg_t={s['avg_response_ms']}ms"
            )

        print("\n--- COMPARISON MATRIX ---")
        header = ["model", *sorted(_GAMES)]
        col_w = max(max(len(h) for h in header), 10)
        print("  " + "  ".join(h.ljust(col_w) for h in header))
        print("  " + "  ".join("-" * col_w for _ in header))
        for mk in sorted(report.keys()):
            s = report[mk]
            vals = [mk.ljust(col_w)]
            for g in sorted(_GAMES):
                row = next((r for r in s.get("per_game", []) if r["game"] == g), None)
                if row:
                    flag = "+" if (row["ast_valid"] and row["runnable"]) else "-"
                    vals.append(f"{flag} {row['lines_of_code']}/{row['response_time_ms']}ms".ljust(col_w))
                else:
                    vals.append("N/A".ljust(col_w))
            print("  " + "  ".join(vals))

        print(f"\nScores saved to {_SCORES_FILE}\n", flush=True)
        assert len(report) > 0
