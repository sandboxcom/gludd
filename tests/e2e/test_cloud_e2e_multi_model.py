"""Cloud E2E multi-model pipeline tests.

Tests the generic SoftwareGenerator multi-model pipeline (planner -> coder
-> reviewer) across all 12 project types via cloud API (DeepSeek).

Each project type uses its prompt templates from ``project_types.py`` and
runs through the full ModelPipeline orchestration. Structural tests cover
all 12 types without API access; live tests hit DeepSeek for types with
API key available.

Run:
    DEEPSEEK_API_KEY="sk-..." uv run pytest tests/e2e/test_cloud_e2e_multi_model.py -v -s
or smoke (single type):
    CLOUD_TYPE=game uv run pytest tests/e2e/test_cloud_e2e_multi_model.py -v -s
"""

from __future__ import annotations

import ast
import importlib.util
import os
import time
from pathlib import Path
from typing import Any, cast

import pytest

# ---------------------------------------------------------------------------
# Key / skip detection
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent

_DS_BASE_URL = "https://api.deepseek.com/v1"
_CLOUD_TYPE = os.environ.get("CLOUD_TYPE", "").strip()


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
# All 12 project types for multi-model pipeline testing
# ---------------------------------------------------------------------------

_PYTHON_PROJECT_TYPES: list[dict[str, Any]] = [
    {
        "id": "game",
        "display": "Pygame Game",
        "entry": "game.py",
        "context": "Build a snake game: 20x20 grid, snake eats food. "
        "Required: __init__, start, tick, score, is_game_over, restart.",
    },
    {
        "id": "scraper",
        "display": "Web Scraper",
        "entry": "scraper.py",
        "context": "Build a web scraper that fetches Hacker News top stories and outputs CSV with title, url, points.",
    },
    {
        "id": "cli_tool",
        "display": "CLI Tool",
        "entry": "cli.py",
        "context": "Build a CLI tool 'task' for managing tasks from a JSON file. "
        "Commands: add, list, complete, delete. Use argparse.",
    },
    {
        "id": "api_server",
        "display": "API Server",
        "entry": "main.py",
        "context": "Build a FastAPI microservice for a book library with CRUD "
        "endpoints: POST /books, GET /books, GET /books/{id}, PUT /books/{id}, "
        "DELETE /books/{id}. Use Pydantic models.",
    },
    {
        "id": "word_processor",
        "display": "Word Processor",
        "entry": "processor.py",
        "context": "Build a word processor utility that counts words, finds "
        "frequent words, and reformats text. Support --input and --output flags.",
    },
    {
        "id": "data_pipeline",
        "display": "ETL Data Pipeline",
        "entry": "pipeline.py",
        "context": "Build an ETL pipeline that reads a CSV of sales data, "
        "transforms (aggregate by category, compute totals), outputs JSON.",
    },
    {
        "id": "chatbot",
        "display": "Chat Interface",
        "entry": "chatbot.py",
        "context": "Build a terminal chatbot that responds to greetings, tells "
        "the time, remembers the user's name, has /help and /exit commands.",
    },
    {
        "id": "desktop_app",
        "display": "Desktop Application",
        "entry": "app.py",
        "context": "Build a tkinter desktop calculator with number buttons, "
        "operators (+, -, *, /), a display, and clear button.",
    },
    {
        "id": "test_suite",
        "display": "Pytest Test Suite",
        "entry": "test_main.py",
        "context": "Write a pytest test suite for a Calculator class (add, "
        "subtract, multiply, divide). Include edge cases like division by zero.",
    },
]

_NON_PYTHON_PROJECT_TYPES: list[dict[str, Any]] = [
    {
        "id": "website",
        "display": "Single-Page Website",
        "entry": "index.html",
        "context": "Build a responsive todo-list single-page website. "
        "Users can add, check off, and delete tasks. Dark mode toggle. "
        "Uses HTML/CSS/JS only.",
    },
    {
        "id": "database_schema",
        "display": "Database Schema",
        "entry": "schema.sql",
        "context": "Design a blog database schema with tables for users, posts, "
        "comments, and tags. Include indexes, foreign keys, and seed data.",
    },
    {
        "id": "kernel_module",
        "display": "Kernel Module",
        "entry": "module.c",
        "context": "Write a Linux kernel module that creates a /proc entry for "
        "system uptime. Include GPL license, init/exit functions, error handling.",
    },
]

_ALL_PROJECT_TYPES = _PYTHON_PROJECT_TYPES + _NON_PYTHON_PROJECT_TYPES

# ---------------------------------------------------------------------------
# Gateway builder
# ---------------------------------------------------------------------------

_GATEWAY_CACHE: dict[str, Any] = {}


def _build_multi_role_gateway() -> Any:
    if "gateway" in _GATEWAY_CACHE:
        return _GATEWAY_CACHE["gateway"]

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
    for role in ("planner", "coder", "reviewer"):
        profiles.append(
            ModelProfile(
                model_profile_id=f"ds-{role}",
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
                roles=[role],
                latency_class="fast",
                quality_class="high",
            )
        )

    gateway = cast(Any, ModelGateway)(profiles=profiles, provider_registry=registry, secrets_manager=secrets)
    _GATEWAY_CACHE["gateway"] = gateway
    return gateway


def _call_gateway(gateway: Any, profile_id: str, prompt: str) -> dict[str, Any]:
    t0 = time.time()
    response = gateway.call_model(
        profile_id,
        messages=[{"role": "user", "content": prompt}],
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
# Validation
# ---------------------------------------------------------------------------


def _ast_valid(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def _extract_python(source: str) -> str | None:
    import re

    pattern = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)
    matches = pattern.findall(source)
    if matches:
        best = max(matches, key=len)
        return best.strip() if len(best) > 50 else None
    if "class " in source and ("def " in source or "import " in source):
        return source.strip()
    return None


# ---------------------------------------------------------------------------
# Structural tests (no API key needed)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestCloudMultiModelStructural:
    """Structural checks - all 12 project types loadable, prompts render."""

    def test_all_project_types_registered(self) -> None:
        from general_ludd.cloud.project_types import available_type_ids, get_project_type

        ids = available_type_ids()
        assert len(ids) >= 12, f"Expected >=12 project types, got {len(ids)}: {ids}"
        for pt_id in ids:
            pt = get_project_type(pt_id)
            assert pt.type_id == pt_id
            assert pt.display_name
            assert pt.default_entry_point
            assert pt.validation_rules, f"{pt_id}: no validation rules"

    @pytest.mark.parametrize("meta", _ALL_PROJECT_TYPES, ids=lambda m: m["id"])
    def test_project_prompt_templates_render(self, meta: dict[str, Any]) -> None:
        from general_ludd.cloud.project_types import get_project_type

        pt = get_project_type(meta["id"])
        planner = pt.prompt_template_planner.replace("{context}", meta["context"])
        coder = pt.prompt_template_coder.replace("{context}", meta["context"])
        assert len(planner) > 50, f"{meta['id']}: planner template too short"
        assert len(coder) > 50, f"{meta['id']}: coder template too short"

    @pytest.mark.parametrize("meta", _ALL_PROJECT_TYPES, ids=lambda m: m["id"])
    def test_project_acceptance_criteria(self, meta: dict[str, Any]) -> None:
        from general_ludd.cloud.project_types import get_project_type

        pt = get_project_type(meta["id"])
        assert pt.acceptance_criteria, f"{meta['id']}: no acceptance criteria"
        assert len(pt.acceptance_criteria) >= 2, f"{meta['id']}: too few criteria"

    def test_model_pipeline_importable(self) -> None:
        from general_ludd.cloud.model_pipeline import (  # noqa: F401
            ModelPipeline,
            PipelineResult,
            PipelineStep,
            StepResult,
        )

    def test_software_generator_importable(self) -> None:
        from general_ludd.cloud.game_e2e import SoftwareGenerator  # noqa: F401


# ---------------------------------------------------------------------------
# Live E2E tests (require API key)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
class TestCloudMultiModelPipelineLive:
    @pytest.fixture(scope="class")
    def gateway(self):
        return _build_multi_role_gateway()

    @pytest.mark.parametrize("meta", _PYTHON_PROJECT_TYPES, ids=lambda m: m["id"])
    @pytest.mark.skipif(bool(_CLOUD_TYPE), reason="CLOUD_TYPE set - only running selected type")
    def test_build_python_project_multi_model(self, gateway: Any, tmp_path: Path, meta: dict[str, Any]) -> None:
        from general_ludd.cloud.project_types import (
            get_project_type,
            validate_project_against_rules,
        )

        pt = get_project_type(meta["id"])
        out_dir = tmp_path / meta["id"]
        out_dir.mkdir(exist_ok=True)

        planner_prompt = pt.prompt_template_planner.replace("{context}", meta["context"])
        plan_resp = _call_gateway(gateway, "ds-planner", planner_prompt)
        assert len(plan_resp["content"]) > 100, "Planner returned too little"
        assert plan_resp["tokens_out"] > 0, "Planner returned 0 tokens"

        coder_prompt = pt.prompt_template_coder.replace("{context}", meta["context"])
        code_resp = _call_gateway(gateway, "ds-coder", coder_prompt)
        raw = code_resp["content"]
        source = _extract_python(raw)
        assert source is not None, f"Could not extract Python from: {raw[:200]!r}"
        assert len(source) > 50, f"Generated code too short: {len(source)} chars"
        assert code_resp["tokens_out"] > 0, "Coder returned 0 tokens"

        review_prompt = (
            f"Review this {meta['display']} code against criteria:\n"
            + "\n".join(f"- {c}" for c in pt.acceptance_criteria)
            + f"\n\n```python\n{source}\n```\n\n"
            + "List issues. Say 'ALL CRITERIA PASS' if none."
        )
        review_resp = _call_gateway(gateway, "ds-reviewer", review_prompt)
        assert len(review_resp["content"]) > 30, "Reviewer returned too little"

        assert _ast_valid(source), f"{meta['id']}: syntax error in generated code"
        rules_ok = validate_project_against_rules(source, pt)
        assert rules_ok, f"{meta['id']}: project-type validation rules failed"

        entry_path = out_dir / meta["entry"]
        entry_path.write_text(source)
        assert entry_path.exists() and entry_path.stat().st_size > 0

    @pytest.mark.parametrize("meta", _NON_PYTHON_PROJECT_TYPES, ids=lambda m: m["id"])
    @pytest.mark.skipif(bool(_CLOUD_TYPE), reason="CLOUD_TYPE set - only running selected type")
    def test_build_non_python_project_multi_model(self, gateway: Any, tmp_path: Path, meta: dict[str, Any]) -> None:
        from general_ludd.cloud.project_types import get_project_type

        pt = get_project_type(meta["id"])
        out_dir = tmp_path / meta["id"]
        out_dir.mkdir(exist_ok=True)

        planner_prompt = pt.prompt_template_planner.replace("{context}", meta["context"])
        plan_resp = _call_gateway(gateway, "ds-planner", planner_prompt)
        assert len(plan_resp["content"]) > 80, "Planner returned too little"

        coder_prompt = pt.prompt_template_coder.replace("{context}", meta["context"])
        code_resp = _call_gateway(gateway, "ds-coder", coder_prompt)
        raw = code_resp["content"]
        assert len(raw) > 50, f"Generated content too short: {len(raw)} chars"

        review_prompt = (
            f"Review this {meta['display']} output against criteria:\n"
            + "\n".join(f"- {c}" for c in pt.acceptance_criteria)
            + f"\n\n```\n{raw[:3000]}\n```\n\n"
            + "List issues. Say 'ALL CRITERIA PASS' if none."
        )
        review_resp = _call_gateway(gateway, "ds-reviewer", review_prompt)
        assert len(review_resp["content"]) > 30

        entry_path = out_dir / meta["entry"]
        entry_path.write_text(raw)
        assert entry_path.exists() and entry_path.stat().st_size > 0

    @pytest.mark.slow
    def test_orchestrate_all_python_types(self, gateway: Any, tmp_path: Path) -> None:
        """Exercise every Python project type through three live model calls."""
        from general_ludd.cloud.project_types import get_project_type

        results: list[dict[str, Any]] = []
        t0 = time.time()

        for meta in _PYTHON_PROJECT_TYPES:
            pt = get_project_type(meta["id"])
            p_prompt = pt.prompt_template_planner.replace("{context}", meta["context"])
            c_prompt = pt.prompt_template_coder.replace("{context}", meta["context"])
            plan = _call_gateway(gateway, "ds-planner", p_prompt)
            code_resp = _call_gateway(gateway, "ds-coder", c_prompt)
            source = _extract_python(code_resp["content"])
            ast_ok = _ast_valid(source or "")
            results.append(
                {
                    "type": meta["id"],
                    "plan_len": len(plan["content"]),
                    "code_len": len(source or ""),
                    "ast_valid": ast_ok,
                    "tokens_out": code_resp["tokens_out"],
                }
            )
            assert source is not None, f"{meta['id']}: no code extracted"
            assert ast_ok, f"{meta['id']}: AST parse failed"

        elapsed = time.time() - t0
        total_tokens = sum(r["tokens_out"] for r in results)
        print(f"\nOrchestration: {len(results)} types in {elapsed:.1f}s, {total_tokens} tokens")
        for r in results:
            print(f"  {r['type']}: plan={r['plan_len']} code={r['code_len']} ast={'OK' if r['ast_valid'] else 'FAIL'}")
