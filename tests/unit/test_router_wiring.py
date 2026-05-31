"""Unit tests for Feature #12: Model Router Wiring."""

from unittest.mock import MagicMock, patch

import pytest

from agentic_harness.models.gateway import ModelGateway, ModelProfile
from agentic_harness.models.router import ModelRouter
from agentic_harness.prompts.registry import PromptRegistry
from agentic_harness.review.reviewer import ReturnReviewer
from agentic_harness.schemas.task_return import TaskReturn


def _make_task_return(**overrides):
    defaults = {
        "return_id": "RET-001",
        "todo_id": "TODO-001",
        "job_id": "JOB-001",
        "playbook": "test_playbook",
        "queue": "core",
        "result_summary": "All tests passed",
        "exit_code": 0,
        "artifacts": [],
    }
    defaults.update(overrides)
    return TaskReturn(**defaults)


class TestCallModelByRole:
    def _make_gateway(self, router=None):
        from agentic_harness.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        profile = ModelProfile(
            model_profile_id="reviewer_profile",
            enabled=True,
            provider="openai",
            model_name="gpt-4",
            run_budget_usd=100.0,
        )
        weak_profile = ModelProfile(
            model_profile_id="weak_profile",
            enabled=True,
            provider="openai",
            model_name="gpt-3.5-turbo",
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile, weak_profile],
            provider_registry=reg,
            router=router,
        )
        return gw, reg

    def test_call_model_by_role_resolves_through_router(self):
        router = ModelRouter(role_mapping={"reviewer": "reviewer_profile"})
        gw, reg = self._make_gateway(router=router)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="reviewed",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_by_role(
                "reviewer", [{"role": "user", "content": "review this"}]
            )

        assert resp.content == "reviewed"

    def test_call_model_by_role_raises_for_unknown_role(self):
        router = ModelRouter(role_mapping={"coder": "gpt4"})
        gw, _ = self._make_gateway(router=router)

        with pytest.raises(ValueError, match="No profile resolved"):
            gw.call_model_by_role("unknown_role", [{"role": "user", "content": "hi"}])

    def test_call_model_by_role_uses_default_when_no_mapping(self):
        router = ModelRouter(
            role_mapping={},
            default_profile_id="reviewer_profile",
        )
        gw, reg = self._make_gateway(router=router)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="default response",
            usage_metadata={"input_tokens": 5, "output_tokens": 3},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_by_role(
                "anything", [{"role": "user", "content": "hi"}]
            )

        assert resp.content == "default response"


class TestCallModelByPattern:
    def _make_gateway(self, router=None):
        from agentic_harness.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        profiles = [
            ModelProfile(
                model_profile_id="reviewer_profile",
                enabled=True,
                provider="openai",
                model_name="gpt-4",
                run_budget_usd=100.0,
            ),
            ModelProfile(
                model_profile_id="weak_profile",
                enabled=True,
                provider="openai",
                model_name="gpt-3.5-turbo",
                run_budget_usd=100.0,
            ),
            ModelProfile(
                model_profile_id="fast_profile",
                enabled=True,
                provider="openai",
                model_name="gpt-4o-mini",
                run_budget_usd=100.0,
            ),
        ]

        gw = ModelGateway(
            profiles=profiles,
            provider_registry=reg,
            router=router,
        )
        return gw, reg

    def test_pattern_return_review_maps_to_reviewer(self):
        router = ModelRouter(role_mapping={"reviewer": "reviewer_profile"})
        router.add_pattern_mapping("return_review", "reviewer")
        gw, reg = self._make_gateway(router=router)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="reviewed",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_by_pattern(
                "return_review", [{"role": "user", "content": "review"}]
            )

        assert resp.content == "reviewed"

    def test_pattern_commit_message_maps_to_weak(self):
        router = ModelRouter(
            role_mapping={"weak": "weak_profile"},
            weak_model_profile_id="weak_profile",
        )
        router.add_pattern_mapping("commit_message", "weak")
        gw, reg = self._make_gateway(router=router)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="chore: update deps",
            usage_metadata={"input_tokens": 5, "output_tokens": 3},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_by_pattern(
                "commit_message", [{"role": "user", "content": "diff"}]
            )

        assert resp.content == "chore: update deps"

    def test_pattern_gap_analysis_maps_to_fast(self):
        router = ModelRouter(role_mapping={"fast": "fast_profile"})
        router.add_pattern_mapping("gap_analysis", "fast")
        gw, reg = self._make_gateway(router=router)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="gaps found",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_by_pattern(
                "gap_analysis", [{"role": "user", "content": "analyze"}]
            )

        assert resp.content == "gaps found"

    def test_unknown_pattern_raises(self):
        router = ModelRouter(role_mapping={"reviewer": "reviewer_profile"})
        gw, _ = self._make_gateway(router=router)

        with pytest.raises(ValueError, match="No profile resolved"):
            gw.call_model_by_pattern(
                "unknown_pattern", [{"role": "user", "content": "hi"}]
            )


class TestReviewerUsesRouter:
    def test_reviewer_uses_routed_profile(self):
        router = ModelRouter(role_mapping={"return_review": "reviewer_profile"})
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model_by_role.return_value = MagicMock(content="reviewed")

        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            router=router,
        )

        task_return = _make_task_return()
        with patch.object(reviewer, "_call_model", return_value="reviewed"):
            reviewer.review_return(task_return, [], [])

    def test_reviewer_without_router_uses_default_profile(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            model_profile_id="default",
        )

        task_return = _make_task_return()
        from agentic_harness.schemas.task_decision import TaskDecision

        expected = TaskDecision(
            return_id="RET-001",
            matched_todo_id="TODO-001",
            decision="complete",
            confidence=0.9,
        )
        with patch.object(reviewer, "_call_model", return_value=expected):
            result = reviewer.review_return(task_return, [], [])

        assert result.decision == "complete"
