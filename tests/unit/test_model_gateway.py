"""Unit tests for enhanced model gateway and model router."""

from __future__ import annotations

import contextlib
import logging
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.router import ModelRouter
from general_ludd.secrets.manager import SecretAlias, SecretsManager


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
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-test-key"}}
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
                {"name": "read_file", "args": {"path": "/x"}, "id": "c1",
                 "type": "tool_call"},
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
            job_id="j1", playbook="p", queue="q", model_profile="default",
        )

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(
                reg, "get_provider_class",
                return_value=lambda **kw: _ScriptedChatModel(),
            ),
        ):
            result = asyncio.run(
                loop.run_with_tools(job, "system", "please read /x")
            )

        # The tool WAS dispatched (the dead-loop bug = this list stays empty).
        assert dispatched == [("fs", "read_file", {"path": "/x"})]
        assert result == "done: contents were FOO"


class TestModelGatewayRecordsUsage:
    def test_usage_metadata_recorded(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-test-key"}}
        }
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
            assert "sk-super-secret-key-12345" not in record.message


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
            c for c in mock_tracker.record_success.call_args_list
            if c.args and c.args[0] == "fallback_model"
        ]
        assert len(success_calls) == 1, (
            f"record_success('fallback_model') called {len(success_calls)} times, "
            "expected exactly 1"
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
        gw = ModelGateway([ModelProfile(
            model_profile_id="local_llm",
            enabled=False,
            resource_profile="local_heavy",
        )])
        assert gw.is_available("local_llm") is False

    def test_local_model_enabled(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="local_llm",
            enabled=True,
            resource_profile="local_heavy",
        )])
        assert gw.is_available("local_llm") is True


class TestExampleConfigsAreValidYaml:
    def test_example_configs_are_valid_yaml(self):
        import os

        import yaml

        config_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "model_profiles"
        )
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
                estimated_cost=0.0,   # caller under-reports: should not matter
                budget_remaining=float("inf"),
            )


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
            f"expected 0 lock entries after call, got {len(gw._cache_key_locks)}: "
            f"{list(gw._cache_key_locks.keys())}"
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
            f"lock dict leaked {len(gw._cache_key_locks)} entries: "
            f"{list(gw._cache_key_locks.keys())[:5]}"
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

        def _fake_sleep(s: float) -> None:
            slept.append(s)

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch("general_ludd.models.gateway.ModelGateway.call_model_with_retry.__wrapped__", create=True),
        ):
            # Patch `time.sleep` inside the method's closure via the module-level
            # `time` import that the closure captures as `_time`.

            # Patch time.sleep at the source: the closure imports `time as _time`
            # and calls `_time.sleep`. We patch `time.sleep` globally for this
            # test's duration.
            import time as _time_mod
            with (
                patch.object(_time_mod, "sleep", side_effect=_fake_sleep),
                contextlib.suppress(Exception),
            ):
                gw.call_model_with_retry(
                    "retry_cap",
                    [{"role": "user", "content": "hi"}],
                    max_retries=3,
                )

        total_slept = sum(slept)
        assert total_slept <= 300.0 + 1e-6, (
            f"cumulative sleep {total_slept:.1f}s exceeded 300s cap "
            f"(individual sleeps: {slept})"
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

        def _fake_sleep(s: float) -> None:
            slept.append(s)

        import time as _time_mod
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(_time_mod, "sleep", side_effect=_fake_sleep),contextlib.suppress(Exception)
        ):
            gw.call_model_with_retry(
                "retry_cap2",
                [{"role": "user", "content": "hi"}],
                max_retries=3,
            )

        total_slept = sum(slept)
        # Hard cap: never exceed 300s regardless of per-attempt amounts.
        assert total_slept <= 300.0 + 1e-6, (
            f"total sleep {total_slept:.1f}s exceeded cap; individual: {slept}"
        )
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
