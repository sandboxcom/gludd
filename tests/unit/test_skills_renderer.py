"""Unit tests for skills/renderer.py — Jinja2 skill body renderer."""

from __future__ import annotations

import pytest

from general_ludd.skills.renderer import SkillRenderError, render_skill


class TestSkillRenderError:
    def test_is_value_error_subclass(self) -> None:
        assert issubclass(SkillRenderError, ValueError)

    def test_instantiable_with_message(self) -> None:
        err = SkillRenderError("test error message")
        assert str(err) == "test error message"


class TestRenderSkillBasic:
    def test_renders_plain_text(self) -> None:
        result = render_skill("Hello, world!")
        assert result == "Hello, world!"

    def test_renders_variable_substitution(self) -> None:
        result = render_skill("Hello, {{ name }}!", {"name": "Alice"})
        assert result == "Hello, Alice!"

    def test_renders_multiple_variables(self) -> None:
        result = render_skill(
            "{{ greeting }}, {{ name }}!",
            {"greeting": "Hi", "name": "Bob"},
        )
        assert result == "Hi, Bob!"

    def test_no_variables_dict_plain_text_passes(self) -> None:
        result = render_skill("Hello, world!")
        assert result == "Hello, world!"

    def test_empty_body(self) -> None:
        result = render_skill("")
        assert result == ""

    def test_body_with_jinja_conditionals(self) -> None:
        body = "{% if x %}yes{% else %}no{% endif %}"
        result = render_skill(body, {"x": True})
        assert result == "yes"

    def test_body_with_jinja_loop(self) -> None:
        body = "{% for item in items %}{{ item }}{% endfor %}"
        result = render_skill(body, {"items": ["a", "b"]})
        assert result == "ab"


class TestRenderSkillErrorCases:
    def test_undefined_variable_raises_skill_render_error(self) -> None:
        with pytest.raises(SkillRenderError, match="undefined variable"):
            render_skill("{{ missing_var }}")

    def test_sandbox_violation_raises_skill_render_error(self) -> None:
        with pytest.raises(SkillRenderError, match="sandbox-forbidden"):
            render_skill("{{ ''.__class__.__mro__ }}")

    def test_global_access_blocked(self) -> None:
        with pytest.raises(SkillRenderError):
            render_skill("{{ cycler.__init__.__globals__ }}")
