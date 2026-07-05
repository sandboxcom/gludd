from __future__ import annotations

from general_ludd.langchain.prompt_adapter import prompt_registry_to_chat_template
from general_ludd.prompts.registry import PromptRegistry


class TestPromptRegistryToChatTemplate:
    def test_basic_template_wrapping(self):
        registry = PromptRegistry()
        registry.register("greeting", "Hello, {{ name }}!")

        template = prompt_registry_to_chat_template(
            registry, "greeting", name="World"
        )

        messages = template.format_messages()
        assert len(messages) == 1
        assert messages[0].type == "human"
        assert messages[0].content == "Hello, World!"

    def test_no_variables(self):
        registry = PromptRegistry()
        registry.register("static", "Just a static prompt.")

        template = prompt_registry_to_chat_template(registry, "static")

        messages = template.format_messages()
        assert len(messages) == 1
        assert messages[0].type == "human"
        assert messages[0].content == "Just a static prompt."

    def test_multiple_variables(self):
        registry = PromptRegistry()
        registry.register("multi", "Role: {{ role }}\nTask: {{ task }}")

        template = prompt_registry_to_chat_template(
            registry, "multi", role="coder", task="write tests"
        )

        messages = template.format_messages()
        assert len(messages) == 1
        assert messages[0].type == "human"
        assert "coder" in messages[0].content
        assert "write tests" in messages[0].content

    def test_returns_chat_prompt_template_type(self):
        from langchain_core.prompts import ChatPromptTemplate as CPT

        registry = PromptRegistry()
        registry.register("greeting", "Hello, {{ name }}!")

        template = prompt_registry_to_chat_template(
            registry, "greeting", name="World"
        )

        assert isinstance(template, CPT)

    def test_prompt_rendered_with_jinja2_markup(self):
        registry = PromptRegistry()
        registry.register("loop", "{% for item in items %}- {{ item }}\n{% endfor %}")

        template = prompt_registry_to_chat_template(
            registry, "loop", items=["a", "b", "c"]
        )

        messages = template.format_messages()
        assert len(messages) == 1
        assert messages[0].type == "human"
        assert "- a" in messages[0].content
        assert "- b" in messages[0].content
        assert "- c" in messages[0].content
