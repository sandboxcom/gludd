"""Remediation-B: unit tests for gateway.py bind_tools fix.

Verifies that:
  1. When tools= is passed to call_model(), bind_tools() is called on the
     chat model BEFORE .invoke() — the LangChain pattern for tool-use.
  2. tools= is NOT forwarded to the provider constructor (would raise TypeError
     on ChatOpenAI and all openai-compat providers).
  3. When no tools= is passed, the existing behaviour is unchanged (bind_tools
     is never called, invoke is called directly on the constructed model).
  4. When the provider class does not implement bind_tools, the call falls back
     gracefully to plain invoke (no AttributeError propagates).
"""

from __future__ import annotations

import datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile

# ---------------------------------------------------------------------------
# Minimal fake provider + registry
# ---------------------------------------------------------------------------


class _FakeChatModel:
    """Fake LangChain-style chat model that records invocations."""

    def __init__(self, **kwargs: Any) -> None:
        # Capture constructor kwargs so we can assert tools was NOT passed here.
        self.init_kwargs = kwargs
        self._bound_tools: list[Any] | None = None
        self._invoked_with: list[Any] | None = None

    def bind_tools(self, tools: list[Any]) -> _FakeChatModel:
        """Return self with tools recorded (LangChain returns a new runnable)."""
        bound = _FakeChatModel(**{k: v for k, v in self.init_kwargs.items()})
        bound._bound_tools = list(tools)
        # Share the invocation recorder with the parent so the test can find it.
        cast(Any, bound)._parent = self
        self._bind_tools_result = bound
        return bound

    def invoke(self, messages: list[Any]) -> Any:
        self._invoked_with = messages
        # Return a minimal LangChain-style message object.
        result = MagicMock()
        result.content = "fake model response"
        result.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        return result


class _FakeChatModelNoBindTools:
    """Provider that deliberately omits bind_tools to test the fallback path."""

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self._invoked_with: list[Any] | None = None

    def invoke(self, messages: list[Any]) -> Any:
        self._invoked_with = messages
        result = MagicMock()
        result.content = "no-bind-tools response"
        result.usage_metadata = {"input_tokens": 5, "output_tokens": 3}
        return result


class _FakeProviderRegistry:
    """Returns the requested fake provider class."""

    def __init__(self, provider_cls: type) -> None:
        self._cls = provider_cls

    def is_installed(self, provider_name: str) -> bool:
        return True

    def install_provider(self, provider_name: str) -> None:
        pass

    def get_provider_class(self, provider_name: str) -> type:
        return self._cls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "echo",
    "description": "Echo the input back.",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}

_MESSAGES: list[dict[str, str]] = [{"role": "user", "content": "hello"}]


def _make_gateway(provider_cls: type) -> tuple[ModelGateway, ModelProfile]:
    profile = ModelProfile(
        model_profile_id="test-profile",
        provider="openai",
        model_name="gpt-4o-mini",
        enabled=True,
        api_metered=False,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
    )
    registry = _FakeProviderRegistry(provider_cls)
    gw = ModelGateway(
        profiles=[profile],
        provider_registry=registry,
    )
    return gw, profile


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBindToolsFix:
    """Verify the bind_tools fix in ModelGateway._invoke_and_bill."""

    def test_bind_tools_called_when_tools_present(self) -> None:
        """bind_tools() is called on the chat model when tools= is non-empty."""
        gw, _ = _make_gateway(_FakeChatModel)

        # Track which instance was constructed by the provider registry.
        constructed: list[_FakeChatModel] = []

        class _Tracking(_FakeChatModel):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                constructed.append(self)

        gw._registry = _FakeProviderRegistry(_Tracking)

        response = gw.call_model(
            "test-profile",
            _MESSAGES,
            tools=[_SIMPLE_TOOL_SCHEMA],
        )

        assert response.content == "fake model response"
        assert len(constructed) == 1

        base_model = constructed[0]

        # bind_tools must have been called on the base model.
        assert hasattr(base_model, "_bind_tools_result"), "bind_tools() was never called on the constructed chat model"
        bound_model = base_model._bind_tools_result

        # The bound model must have seen the right tool schema.
        assert bound_model._bound_tools == [_SIMPLE_TOOL_SCHEMA], f"bound_tools mismatch: {bound_model._bound_tools!r}"

        # The bound model must have been invoked (not the original).
        assert bound_model._invoked_with is not None, "invoke() was not called on the bind_tools result"
        assert base_model._invoked_with is None, "invoke() was called on the BASE model instead of the bound result"

    def test_tools_not_forwarded_to_constructor(self) -> None:
        """tools= must NOT appear in the provider constructor kwargs."""
        constructed_kwargs: list[dict[str, Any]] = []

        class _Recording(_FakeChatModel):
            def __init__(self, **kwargs: Any) -> None:
                constructed_kwargs.append(dict(kwargs))
                super().__init__(**kwargs)

        gw, _ = _make_gateway(_Recording)
        gw._registry = _FakeProviderRegistry(_Recording)

        gw.call_model(
            "test-profile",
            _MESSAGES,
            tools=[_SIMPLE_TOOL_SCHEMA],
        )

        assert constructed_kwargs, "Provider constructor was never called"
        for ck in constructed_kwargs:
            assert "tools" not in ck, f"'tools' was forwarded to provider constructor kwargs: {ck}"

    def test_no_tools_path_unchanged(self) -> None:
        """When no tools= arg is given, bind_tools is never called."""
        gw, _ = _make_gateway(_FakeChatModel)
        constructed: list[_FakeChatModel] = []

        class _Tracking(_FakeChatModel):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                constructed.append(self)

        gw._registry = _FakeProviderRegistry(_Tracking)

        response = gw.call_model("test-profile", _MESSAGES)

        assert response.content == "fake model response"
        assert len(constructed) == 1
        base_model = constructed[0]

        # bind_tools should NOT have been called.
        assert not hasattr(base_model, "_bind_tools_result"), (
            "bind_tools() was unexpectedly called when no tools= was passed"
        )

        # The base model itself must have been invoked directly.
        assert base_model._invoked_with is not None, "invoke() was not called on the chat model when tools= absent"

    def test_empty_tools_list_skips_bind(self) -> None:
        """tools=[] (empty list) is falsy — bind_tools must not be called."""
        gw, _ = _make_gateway(_FakeChatModel)
        constructed: list[_FakeChatModel] = []

        class _Tracking(_FakeChatModel):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                constructed.append(self)

        gw._registry = _FakeProviderRegistry(_Tracking)

        response = gw.call_model("test-profile", _MESSAGES, tools=[])

        assert response.content == "fake model response"
        base_model = constructed[0]
        assert not hasattr(base_model, "_bind_tools_result"), "bind_tools() was called for an empty tools list"

    def test_no_bind_tools_support_fallback(self) -> None:
        """Provider without bind_tools falls back to plain invoke (no AttributeError)."""
        gw, _ = _make_gateway(_FakeChatModelNoBindTools)
        gw._registry = _FakeProviderRegistry(_FakeChatModelNoBindTools)

        # Should NOT raise — falls back to plain invoke with a logged warning.
        response = gw.call_model(
            "test-profile",
            _MESSAGES,
            tools=[_SIMPLE_TOOL_SCHEMA],
        )

        assert response.content == "no-bind-tools response"

    def test_bind_tools_multiple_tools(self) -> None:
        """bind_tools receives all tools when multiple schemas are passed."""
        gw, _ = _make_gateway(_FakeChatModel)
        constructed: list[_FakeChatModel] = []

        class _Tracking(_FakeChatModel):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                constructed.append(self)

        gw._registry = _FakeProviderRegistry(_Tracking)

        tool_a = dict(_SIMPLE_TOOL_SCHEMA, name="tool_a")
        tool_b = dict(_SIMPLE_TOOL_SCHEMA, name="tool_b")

        gw.call_model("test-profile", _MESSAGES, tools=[tool_a, tool_b])

        bound = constructed[0]._bind_tools_result
        assert bound._bound_tools == [tool_a, tool_b]

    def test_billing_still_works_with_tools(self) -> None:
        """Cost/billing path is unaffected when tools are bound."""
        profile = ModelProfile(
            model_profile_id="billing-profile",
            provider="openai",
            model_name="gpt-4o-mini",
            enabled=True,
            cost_per_input_token=0.001,
            cost_per_output_token=0.002,
        )
        registry = _FakeProviderRegistry(_FakeChatModel)
        budget_guard = MagicMock()

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=registry,
            budget_guard=budget_guard,
            # Peak-time billing clock (multiplier 1.0) keeps the cost pin
            # deterministic against peak/off-peak pricing variance.
            billing_clock=lambda: datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC),
        )

        response = gw.call_model(
            "billing-profile",
            _MESSAGES,
            tools=[_SIMPLE_TOOL_SCHEMA],
        )

        # response.content comes from the fake model (non-empty → billed)
        assert response.content == "fake model response"
        # budget_guard.record_spend must have been called with a positive cost
        budget_guard.record_spend.assert_called_once()
        recorded_cost = budget_guard.record_spend.call_args[0][0]
        # 10 * 0.001 + 5 * 0.002 = 0.01 + 0.01 = 0.02
        assert recorded_cost == pytest.approx(0.02)
