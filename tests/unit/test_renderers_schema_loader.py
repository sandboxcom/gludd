"""Structural tests for renderers/schema_loader.py — JSON Schema loader + validator."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from general_ludd.renderers.schema_loader import (
    FieldMeta,
    extract_field_metadata,
    load_schema,
)


class TestFieldMeta:
    def test_minimal_construction(self):
        fm = FieldMeta(name="foo", title="Foo", description="A foo", type="string")
        assert fm.name == "foo"
        assert fm.required is False
        assert fm.enum is None
        assert fm.format is None
        assert fm.children is None
        assert fm.items is None

    def test_with_enum(self):
        fm = FieldMeta(name="status", title="Status", description="", type="string", enum=["a", "b"])
        assert fm.enum == ["a", "b"]

    def test_to_dict_minimal(self):
        fm = FieldMeta(name="foo", title="Foo", description="", type="string")
        d = fm.to_dict()
        assert d["name"] == "foo"
        assert d["type"] == "string"
        assert d["required"] is False
        assert d["children"] is None

    def test_to_dict_with_children(self):
        child = FieldMeta(name="bar", title="Bar", description="", type="integer")
        fm = FieldMeta(name="foo", title="Foo", description="", type="object", children=[child])
        d = fm.to_dict()
        assert d["children"] is not None
        assert len(d["children"]) == 1
        assert d["children"][0]["name"] == "bar"

    def test_to_dict_with_items(self):
        item = FieldMeta(name="row", title="Row", description="", type="string")
        fm = FieldMeta(name="tags", title="Tags", description="", type="array", items=item)
        d = fm.to_dict()
        assert d["items"] is not None
        assert d["items"]["name"] == "row"


class TestLoadSchema:
    def test_loads_valid_json_schema(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"type": "object", "properties": {"name": {"type": "string"}}}, f)
            f.flush()
            path = Path(f.name)
        try:
            schema = load_schema(path)
            assert schema is not None
            assert schema["type"] == "object"
        finally:
            path.unlink()

    def test_returns_none_for_missing_file(self):
        path = Path("/tmp/nonexistent_schema_999.json")
        assert load_schema(path) is None

    def test_raises_on_non_dict_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            f.flush()
            path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="JSON object"):
                load_schema(path)
        finally:
            path.unlink()


class TestExtractFieldMetadata:
    def test_basic_properties(self):
        schema = {"type": "object", "properties": {"name": {"type": "string", "title": "Name"}}}
        fields = extract_field_metadata(schema)
        assert len(fields) == 1
        assert fields[0].name == "name"
        assert fields[0].type == "string"

    def test_required_field(self):
        schema = {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}
        fields = extract_field_metadata(schema)
        assert fields[0].required is True

    def test_no_properties_returns_empty(self):
        schema: dict[str, object] = {"type": "object"}
        fields = extract_field_metadata(schema)
        assert fields == []

    def test_missing_properties_is_empty(self):
        schema: dict[str, object] = {}
        fields = extract_field_metadata(schema)
        assert fields == []

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {"street": {"type": "string"}},
                    "required": ["street"],
                }
            },
        }
        fields = extract_field_metadata(schema)
        assert len(fields) == 1
        assert fields[0].children is not None
        assert len(fields[0].children) == 1
        assert fields[0].children[0].required is True

    def test_array_with_scalar_items(self):
        schema = {
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        }
        fields = extract_field_metadata(schema)
        assert fields[0].items is not None
        assert fields[0].items.type == "string"

    def test_array_with_object_items(self):
        schema = {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"col1": {"type": "string"}}},
                }
            },
        }
        fields = extract_field_metadata(schema)
        assert fields[0].items is not None
        assert fields[0].items.type == "object"
        assert fields[0].items.children is not None

    def test_non_dict_schema_raises(self):
        with pytest.raises(ValueError, match="Expected schema to be a dict"):
            extract_field_metadata([])  # pyright: ignore[reportArgumentType]

    def test_non_dict_properties_raises(self):
        schema: dict[str, object] = {"properties": ["not", "a", "dict"]}
        with pytest.raises(ValueError, match="must be a dict"):
            extract_field_metadata(schema)

    def test_enum_on_scalar(self):
        schema = {
            "type": "object",
            "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
        }
        fields = extract_field_metadata(schema)
        assert fields[0].enum == ["red", "green", "blue"]

    def test_format_field(self):
        schema = {
            "type": "object",
            "properties": {"email": {"type": "string", "format": "email"}},
        }
        fields = extract_field_metadata(schema)
        assert fields[0].format == "email"

    def test_malformed_property_is_type_any(self):
        schema = {"type": "object", "properties": {"bad": "not a dict"}}
        fields = extract_field_metadata(schema)
        assert fields[0].type == "any"

    def test_description_preserved(self):
        schema = {
            "type": "object",
            "properties": {"desc": {"type": "string", "description": "A helpful description"}},
        }
        fields = extract_field_metadata(schema)
        assert fields[0].description == "A helpful description"
