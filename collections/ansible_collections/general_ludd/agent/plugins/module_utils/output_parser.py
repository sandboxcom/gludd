"""
Thin Ansible-compatible output parsing utilities for model responses.

Parsers extract and validate structured output from model-generated text.
Stdlib + jsonschema; Pydantic available when installed.

Usage in a module::

    from ansible_collections.general_ludd.agent.plugins.module_utils.output_parser import (
        JsonOutputParser,
        PydanticOutputParser,
        MarkdownOutputParser,
        parse_model_response,
    )

    result = JsonOutputParser(schema=my_schema).parse(model_text)
    typed = PydanticOutputParser(model=MyModel).parse(model_text)
    md = MarkdownOutputParser().parse(model_text)
"""

from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Utility: JSON extraction helpers
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Extract and parse JSON from text. Tries code blocks first, then raw objects."""
    code_block_match = _JSON_BLOCK_RE.search(text)
    if code_block_match:
        return json.loads(code_block_match.group(1))

    for pattern in (_JSON_OBJECT_RE, _JSON_ARRAY_RE):
        matches = list(pattern.finditer(text))
        for m in matches:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue

    raise ValueError(f"No valid JSON found in text: {text[:200]}")


# ---------------------------------------------------------------------------
# OutputParser — base class
# ---------------------------------------------------------------------------


class OutputParser:
    """Abstract base for output parsers."""

    def parse(self, text: str) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# JsonOutputParser
# ---------------------------------------------------------------------------


class JsonOutputParser(OutputParser):
    """Extract JSON from model output, optionally validate against a JSON Schema."""

    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self._schema = schema

    def parse(self, text: str) -> Any:
        data = _extract_json(text)
        if self._schema is not None:
            import jsonschema

            try:
                jsonschema.validate(instance=data, schema=self._schema)
            except jsonschema.ValidationError as exc:
                raise ValueError(str(exc)) from exc
        return data


# ---------------------------------------------------------------------------
# PydanticOutputParser
# ---------------------------------------------------------------------------


class PydanticOutputParser(OutputParser):
    """Extract JSON and validate against a Pydantic model. Returns typed objects."""

    def __init__(self, model: type[Any]) -> None:
        import pydantic

        if not issubclass(model, pydantic.BaseModel):
            raise TypeError(f"model must be a pydantic.BaseModel subclass, got {model}")
        self._model = model

    def parse(self, text: str) -> Any:
        data = _extract_json(text)
        return self._model.model_validate(data)


# ---------------------------------------------------------------------------
# MarkdownOutputParser — helpers
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(r"```(\w*)\s*\n?(.*?)\n?```", re.DOTALL)
_UNORDERED_LIST_RE = re.compile(r"^[\-\*]\s+(.*)", re.MULTILINE)
_ORDERED_LIST_RE = re.compile(r"^\d+\.\s+(.*)", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|.*\|$", re.MULTILINE)

# Separator row patterns: |---| or |:---| etc.
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+$")


def _extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Return list of (language, content) tuples from fenced code blocks."""
    return [(lang.strip() or "", content.strip()) for lang, content in _CODE_BLOCK_RE.findall(text)]


def _extract_lists(
    text: str,
) -> list[list[str]]:
    """
    Extract markdown lists from text. Returns a list of lists — each
    inner list is one contiguous markdown list (unordered or ordered).
    """
    lines = text.splitlines()
    results: list[list[str]] = []
    current: list[str] = []
    current_kind: str | None = None

    for line in lines:
        stripped = line.strip()
        ul_match = _UNORDERED_LIST_RE.match(stripped)
        ol_match = _ORDERED_LIST_RE.match(stripped)

        if ul_match:
            kind = "ul"
            item = ul_match.group(1).strip()
        elif ol_match:
            kind = "ol"
            item = ol_match.group(1).strip()
        else:
            kind = None
            item = ""

        if kind is not None:
            if current_kind is None:
                current_kind = kind
            if kind == current_kind:
                current.append(item)
            else:
                if current:
                    results.append(current)
                current = [item]
                current_kind = kind
        else:
            if current:
                results.append(current)
                current = []
                current_kind = None

    if current:
        results.append(current)

    return results


def _extract_tables(
    text: str,
) -> list[dict[str, Any]]:
    """
    Extract markdown tables from text. Returns a list of dicts, each with
    ``headers`` and ``rows`` keys.
    """
    lines = text.splitlines()
    tables: list[dict[str, Any]] = []
    current_rows: list[list[str]] = []
    headers: list[str] = []
    found_sep = False

    for line in lines:
        stripped = line.strip()
        if not _TABLE_ROW_RE.match(stripped):
            if headers and current_rows:
                tables.append({"headers": headers, "rows": current_rows})
            headers = []
            current_rows = []
            found_sep = False
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]

        if _TABLE_SEP_RE.match(stripped):
            found_sep = True
            continue

        if not found_sep:
            headers = cells
        else:
            current_rows.append(cells)

    if headers and current_rows:
        tables.append({"headers": headers, "rows": current_rows})

    return tables


# ---------------------------------------------------------------------------
# MarkdownOutputParser
# ---------------------------------------------------------------------------


class MarkdownOutputParser(OutputParser):
    """Extract code blocks, lists, and tables from markdown text."""

    def parse(self, text: str) -> dict[str, Any]:
        return {
            "code_blocks": self.extract_code_blocks(text),
            "lists": self.extract_lists(text),
            "tables": self.extract_tables(text),
        }

    def extract_code_blocks(self, text: str) -> list[tuple[str, str]]:
        return _extract_code_blocks(text)

    def extract_lists(self, text: str) -> list[list[str]]:
        return _extract_lists(text)

    def extract_tables(self, text: str) -> list[dict[str, Any]]:
        return _extract_tables(text)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_PARSER_MAP: dict[str, type[OutputParser]] = {
    "json": JsonOutputParser,
    "pydantic": PydanticOutputParser,
    "markdown": MarkdownOutputParser,
}


def parse_model_response(
    text: str,
    parser_type: str = "json",
    **kwargs: Any,
) -> Any:
    """
    Parse model response text using the named parser type.

    ``parser_type`` values: ``"json"``, ``"pydantic"``, ``"markdown"``.

    Keyword arguments are forwarded to the parser constructor — e.g.
    ``schema=...`` for JSON, ``model=...`` for Pydantic.
    """
    cls = _PARSER_MAP.get(parser_type)
    if cls is None:
        raise ValueError(f"Unknown parser type: {parser_type!r}. Choose from: {list(_PARSER_MAP)}")
    parser = cls(**kwargs)
    return parser.parse(text)
