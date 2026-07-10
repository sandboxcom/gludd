from __future__ import annotations

import logging
from typing import Any

import jinja2.exceptions
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)


class PromptRenderError(RuntimeError):
    """Raised when a registry prompt template fails to render.

    Wraps a ``jinja2.exceptions.TemplateError`` (which includes
    ``SecurityError`` for a sandboxed-template violation) so callers of this
    public API get a typed, documented failure instead of a raw Jinja2
    exception leaking across the module boundary.
    """


def prompt_registry_to_chat_template(
    registry: Any,
    prompt_name: str,
    **variables: object,
) -> ChatPromptTemplate:
    try:
        rendered: str = registry.render(prompt_name, **variables)
    except jinja2.exceptions.TemplateError as exc:
        logger.warning(
            "Prompt render failed for %r: %s", prompt_name, exc, exc_info=True
        )
        raise PromptRenderError(
            f"failed to render prompt {prompt_name!r}: {exc}"
        ) from exc
    return ChatPromptTemplate.from_messages([("human", rendered)])
