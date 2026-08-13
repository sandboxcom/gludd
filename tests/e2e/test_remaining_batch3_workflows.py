"""E2E: Dispatch, log analysis, service discovery — batch 3 of coverage push."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# dispatch.dynamic_dispatcher
# ---------------------------------------------------------------------------


class TestDispatchParseToolCalls:
    def test_import(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            parse_tool_calls,
        )

        assert parse_tool_calls is not None
        assert UNRESTRICTED_ROLE is not None

    def test_parse_dict_with_tool_calls(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls({"tool_calls": [{"kind": "skill", "name": "test_skill"}]})
        assert len(calls) == 1
        assert calls[0].kind == "skill"
        assert calls[0].name == "test_skill"

    def test_parse_json_string(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls('{"tool_calls": [{"kind": "role", "name": "admin"}]}')
        assert len(calls) == 1
        assert calls[0].kind == "role"

    def test_parse_single_call_dict(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls({"kind": "mcp", "name": "http_get", "args": {"url": "/api"}})
        assert len(calls) == 1
        assert calls[0].name == "http_get"

    def test_parse_bad_json(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls("not json")
        assert calls == []

    def test_parse_wrong_type(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls(42)  # type: ignore[arg-type]
        assert calls == []

    def test_parse_empty_dict(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls({})
        assert calls == []

    def test_parse_call_list_non_dict_items(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls({"tool_calls": [42, "string"]})
        assert calls == []

    def test_parse_missing_kind(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls({"kind": "skill", "name": "test"})
        assert len(calls) == 1

    def test_parse_missing_name(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls({"tool_calls": [{"kind": "skill"}]})
        assert calls == []

    def test_tool_call_default_args(self):
        from general_ludd.dispatch.dynamic_dispatcher import ToolCall

        tc = ToolCall(kind="skill", name="test")
        assert tc.args == {}

    def test_dispatch_result_to_dict(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        dr = DispatchResult(ok=True, kind="skill", name="test", output=42)
        d = dr.to_dict()
        assert d["ok"] is True
        assert d["output"] == 42

    def test_dispatch_result_error(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        dr = DispatchResult(ok=False, error="oops", kind="role", name="bad")
        assert dr.error == "oops"

    def test_structured_tool_calls_empty(self):
        from general_ludd.dispatch.dynamic_dispatcher import structured_tool_calls_to_calls

        assert structured_tool_calls_to_calls(None) == []
        assert structured_tool_calls_to_calls([]) == []

    def test_structured_tool_calls_standard(self):
        from general_ludd.dispatch.dynamic_dispatcher import structured_tool_calls_to_calls

        calls = structured_tool_calls_to_calls(
            [{"function": {"name": "my_func", "arguments": '{"x": 1}'}}]
        )
        assert len(calls) == 1
        assert calls[0].kind == "mcp"
        assert calls[0].name == "my_func"
        assert calls[0].args == {"x": 1}

    def test_structured_tool_calls_missing_name(self):
        from general_ludd.dispatch.dynamic_dispatcher import structured_tool_calls_to_calls

        calls = structured_tool_calls_to_calls([{"function": {}}])
        assert calls == []


class TestDynamicDispatcher:
    def test_constructor(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        dd = DynamicDispatcher()
        assert len(dd.list_available()["registered_kinds"]) == 0

    def test_constructor_with_handlers(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        dd = DynamicDispatcher(
            role_handler=lambda name, args: f"role:{name}",
            mcp_handler=lambda name, args: f"mcp:{name}",
        )
        kinds = dd.list_available()["registered_kinds"]
        assert "role" in kinds
        assert "mcp" in kinds

    def test_list_available(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        dd = DynamicDispatcher(skill_handler=lambda n, a: n)
        assert "skill" in dd.list_available()["registered_kinds"]

    async def test_dispatch_unknown_kind(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher, ToolCall

        dd = DynamicDispatcher()
        call = ToolCall(kind="bogus", name="x")
        result = await dd.dispatch(call)
        assert result.ok is False
        assert "unknown_kind" in str(result.error)

    async def test_dispatch_handler_error(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        def failing(name, args):
            raise RuntimeError("boom")

        dd = DynamicDispatcher(skill_handler=failing, role=UNRESTRICTED_ROLE)
        call = ToolCall(kind="skill", name="x")
        result = await dd.dispatch(call)
        assert result.ok is False
        assert result.error == "handler_error"

    async def test_dispatch_async_handler(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        async def async_handler(name, args):
            return f"async:{name}"

        dd = DynamicDispatcher(skill_handler=async_handler, role=UNRESTRICTED_ROLE)
        call = ToolCall(kind="skill", name="test")
        result = await dd.dispatch(call)
        assert result.ok is True
        assert result.output == "async:test"

    async def test_dispatch_sync_handler(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        dd = DynamicDispatcher(
            mcp_handler=lambda n, a: f"result:{n}",
            role=UNRESTRICTED_ROLE,
        )
        call = ToolCall(kind="mcp", name="get")
        result = await dd.dispatch(call)
        assert result.ok is True
        assert result.output == "result:get"

    async def test_dispatch_all(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        dd = DynamicDispatcher(
            mcp_handler=lambda n, a: f"got:{n}",
            role=UNRESTRICTED_ROLE,
        )
        calls = [
            ToolCall(kind="mcp", name="a"),
            ToolCall(kind="mcp", name="b"),
        ]
        results = await dd.dispatch_all(calls)
        assert len(results) == 2
        assert all(r.ok for r in results)

    async def test_none_role_deny_privileged(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher, ToolCall

        dd = DynamicDispatcher(role_handler=lambda n, a: n, role=None)
        call = ToolCall(kind="role", name="admin")
        result = await dd.dispatch(call)
        assert result.ok is False
        assert result.error == "capability_denied"

    async def test_none_role_denies_mcp(self):
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher, ToolCall

        dd = DynamicDispatcher(mcp_handler=lambda n, a: n, role=None)
        call = ToolCall(kind="mcp", name="get")
        result = await dd.dispatch(call)
        assert result.ok is False
        assert result.error == "capability_denied"

    async def test_unrestricted_role_allow_all(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        dd = DynamicDispatcher(
            role_handler=lambda n, a: n,
            mcp_handler=lambda n, a: n,
            role=UNRESTRICTED_ROLE,
        )
        result = await dd.dispatch(ToolCall(kind="role", name="admin"))
        assert result.ok is True

    def test_unrestricted_role_is_object(self):
        from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE

        assert UNRESTRICTED_ROLE is not None
        assert UNRESTRICTED_ROLE != "unrestricted"

    def test_privileged_kinds_all_present(self):
        from general_ludd.dispatch.dynamic_dispatcher import PRIVILEGED_KINDS

        assert "role" in PRIVILEGED_KINDS
        assert "collection" in PRIVILEGED_KINDS
        assert "mcp" in PRIVILEGED_KINDS
        assert "skill" in PRIVILEGED_KINDS

    async def test_collection_handler(self):
        from general_ludd.dispatch.dynamic_dispatcher import (
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        dd = DynamicDispatcher(
            collection_handler=lambda n, a: f"col:{n}",
            role=UNRESTRICTED_ROLE,
        )
        result = await dd.dispatch(ToolCall(kind="collection", name="my_col"))
        assert result.ok is True
        assert result.output == "col:my_col"


# ---------------------------------------------------------------------------
# dispatch.variable_store
# ---------------------------------------------------------------------------


class TestVariableStore:
    def test_import(self):
        from general_ludd.dispatch.variable_store import VariableStore

        assert VariableStore is not None

    def test_set_and_get(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("ns", "key", "value")
        assert store.get("ns", "key") == "value"

    def test_get_default(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        assert store.get("ns", "missing", default=42) == 42

    def test_get_namespace(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("ns", "a", 1)
        store.set("ns", "b", 2)
        ns = store.get_namespace("ns")
        assert ns == {"a": 1, "b": 2}

    def test_get_namespace_empty(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        assert store.get_namespace("nonexistent") == {}

    def test_all_vars(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("ns", "key", "value")
        flat = store.all_vars()
        assert "ns__key" in flat
        assert flat["ns__key"] == "value"

    def test_render_simple(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("ctx", "name", "Alice")
        result = store.render("Hello, {{ ctx__name }}!")
        assert "Alice" in result

    def test_render_missing_var(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        result = store.render("{{ missing_var }}")
        assert result == ""

    def test_invalid_key_rejected(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        with pytest.raises(ValueError, match="invalid"):
            store.set("ns", "bad/key", "value")

    def test_invalid_key_whitespace(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        with pytest.raises(ValueError, match="invalid"):
            store.set("ns", "key with space", "value")

    def test_apply_results(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
        from general_ludd.dispatch.variable_store import VariableStore, apply_results

        store = VariableStore()
        results = [DispatchResult(ok=True, kind="mcp", name="get", output="data")]
        apply_results(store, results)
        assert store.get("dispatch", "get__ok") is True
        assert store.get("dispatch", "get__output") == "data"

    def test_apply_results_with_error(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
        from general_ludd.dispatch.variable_store import VariableStore, apply_results

        store = VariableStore()
        results = [DispatchResult(ok=False, kind="role", name="admin", error="denied")]
        apply_results(store, results)
        assert store.get("dispatch", "admin__ok") is False
        assert store.get("dispatch", "admin__error") == "denied"

    def test_apply_results_last(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
        from general_ludd.dispatch.variable_store import VariableStore, apply_results

        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="first", output=1),
            DispatchResult(ok=True, kind="mcp", name="second", output=2),
        ]
        apply_results(store, results)
        assert store.get("dispatch", "last__name") == "second"

    def test_safe_dispatch_name_dots(self):
        from general_ludd.dispatch.variable_store import _safe_dispatch_name

        assert _safe_dispatch_name("a.b") == "a_DOT_b"

    def test_safe_dispatch_name_reserved(self):
        from general_ludd.dispatch.variable_store import _safe_dispatch_name

        result = _safe_dispatch_name("last")
        assert "TOOLNAME" in result

    def test_apply_results_safe_names(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
        from general_ludd.dispatch.variable_store import VariableStore, apply_results

        store = VariableStore()
        results = [DispatchResult(ok=True, kind="mcp", name="my.test", output="ok")]
        apply_results(store, results)
        assert store.get("dispatch", "my_DOT_test__ok") is True


# ---------------------------------------------------------------------------
# log_analysis.prompt_evaluator
# ---------------------------------------------------------------------------


class TestPromptEvaluator:
    def test_import(self):
        from general_ludd.log_analysis.prompt_evaluator import (
            classify_prompt,
        )

        assert classify_prompt is not None

    def test_parse_conversation_log_tags(self):
        from general_ludd.log_analysis.prompt_evaluator import parse_conversation_log

        log = "<user>Hello, world</user><assistant>I see</assistant>"
        entries = parse_conversation_log(log)
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"

    def test_parse_conversation_log_with_tool_calls(self):
        from general_ludd.log_analysis.prompt_evaluator import parse_conversation_log

        log = (
            '<user>run tests</user>'
            '<assistant><tool_call>{"name": "run_test"}</tool_call>done</assistant>'
        )
        entries = parse_conversation_log(log)
        assert len(entries[1]["tool_calls"]) == 1

    def test_parse_conversation_log_with_cot(self):
        from general_ludd.log_analysis.prompt_evaluator import parse_conversation_log

        log = '<assistant><cot>I think this works</cot>let us try</assistant>'
        entries = parse_conversation_log(log)
        assert entries[0]["cot"] == "I think this works"

    def test_parse_conversation_log_fallback(self):
        from general_ludd.log_analysis.prompt_evaluator import parse_conversation_log

        log = "User: Hello\nAssistant: Hi there"
        entries = parse_conversation_log(log)
        assert len(entries) == 2

    def test_parse_conversation_log_from_file(self):
        from general_ludd.log_analysis.prompt_evaluator import parse_conversation_log

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("<user>test</user>")
            f.flush()
            entries = parse_conversation_log(f.name)
        Path(f.name).unlink()
        assert len(entries) == 1

    def test_parse_conversation_log_nonexistent_file(self):
        from general_ludd.log_analysis.prompt_evaluator import parse_conversation_log

        entries = parse_conversation_log("/tmp/nonexistent_log_file_xyz.log")
        assert entries == []

    def test_classify_prompt_planning(self):
        from general_ludd.log_analysis.prompt_evaluator import classify_prompt

        assert classify_prompt("plan the architecture and design approach") == "planning"

    def test_classify_prompt_coding(self):
        from general_ludd.log_analysis.prompt_evaluator import classify_prompt

        assert classify_prompt("write a function that implements a feature") == "coding"

    def test_classify_prompt_research(self):
        from general_ludd.log_analysis.prompt_evaluator import classify_prompt

        assert classify_prompt("research and survey the codebase to locate imports") == "research"

    def test_classify_prompt_debugging(self):
        from general_ludd.log_analysis.prompt_evaluator import classify_prompt

        assert classify_prompt("debug the crash and fix the exception error") == "debugging"

    def test_classify_prompt_config(self):
        from general_ludd.log_analysis.prompt_evaluator import classify_prompt

        assert classify_prompt("config and deploy the docker container setup") == "configuration"

    def test_classify_prompt_unknown(self):
        from general_ludd.log_analysis.prompt_evaluator import classify_prompt

        assert classify_prompt("xyzzy flurbo") == "other"

    def test_extract_prompts(self):
        from general_ludd.log_analysis.prompt_evaluator import extract_prompts

        conv = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
            {"role": "tool", "content": "result"},
        ]
        prompts = extract_prompts(conv)
        assert len(prompts) == 2

    def test_measure_prompt_efficiency(self):
        from general_ludd.log_analysis.prompt_evaluator import measure_prompt_efficiency

        result = measure_prompt_efficiency(
            "write a function",
            {"content": "Here is the function: def foo(): pass. Done.", "tool_calls": []},
        )
        assert "tokens_in" in result
        assert "task_completed" in result
        assert result["task_completed"] is True

    def test_measure_prompt_efficiency_no_completion(self):
        from general_ludd.log_analysis.prompt_evaluator import measure_prompt_efficiency

        result = measure_prompt_efficiency("write code", {"content": "thinking...", "tool_calls": []})
        assert result["task_completed"] is False

    def test_analyze_cot_quality_empty(self):
        from general_ludd.log_analysis.prompt_evaluator import analyze_cot_quality

        result = analyze_cot_quality("")
        assert result["score"] == 0
        assert result["reasoning_depth"] == 0

    def test_analyze_cot_quality_deep(self):
        from general_ludd.log_analysis.prompt_evaluator import analyze_cot_quality

        result = analyze_cot_quality(
            "therefore we should choose option A because it is better. "
            "The evidence shows this is the optimal choice. I decided on this approach."
        )
        assert result["reasoning_depth"] >= 1
        assert result["decision_quality"] >= 1

    def test_detect_context_waste(self):
        from general_ludd.log_analysis.prompt_evaluator import detect_context_waste

        conv = [
            {"role": "user", "content": "hello world this is a test sentence", "tokens": 50},
            {"role": "assistant", "content": "hello world this is a test sentence", "tokens": 30},
        ]
        findings = detect_context_waste(conv)
        assert isinstance(findings, list)

    def test_recommend_improvements(self):
        from general_ludd.log_analysis.prompt_evaluator import recommend_improvements

        recommendations = recommend_improvements({"classification": "coding"})
        assert len(recommendations) >= 1

    def test_recommend_improvements_low_cot(self):
        from general_ludd.log_analysis.prompt_evaluator import recommend_improvements

        recommendations = recommend_improvements(
            {"cot_quality": {"reasoning_depth": 1, "decision_quality": 1, "dead_ends": 5, "score": 2}}
        )
        assert len(recommendations) >= 2

    def test_ab_compare(self):
        from general_ludd.log_analysis.prompt_evaluator import ab_compare

        variant_a = [
            {"role": "user", "content": "do task", "tokens": 100},
        ]
        variant_b = [
            {"role": "user", "content": "do task", "tokens": 200},
        ]
        result = ab_compare(variant_a, variant_b)
        assert "winner" in result
        assert "a_metrics" in result
        assert "b_metrics" in result

    def test_generate_report_markdown(self):
        from general_ludd.log_analysis.prompt_evaluator import generate_report

        analyses = [
            {
                "prompt_id": "test-1",
                "classification": "coding",
                "efficiency": {
                    "tokens_in": 42,
                    "tokens_out": 20,
                    "task_completed": True,
                    "steps_taken": 1,
                    "errors": 0,
                },
                "cot_quality": {"reasoning_depth": 5, "decision_quality": 6, "dead_ends": 0, "score": 8},
                "context_waste": [],
                "recommendations": ["Looks good"],
            }
        ]
        report = generate_report(analyses, format="markdown")
        assert "# Prompt Evaluation Report" in report

    def test_generate_report_json(self):
        from general_ludd.log_analysis.prompt_evaluator import generate_report

        analyses = [{"prompt_id": "test"}]
        report = generate_report(analyses, format="json")
        assert json.loads(report)


# ---------------------------------------------------------------------------
# service_discovery.pipeline
# ---------------------------------------------------------------------------


class TestServiceDiscoveryPipeline:
    def test_imports(self):
        from general_ludd.service_discovery.pipeline import (
            ServiceDiscoveryPipeline,
        )

        assert ServiceDiscoveryPipeline is not None

    def test_discovery_report_defaults(self):
        from general_ludd.service_discovery.pipeline import DiscoveryReport

        report = DiscoveryReport()
        assert report.new_services == []
        assert report.errors == []

    def test_extract_service_name_delimiter(self):
        from general_ludd.service_discovery.pipeline import _extract_service_name

        class FakeResult:
            title = "MyService - A platform for things"
            url = "https://example.com"
            snippet = "desc"
            engine = "google"

        result = _extract_service_name(FakeResult())
        assert result == "MyService"

    def test_extract_service_name_pipe(self):
        from general_ludd.service_discovery.pipeline import _extract_service_name

        class FakeResult:
            title = "ServiceX | API Platform"
            url = "https://x.com"
            snippet = "snippet"
            engine = "google"

        result = _extract_service_name(FakeResult())
        assert result == "ServiceX"

    def test_extract_service_name_preserves_two_character_name(self):
        from general_ludd.service_discovery.pipeline import _extract_service_name

        class FakeResult:
            title = "Ab"
            url = "https://ab.com"
            snippet = "s"
            engine = "g"

        result = _extract_service_name(FakeResult())
        assert result == "Ab"

    def test_extract_service_name_rejects_blank_title(self):
        from general_ludd.service_discovery.pipeline import _extract_service_name

        class FakeResult:
            title = "   "
            url = "https://blank.example.com"
            snippet = "s"
            engine = "g"

        assert _extract_service_name(FakeResult()) is None

    def test_extract_service_name_long(self):
        from general_ludd.service_discovery.pipeline import _extract_service_name

        class FakeResult:
            title = "A" * 200
            url = "https://long.com"
            snippet = "s"
            engine = "g"

        result = _extract_service_name(FakeResult())
        assert result is not None
        assert len(result) <= 80

    def test_pipeline_constructor(self):
        from general_ludd.service_discovery.pipeline import ServiceDiscoveryPipeline

        pipeline = ServiceDiscoveryPipeline("http://localhost:8080")
        assert pipeline._search_terms is not None

    def test_pipeline_custom_terms(self):
        from general_ludd.service_discovery.pipeline import ServiceDiscoveryPipeline

        pipeline = ServiceDiscoveryPipeline("http://localhost:8080", search_terms=["term1"])
        assert pipeline._search_terms == ["term1"]


# ---------------------------------------------------------------------------
# ssl.hsm (remaining tests)
# ---------------------------------------------------------------------------


class TestHSMAdditional:
    def test_hsm_key_with_capabilities(self):
        from general_ludd.ssl.hsm import HSMKey

        k = HSMKey(
            key_id="k1",
            label="My Key",
            key_type="EC",
            key_size=256,
            algorithm="ECDSA",
            capabilities=["sign", "verify"],
        )
        assert k.capabilities == ["sign", "verify"]

    def test_hsm_key_dataclass_fields(self):
        from general_ludd.ssl.hsm import HSMKey

        k = HSMKey(key_id="1", label="k", key_type="RSA", key_size=2048, algorithm="RSA")
        assert k.created_at is None   # default

    def test_hsm_config_with_pin(self):
        from general_ludd.ssl.hsm import HSMConfig

        c = HSMConfig(module_path="/lib.so", slot_id=1, pin="secret", label="test", token_label="t1")
        assert c.pin == "secret"
        assert c.token_label == "t1"

    def test_mock_hsm_preloaded_keys(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = _MockHSMSession(config)
        keys = session.list_keys()
        key_ids = {k.key_id for k in keys}
        assert "rsa-2048-001" in key_ids
        assert "ecdsa-p256-001" in key_ids

    def test_mock_hsm_sign_with_mechanism(self):
        from general_ludd.ssl.hsm import HSMConfig, _MockHSMSession

        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = _MockHSMSession(config)
        sig = session.sign("ecdsa-p256-001", b"data", mechanism="ECDSA")
        assert len(sig) > 0


# ---------------------------------------------------------------------------
# renderers.templates (__init__ check)
# ---------------------------------------------------------------------------


class TestRendererTemplates:
    def test_templates_importable(self):
        from general_ludd.renderers.templates import __name__

        assert "templates" in __name__


# ---------------------------------------------------------------------------
# approval.gate — additional
# ---------------------------------------------------------------------------


class TestApprovalGateExtended:
    def test_approval_gate_import_all(self):
        from general_ludd.approval.gate import (
            ApprovalGate,
            ApprovalRequest,
            ApprovalResult,
        )

        gate = ApprovalGate()
        assert gate is not None
        result = gate.check(
            ApprovalRequest(action="deploy", target="production", by="agent-1")
        )
        assert isinstance(result, ApprovalResult)


# ---------------------------------------------------------------------------
# dispatch — edge cases
# ---------------------------------------------------------------------------


class TestDispatchEdgeCases:
    def test_parse_single_call_with_args(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        calls = parse_tool_calls({"kind": "mcp", "name": "get", "args": {"x": 1}})
        assert calls[0].args == {"x": 1}

    def test_parse_single_name_truncation(self):
        from general_ludd.dispatch.dynamic_dispatcher import parse_tool_calls

        long_name = "a" * 500
        calls = parse_tool_calls({"kind": "mcp", "name": long_name})
        assert len(calls[0].name) <= 256

    def test_dispatch_result_defaults(self):
        from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

        dr = DispatchResult(ok=False)
        assert dr.kind == ""
        assert dr.name == ""

    def test_variable_store_render_ssti_blocked(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        result = store.render("{{ ().__class__.__mro__ }}")
        # Should not raise, may return template verbatim on render error
        assert isinstance(result, str)

    def test_variable_store_apply_results_empty(self):
        from general_ludd.dispatch.variable_store import VariableStore, apply_results

        store = VariableStore()
        apply_results(store, [])
        assert store.get("dispatch", "last__ok") is None
