"""Unit tests for enhanced model gateway and model router."""

from __future__ import annotations

import contextlib
import datetime
import logging
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import (
    ModelGateway,
    ModelProfile,
    ModelResponse,
)
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.router import ModelRouter
from general_ludd.secrets.manager import SecretAlias, SecretsManager

# Historical catalog rates converted from USD per 1,000 tokens to per-token rates.
_GPT_4_INPUT_COST_PER_TOKEN = 0.00003
_GPT_4_OUTPUT_COST_PER_TOKEN = 0.00006
_GPT_35_INPUT_COST_PER_TOKEN = 0.0000005
_GPT_35_OUTPUT_COST_PER_TOKEN = 0.0000015


def _make_fake_chat_model():
    FakeChatModel = MagicMock()
    fake_instance = MagicMock()
    FakeChatModel.return_value = fake_instance
    return FakeChatModel, fake_instance


class TestModelProfileEnhanced:
    def test_profile_has_new_fields(self):
        p = ModelProfile(
            model_profile_id="test_enhanced",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            roles=["coder", "reviewer"],
            latency_class="fast",
            quality_class="high",
            fallback_profiles=["openrouter_coder"],
            probe_enabled=True,
        )
        assert p.provider_package == "langchain-openai"
        assert p.provider_class_hint == "ChatOpenAI"
        assert p.roles == ["coder", "reviewer"]
        assert p.latency_class == "fast"
        assert p.quality_class == "high"
        assert p.fallback_profiles == ["openrouter_coder"]
        assert p.probe_enabled is True

    def test_profile_defaults(self):
        p = ModelProfile(model_profile_id="defaults_test")
        assert p.roles == []
        assert p.fallback_profiles == []
        assert p.probe_enabled is False
        assert p.latency_class is None
        assert p.quality_class is None


class TestModelGatewayCallModelWithStub:
    def _make_gateway(self) -> tuple[ModelGateway, ProviderRegistry, SecretsManager]:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "sk-test-key"}}}
        secrets = SecretsManager(
            client=fake_secret_client,
            aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
        )

        profile = ModelProfile(
            model_profile_id="gpt4",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            credential_alias="openai_key",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )
        return gw, reg, secrets

    def test_call_model_returns_response(self):
        gw, reg, _ = self._make_gateway()

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="Hello!",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model("gpt4", [{"role": "user", "content": "hi"}])

        assert isinstance(resp, ModelResponse)
        assert resp.content == "Hello!"
        assert resp.usage_metadata["input_tokens"] == 10

    def test_call_model_raises_for_missing_profile(self):
        gw = ModelGateway()
        with pytest.raises(ValueError, match="not found"):
            gw.call_model("nonexistent", [])

    def test_call_model_rejects_over_budget(self):
        gw, reg, _ = self._make_gateway()
        with (
            patch.object(reg, "is_installed", return_value=True),
            pytest.raises(ValueError, match="budget"),
        ):
            gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                estimated_cost=999.0,
                budget_remaining=1.0,
            )

    def test_call_model_creates_dep_todo_for_missing_provider(self):
        gw, reg, _ = self._make_gateway()

        with (
            patch.object(reg, "is_installed", return_value=False),
            patch.object(reg, "install_provider", return_value=MagicMock()) as mock_install,
            pytest.raises(ImportError, match="not installed"),
        ):
            gw.call_model("gpt4", [{"role": "user", "content": "hi"}])
        mock_install.assert_called_once_with("openai")


def _capture_provider(recorder: dict):
    """Provider class stand-in that records the ctor kwargs into ``recorder``.

    Lets a test assert exactly what ``_invoke_and_bill`` forwards to
    ``provider_cls(**init_kwargs)`` (e.g. that a caller base_url/api_key never
    reaches the constructor, or that a validated one does).
    """

    def factory(**kwargs):
        recorder.clear()
        recorder.update(kwargs)
        instance = MagicMock()
        instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
            tool_calls=[],
        )
        instance.bind_tools.return_value = instance
        return instance

    return factory


class TestCallerBaseUrlSSRFGuard:
    """Defense-in-depth: a caller-supplied base_url/api_key in **kwargs must not
    bypass the alias path's is_safe_fetch_url egress guard (it used to, because
    ``init_kwargs.update(kwargs)`` ran AFTER the validated alias base_url was set).
    """

    def _make_gateway(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-alias-key"}}
        }
        secrets = SecretsManager(
            client=fake_secret_client,
            aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
        )
        profile = ModelProfile(
            model_profile_id="gpt4",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            credential_alias="openai_key",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )
        return gw, reg

    def test_caller_base_url_metadata_ip_is_ignored(self):
        """C6 hardening: caller base_url (even unsafe metadata IP) is DENIED
        outright rather than raising SSRFRejectionError. The caller's base_url
        is silently ignored; the profile's api_base_alias wins."""
        gw, reg = self._make_gateway()
        captured: dict = {}
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=_capture_provider(captured)),
        ):
            resp = gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                base_url="http://169.254.169.254/latest/meta-data/",
            )
        # Call succeeds normally — the caller's base_url never reaches the provider.
        assert isinstance(resp, ModelResponse)
        assert resp.content == "ok"
        assert "base_url" not in captured, f"caller base_url leaked to provider: {captured.get('base_url')}"

    def test_caller_base_url_safe_https_is_ignored(self):
        """A caller base_url is now DENIED outright (C6 hardening) — it must
        NEVER override the alias-resolved base_url, even if it passes the
        SSRF guard."""
        gw, reg = self._make_gateway()
        captured: dict = {}
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=_capture_provider(captured)),
        ):
            resp = gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                base_url="https://api.example.com",
            )
        assert isinstance(resp, ModelResponse)
        # Caller base_url must NOT reach the provider constructor — it is now denied.
        assert "base_url" not in captured, f"caller base_url leaked to provider: {captured.get('base_url')}"

    def test_caller_api_key_is_ignored_alias_credential_wins(self):
        """A caller-injected api_key must never override the validated secrets
        alias credential; it is dropped and the alias key reaches the ctor."""
        gw, reg = self._make_gateway()
        captured: dict = {}
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=_capture_provider(captured)),
        ):
            gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                api_key="caller-injected-evil-key",  # pragma: allowlist secret
            )
        assert captured["api_key"] == "sk-alias-key"  # pragma: allowlist secret

    def test_normal_kwargs_still_work_regression(self):
        """tools + work_type are still popped/handled correctly and never leak to
        the provider constructor (the fix must not disturb existing kwargs)."""
        gw, reg = self._make_gateway()
        captured: dict = {}
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=_capture_provider(captured)),
        ):
            resp = gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                tools=[{"name": "read_file", "description": "", "input_schema": {}}],
                work_type="coding",
            )
        assert isinstance(resp, ModelResponse)
        assert resp.content == "ok"
        assert "tools" not in captured
        assert "work_type" not in captured
        assert "base_url" not in captured


class _AIMessageLike:
    """Minimal stand-in for a langchain_openai AIMessage.

    A real ChatOpenAI.invoke() returns an AIMessage whose ``.tool_calls`` is the
    LangChain FLAT shape: ``[{"name", "args": dict, "id", "type": "tool_call"}]``.
    We replicate exactly that here so the test drives the REAL production
    extraction path (gateway._extract_tool_calls reads raw_response.tool_calls),
    NOT a preset ModelResponse.tool_calls. Using an explicit class (not MagicMock)
    is deliberate: a MagicMock would auto-create a truthy ``.tool_calls`` and mask
    whether extraction actually works.
    """

    def __init__(self, content, usage_metadata, tool_calls=None):
        self.content = content
        self.usage_metadata = usage_metadata
        self.tool_calls = tool_calls or []


class _BindableChatModel:
    """Fake ChatOpenAI: supports bind_tools() (returns self) + scripted invoke."""

    def __init__(self, scripted_response):
        self._scripted = scripted_response
        self.bound_tools = None

    def bind_tools(self, tools):
        # Real bind_tools returns a new runnable; the gateway reassigns
        # chat_model = chat_model.bind_tools(tools). Return self for chaining.
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        return self._scripted


class TestToolCallsReachToolLoopProductionPath:
    """Pins the previously-dead MCP tool-call loop.

    Before the fix, ModelResponse had no ``tool_calls`` field, so
    _invoke_and_bill dropped the provider's tool calls and ToolCallLoop's
    ``getattr(response, "tool_calls", None)`` was ALWAYS None — no tool was ever
    dispatched in production. These tests exercise the real gateway invoke path
    (provider AIMessage -> _extract_tool_calls -> ModelResponse.tool_calls) and
    then the real ToolCallLoop, asserting a tool is actually dispatched.
    """

    def _make_gateway(self, scripted_response):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="gpt4_tools",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)
        fake = _BindableChatModel(scripted_response)
        return gw, reg, fake

    def test_langchain_flat_tool_calls_populate_model_response(self):
        """A LangChain AIMessage's flat tool_calls become ModelResponse.tool_calls
        normalized into the nested OpenAI shape the loop reads."""
        msg = _AIMessageLike(
            content="",  # tool-call turns may have empty text content...
            usage_metadata={"input_tokens": 5, "output_tokens": 2},
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "/etc/hosts"},
                    "id": "call_abc",
                    "type": "tool_call",
                }
            ],
        )
        gw, reg, fake = self._make_gateway(msg)

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=lambda **kw: fake),
        ):
            resp = gw.call_model(
                "gpt4_tools",
                [{"role": "user", "content": "read it"}],
                tools=[{"name": "read_file", "description": "", "input_schema": {}}],
            )

        # bind_tools was actually exercised (production tool-binding path).
        assert fake.bound_tools is not None
        # The dead-loop bug: this list used to never exist. Now it is populated
        # and normalized to the nested shape the loop consumes.
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc["id"] == "call_abc"
        assert tc["function"]["name"] == "read_file"
        # arguments is a JSON STRING (the loop json.loads() it).
        import json as _json

        assert _json.loads(tc["function"]["arguments"]) == {"path": "/etc/hosts"}

    def test_no_tool_calls_yields_none(self):
        """A plain text answer (no tool calls) leaves tool_calls falsy so the loop
        returns content instead of looping forever."""
        msg = _AIMessageLike(
            content="final answer",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
            tool_calls=[],
        )
        gw, reg, fake = self._make_gateway(msg)
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=lambda **kw: fake),
        ):
            resp = gw.call_model(
                "gpt4_tools",
                [{"role": "user", "content": "hi"}],
                tools=[{"name": "read_file", "description": "", "input_schema": {}}],
            )
        assert not resp.tool_calls
        assert resp.content == "final answer"

    def test_tool_loop_actually_dispatches_via_real_gateway(self):
        """END-TO-END: a real ToolCallLoop + real gateway invoke dispatches the
        model's tool call to the MCP client, then returns the final answer.

        This is the test that would have FAILED before the fix: the first
        gateway turn carries tool_calls, the loop must dispatch read_file to the
        MCP client, feed the result back, and return the second turn's content.
        """
        import asyncio

        from general_ludd.execution.tool_loop import ToolCallLoop
        from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
        from general_ludd.schemas.job import JobSpec

        # Turn 1: model asks to call read_file. Turn 2: model gives final answer.
        turn1 = _AIMessageLike(
            content="",
            usage_metadata={"input_tokens": 5, "output_tokens": 2},
            tool_calls=[
                {"name": "read_file", "args": {"path": "/x"}, "id": "c1", "type": "tool_call"},
            ],
        )
        turn2 = _AIMessageLike(
            content="done: contents were FOO",
            usage_metadata={"input_tokens": 5, "output_tokens": 2},
            tool_calls=[],
        )

        responses = [turn1, turn2]

        class _ScriptedChatModel:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                return responses.pop(0)

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="default",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)

        # MCP client + registry advertising read_file on server "fs".
        mcp_reg = MCPToolRegistry()
        mcp_reg.register_tool("fs", MCPTool(name="read_file"))

        dispatched: list[tuple] = []

        class _FakeMCPClient:
            _registry = mcp_reg

            async def list_tools(self):
                return [MCPTool(name="read_file", server_id="fs")]

            async def call_tool(self, server_id, name, args):
                dispatched.append((server_id, name, args))
                return "FOO"

        loop = ToolCallLoop(
            model_gateway=gw,
            mcp_client=_FakeMCPClient(),
            mcp_registry=mcp_reg,
        )

        job = JobSpec(
            job_id="j1",
            playbook="p",
            queue="q",
            model_profile="default",
        )

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(
                reg,
                "get_provider_class",
                return_value=lambda **kw: _ScriptedChatModel(),
            ),
        ):
            result = asyncio.run(loop.run_with_tools(job, "system", "please read /x"))

        # The tool WAS dispatched (the dead-loop bug = this list stays empty).
        assert dispatched == [("fs", "read_file", {"path": "/x"})]
        assert result == "done: contents were FOO"


class TestModelGatewayRecordsUsage:
    def test_usage_metadata_recorded(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "sk-test-key"}}}
        secrets = SecretsManager(
            client=fake_secret_client,
            aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
        )

        profile = ModelProfile(
            model_profile_id="gpt4_usage",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            credential_alias="openai_key",
            cost_per_input_token=0.01,
            cost_per_output_token=0.03,
            # Server-side budget re-estimation (D-21) prices the call at up to
            # max_output_tokens (8000) * cost_per_output_token (0.03) = ~240 USD,
            # so run_budget_usd must comfortably exceed that or the budget gate
            # (gateway.py check_budget, api_metered defaults True) rejects the
            # call before usage metadata can be recorded.
            run_budget_usd=10000.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
            # Peak-time billing clock (multiplier 1.0) keeps the usage-cost
            # pin deterministic against peak/off-peak pricing variance.
            billing_clock=lambda: datetime.datetime(2026, 8, 3, 12, 0, 0, tzinfo=datetime.UTC),
        )

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="result",
            usage_metadata={"input_tokens": 100, "output_tokens": 50},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model("gpt4_usage", [{"role": "user", "content": "hi"}])

        expected_cost = 100 * 0.01 + 50 * 0.03
        assert resp.cost_estimate == pytest.approx(expected_cost)


class TestModelGatewayRedactsCredentials:
    def test_credentials_redacted_in_logs(self, caplog):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-super-secret-key-12345"}}
        }
        secrets = SecretsManager(
            client=fake_secret_client,
            aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
        )

        profile = ModelProfile(
            model_profile_id="gpt4_redact",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            credential_alias="openai_key",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        FakeChatModel.return_value = fake_instance

        with (
            caplog.at_level(logging.DEBUG, logger="general_ludd.models.gateway"),
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            gw.call_model("gpt4_redact", [{"role": "user", "content": "hi"}])

        for record in caplog.records:
            assert "sk-super-secret-key-12345" not in record.getMessage()


class TestCallModelByRoleStrictMode:
    """M-3: unknown role must raise ValueError, not silently fall through."""

    def _make_gateway_with_router(self) -> ModelGateway:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="gpt4_role",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )
        router = ModelRouter(
            role_mapping={"coder": "gpt4_role"},
            default_profile_id="gpt4_role",
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)
        gw._router = router
        return gw

    def test_unknown_role_raises_value_error(self):
        """An unrecognised role must raise ValueError (fail-closed)."""
        gw = self._make_gateway_with_router()
        with pytest.raises(ValueError, match="Unrecognised role"):
            gw.call_model_by_role(
                "nonexistent_role",
                [{"role": "user", "content": "hi"}],
            )

    def test_known_role_resolves_without_error(self):
        """A mapped role must resolve normally (smoke-check that strict only
        blocks *unknown* roles)."""
        gw = self._make_gateway_with_router()
        reg = gw._registry

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model_by_role(
                "coder",
                [{"role": "user", "content": "hi"}],
            )
        assert resp.content == "ok"


class TestFallbackRecordSuccessOnce:
    """Fix B: a successful fallback call must record_success exactly once."""

    def _make_gateway_with_fallback(self) -> tuple[ModelGateway, ProviderRegistry, MagicMock]:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        primary = ModelProfile(
            model_profile_id="primary",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
            fallback_profiles=["fallback_model"],
        )
        fallback = ModelProfile(
            model_profile_id="fallback_model",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-3.5-turbo",
            cost_per_input_token=_GPT_35_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_35_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )

        mock_tracker = MagicMock()
        mock_tracker.is_healthy.return_value = True

        gw = ModelGateway(
            profiles=[primary, fallback],
            provider_registry=reg,
            health_tracker=mock_tracker,
        )
        return gw, reg, mock_tracker

    def test_successful_fallback_records_success_exactly_once(self):
        """record_success must be called exactly once for the fallback profile
        (via _invoke_and_bill); the old extra call in _call_fallback would make
        it two."""
        gw, reg, mock_tracker = self._make_gateway_with_fallback()

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="fallback reply",
            usage_metadata={"input_tokens": 5, "output_tokens": 3},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw._call_fallback(
                "fallback_model",
                [{"role": "user", "content": "hi"}],
            )

        assert resp.content == "fallback reply"
        # record_success must be called exactly once (from _invoke_and_bill),
        # NOT twice (the old bug added a second call in _call_fallback itself).
        success_calls = [
            c for c in mock_tracker.record_success.call_args_list if c.args and c.args[0] == "fallback_model"
        ]
        assert len(success_calls) == 1, (
            f"record_success('fallback_model') called {len(success_calls)} times, expected exactly 1"
        )


class TestModelRouter:
    def test_resolve_role_returns_profile_id(self):
        router = ModelRouter(role_mapping={"coder": "gpt4", "reviewer": "claude3"})
        assert router.resolve_role("coder") == "gpt4"
        assert router.resolve_role("reviewer") == "claude3"

    def test_unmapped_role_returns_none(self):
        router = ModelRouter(role_mapping={"coder": "gpt4"})
        assert router.resolve_role("unknown") is None

    def test_add_role_mapping(self):
        router = ModelRouter()
        router.add_role("planner", "gpt4")
        assert router.resolve_role("planner") == "gpt4"

    def test_list_roles(self):
        router = ModelRouter(role_mapping={"coder": "gpt4", "reviewer": "claude3"})
        roles = router.list_roles()
        assert "coder" in roles
        assert "reviewer" in roles


class TestLocalModelProfileIgnoredUnlessEnabled:
    def test_local_model_disabled(self):
        gw = ModelGateway(
            [
                ModelProfile(
                    model_profile_id="local_llm",
                    enabled=False,
                    api_metered=False,
                    resource_profile="local_heavy",
                )
            ]
        )
        assert gw.is_available("local_llm") is False

    def test_local_model_enabled(self):
        gw = ModelGateway(
            [
                ModelProfile(
                    model_profile_id="local_llm",
                    enabled=True,
                    api_metered=False,
                    resource_profile="local_heavy",
                )
            ]
        )
        assert gw.is_available("local_llm") is True


class TestExampleConfigsAreValidYaml:
    def test_example_configs_are_valid_yaml(self):
        import os

        import yaml

        config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "config", "model_profiles")
        config_dir = os.path.normpath(config_dir)
        if not os.path.isdir(config_dir):
            pytest.skip("config/model_profiles directory not found")

        files = [f for f in os.listdir(config_dir) if f.endswith((".yml", ".yaml"))]
        assert len(files) > 0, "No YAML config files found"

        for fname in files:
            with open(os.path.join(config_dir, fname)) as fh:
                data = yaml.safe_load(fh)
            assert isinstance(data, dict), f"{fname} did not parse to a dict"
            assert "model_profile_id" in data, f"{fname} missing model_profile_id"
            assert "provider" in data, f"{fname} missing provider"
            assert "provider_package" in data, f"{fname} missing provider_package"


class TestCheckBudgetServerSideReEstimation:
    """D-21 regression: call_model must pass messages= to check_budget so the
    server-side cost re-estimator fires even when estimated_cost=0.0."""

    def _make_gateway(self, run_budget_usd: float = 0.001) -> tuple:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="budget_probe",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            api_metered=True,
            run_budget_usd=run_budget_usd,
            # High per-token prices so even a short prompt exceeds the tiny budget.
            cost_per_input_token=1.0,
            cost_per_output_token=1.0,
            max_output_tokens=8192,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)
        return gw, reg

    def test_zero_estimated_cost_large_prompt_raises_budget_exceeded(self):
        """A caller that passes estimated_cost=0.0 with a huge prompt must still
        be rejected when the server-side re-estimate exceeds run_budget_usd.

        Before the fix (gateway.py:346 missing messages=), check_budget received
        no messages, fell back to estimated_cost=0.0, and silently let the call
        through. After the fix the server-side estimate is computed and the gate
        closes correctly.
        """
        from general_ludd.models.gateway import BudgetExceededError

        gw, _reg = self._make_gateway(run_budget_usd=0.001)

        # 4000-char message → ~1000 input tokens x cost_per_input_token=1.0
        # plus max_output_tokens=8192 x 1.0 = ~9192 USD >> 0.001 USD budget.
        large_messages = [{"role": "user", "content": "x" * 4000}]

        with pytest.raises(BudgetExceededError):
            gw.call_model(
                "budget_probe",
                large_messages,
                estimated_cost=0.0,  # caller under-reports: should not matter
                budget_remaining=float("inf"),
            )


class TestRequestedMaxOutputTokensBudgetEstimate:
    """D-21 over-conservatism fix: requested_max_output_tokens lets a call that
    caps its output be estimated at its real (smaller) cost instead of the
    worst-case profile.max_output_tokens — without weakening the under-report
    protection (the estimate is still min()'d against the profile cap).

    Without the param the worst-case estimate is used (the existing D-21
    rejection behaviour is preserved, asserted in
    TestCheckBudgetServerSideReEstimation above).
    """

    def _make_gateway(self, run_budget_usd: float = 0.1) -> tuple:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="lowbudget",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            api_metered=True,
            run_budget_usd=run_budget_usd,
            # Worst-case output leg = 8000 * 0.00006 = 0.48 USD > 0.1 budget, so a
            # call estimated at max_output_tokens is rejected. A call that caps
            # output at 50 tokens estimates ~0.003 USD < 0.1 → allowed.
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            max_output_tokens=8000,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)
        return gw, reg

    def test_check_budget_rejects_without_requested_cap(self):
        """No requested cap → worst-case max_output_tokens estimate → rejected
        against a finite remaining budget (D-21 security behaviour preserved).
        budget_remaining=0.001 ensures the rejection path actually executes
        rather than being skipped by inf → no-cost-exceeds-inf semantics."""
        gw, _reg = self._make_gateway(run_budget_usd=0.1)
        msg = [{"role": "user", "content": "hi"}]
        # 8000 * 0.00006 = 0.48 USD output leg > 0.001 remaining → rejected.
        assert (
            gw.check_budget(
                "lowbudget",
                estimated_cost=0.0,
                budget_remaining=0.001,
                messages=msg,
            )
            is False
        )

    def test_check_budget_allows_with_small_requested_cap(self):
        """A small requested_max_output_tokens → realistic small estimate →
        ALLOWED against the same modest run_budget_usd that rejected the
        worst-case estimate."""
        gw, _reg = self._make_gateway(run_budget_usd=0.1)
        msg = [{"role": "user", "content": "hi"}]
        # 50 * 0.00006 = 0.003 USD output leg < 0.1 budget → allowed.
        assert (
            gw.check_budget(
                "lowbudget",
                estimated_cost=0.0,
                budget_remaining=float("inf"),
                messages=msg,
                requested_max_output_tokens=50,
            )
            is True
        )

    def test_requested_cap_cannot_exceed_profile_max(self):
        """Security: requesting MORE than the profile cap cannot inflate the
        estimate below the profile's real worst case — it is min()'d to the
        profile max, so a giant requested value yields the same worst-case
        (rejecting) estimate as no cap at all."""
        gw, _reg = self._make_gateway(run_budget_usd=0.1)
        msg = [{"role": "user", "content": "hi"}]
        # Request 10x the profile cap; estimate uses min(80000, 8000)=8000 →
        # 0.48 USD > 0.1 → still rejected (cannot bypass the gate upward).
        assert (
            gw.check_budget(
                "lowbudget",
                estimated_cost=0.0,
                budget_remaining=float("inf"),
                messages=msg,
                requested_max_output_tokens=80_000,
            )
            is False
        )

    def test_estimate_cost_uses_min_of_requested_and_profile_max(self):
        """Unit-level: estimate_cost output leg = min(requested, profile max)."""
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(
            model_profile_id="p",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            max_output_tokens=8000,
            enabled=True,
        )
        msg = [{"role": "user", "content": "hi"}]
        # No cap → worst case 8000 * 0.00006 = 0.48
        assert ModelGateway.estimate_cost(profile, msg) == pytest.approx(0.48)
        # Small cap → 50 * 0.00006 = 0.003
        assert ModelGateway.estimate_cost(profile, msg, requested_max_output_tokens=50) == pytest.approx(0.003)
        # Cap above profile max → min()'d to 8000 → 0.48 (unchanged worst case)
        assert ModelGateway.estimate_cost(profile, msg, requested_max_output_tokens=99_999) == pytest.approx(0.48)
        # Non-positive cap is ignored → worst case (defensive backward-compat)
        assert ModelGateway.estimate_cost(profile, msg, requested_max_output_tokens=0) == pytest.approx(0.48)

    def test_call_model_allows_capped_call_on_low_budget(self):
        """End-to-end through call_model: a call that would be rejected under the
        worst-case estimate succeeds when it passes a small
        requested_max_output_tokens, on a low run_budget_usd deployment."""
        from unittest.mock import MagicMock, patch

        gw, reg = self._make_gateway(run_budget_usd=0.1)
        msg = [{"role": "user", "content": "hi"}]

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            # Worst-case (no cap) would raise BudgetExceededError; with the cap
            # the budget gate passes and the call returns normally.
            resp = gw.call_model(
                "lowbudget",
                msg,
                requested_max_output_tokens=50,
            )
        assert resp.content == "ok"

    def test_call_model_rejects_uncapped_call_on_low_budget(self):
        """The inverse: same low-budget profile, NO requested cap → the
        worst-case estimate rejects (D-21 security still enforced)."""
        from general_ludd.models.gateway import BudgetExceededError

        gw, _reg = self._make_gateway(run_budget_usd=0.1)
        msg = [{"role": "user", "content": "hi"}]
        with pytest.raises(BudgetExceededError):
            gw.call_model("lowbudget", msg)


class TestCacheKeyLockEviction:
    """Bug regression: _cache_key_locks dict must not grow unboundedly.

    Before the fix, every distinct cache key accumulated a Lock entry in
    _cache_key_locks forever (the lock was created on first miss and never
    removed). Under any workload that naturally cycles through many keys the
    dict becomes an unbounded heap leak. The fix introduces ref-counting:
    the entry is deleted in a finally block when the last waiter for that
    key releases, keeping dict size bounded to the number of *in-flight*
    waiters rather than the number of distinct keys ever seen.
    """

    def _make_gateway_with_cache(self):

        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(
            model_profile_id="p",
            enabled=True,
            provider="openai",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )

        # Minimal dict-backed cache stub.
        _store: dict = {}

        class _DictCache:
            def get(self, key):
                return _store.get(key)

            def set(self, key, value, ttl=None, expire=None, **kwargs):
                _store[key] = value

        return ModelGateway(profiles=[profile], response_cache=_DictCache()), _store

    def test_lock_dict_evicted_after_single_flight(self):
        """After a single-flight section completes the lock entry is removed."""
        from unittest.mock import MagicMock, patch

        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="evict_test",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )
        _store: dict = {}

        class _DictCache:
            def get(self, key):
                return _store.get(key)

            def set(self, key, value, ttl=None, expire=None, **kwargs):
                _store[key] = value

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            response_cache=_DictCache(),
        )

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            gw.call_model("evict_test", [{"role": "user", "content": "hi"}])

        # After the call completes the lock dict must be empty — the entry was
        # evicted in the finally block when the ref-count dropped to zero.
        assert len(gw._cache_key_locks) == 0, (
            f"expected 0 lock entries after call, got {len(gw._cache_key_locks)}: {list(gw._cache_key_locks.keys())}"
        )
        assert len(gw._cache_key_lock_refs) == 0

    def test_lock_dict_stays_bounded_across_many_keys(self):
        """Dict size stays at 0 between calls regardless of how many distinct keys
        were used — not bounded to a small constant but provably zero (no residue)."""
        from unittest.mock import MagicMock, patch

        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        _store: dict = {}

        class _DictCache:
            def get(self, key):
                return _store.get(key)

            def set(self, key, value, ttl=None, expire=None, **kwargs):
                _store[key] = value

        profiles = []
        for i in range(20):
            profiles.append(
                ModelProfile(
                    model_profile_id=f"p{i}",
                    enabled=True,
                    provider="openai",
                    provider_package="langchain-openai",
                    provider_class_hint="ChatOpenAI",
                    model_name="gpt-4",
                    cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
                    cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
                    run_budget_usd=100.0,
                )
            )
        gw = ModelGateway(
            profiles=profiles,
            provider_registry=reg,
            response_cache=_DictCache(),
        )

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            # Drive 20 distinct profile/message combinations → 20 distinct cache keys.
            for i in range(20):
                gw.call_model(
                    f"p{i}",
                    [{"role": "user", "content": f"msg{i}"}],
                )

        # Every entry must have been evicted — zero residue after all calls.
        assert len(gw._cache_key_locks) == 0, (
            f"lock dict leaked {len(gw._cache_key_locks)} entries: {list(gw._cache_key_locks.keys())[:5]}"
        )
        assert len(gw._cache_key_lock_refs) == 0


class TestRetryBackoffCumulativeCap:
    """Bug regression: _before_sleep must not sleep more than _MAX_CUMULATIVE_SLEEP_S
    total wall-clock seconds across all retry attempts.

    Before the fix, each attempt could sleep up to overload_max_backoff_seconds=120s
    and with up to 10 overload retries the worst-case cumulative sleep was 1200s
    (~20 min), blocking the calling thread for an absurd amount of time.
    The fix tracks _cumulative_sleep_s and truncates / skips sleep once the
    300s cap is reached.
    """

    def test_cumulative_sleep_is_capped(self):
        """Total sleep across retries must not exceed _MAX_CUMULATIVE_SLEEP_S."""
        from unittest.mock import MagicMock, patch

        import httpx

        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="retry_cap",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)

        # Build a 503 that will be retried as PROVIDER_ERROR (overload kind).
        request = httpx.Request("POST", "https://provider.invalid/v1/chat")
        response = httpx.Response(503, request=request)
        exc_503 = httpx.HTTPStatusError("503", request=request, response=response)

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        # Always raises a retryable 503.
        fake_instance.invoke.side_effect = exc_503
        FakeChatModel.return_value = fake_instance

        slept: list[float] = []

        async def _fake_sleep(s: float) -> None:
            slept.append(s)

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch("general_ludd.models.gateway.ModelGateway.call_model_with_retry.__wrapped__", create=True),
            patch("general_ludd.models.gateway.asyncio.sleep", side_effect=_fake_sleep),
            contextlib.suppress(Exception),
        ):
            gw.call_model_with_retry(
                "retry_cap",
                [{"role": "user", "content": "hi"}],
                max_retries=3,
            )

        total_slept = sum(slept)
        assert total_slept <= 300.0 + 1e-6, (
            f"cumulative sleep {total_slept:.1f}s exceeded 300s cap (individual sleeps: {slept})"
        )

    def test_no_sleep_once_cap_reached(self):
        """Once the cumulative cap is hit subsequent retries sleep 0 seconds."""
        from unittest.mock import patch

        import httpx

        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="retry_cap2",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=_GPT_4_INPUT_COST_PER_TOKEN,
            cost_per_output_token=_GPT_4_OUTPUT_COST_PER_TOKEN,
            run_budget_usd=100.0,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)

        request = httpx.Request("POST", "https://provider.invalid/v1/chat")
        response = httpx.Response(429, request=request)
        exc_429 = httpx.HTTPStatusError("429", request=request, response=response)

        from unittest.mock import MagicMock

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.side_effect = exc_429
        FakeChatModel.return_value = fake_instance

        slept: list[float] = []

        async def _fake_sleep(s: float) -> None:
            slept.append(s)

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch("general_ludd.models.gateway.asyncio.sleep", side_effect=_fake_sleep),
            contextlib.suppress(Exception),
        ):
            gw.call_model_with_retry(
                "retry_cap2",
                [{"role": "user", "content": "hi"}],
                max_retries=3,
            )

        total_slept = sum(slept)
        # Hard cap: never exceed 300s regardless of per-attempt amounts.
        assert total_slept <= 300.0 + 1e-6, f"total sleep {total_slept:.1f}s exceeded cap; individual: {slept}"
        # At least one sleep must have been truncated: if any individual sleep
        # after the cap was exceeded was nonzero it was supposed to be skipped.
        # Verify the invariant: once cumulative > 300, all subsequent are 0.
        running = 0.0
        for s in slept:
            if running >= 300.0:
                assert s == 0.0 or s <= 300.0 - (running - s), (
                    f"sleep of {s}s issued after cap already at {running:.1f}s"
                )
            running += s
