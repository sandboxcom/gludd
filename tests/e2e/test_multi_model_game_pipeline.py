"""Multi-model game pipeline E2E: planner→coder→reviewer flow.

Tests the multi-phase pipeline where:
  1. PLANNER: generates a design spec from game requirements
  2. CODER: implements the game from the spec
  3. REVIEWER: audits the output for correctness and gaps

Also tests single-model fallback (when only one profile is available,
the same model serves all three phases) and per-phase authorization
(model roles must match phase requirements). Metrics: latency per phase,
token counts, AST validity, feature pass rate.

Skip conditions:
    - DEEPSEEK_API_KEY not set in env and .deepseek.key not found
    - langchain-openai not installed

Run:
    DEEPSEEK_API_KEY="sk-..." uv run pytest tests/e2e/test_multi_model_game_pipeline.py -v -s
or smoke (one game):
    MP_GAME=snake uv run pytest tests/e2e/test_multi_model_game_pipeline.py -v -s
"""

from __future__ import annotations

import importlib.util
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

_DS_BASE_URL = "https://api.deepseek.com/v1"
_TARGET_GAME = os.environ.get("MP_GAME", "").strip().lower()

_PIPELINE_PHASES = ("planner", "coder", "reviewer")

_PLANNER_PROMPT_TEMPLATE = """You are a game design architect. Given the requirements below, produce a DETAILED
implementation specification. Include: data structures, class API (methods + signatures),
edge cases, and lifecycle states. Output ONLY the specification — no code.

Requirements:
{requirements}"""

_CODER_PROMPT_TEMPLATE = """You are a game programmer. Implement the following specification as a complete,
self-contained Python module. NO external dependencies except stdlib. NO display code.
NO prose, no markdown, no explanations.

Specification:
{spec}

Output ONLY the Python code."""

_REVIEWER_PROMPT_TEMPLATE = """You are a code reviewer. Audit the following Python game implementation against its
specification. Return a structured report with:
  - spec_conformance: list of spec requirements that ARE met
  - gaps: list of spec requirements that are MISSING or WRONG
  - bugs: list of logic errors found
  - verdict: "PASS" if all requirements met, "FAIL" otherwise

Specification:
{spec}

Implementation:
{code}"""


# ---------------------------------------------------------------------------
# Key / skip detection
# ---------------------------------------------------------------------------


def _load_ds_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    kf = _REPO_ROOT / ".deepseek.key"
    if kf.exists():
        v = kf.read_text().strip()
        return v if v else None
    return None


_DS_KEY = _load_ds_key()
_HAS_LANGCHAIN_OPENAI = importlib.util.find_spec("langchain_openai") is not None

_SKIP_REASON: str | None = None
if not _DS_KEY:
    _SKIP_REASON = "DEEPSEEK_API_KEY not set and .deepseek.key not found"
elif not _HAS_LANGCHAIN_OPENAI:
    _SKIP_REASON = "langchain-openai not installed"


# ---------------------------------------------------------------------------
# Gateway builder (single model, multi-role)
# ---------------------------------------------------------------------------


def _build_multi_role_gateway() -> Any:
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.provider_registry import ProviderRegistry
    from general_ludd.secrets.env import EnvSecretsManager

    assert _DS_KEY, "key must be set before building gateway"
    secrets = EnvSecretsManager()
    secrets.set("DEEPSEEK_API_KEY", _DS_KEY)
    secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    profiles = []
    for phase in _PIPELINE_PHASES:
        profiles.append(
            ModelProfile(
                model_profile_id=f"mp-{phase}",
                provider="openai",
                provider_package="langchain_openai",
                provider_class_hint="ChatOpenAI",
                model_name="deepseek-chat",
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
                roles=[phase],
                latency_class="fast",
                quality_class="high",
            )
        )

    return cast(Any, ModelGateway)(profiles=profiles, provider_registry=registry, secrets_manager=secrets)


def _call_gateway_phase(
    gateway: Any,
    profile_id: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    t0 = time.time()
    response = gateway.call_model(
        profile_id,
        messages=messages,
        estimated_cost=0.0,
        budget_remaining=5.0,
    )
    latency_ms = int((time.time() - t0) * 1000)
    usage = response.usage_metadata or {}
    return {
        "content": response.content,
        "tokens_in": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "tokens_out": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "latency_ms": latency_ms,
    }


# ---------------------------------------------------------------------------
# Per-phase authorization check
# ---------------------------------------------------------------------------


def _check_phase_authorization(gateway: Any, phase: str) -> str | None:
    """Verify gateway profile for ``phase`` exists and has matching role. Returns error or None."""
    profile_id = f"mp-{phase}"
    try:
        gateway.get_profile(profile_id)
    except Exception as exc:
        return f"profile {profile_id} not found: {type(exc).__name__}: {exc}"
    return None


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------


def _phase_metrics(
    phase: str,
    result: dict[str, Any],
    auth_error: str | None = None,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "latency_ms": result.get("latency_ms", 0),
        "tokens_in": result.get("tokens_in", 0),
        "tokens_out": result.get("tokens_out", 0),
        "content_len": len(result.get("content", "")),
        "auth_ok": auth_error is None,
        "auth_error": auth_error,
    }


# ---------------------------------------------------------------------------
# Structural tests (no API key needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestMultiModelPipelineStructural:
    def test_game_definitions_available(self) -> None:
        assert len(GAME_DEFINITIONS) >= 8
        for gid, gdef in GAME_DEFINITIONS.items():
            assert gdef["prompt"], f"{gid}: empty prompt"
            assert gdef["class_name"], f"{gid}: no class_name"
            assert gdef["verifications"], f"{gid}: no verifications"

    def test_pipeline_phases_defined(self) -> None:
        assert _PIPELINE_PHASES == ("planner", "coder", "reviewer")
        assert len(_PIPELINE_PHASES) == 3

    @pytest.mark.parametrize("phase", _PIPELINE_PHASES)
    def test_phase_profile_id_format(self, phase: str) -> None:
        pid = f"mp-{phase}"
        assert pid.startswith("mp-")
        assert len(pid) > 3

    def test_prompt_templates_non_empty(self) -> None:
        for name, tmpl in [
            ("planner", _PLANNER_PROMPT_TEMPLATE),
            ("coder", _CODER_PROMPT_TEMPLATE),
            ("reviewer", _REVIEWER_PROMPT_TEMPLATE),
        ]:
            assert tmpl, f"{name}: empty template"
            assert "{requirements}" in tmpl or "{spec}" in tmpl or "{code}" in tmpl, (
                f"{name}: template missing substitution placeholder"
            )

    def test_single_model_fallback_profile_ids(self) -> None:
        fallback_ids = {f"mp-{p}" for p in _PIPELINE_PHASES}
        assert len(fallback_ids) == 3
        assert "mp-planner" in fallback_ids
        assert "mp-coder" in fallback_ids
        assert "mp-reviewer" in fallback_ids

    def test_gateway_build_structural(self) -> None:
        if not _DS_KEY:
            pytest.skip("no API key — structural only")
        gw = _build_multi_role_gateway()
        assert gw is not None

    @pytest.mark.parametrize("phase", _PIPELINE_PHASES)
    def test_per_phase_auth_structural(self, phase: str) -> None:
        if not _DS_KEY:
            pytest.skip("no API key — structural only")
        gw = _build_multi_role_gateway()
        err = _check_phase_authorization(gw, phase)
        assert err is None, f"phase {phase}: {err}"

    def test_metrics_collector_shape(self) -> None:
        m = _phase_metrics("planner", {"latency_ms": 100, "tokens_in": 50, "tokens_out": 200})
        assert m["phase"] == "planner"
        assert m["latency_ms"] == 100
        assert m["auth_ok"] is True

    # ------------------------------------------------------------------
    # Source module integration (MultiModelGamePipeline, DesignSpec, ReviewResult)
    # ------------------------------------------------------------------

    def test_source_module_importable(self) -> None:
        from general_ludd.cloud.multi_model_game_pipeline import (
            DesignSpec,
            MultiModelGamePipeline,
            ReviewResult,
        )

        assert DesignSpec is not None
        assert MultiModelGamePipeline is not None
        assert ReviewResult is not None

    def test_design_spec_to_prompt(self) -> None:
        from general_ludd.cloud.multi_model_game_pipeline import DesignSpec

        spec = DesignSpec(
            name="TestGame",
            genre="arcade",
            description="A test game",
            architecture_plan="Pygame main loop",
            component_list=("Paddle", "Ball"),
            tech_stack=("pygame",),
            acceptance_criteria=("Score tracks", "Lives decrement"),
        )
        prompt = spec.to_prompt()
        assert "TestGame" in prompt
        assert "arcade" in prompt
        assert "Pygame main loop" in prompt
        assert "Paddle" in prompt
        assert "pygame" in prompt
        assert "Score tracks" in prompt

    def test_review_result_to_feedback_prompt(self) -> None:
        from general_ludd.cloud.multi_model_game_pipeline import ReviewResult

        rr = ReviewResult(
            code="print('hello')",
            issues_found=("Missing game loop", "No pygame.init"),
            fixes_applied=(),
            quality_score=0.3,
            passed=False,
        )
        feedback = rr.to_feedback_prompt()
        assert "Missing game loop" in feedback
        assert "No pygame.init" in feedback
        assert "Code Review Feedback" in feedback

    def test_review_result_passed_no_feedback(self) -> None:
        from general_ludd.cloud.multi_model_game_pipeline import ReviewResult

        rr = ReviewResult(code="ok", passed=True)
        assert rr.to_feedback_prompt() == ""

    def test_pipeline_init_requires_gateway(self) -> None:
        import inspect

        from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline

        sig = inspect.signature(MultiModelGamePipeline.__init__)
        params = list(sig.parameters.keys())
        assert "gateway" in params
        assert "task_policy" in params

    def test_design_spec_defaults(self) -> None:
        from general_ludd.cloud.multi_model_game_pipeline import DesignSpec

        spec = DesignSpec(name="Min", genre="demo", description="Minimal")
        assert spec.architecture_plan == ""
        assert spec.component_list == ()
        assert spec.tech_stack == ()
        assert spec.acceptance_criteria == ()
        assert spec.to_prompt()  # does not crash on empty fields


# ---------------------------------------------------------------------------
# Live pipeline tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
class TestMultiModelGamePipeline:
    """Run planner→coder→reviewer against each game. Score per phase."""

    @pytest.fixture(scope="class")
    def gateway(self) -> Any:
        if _SKIP_REASON:
            pytest.skip(_SKIP_REASON)
        return _build_multi_role_gateway()

    def _run_pipeline(
        self,
        gateway: Any,
        game_id: str,
    ) -> dict[str, Any]:
        """Run full planner→coder→reviewer pipeline for one game."""
        definition = GAME_DEFINITIONS[game_id]
        requirements = definition["prompt"]
        definition["class_name"]

        metrics: list[dict[str, Any]] = []
        overall: dict[str, Any] = {
            "game_id": game_id,
            "planner": None,
            "coder": None,
            "reviewer": None,
            "ast_ok": False,
            "imported": False,
            "feature_failures": [],
            "lifecycle_failures": [],
        }

        # ---- PHASE 1: Planner ----
        print(f"\n[PIPELINE] {game_id}: PHASE 1 — Planner")
        planner_err = _check_phase_authorization(gateway, "planner")
        if planner_err is not None:
            overall["planner"] = {"error": planner_err}
            overall["feature_failures"] = [planner_err]
            overall["metrics"] = metrics
            return overall

        planner_prompt = _PLANNER_PROMPT_TEMPLATE.format(requirements=requirements)
        try:
            planner_result = _call_gateway_phase(
                gateway,
                "mp-planner",
                [
                    {"role": "system", "content": "You are a game design architect."},
                    {"role": "user", "content": planner_prompt},
                ],
            )
        except Exception as exc:
            overall["planner"] = {"error": f"call failed: {type(exc).__name__}: {exc}"}
            overall["feature_failures"] = [f"planner phase: {type(exc).__name__}: {exc}"]
            return overall

        spec = planner_result["content"]
        overall["planner"] = {"content_len": len(spec), "ok": len(spec) > 100}
        metrics.append(_phase_metrics("planner", planner_result))
        print(f"  spec length: {len(spec)} chars, latency: {planner_result['latency_ms']}ms")

        if len(spec) < 50:
            overall["feature_failures"] = [f"planner produced too-short spec ({len(spec)} chars)"]
            overall["metrics"] = metrics
            return overall

        # ---- PHASE 2: Coder ----
        print(f"[PIPELINE] {game_id}: PHASE 2 — Coder")
        coder_err = _check_phase_authorization(gateway, "coder")
        if coder_err is not None:
            overall["coder"] = {"error": coder_err}
            overall["feature_failures"] = [coder_err]
            overall["metrics"] = metrics
            return overall

        coder_prompt = _CODER_PROMPT_TEMPLATE.format(spec=spec[:8000])
        try:
            coder_result = _call_gateway_phase(
                gateway,
                "mp-coder",
                [{"role": "user", "content": coder_prompt}],
            )
        except Exception as exc:
            overall["coder"] = {"error": f"call failed: {type(exc).__name__}: {exc}"}
            overall["feature_failures"] = [f"coder phase: {type(exc).__name__}: {exc}"]
            overall["metrics"] = metrics
            return overall

        source = _extract_python_module(coder_result["content"])
        overall["coder"] = {
            "content_len": len(coder_result["content"]),
            "source_extracted": source is not None,
            "source_lines": source.count("\n") + 1 if source else 0,
        }
        metrics.append(_phase_metrics("coder", coder_result))
        print(
            f"  source extracted: {source is not None}, "
            f"lines: {overall['coder']['source_lines']}, "
            f"latency: {coder_result['latency_ms']}ms"
        )

        if source is None:
            overall["feature_failures"] = ["coder did not produce extractable Python code"]
            overall["metrics"] = metrics
            return overall

        # AST check
        ast_result = _parse_ast(source)
        overall["ast_ok"] = ast_result["parseable"]
        if not ast_result["parseable"]:
            overall["feature_failures"].append(f"AST parse failed: {ast_result.get('error')}")

        # Import + feature verification
        if ast_result["parseable"]:
            with __import__("tempfile").TemporaryDirectory(prefix="gludd-mp-") as td:
                tp = Path(td)
                try:
                    mod = _load_generated_module(source, f"mp_{game_id}", tp)
                    overall["imported"] = True
                    overall["feature_failures"] = verify_features(game_id, mod)
                    overall["lifecycle_failures"] = run_lifecycle_checks(game_id, mod)
                except Exception as exc:
                    overall["feature_failures"] = [f"import/verify: {type(exc).__name__}: {exc}"]

        # ---- PHASE 3: Reviewer ----
        print(f"[PIPELINE] {game_id}: PHASE 3 — Reviewer")
        reviewer_err = _check_phase_authorization(gateway, "reviewer")
        if reviewer_err is not None:
            overall["reviewer"] = {"error": reviewer_err}
            overall["metrics"] = metrics
            return overall

        reviewer_prompt = _REVIEWER_PROMPT_TEMPLATE.format(spec=spec[:4000], code=source[:6000])
        try:
            reviewer_result = _call_gateway_phase(
                gateway,
                "mp-reviewer",
                [{"role": "user", "content": reviewer_prompt}],
            )
        except Exception as exc:
            overall["reviewer"] = {"error": f"call failed: {type(exc).__name__}: {exc}"}
            overall["metrics"] = metrics
            return overall

        overall["reviewer"] = {
            "content_len": len(reviewer_result["content"]),
            "ok": len(reviewer_result["content"]) > 30,
        }
        metrics.append(_phase_metrics("reviewer", reviewer_result))
        print(f"  review length: {len(reviewer_result['content'])} chars, latency: {reviewer_result['latency_ms']}ms")

        overall["metrics"] = metrics
        return overall

    @pytest.mark.parametrize("game_id", sorted(GAME_DEFINITIONS.keys()))
    def test_pipeline_game(self, gateway: Any, game_id: str) -> None:
        if _TARGET_GAME and game_id != _TARGET_GAME:
            pytest.skip(f"MP_GAME={_TARGET_GAME}, skipping {game_id}")

        result = self._run_pipeline(gateway, game_id)

        print(f"\n[PIPELINE RESULT] {game_id}")
        print(f"  planner:   {result['planner']}")
        print(f"  coder:     {result['coder']}")
        print(f"  reviewer:  {result['reviewer']}")
        print(f"  ast_ok:    {result['ast_ok']}")
        print(f"  imported:  {result['imported']}")
        print(f"  features:  {len(result['feature_failures'])} failures")
        print(f"  lifecycle: {len(result['lifecycle_failures'])} failures")
        if result.get("metrics"):
            total_latency = sum(m["latency_ms"] for m in result["metrics"])
            total_tokens = sum(m["tokens_in"] + m["tokens_out"] for m in result["metrics"])
            print(f"  total latency: {total_latency}ms, total tokens: {total_tokens}")

        # Planner must produce a non-trivial spec
        planner = result.get("planner") or {}
        assert planner.get("ok") or planner.get("content_len", 0) > 50, (
            f"{game_id}: planner produced insufficient spec ({planner.get('content_len', 0)} chars)"
        )

        # Coder must produce extractable code
        coder = result.get("coder") or {}
        assert coder.get("source_extracted"), f"{game_id}: coder did not produce extractable code"

        # AST must parse
        assert result["ast_ok"], f"{game_id}: AST parse failed: {result.get('feature_failures', [])}"

        # Module must be importable
        assert result["imported"], f"{game_id}: module not importable"

        # Feature failures must be zero
        feature_fails = result.get("feature_failures", [])
        assert len(feature_fails) == 0, f"{game_id}: {len(feature_fails)} feature failures: {feature_fails[:5]}"

        # Lifecycle failures must be zero
        lifecycle_fails = result.get("lifecycle_failures", [])
        assert len(lifecycle_fails) == 0, f"{game_id}: {len(lifecycle_fails)} lifecycle failures: {lifecycle_fails[:5]}"


# ---------------------------------------------------------------------------
# Single-model fallback tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSingleModelFallback:
    """Verify that the pipeline works when all phases use the same model."""

    def _build_single_profile_gateway(self) -> Any:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager

        assert _DS_KEY
        secrets = EnvSecretsManager()
        secrets.set("DEEPSEEK_API_KEY", _DS_KEY)
        secrets.set("DEEPSEEK_API_BASE", _DS_BASE_URL)
        registry = ProviderRegistry()
        registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

        profile = ModelProfile(
            model_profile_id="mp-universal",
            provider="openai",
            provider_package="langchain_openai",
            provider_class_hint="ChatOpenAI",
            model_name="deepseek-chat",
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
            roles=["planner", "coder", "reviewer"],
            latency_class="fast",
            quality_class="high",
        )
        return cast(Any, ModelGateway)(profiles=[profile], provider_registry=registry, secrets_manager=secrets)

    def test_single_model_fallback_smoke(self) -> None:
        """Smoke test: one game through the pipeline with a single universal profile."""
        if _SKIP_REASON:
            pytest.skip(_SKIP_REASON)

        game_id = _TARGET_GAME or "pong"
        if game_id not in GAME_DEFINITIONS:
            pytest.skip(f"unknown game: {game_id}")

        gw = self._build_single_profile_gateway()
        assert gw is not None

        pipeline_tester = TestMultiModelGamePipeline()
        result = pipeline_tester._run_pipeline(gw, game_id)

        assert result["ast_ok"], f"fallback: {game_id}: AST parse failed"
        assert result["imported"], f"fallback: {game_id}: module not importable"


# ---------------------------------------------------------------------------
# Metrics aggregation test
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPipelineMetrics:
    """Aggregate pipeline metrics across games for comparison."""

    def test_metrics_aggregation_structural(self) -> None:
        """Verify metrics collection shape without live calls."""
        sample_metrics = [
            _phase_metrics("planner", {"latency_ms": 500, "tokens_in": 100, "tokens_out": 300}),
            _phase_metrics("coder", {"latency_ms": 2000, "tokens_in": 400, "tokens_out": 1200}),
            _phase_metrics("reviewer", {"latency_ms": 800, "tokens_in": 200, "tokens_out": 150}),
        ]
        total_latency = sum(m["latency_ms"] for m in sample_metrics)
        total_tokens = sum(m["tokens_in"] + m["tokens_out"] for m in sample_metrics)
        assert total_latency == 3300
        assert total_tokens == 2350
        assert all(m["auth_ok"] for m in sample_metrics)

    def test_metrics_with_auth_failure(self) -> None:
        m = _phase_metrics("planner", {}, auth_error="profile not found")
        assert m["auth_ok"] is False
        assert m["auth_error"] == "profile not found"
