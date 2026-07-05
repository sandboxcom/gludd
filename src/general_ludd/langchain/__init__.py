"""LangChain integration adapters for gludd subsystems."""

from general_ludd.langchain.prompt_adapter import prompt_registry_to_chat_template

__all__ = [
    "prompt_registry_to_chat_template",
]
