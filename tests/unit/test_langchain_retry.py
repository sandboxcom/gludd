from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.models.gateway import (
    BudgetExceededError,
    ModelGateway,
    ModelResponse,
)
from general_ludd.models.langchain_retry import LangChainRetryGateway


def _fake_response(content: str) -> MagicMock:
    resp = MagicMock(spec=ModelResponse)
    resp.content = content
    resp.model_name = "model-test"
    return resp


class TestLangChainRetryPrimarySuccess:
    def test_call_requires_a_built_chain(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        retry_gateway = LangChainRetryGateway(gateway)

        with pytest.raises(RuntimeError, match="build_chain"):
            retry_gateway.call([{"role": "user", "content": "hi"}])

    def test_call_forwards_tools_context_and_runnable_config(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.return_value = _fake_response("ok")
        retry_gateway = LangChainRetryGateway(gateway)
        retry_gateway.build_chain(
            "primary",
            [],
            retry_config={"stop_after_attempt": 1},
        )

        result = retry_gateway.call(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function"}],
            config={"tags": ["coverage-contract"]},
            context={"project_id": "project-1"},
        )

        assert result.content == "ok"
        gateway.call_model.assert_called_once_with(
            "primary",
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function"}],
            project_id="project-1",
        )

    def test_primary_succeeds_no_fallback_used(self):
        primary_id = "primary"
        fallback_id = "fallback"

        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.return_value = _fake_response("primary response")

        lcg = LangChainRetryGateway(gateway)
        chain = lcg.build_chain(
            primary_id,
            [fallback_id],
            retry_config={"stop_after_attempt": 1},
        )

        result = chain.invoke(
            {"messages": [{"role": "user", "content": "hi"}]}
        )

        assert result.content == "primary response"
        assert gateway.call_model.call_count == 1
        assert gateway.call_model.call_args[0][0] == primary_id


class TestLangChainRetryPrimaryFailsFallbackUsed:
    def test_primary_fails_fallback_tried(self):
        primary_id = "primary"
        fallback_id = "fallback"

        gateway = MagicMock(spec=ModelGateway)

        def _call_model(profile_id, messages, **kwargs):
            if profile_id == primary_id:
                raise RuntimeError("primary down")
            return _fake_response(f"fallback: {profile_id}")

        gateway.call_model.side_effect = _call_model

        lcg = LangChainRetryGateway(gateway)
        chain = lcg.build_chain(
            primary_id,
            [fallback_id],
            retry_config={"stop_after_attempt": 1},
        )

        result = chain.invoke(
            {"messages": [{"role": "user", "content": "hi"}]}
        )

        assert result.content == "fallback: fallback"
        assert gateway.call_model.call_count == 2


class TestLangChainRetryAllFail:
    def test_all_models_fail_error_propagated(self):
        primary_id = "primary"
        fallback_id = "fallback"

        gateway = MagicMock(spec=ModelGateway)

        profile_order: list[str] = []

        def _call_model(profile_id, messages, **kwargs):
            profile_order.append(profile_id)
            raise RuntimeError(f"{profile_id} error")

        gateway.call_model.side_effect = _call_model

        lcg = LangChainRetryGateway(gateway)
        chain = lcg.build_chain(
            primary_id,
            [fallback_id],
            retry_config={"stop_after_attempt": 1},
        )

        with pytest.raises(RuntimeError, match="primary error"):
            chain.invoke(
                {"messages": [{"role": "user", "content": "hi"}]}
            )

        assert profile_order == ["primary", "fallback"]


class TestLangChainRetryTransient:
    def test_retry_on_transient_error_succeeds_on_retry(self):
        primary_id = "primary"

        gateway = MagicMock(spec=ModelGateway)

        call_count = [0]

        def _call_model(profile_id, messages, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("transient error")
            return _fake_response("recovered")

        gateway.call_model.side_effect = _call_model

        lcg = LangChainRetryGateway(gateway)
        chain = lcg.build_chain(
            primary_id,
            [],
            retry_config={
                "stop_after_attempt": 3,
                "wait_exponential_jitter": False,
            },
        )

        result = chain.invoke(
            {"messages": [{"role": "user", "content": "hi"}]}
        )

        assert result.content == "recovered"
        assert call_count[0] == 2
        assert gateway.call_model.call_count == 2


class TestLangChainRetryNonTransient:
    def test_non_transient_error_no_retry_immediate_failure(self):
        primary_id = "primary"

        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = BudgetExceededError(
            "budget exhausted"
        )

        lcg = LangChainRetryGateway(gateway)
        chain = lcg.build_chain(
            primary_id,
            [],
            retry_config={
                "stop_after_attempt": 3,
                "retry_if_exception_type": (RuntimeError,),
                "wait_exponential_jitter": False,
            },
        )

        with pytest.raises(BudgetExceededError, match="budget exhausted"):
            chain.invoke(
                {"messages": [{"role": "user", "content": "hi"}]}
            )

        assert gateway.call_model.call_count == 1
