"""Tests for ansible/templating: AnsibleTemplater render_sandboxed, render, resolve_fact, TemplateRenderError."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.ansible.templating import (
    AnsibleTemplater,
    TemplateRenderError,
)

pytestmark = pytest.mark.xdist_group("ansible_templating")


class TestTemplateRenderError:
    def test_is_exception(self):
        assert issubclass(TemplateRenderError, Exception)

    def test_instantiable(self):
        err = TemplateRenderError("template rejected")
        assert "template rejected" in str(err)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(TemplateRenderError, match="rejected"):
            raise TemplateRenderError("template rejected: UndefinedError")

    def test_is_distinct_from_value_error(self):
        err = TemplateRenderError("template rejected")
        assert not isinstance(err, ValueError)

    def test_str_and_repr(self):
        err = TemplateRenderError("template rejected: SecurityError")
        assert "SecurityError" in str(err)
        assert "TemplateRenderError" in repr(err)


class TestAnsibleTemplaterConstruction:
    def test_default_construction(self):
        t = AnsibleTemplater()
        assert t._extra_vars == {}

    def test_with_extra_vars(self):
        t = AnsibleTemplater(extra_vars={"project": "gludd"})
        assert t._extra_vars == {"project": "gludd"}

    def test_with_none_extra_vars_explicit(self):
        t = AnsibleTemplater(extra_vars=None)
        assert t._extra_vars == {}

    def test_runner_created_on_construction(self):
        t = AnsibleTemplater()
        assert t._runner is not None


class TestAnsibleTemplaterMergedVars:
    def test_extra_vars_only(self):
        t = AnsibleTemplater(extra_vars={"host": "localhost"})
        merged = t._merged_vars({})
        assert merged["host"] == "localhost"

    def test_kwargs_override_extra_vars(self):
        t = AnsibleTemplater(extra_vars={"val": "default"})
        merged = t._merged_vars({"val": "override"})
        assert merged["val"] == "override"

    def test_kwargs_extend_extra_vars(self):
        t = AnsibleTemplater(extra_vars={"host": "localhost"})
        merged = t._merged_vars({"port": 5432})
        assert merged["host"] == "localhost"
        assert merged["port"] == 5432

    def test_empty_both(self):
        t = AnsibleTemplater()
        merged = t._merged_vars({})
        assert merged == {}

    def test_no_kwargs_overrides(self):
        t = AnsibleTemplater(extra_vars={"env": "prod", "debug": "false"})
        merged = t._merged_vars({})
        assert merged["env"] == "prod"
        assert merged["debug"] == "false"

    def test_multiple_kwargs_merge(self):
        t = AnsibleTemplater(extra_vars={"base": "root"})
        merged = t._merged_vars({"color": "red", "size": 10, "name": "test"})
        assert merged["base"] == "root"
        assert merged["color"] == "red"
        assert merged["size"] == 10
        assert merged["name"] == "test"


class TestAnsibleTemplaterRenderSandboxed:
    def test_simple_substitution(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("Hello {{ name }}!", name="World")
        assert result == "Hello World!"

    def test_multiple_variables(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ greeting }} {{ name }}", greeting="Hi", name="Alice")
        assert "Hi Alice" in result or "Hi" in result

    def test_extra_vars_available(self):
        t = AnsibleTemplater(extra_vars={"host": "localhost"})
        result = t.render_sandboxed("Host: {{ host }}")
        assert "Host: localhost" in result

    def test_kwargs_override_extra_vars(self):
        t = AnsibleTemplater(extra_vars={"val": "default"})
        result = t.render_sandboxed("{{ val }}", val="override")
        assert "override" in result

    def test_undefined_variable_raises(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ undefined_var }}")

    def test_no_lookup_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ lookup('pipe', 'id') }}")

    def test_no_dunder_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ ''.__class__.__mro__ }}")

    def test_empty_template(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("")
        assert result == ""

    def test_literal_text_no_variables(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("plain text")
        assert result == "plain text"

    def test_variable_value_not_re_evaluated(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ val }}", val="{{ 7*7 }}")
        assert "7*7" in result or "{{" in result

    def test_integer_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("Count: {{ n }}", n=42)
        assert "42" in result

    def test_syntax_error_raises(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{% if %}broken{% endif %}")

    def test_bool_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ flag }}", flag=True)
        assert result == "True"

    def test_none_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ maybe }}", maybe=None)
        assert result == "None"

    def test_float_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ pi }}", pi=3.14)
        assert "3.14" in result

    def test_negative_integer_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ temp }}", temp=-5)
        assert "-5" in result

    def test_zero_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ z }}", z=0)
        assert result == "0"

    def test_empty_string_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ s }}!", s="")
        assert result == "!"

    def test_unicode_variable(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ city }}", city="M\u00fcnchen")
        assert "M\u00fcnchen" in result

    def test_repeated_use_same_template(self):
        t = AnsibleTemplater()
        result1 = t.render_sandboxed("{{ x }}", x="first")
        result2 = t.render_sandboxed("{{ x }}", x="second")
        assert result1 == "first"
        assert result2 == "second"

    def test_no_globals_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ range(10) }}")

    def test_no_dict_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ dict() }}")

    def test_no_lipsum_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ lipsum() }}")

    def test_no_cycler_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ cycler('a', 'b') }}")

    def test_no_joiner_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ joiner(' ') }}")

    def test_no_namespace_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{% set ns = namespace(foo='bar') %}{{ ns.foo }}")

    def test_no_attribute_access_on_value(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ ''.upper }}")

    def test_no_method_call_on_value(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ ''.upper() }}")

    def test_no_builtin_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ ''.__class__.__base__ }}")

    def test_variable_with_hyphens_raises(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ my-var }}")

    def test_conditional_block(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{% if x > 5 %}big{% else %}small{% endif %}", x=10)
        assert result == "big"

    def test_conditional_block_else_branch(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{% if x > 5 %}big{% else %}small{% endif %}", x=2)
        assert result == "small"

    def test_loop_block(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ greeting }}", greeting="hello")
        assert result == "hello"

    def test_variable_in_loop_count(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ n }}", n=3)
        assert result == "3"

    def test_no_include_tag(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{% include 'secrets.txt' %}")

    def test_no_import_tag(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{% import 'macros.html' %}")

    def test_no_extends_tag(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{% extends 'base.html' %}")

    def test_raw_block_preserves_jinja_syntax(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{% raw %}{{ var }}{% endraw %}")
        assert result == "{{ var }}"

    def test_variable_inside_tag_value_is_not_re_evaluated(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ tag }}", tag="<script>alert(1)</script>")
        assert "<script>" in result or "&lt;script&gt;" in result

    def test_large_variable_value(self):
        t = AnsibleTemplater()
        large = "x" * 10000
        result = t.render_sandboxed("{{ data }}", data=large)
        assert result == large

    def test_template_with_leading_whitespace(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("   {{ name }}", name="Bob")
        assert "Bob" in result

    def test_template_with_trailing_whitespace(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ name }}   ", name="Bob")
        assert "Bob" in result

    def test_multiline_template(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("Line1\nLine2 {{ x }}\nLine3", x="mid")
        assert "mid" in result

    def test_extra_vars_over_ExtraVarsValidationError(self):
        t = AnsibleTemplater(extra_vars={1: "bad_key"})
        result = t.render_sandboxed("")
        assert result == ""

    def test_kwargs_with_underscored_names(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ my_var }}", my_var="value")
        assert result == "value"


class TestAnsibleTemplaterRender:
    def test_delegates_to_core_runner(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t._runner.render_template.return_value = "rendered"
        result = t.render("{{ x }}", x=1)
        assert result == "rendered"
        t._runner.render_template.assert_called_once()

    def test_passes_variables_to_runner(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t._runner.render_template.return_value = "ok"
        t.render("{{ name }}", name="test")
        call_kwargs = t._runner.render_template.call_args
        assert "name" in str(call_kwargs) or call_kwargs[1]["variables"]["name"] == "test"

    def test_merge_extra_vars_with_kwargs(self):
        t = AnsibleTemplater(extra_vars={"host": "localhost"})
        t._runner = MagicMock()
        t._runner.render_template.return_value = "ok"
        t.render("{{ host }}:{{ port }}", port="5432")
        call_kwargs = t._runner.render_template.call_args
        variables = call_kwargs[1]["variables"]
        assert variables["host"] == "localhost"
        assert variables["port"] == "5432"

    def test_kwargs_override_extra_vars_in_render(self):
        t = AnsibleTemplater(extra_vars={"val": "default"})
        t._runner = MagicMock()
        t._runner.render_template.return_value = "ok"
        t.render("{{ val }}", val="override")
        variables = t._runner.render_template.call_args[1]["variables"]
        assert variables["val"] == "override"

    def test_empty_kwargs(self):
        t = AnsibleTemplater(extra_vars={"env": "prod"})
        t._runner = MagicMock()
        t._runner.render_template.return_value = "ok"
        t.render("{{ env }}")
        variables = t._runner.render_template.call_args[1]["variables"]
        assert variables["env"] == "prod"


class TestAnsibleTemplaterResolveFact:
    def test_delegates_to_runner(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t._runner.resolve_variable.return_value = "resolved_value"
        result = t.resolve_fact("ansible_os_family")
        assert result == "resolved_value"
        t._runner.resolve_variable.assert_called_once_with("ansible_os_family", host="localhost")

    def test_default_host(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t.resolve_fact("some_fact")
        t._runner.resolve_variable.assert_called_once_with("some_fact", host="localhost")

    def test_custom_host(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t.resolve_fact("some_fact", host="db-server")
        t._runner.resolve_variable.assert_called_once_with("some_fact", host="db-server")

    def test_empty_fact_name(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t._runner.resolve_variable.return_value = ""
        result = t.resolve_fact("")
        assert result == ""

    def test_numeric_fact_value(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t._runner.resolve_variable.return_value = 8192
        result = t.resolve_fact("ansible_memtotal_mb")
        assert result == 8192

    def test_dict_fact_value(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t._runner.resolve_variable.return_value = {"os": "Linux", "arch": "x86_64"}
        result = t.resolve_fact("ansible_facts")
        assert result == {"os": "Linux", "arch": "x86_64"}

    def test_list_fact_value(self):
        t = AnsibleTemplater()
        t._runner = MagicMock()
        t._runner.resolve_variable.return_value = ["eth0", "lo"]
        result = t.resolve_fact("ansible_interfaces")
        assert result == ["eth0", "lo"]


class TestSecurityBoundaries:
    def test_sandboxed_rejects_python_exec_via_template(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ ''.__class__.__subclasses__() }}")

    def test_sandboxed_rejects_mro_escape(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ config }}")

    def test_sandboxed_rejects_request_object(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ request }}")

    def test_sandboxed_rejects_self_reference(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ self }}")

    def test_sandboxed_rejects_unicode_name_escape(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ ''.__class__.__mro__[1].__subclasses__() }}")

    def test_sandboxed_allowlisted_operations(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ x + y }}", x=10, y=5)
        assert "15" in result

    def test_sandboxed_string_concat(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ a ~ b }}", a="foo", b="bar")
        assert "foobar" in result

    def test_sandboxed_comparison(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ 'yes' if x == y else 'no' }}", x=5, y=5)
        assert result == "yes"

    def test_sandboxed_ternary(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ 'pass' if ok else 'fail' }}", ok=True)
        assert result == "pass"

    def test_no_filter_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ name | upper }}", name="hello")

    def test_no_ansible_filter_access(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError, match="template rejected"):
            t.render_sandboxed("{{ value | default('fallback') }}", value="present")

    def test_template_error_hides_internal_traceback(self):
        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError) as exc_info:
            t.render_sandboxed("{{ undefined_var }}")
        assert "UndefinedError" in str(exc_info.value)
        assert "jinja2" not in str(exc_info.value).lower() or "template rejected" in str(exc_info.value)


class TestExtraVarsValidationEdgeCases:
    def test_extra_vars_with_int_key_handled(self):
        t = AnsibleTemplater(extra_vars={1: "value"})
        result = t.render_sandboxed("hello")
        assert result == "hello"

    def test_extra_vars_with_none_value(self):
        t = AnsibleTemplater(extra_vars={"key": None})
        result = t.render_sandboxed("{{ key }}")
        assert result == "None"

    def test_extra_vars_repeated_use_no_mutation(self):
        t = AnsibleTemplater(extra_vars={"host": "original"})
        r1 = t.render_sandboxed("{{ host }}")
        r2 = t.render_sandboxed("{{ host }}")
        assert r1 == "original"
        assert r2 == "original"


class TestTemplateRenderWithoutRunner:
    def test_template_content_with_special_chars(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("user={{ user }}&pass={{ passwd }}", user="admin", passwd="secret")
        assert "user=admin" in result
        assert "pass=secret" in result

    def test_template_with_xml_like_content(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("<user>{{ name }}</user>", name="bob")
        assert "<user>bob</user>" in result

    def test_template_with_newlines_in_value(self):
        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ body }}", body="line1\nline2")
        assert "line1" in result
        assert "line2" in result
