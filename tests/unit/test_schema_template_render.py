"""Tests for schema-driven Jinja2 templates.

These load the templates via Jinja2's FileSystemLoader directly (no FastAPI
daemon) so they exercise the template layer in isolation. The canonical-shape
``page.html.j2`` is covered separately; these tests pin the formal-spec
templates (``schema_page.html.j2``, ``_schema_field.html.j2``,
``schema_error.html.j2``).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

RENDER_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "general_ludd"
    / "templates"
    / "render"
)


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(RENDER_DIR)),
        autoescape=select_autoescape(["html", "j2", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render(template_name: str, **context: object) -> str:
    return _env().get_template(template_name).render(**context)


# ---------------------------------------------------------------------------
# schema_page.html.j2 + _schema_field.html.j2
# ---------------------------------------------------------------------------


def test_render_simple_string_field() -> None:
    schema = {
        "title": "User Profile",
        "description": "A simple user profile",
        "type": "object",
        "properties": {
            "name": {"type": "string", "title": "Name"},
        },
    }
    data = {"name": "Alice"}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "User Profile" in html
    assert "A simple user profile" in html
    assert "Alice" in html
    assert "Name" in html


def test_render_enum_field_renders_select() -> None:
    schema = {
        "title": "Settings",
        "type": "object",
        "properties": {
            "color": {
                "type": "string",
                "title": "Color",
                "enum": ["red", "green", "blue"],
            },
        },
    }
    data = {"color": "green"}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "<select" in html
    assert "red" in html
    assert "green" in html
    assert "blue" in html
    # current value is selected
    assert "selected" in html


def test_render_array_of_objects_as_table() -> None:
    schema = {
        "title": "Hosts",
        "type": "object",
        "properties": {
            "hosts": {
                "type": "array",
                "title": "Host list",
                "items": {
                    "type": "object",
                    "properties": {
                        "hostname": {"type": "string", "title": "Hostname"},
                        "cpus": {"type": "integer", "title": "CPUs"},
                    },
                },
            },
        },
    }
    data = {
        "hosts": [
            {"hostname": "node-1", "cpus": 4},
            {"hostname": "node-2", "cpus": 8},
        ],
    }
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "<table" in html
    # header row uses item property titles
    assert "Hostname" in html
    assert "CPUs" in html
    assert "node-1" in html
    assert "node-2" in html


def test_render_nested_object_as_fieldset() -> None:
    schema = {
        "title": "Config",
        "type": "object",
        "properties": {
            "network": {
                "type": "object",
                "title": "Network",
                "properties": {
                    "host": {"type": "string", "title": "Host"},
                    "port": {"type": "integer", "title": "Port"},
                },
            },
        },
    }
    data = {"network": {"host": "localhost", "port": 8080}}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "<fieldset" in html
    # nested titles appear inside the fieldset
    assert "Network" in html
    assert "localhost" in html
    assert "8080" in html


def test_render_missing_required_marked() -> None:
    schema = {
        "title": "Required demo",
        "type": "object",
        "required": ["email"],
        "properties": {
            "email": {"type": "string", "title": "Email"},
        },
    }
    # data is empty -> required field has no value
    data: dict[str, object] = {}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "field-required" in html
    assert "*" in html


def test_render_error_template_shows_path() -> None:
    schema = {"title": "Failing Schema", "type": "object"}
    errors = [
        {
            "path": "$.foo",
            "message": "foo is required",
            "schema_snippet": '{"type": "object", "required": ["foo"]}',
        },
    ]
    html = _render("schema_error.html.j2", schema=schema, errors=errors)
    assert "$.foo" in html
    assert "foo is required" in html
    # schema title used in header
    assert "Failing Schema" in html


def test_value_escaping() -> None:
    payload = "<script>alert(1)</script>"
    schema = {
        "title": "Escaping",
        "type": "object",
        "properties": {"comment": {"type": "string", "title": "Comment"}},
    }
    data = {"comment": payload}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "&lt;script&gt;" in html
    # never unescaped
    assert "<script>alert(1)</script>" not in html


def test_uri_format_renders_link() -> None:
    schema = {
        "title": "Links",
        "type": "object",
        "properties": {
            "homepage": {
                "type": "string",
                "title": "Homepage",
                "format": "uri",
            },
        },
    }
    data = {"homepage": "https://example.com"}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "<a href=" in html
    assert "https://example.com" in html


# ---------------------------------------------------------------------------
# Additional dispatch coverage
# ---------------------------------------------------------------------------


def test_render_boolean_field_renders_checkbox() -> None:
    schema = {
        "title": "Flags",
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "title": "Enabled"},
        },
    }
    data = {"enabled": True}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert 'type="checkbox"' in html
    assert "checked" in html


def test_render_array_of_scalars_renders_ul() -> None:
    schema = {
        "title": "Tags",
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    data = {"tags": ["a", "b", "c"]}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "<ul>" in html
    assert "<li>a</li>" in html
    assert "<table" not in html.split("<ul")[0]  # no table for scalar array


def test_render_number_with_unit_suffix() -> None:
    schema = {
        "title": "Metrics",
        "type": "object",
        "properties": {
            "cost": {"type": "number", "title": "Cost", "unit": "USD"},
        },
    }
    data = {"cost": 12.5}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "12.5" in html
    assert "USD" in html


def test_render_datetime_format_uses_time_element() -> None:
    schema = {
        "title": "Timestamps",
        "type": "object",
        "properties": {
            "created_at": {
                "type": "string",
                "title": "Created At",
                "format": "date-time",
            },
        },
    }
    data = {"created_at": "2026-06-28T14:03:22Z"}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "<time" in html
    assert "2026-06-28T14:03:22Z" in html


def test_render_null_type_renders_em_null() -> None:
    schema = {
        "title": "Null demo",
        "type": "object",
        "properties": {
            "absent": {"type": "null", "title": "Absent"},
        },
    }
    data: dict[str, object] = {"absent": None}
    html = _render("schema_page.html.j2", schema=schema, data=data)
    assert "<em>null</em>" in html


def test_render_error_template_no_errors() -> None:
    html = _render("schema_error.html.j2", errors=[])
    assert "No error details available" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
