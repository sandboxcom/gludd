from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


def prompt_registry_to_chat_template(
    registry: object,
    prompt_name: str,
    **variables: object,
) -> ChatPromptTemplate:
    rendered: str = registry.render(prompt_name, **variables)  # type: ignore[attr-defined]
    return ChatPromptTemplate.from_messages([("human", rendered)])
