"""Deep template engine and prompt builder tests.

Covers variable substitution, template inheritance, sandbox escape prevention,
custom filters, error handling, and cross-module consistency across:
  - PromptRegistry (prompts/registry.py)
  - OutputTemplateRegistry (output_templates.py)
  - VariableStore (dispatch/variable_store.py)
  - Skill renderer (skills/renderer.py)
  - PromptTemplate / PromptRegistry-dspy (ag13_dspy/registry.py)
  - AnsibleTemplater sandboxed (ansible/templating.py)
  - PromptEnhancer (prompts/enhancer.py)
  - prompt_registry_to_chat_template (langchain/prompt_adapter.py)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jinja2 import TemplateError
from jinja2.exceptions import SecurityError

from general_ludd.ag13_dspy.registry import PromptRegistry as DspyPromptRegistry
from general_ludd.ag13_dspy.registry import PromptSpec, PromptTemplate
from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError
from general_ludd.dispatch.variable_store import VariableStore
from general_ludd.langchain.prompt_adapter import PromptRenderError, prompt_registry_to_chat_template
from general_ludd.output_templates import OutputTemplateRegistry
from general_ludd.prompts.enhancer import PromptEnhancer
from general_ludd.prompts.registry import (
    PromptRegistry,
    get_template_name_for_work_type,
    render_message_queue_section,
)
from general_ludd.skills.renderer import SkillRenderError, render_skill

# ---------------------------------------------------------------------------
# VARIABLE SUBSTITUTION
# ---------------------------------------------------------------------------


class TestVariableSubstitution:
    def test_prompt_registry_basic_interpolation(self):
        registry = PromptRegistry()
        registry.register("greet.j2", "Hello {{ name }}, you have {{ count }} items.")
        result = registry.render("greet.j2", name="Ada", count=3)
        assert result == "Hello Ada, you have 3 items."

    def test_prompt_registry_nested_dict_access(self):
        registry = PromptRegistry()
        registry.register("nested.j2", "{{ config.server.host }}:{{ config.server.port }}")
        result = registry.render("nested.j2", config={"server": {"host": "localhost", "port": 8080}})
        assert result == "localhost:8080"

    def test_prompt_registry_default_filter_chain(self):
        registry = PromptRegistry()
        registry.register("default.j2", "{{ title | default('Untitled') | upper }}")
        result = registry.render("default.j2")
        assert result == "UNTITLED"

    def test_output_template_variable_substitution(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "info.j2").write_text(
            "{{ provider }}|{{ metrics.uptime }}|{{ flags.enabled | default(false) }}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        rendered = registry.render(
            "info.j2",
            provider="aws",
            metrics={"uptime": 99.9},
            flags={"enabled": False},
        )
        assert "aws" in rendered
        assert "99.9" in rendered
        assert "False" in rendered

    def test_variable_store_complex_interpolation(self):
        store = VariableStore()
        store.set("env", "model", "gpt-4o")
        store.set("config", "temperature", 0.7)
        store.set("dispatch", "last__ok", True)
        result = store.render("Model: {{ env__model }}, temp: {{ config__temperature }}, ok: {{ dispatch__last__ok }}")
        assert "gpt-4o" in result
        assert "0.7" in result
        assert "True" in result

    def test_skill_renderer_variable_substitution(self):
        body = "Task: {{ task_name }} for {{ assignee }}."
        result = render_skill(body, {"task_name": "Refactor", "assignee": "coder"})
        assert result == "Task: Refactor for coder."

    def test_ansible_sandboxed_basic_interpolation(self):
        templater = AnsibleTemplater()
        result = templater.render_sandboxed("Hello {{ name }}!", name="World")
        assert result == "Hello World!"

    def test_prompt_template_call_method(self):
        spec = PromptSpec(name="greet", inputs={"name": str}, description="Greet someone")
        tmpl = PromptTemplate(spec=spec, template="Hello {{ name }}!")
        result = tmpl.call(name="Bob")
        assert result == "Hello Bob!"

    def test_conditional_and_loop_expressions(self):
        registry = PromptRegistry()
        template = "{% for item in items %}{{ item }},{% endfor %}{% if done %}finished{% else %}pending{% endif %}"
        registry.register("loops.j2", template)
        result = registry.render("loops.j2", items=["a", "b", "c"], done=True)
        assert result == "a,b,c,finished"

    def test_conditional_falsy_branch(self):
        registry = PromptRegistry()
        registry.register("cond.j2", "{% if enabled %}ON{% else %}OFF{% endif %}")
        assert registry.render("cond.j2", enabled=False) == "OFF"
        assert registry.render("cond.j2", enabled=True) == "ON"

    def test_empty_string_variable(self):
        registry = PromptRegistry()
        registry.register("empty.j2", "[{{ val }}]")
        result = registry.render("empty.j2", val="")
        assert result == "[]"

    def test_none_variable_renders_as_empty(self):
        registry = PromptRegistry()
        registry.register("none.j2", "[{{ val }}]")
        result = registry.render("none.j2", val=None)
        assert result == "[None]"


# ---------------------------------------------------------------------------
# TEMPLATE INHERITANCE
# ---------------------------------------------------------------------------


class TestTemplateInheritance:
    def test_include_renders_composed_template(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "partial.j2").write_text("shared: {{ topic }}", encoding="utf-8")
        (tmpl / "main.j2").write_text("Intro. {% include 'partial.j2' %}. Outro.", encoding="utf-8")
        registry = PromptRegistry(template_dir=str(tmpl))
        registry.refresh()
        result = registry.render("main.j2", topic="security")
        assert "shared: security" in result
        assert "Intro." in result
        assert "Outro." in result

    def test_include_with_missing_template_falls_back_to_loader(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "main.j2").write_text("{% include 'missing.j2' %}", encoding="utf-8")
        registry = PromptRegistry(template_dir=str(tmpl))
        registry.refresh()
        with pytest.raises((TemplateError,)):
            registry.render("main.j2")

    def test_output_template_extends_with_block_override(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "base.j2").write_text("HEADER{% block body %}default body{% endblock %}FOOTER", encoding="utf-8")
        (tmpl / "child.j2").write_text(
            "{% extends 'base.j2' %}{% block body %}overridden{% endblock %}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        result = registry.render("child.j2")
        assert "HEADER" in result
        assert "overridden" in result
        assert "default body" not in result
        assert "FOOTER" in result

    def test_output_template_extends_with_super(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "base.j2").write_text("{% block title %}Base Title{% endblock %}", encoding="utf-8")
        (tmpl / "child.j2").write_text(
            "{% extends 'base.j2' %}{% block title %}{{ super() }} - Child{% endblock %}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        result = registry.render("child.j2")
        assert "Base Title - Child" in result

    def test_output_template_extends_nested_block(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "outer.j2").write_text(
            "A{% block outer %}outer_default{% endblock %}Z",
            encoding="utf-8",
        )
        (tmpl / "inner.j2").write_text(
            "{% extends 'outer.j2' %}{% block outer %}inner_content{% endblock %}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        result = registry.render("inner.j2")
        assert result == "Ainner_contentZ"

    def test_output_template_include_inside_block(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "fragment.j2").write_text("fragment", encoding="utf-8")
        (tmpl / "base.j2").write_text(
            "{% block content %}{% include 'fragment.j2' %}{% endblock %}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        result = registry.render("base.j2")
        assert result == "fragment"


# ---------------------------------------------------------------------------
# SANDBOX ESCAPE PREVENTION
# ---------------------------------------------------------------------------


class TestSandboxEscapePrevention:
    SSTI_PAYLOADS: tuple[str, ...] = (
        "{{ ''.__class__.__mro__[1].__subclasses__() }}",
        "{{ ().__class__.__mro__ }}",
        "{{ ().__class__.__bases__[0].__subclasses__() }}",
        "{{ self.__init__.__globals__ }}",
    )

    def test_output_template_ssti_dunder_access_blocked(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "attack.j2").write_text("{{ report.__class__.__mro__ }}", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        with pytest.raises((SecurityError, TemplateError)):
            registry.render("attack.j2", report={"key": "val"})
        assert True

    def test_output_template_ssti_globals_access_blocked(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "attack.j2").write_text(
            "{{ self.__init__.__globals__.__builtins__ }}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        with pytest.raises((SecurityError, TemplateError)):
            registry.render("attack.j2")
        assert True

    def test_prompt_registry_ssti_payloads_neutralised(self):
        registry = PromptRegistry()
        for i, payload in enumerate(self.SSTI_PAYLOADS):
            name = f"attack_{i}.j2"
            registry.register(name, payload)
            try:
                result = registry.render(name)
            except (SecurityError, TemplateError):
                continue
            assert "<class " not in result, f"payload {i} leaked class info: {result!r}"
            assert "subclass" not in result.lower()
            assert "0x" not in result

    def test_skill_renderer_ssti_raises_skill_render_error(self):
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        with pytest.raises(SkillRenderError, match="sandbox-forbidden"):
            render_skill(payload)
        assert True

    def test_skill_renderer_ssti_popen_blocked(self):
        payload = "{{ cycler.__init__.__globals__ }}"
        fake_cycler = type("Cycler", (), {"__init__": lambda self: None})
        with pytest.raises(SkillRenderError, match="sandbox-forbidden"):
            render_skill(payload, {"cycler": fake_cycler()})
        assert True

    def test_variable_store_ssti_fail_open_no_leak(self):
        store = VariableStore()
        for payload in self.SSTI_PAYLOADS:
            out = store.render(payload)
            assert "<class" not in out, f"class leaked for {payload!r}: {out!r}"
            assert "0x" not in out, f"address leaked for {payload!r}: {out!r}"

    def test_ansible_sandboxed_ssti_blocked(self):
        templater = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            templater.render_sandboxed("{{ ''.__class__.__mro__ }}")
        assert True

    def test_ansible_sandboxed_globals_cleared(self):
        templater = AnsibleTemplater()
        with pytest.raises(TemplateRenderError):
            templater.render_sandboxed("{{ range(10) }}")
        assert True

    def test_output_template_globals_cleared(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "use_range.j2").write_text("{{ range(5) }}", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        with pytest.raises((SecurityError, TemplateError)):
            registry.render("use_range.j2")
        assert True


# ---------------------------------------------------------------------------
# CUSTOM FILTERS
# ---------------------------------------------------------------------------


class TestCustomFilters:
    def test_output_template_tojson_filter_registered(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "json_test.j2").write_text("{{ data | tojson }}", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        rendered = registry.render("json_test.j2", data={"a": 1, "b": [2, 3]})
        parsed = json.loads(rendered)
        assert parsed == {"a": 1, "b": [2, 3]}

    def test_output_template_tojson_with_indent(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "json_indent.j2").write_text("{{ data | tojson(indent=4) }}", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        rendered = registry.render("json_indent.j2", data={"x": "y"})
        assert '    "x"' in rendered

    def test_output_template_whitelisted_filters_available(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "filters.j2").write_text(
            "{{ name | upper }}|{{ name | length }}|{{ ' a b ' | trim }}|{{ items | first }}|{{ items | join(',') }}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        rendered = registry.render("filters.j2", name="hello", items=["x", "y", "z"])
        assert rendered == "HELLO|5|a b|x|x,y,z"

    def test_output_template_non_whitelisted_filter_unavailable(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "bad_filter.j2").write_text("{{ name | urlize }}", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        with pytest.raises(TemplateError):
            registry.compile()
        assert True

    def test_output_template_default_filter_chain(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "default.j2").write_text(
            "{{ missing | default('fallback') | upper }}",
            encoding="utf-8",
        )
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        result = registry.render("default.j2")
        assert result == "FALLBACK"


# ---------------------------------------------------------------------------
# ERROR HANDLING
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_skill_renderer_undefined_variable_raises(self):
        with pytest.raises(SkillRenderError, match="undefined variable"):
            render_skill("Hello {{ missing_var }}!")

    def test_skill_renderer_syntax_error_raises(self):
        with pytest.raises(SkillRenderError):
            render_skill("{% if True %}{% endfor %}")

    def test_prompt_registry_hash_tracking(self):
        registry = PromptRegistry()
        registry.register("t.j2", "hello")
        info1 = registry.get_template_version_info("t.j2")
        assert info1["hash"] is not None
        assert info1["history"] == []

        registry.register("t.j2", "hello again")
        info2 = registry.get_template_version_info("t.j2")
        assert info2["hash"] != info1["hash"]
        assert len(info2["history"]) == 1

    def test_output_template_undefined_variable_raises(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "strict.j2").write_text("{{ missing_var }}", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[tmpl])
        registry.compile()
        with pytest.raises(TemplateError):
            registry.render("strict.j2")

    def test_output_template_missing_template_raises(self):
        registry = OutputTemplateRegistry()
        registry.compile()
        with pytest.raises(KeyError):
            registry.render("nonexistent.j2")
        assert True

    def test_variable_store_render_returns_raw_on_error(self):
        store = VariableStore()
        result = store.render("{{ 1 / 0 }}")
        assert "1 / 0" in result


# ---------------------------------------------------------------------------
# PROMPT ENHANCER
# ---------------------------------------------------------------------------


class TestPromptEnhancer:
    def test_enhancer_empty_without_store(self):
        enhancer = PromptEnhancer(store=None)
        assert enhancer.generate_avoidance_warning() == ""
        assert enhancer.enhance_prompt("system prompt") == "system prompt"
        assert enhancer.get_recent_blocked_tools() == set()
        assert enhancer.get_blocked_tool_counts() == {}

    def test_enhance_messages_no_warning_returns_unchanged(self):
        enhancer = PromptEnhancer(store=None)
        msgs = [{"role": "user", "content": "hello"}]
        result = enhancer.enhance_messages(msgs)
        assert result == msgs

    def test_format_tool_advice_empty_without_store(self):
        enhancer = PromptEnhancer(store=None)
        assert enhancer.format_tool_advice("some_tool") == ""


# ---------------------------------------------------------------------------
# PROMT WORK-TYPE MAPPING
# ---------------------------------------------------------------------------


class TestWorkTypeMapping:
    def test_known_work_types_map_to_expected_templates(self):
        assert get_template_name_for_work_type("code") == "implementation.md.j2"
        assert get_template_name_for_work_type("test") == "test.md.j2"
        assert get_template_name_for_work_type("analysis") == "gap_analysis.md.j2"
        assert get_template_name_for_work_type("audit") == "audit.md.j2"
        assert get_template_name_for_work_type("docs") == "documentation.md.j2"
        assert get_template_name_for_work_type("security") == "security.md.j2"

    def test_unknown_work_type_falls_back(self):
        result = get_template_name_for_work_type("nonexistent_type")
        assert result == "implementation.md.j2"


# ---------------------------------------------------------------------------
# MESSAGE QUEUE SECTION
# ---------------------------------------------------------------------------


class TestMessageQueueSection:
    def test_disabled_returns_empty(self):
        result = render_message_queue_section("coder", unread_count=5, enabled=False)
        assert result == ""

    def test_enabled_zero_unread(self):
        result = render_message_queue_section("coder", unread_count=0, enabled=True)
        assert "agent 'coder'" in result
        assert "0 unread message(s)" in result

    def test_enabled_one_unread(self):
        result = render_message_queue_section("coder", unread_count=1, enabled=True)
        assert "1 unread message" in result
        assert "message(s)" not in result

    def test_enabled_with_senders(self):
        result = render_message_queue_section("planner", unread_count=3, senders=["coder", "reviewer"], enabled=True)
        assert "from coder, reviewer" in result


# ---------------------------------------------------------------------------
# LANGCHAIN PROMPT ADAPTER
# ---------------------------------------------------------------------------


class TestLangchainPromptAdapter:
    def test_registry_to_chat_template_basic(self):
        registry = PromptRegistry()
        registry.register("test.j2", "System: {{ instruction }}")
        chat_tmpl = prompt_registry_to_chat_template(registry, "test.j2", instruction="Do work")
        assert chat_tmpl is not None
        msgs = chat_tmpl.format_messages()
        assert len(msgs) == 1
        assert "Do work" in msgs[0].content

    def test_registry_to_chat_template_missing_template_raises(self):
        registry = PromptRegistry()
        with pytest.raises(PromptRenderError, match="failed to render"):
            prompt_registry_to_chat_template(registry, "nonexistent.j2")


# ---------------------------------------------------------------------------
# PROMPT TEMPLATE VERSIONING (ag13_dspy)
# ---------------------------------------------------------------------------


class TestDspyPromptRegistry:
    def test_put_and_get(self):
        reg = DspyPromptRegistry()
        spec = PromptSpec(name="test", inputs={"x": int})
        tmpl = PromptTemplate(spec=spec, template="{{ x }}")
        reg.put("test", 1, tmpl, score=0.95)
        fetched = reg.get("test", 1)
        assert fetched is not None
        assert fetched.call(x=42) == "42"
        assert fetched.score == 0.95

    def test_latest_returns_highest_version(self):
        reg = DspyPromptRegistry()
        spec = PromptSpec(name="test", inputs={"x": int})
        reg.put("test", 1, PromptTemplate(spec=spec, template="{{ x }}v1"))
        reg.put("test", 3, PromptTemplate(spec=spec, template="{{ x }}v3"))
        reg.put("test", 2, PromptTemplate(spec=spec, template="{{ x }}v2"))
        latest = reg.latest("test")
        assert latest is not None
        assert latest.call(x=0) == "0v3"

    def test_get_best_returns_highest_score(self):
        reg = DspyPromptRegistry()
        spec = PromptSpec(name="test", inputs={"x": int})
        reg.put("test", 1, PromptTemplate(spec=spec, template="{{ x }}"), score=0.5)
        reg.put("test", 2, PromptTemplate(spec=spec, template="{{ x }}"), score=0.95)
        reg.put("test", 3, PromptTemplate(spec=spec, template="{{ x }}"), score=0.7)
        best = reg.get_best("test")
        assert best is not None
        assert best.score == 0.95
        assert best.version == 2

    def test_list_versions_sorted(self):
        reg = DspyPromptRegistry()
        spec = PromptSpec(name="test", inputs={})
        reg.put("test", 3, PromptTemplate(spec=spec, template=""))
        reg.put("test", 1, PromptTemplate(spec=spec, template=""))
        reg.put("test", 2, PromptTemplate(spec=spec, template=""))
        assert reg.list_versions("test") == [1, 2, 3]

    def test_list_names(self):
        reg = DspyPromptRegistry()
        spec = PromptSpec(name="a", inputs={})
        reg.put("alpha", 1, PromptTemplate(spec=spec, template=""))
        reg.put("beta", 1, PromptTemplate(spec=spec, template=""))
        assert reg.list_names() == ["alpha", "beta"]

    def test_remove(self):
        reg = DspyPromptRegistry()
        spec = PromptSpec(name="test", inputs={})
        reg.put("test", 1, PromptTemplate(spec=spec, template=""))
        reg.remove("test", 1)
        assert reg.get("test", 1) is None


# ---------------------------------------------------------------------------
# OUTPUT TEMPLATE MULTI-DIR SHADOWING
# ---------------------------------------------------------------------------


class TestOutputTemplateShadowing:
    def test_first_dir_wins_in_multi_dir_setup(self, tmp_path):
        a = tmp_path / "dir_a"
        b = tmp_path / "dir_b"
        a.mkdir()
        b.mkdir()
        (a / "report.j2").write_text("alpha", encoding="utf-8")
        (b / "report.j2").write_text("beta", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[a, b])
        registry.compile()
        assert registry.render("report.j2") == "alpha"

    def test_extra_template_dirs_shadow_default(self, tmp_path):
        default = tmp_path / "default"
        override = tmp_path / "override"
        default.mkdir()
        override.mkdir()
        (default / "style.j2").write_text("default_style", encoding="utf-8")
        (override / "style.j2").write_text("override_style", encoding="utf-8")
        registry = OutputTemplateRegistry(template_dirs=[override, default])
        registry.compile()
        assert registry.render("style.j2") == "override_style"


# ---------------------------------------------------------------------------
# CROSS-MODULE CONSISTENCY
# ---------------------------------------------------------------------------


class TestCrossModuleConsistency:
    def test_all_renderers_produce_same_output_for_basic_template(self, tmp_path):
        template_text = "{{ greeting }}, {{ name }}!"
        expected = "Hello, World!"

        pr = PromptRegistry()
        pr.register("test.j2", template_text)
        assert pr.render("test.j2", greeting="Hello", name="World") == expected

        assert render_skill(template_text, {"greeting": "Hello", "name": "World"}) == expected

        store = VariableStore()
        store.set("vars", "greeting", "Hello")
        store.set("vars", "name", "World")
        result = store.render("{{ vars__greeting }}, {{ vars__name }}!")
        assert result == expected

        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "test.j2").write_text(template_text, encoding="utf-8")
        ot_reg = OutputTemplateRegistry(template_dirs=[tmpl])
        ot_reg.compile()
        assert ot_reg.render("test.j2", greeting="Hello", name="World") == expected

    def test_all_sandboxes_block_dunder_access_consistently(self):
        payload = "{{ ().__class__.__mro__ }}"
        checkers: list[tuple[str, Any]] = [
            ("PromptRegistry", PromptRegistry()),
            ("skill_renderer", render_skill),
            ("VariableStore", VariableStore()),
        ]

        for label, obj in checkers:
            if label == "skill_renderer":
                with pytest.raises(SkillRenderError) as exc_info:
                    obj(payload)
                assert "sandbox" in str(exc_info.value).lower()
            elif label == "VariableStore":
                result = obj.render(payload)
                assert "<class" not in result
            elif label == "PromptRegistry":
                obj.register("ssti_test.j2", payload)
                try:
                    result = obj.render("ssti_test.j2")
                except (SecurityError, TemplateError):
                    continue
                assert "<class" not in result
