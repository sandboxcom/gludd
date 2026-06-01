"""Unit tests for prompt registry."""

import pytest
from jinja2 import TemplateNotFound

from general_ludd.prompts.registry import PromptRegistry


class TestPromptRegistry:
    def test_register_and_render(self):
        reg = PromptRegistry()
        reg.register("greeting", "Hello {{ name }}!")
        result = reg.render("greeting", name="World")
        assert result == "Hello World!"

    def test_list_templates(self):
        reg = PromptRegistry()
        reg.register("a", "template a")
        reg.register("b", "template b")
        assert sorted(reg.list_templates()) == ["a", "b"]

    def test_render_missing_template_raises(self):
        reg = PromptRegistry()
        with pytest.raises(TemplateNotFound):
            reg.render("nonexistent")
