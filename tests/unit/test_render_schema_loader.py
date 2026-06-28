"""TDD tests for the JSON Schema loader+validator (renderers.schema_loader).

Spec: docs/design/PLAYBOOK_WEB_RENDERER.md §3.3 (b) — companion JSON Schema
mechanism for renderer playbooks. This module makes schema-driven rendering
first-class: a renderer can be defined by ANY formal JSON Schema, not just the
closed canonical section set in ``schema.py``.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from general_ludd.renderers.schema_loader import (
    FieldMeta,
    extract_field_metadata,
    load_schema,
    validate_against_schema,
)


def test_load_schema_from_file(tmp_path: Path) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"title": {"type": "string"}},
    }
    p = tmp_path / "foo.schema.json"
    p.write_text(json.dumps(schema))
    result = load_schema(p)
    assert result is not None
    assert result["type"] == "object"
    assert "title" in result["properties"]


def test_load_schema_missing_file_returns_none(tmp_path: Path) -> None:
    result = load_schema(tmp_path / "nope.json")
    assert result is None


_SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "count": {"type": "number"},
    },
    "required": ["title"],
}


def test_validate_data_against_schema_success() -> None:
    ok, errors = validate_against_schema({"title": "x", "count": 3}, _SIMPLE_SCHEMA)
    assert ok is True
    assert errors == []


def test_validate_data_against_schema_missing_required() -> None:
    ok, errors = validate_against_schema({}, _SIMPLE_SCHEMA)
    assert ok is False
    assert len(errors) >= 1
    assert any("title" in e for e in errors)


def test_validate_data_against_schema_wrong_type() -> None:
    ok, errors = validate_against_schema({"title": 42}, _SIMPLE_SCHEMA)
    assert ok is False
    assert len(errors) >= 1


def test_extract_field_metadata() -> None:
    schema = {
        "type": "object",
        "required": ["title"],
        "properties": {
            "title": {
                "type": "string",
                "title": "Title heading",
                "description": "The page title.",
                "enum": ["A", "B"],
                "format": "iri",
            },
            "count": {"type": "number", "description": "How many things."},
        },
    }
    fields = extract_field_metadata(schema)
    assert len(fields) == 2

    by_name = {f.name: f for f in fields}

    title = by_name["title"]
    assert title.name == "title"
    assert title.title == "Title heading"
    assert title.description == "The page title."
    assert title.type == "string"
    assert title.required is True
    assert title.enum == ["A", "B"]
    assert title.format == "iri"

    count = by_name["count"]
    assert count.title == "count"
    assert count.description == "How many things."
    assert count.required is False
    assert count.enum is None
    assert count.format is None


def test_extract_field_metadata_handles_nested_object() -> None:
    schema = {
        "type": "object",
        "properties": {
            "address": {
                "type": "object",
                "title": "Address",
                "description": "Mailing address.",
                "properties": {
                    "street": {"type": "string"},
                    "zip": {"type": "string"},
                },
                "required": ["street"],
            },
        },
    }
    fields = extract_field_metadata(schema)
    assert len(fields) == 1
    address = fields[0]
    assert address.type == "object"
    assert address.children is not None
    assert len(address.children) == 2
    names = {c.name for c in address.children}
    assert names == {"street", "zip"}
    street = next(c for c in address.children if c.name == "street")
    assert street.required is True


def test_extract_field_metadata_handles_array_items() -> None:
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "cost": {"type": "number"},
                    },
                    "required": ["model"],
                },
            },
        },
    }
    fields = extract_field_metadata(schema)
    assert len(fields) == 1
    rows = fields[0]
    assert rows.type == "array"
    assert rows.items is not None
    item_meta = rows.items
    assert item_meta.type == "object"
    assert item_meta.children is not None
    assert len(item_meta.children) == 2
    names = {c.name for c in item_meta.children}
    assert names == {"model", "cost"}


def test_schema_with_no_properties_returns_empty() -> None:
    fields = extract_field_metadata({"type": "object"})
    assert fields == []


def test_field_meta_to_dict_for_templates() -> None:
    fm = FieldMeta(
        name="n",
        title="N",
        description="d",
        type="string",
        required=True,
        enum=["a"],
        format=None,
        children=None,
        items=None,
    )
    d = fm.to_dict()
    assert d == {
        "name": "n",
        "title": "N",
        "description": "d",
        "type": "string",
        "required": True,
        "enum": ["a"],
        "format": None,
        "children": None,
        "items": None,
    }


def test_draft_detection_2020_12() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }
    ok, errors = validate_against_schema({"x": "v"}, schema)
    assert ok is True
    assert errors == []


def test_draft_detection_draft_07_emits_deprecation_warning() -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ok, errors = validate_against_schema({"x": "v"}, schema)
    assert ok is True
    assert errors == []
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_extract_field_metadata_malformed_raises_value_error() -> None:
    with pytest.raises(ValueError):
        extract_field_metadata({"type": "object", "properties": ["not", "a", "dict"]})


def test_validate_against_schema_missing_type_is_treated_permissively() -> None:
    ok, _errors = validate_against_schema({"anything": 1}, {})
    assert ok is True
