from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate


def prompt_registry_to_chat_template(
    registry: Any,
    prompt_name: str,
    **variables: object,
) -> ChatPromptTemplate:
    rendered: str = registry.render(prompt_name, **variables)
    return ChatPromptTemplate.from_messages([("human", rendered)])
