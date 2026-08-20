"""Normalize provider responses into executable generated Python source."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence

_PYTHON_FENCE = re.compile(
    r"```[ \t]*(?P<language>python|py)?[ \t]*\r?\n(?P<code>.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_PYTHON_FENCE_START = re.compile(
    r"```[ \t]*(?:python|py)?[ \t]*\r?\n",
    re.IGNORECASE,
)
_TEXT_BLOCK_TYPES = frozenset({"text", "output_text", "input_text"})


def ensure_lifecycle_start_method(source: str, *, class_name: str) -> str:
    """Add the minimal explicit ``start`` transition to one generated game class.

    Small local models sometimes implement the complete state machine but omit
    the mechanically simple ``ready`` to ``playing`` transition requested by
    the game contract.  Only AST-valid source with an exact top-level class
    match is eligible.  Existing methods and unrepairable candidates are
    returned byte-for-byte so downstream validation remains fail closed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    target = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )
    if target is None or any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "start"
        for node in target.body
    ):
        return source

    target.body.append(
        ast.FunctionDef(
            name="start",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg="self")],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=[
                ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr="state",
                            ctx=ast.Store(),
                        )
                    ],
                    value=ast.Constant(value="playing"),
                )
            ],
            decorator_list=[],
            returns=None,
            type_comment=None,
        )
    )
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def normalize_generated_python(response: object) -> str:
    """Return Python source from a LangChain/OpenAI-compatible response.

    LangChain messages may expose content as either a string or a list of typed
    content blocks.  Gludd's gateway also retains the original message on
    ``raw_response`` while keeping a string-compatible public response.  Prefer
    that original structure so a list is never parsed through its Python
    ``repr``.  Markdown is presentation metadata, so supported Python and
    unlabelled fences are removed before the game validator sees the source.
    """
    raw_response = getattr(response, "raw_response", None)
    for candidate in (raw_response, response):
        if candidate is None:
            continue
        text = _message_text(candidate)
        if text.strip():
            return _extract_python_fence(text)
    raise RuntimeError("LLM response contained no text content")


def _message_text(message: object) -> str:
    """Use LangChain's public text adapter when present, then its content."""
    text_adapter = getattr(message, "text", None)
    if isinstance(text_adapter, str) and text_adapter.strip():
        return text_adapter
    if callable(text_adapter):
        text = text_adapter()
        if isinstance(text, str) and text.strip():
            return text
    return _content_text(getattr(message, "content", message))


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        block_type = content.get("type")
        if block_type is not None and str(block_type).lower() not in _TEXT_BLOCK_TYPES:
            return ""
        for key in ("text", "content", "value"):
            if key in content:
                return _content_text(content[key])
        return ""
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        return "\n".join(filter(None, (_content_text(block) for block in content)))
    return ""


def _extract_python_fence(text: str) -> str:
    for match in _PYTHON_FENCE.finditer(text):
        code = match.group("code").strip()
        if code:
            return code
    opening_fence = _PYTHON_FENCE_START.search(text)
    if opening_fence is not None:
        return text[opening_fence.end() :].strip()
    return text.strip()
