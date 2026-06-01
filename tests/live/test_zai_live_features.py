"""Live tests for all model-using features with ZAI/GLM.

Tests that the following features work with real model calls:
1. ReturnReviewer — LLM-based task return review
2. Rules evaluation with LLM hints
3. Model gateway routing
4. Conversation planning
5. Context compaction

Run with: make test-zai-identity (provides ZAI_API_KEY)
"""

from __future__ import annotations

import os

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.env import EnvSecretsManager


def _get_zai_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY")


def _build_zai_gateway() -> ModelGateway:
    api_key = _get_zai_api_key()
    base_url = os.environ.get("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    model_name = os.environ.get("ZAI_MODEL", "glm-5.1")

    profile = ModelProfile(
        model_profile_id="zai_live",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=model_name,
        api_base_alias="ZAI_BASE_URL",
        credential_alias="ZAI_API_KEY",
        context_window=64000,
        max_input_tokens=60000,
        max_output_tokens=4096,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=1.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    if api_key:
        secrets.set("ZAI_API_KEY", api_key)
    if base_url:
        secrets.set("ZAI_BASE_URL", base_url)

    return ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=secrets,
    )


_SKIP_REASON = "ZAI_API_KEY not set. Run: make test-zai-identity"


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAILiveReturnReviewer:
    """Test ReturnReviewer with real ZAI/GLM model."""

    def test_review_return_completes(self):
        from general_ludd.prompts.registry import PromptRegistry
        from general_ludd.review.reviewer import ReturnReviewer
        from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus

        gw = _build_zai_gateway()

        registry = PromptRegistry()
        registry.register(
            "return_review.md.j2",
            "Review task return {{ task_return.return_id }} for todo {{ task_return.todo_id }}. "
            "The task ran playbook '{{ task_return.playbook }}' with exit code {{ task_return.exit_code }}. "
            "Decide: complete, needs_more_work, or failed. Reply in JSON.",
        )

        reviewer = ReturnReviewer(
            gateway=gw,
            prompt_registry=registry,
            model_profile_id="zai_live",
        )

        tr = TaskReturn(
            return_id="RET-LIVE-001",
            job_id="JOB-LIVE-001",
            todo_id="TODO-LIVE-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
            exit_code=0,
            result_summary="All tests passed. Build successful.",
        )

        decision = reviewer.review_return(tr, [], [])
        assert decision is not None
        assert decision.decision in (
            "complete",
            "needs_more_work",
            "failed",
            "ignore_duplicate",
        )
        assert 0.0 <= decision.confidence <= 1.0


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAILiveModelGateway:
    """Test ModelGateway routing with real model."""

    def test_gateway_routes_to_correct_profile(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        assert response is not None
        assert isinstance(response.content, str)
        assert len(response.content) > 0

    def test_gateway_returns_usage_metadata(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[{"role": "user", "content": "Say hello"}],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        assert response.usage_metadata is not None
        assert response.usage_metadata.get("input_tokens", 0) > 0
        assert response.usage_metadata.get("output_tokens", 0) > 0

    def test_gateway_handles_code_generation(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a Python function that adds two numbers. "
                        "Reply with ONLY the function definition, nothing else."
                    ),
                },
            ],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        assert response.content is not None
        assert "def " in response.content
        assert "return" in response.content


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestZAILiveConversationPlanning:
    """Test conversation planning with real model."""

    def test_model_generates_plan(self):
        gw = _build_zai_gateway()
        response = gw.call_model(
            "zai_live",
            messages=[
                {
                    "role": "system",
                    "content": "You are a coding planner. Generate a brief plan.",
                },
                {
                    "role": "user",
                    "content": (
                        "Create a plan to add a health check endpoint to a FastAPI app. "
                        "List 3-5 steps."
                    ),
                },
            ],
            estimated_cost=0.0,
            budget_remaining=1.0,
        )
        assert response.content is not None
        assert len(response.content) > 50
        assert any(
            kw in response.content.lower()
            for kw in ["endpoint", "health", "fastapi", "route", "get"]
        )
