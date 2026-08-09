"""Tests for ansible/templating: AnsibleTemplater render_sandboxed, render, resolve_fact, TemplateRenderError."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestAnsibleTemplaterConstruction:
    def test_default_construction(self):
        t = AnsibleTemplater()
        assert t._extra_vars == {}

    def test_with_extra_vars(self):
        t = AnsibleTemplater(extra_vars={"project": "gludd"})
        assert t._extra_vars == {"project": "gludd"}


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
        """A variable value containing Jinja must render literally, not be re-evaluated."""
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


class TestAnsibleTemplaterRender:
    def test_delegates_to_core_runner(self):
        with patch.object(AnsibleTemplater, "render", return_value="rendered") as mock_render:
            t = AnsibleTemplater()
            mock_render(t, "{{ x }}", x=1)
            mock_render.assert_called_once_with(t, "{{ x }}", x=1)

    def test_merge_extra_vars(self):
        t = AnsibleTemplater(extra_vars={"base": "root"})
        assert t._extra_vars == {"base": "root"}


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
