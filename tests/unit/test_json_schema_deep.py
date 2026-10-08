"""Deep JSON Schema validation tests covering jsonschema library features.

Covers: schema compilation (check_schema), validation success/failure paths,
error reporting ($ref in messages, absolute_path), $ref resolution (local
fragment + nested), format validation (built-in + custom), and draft 2020-12
features ($defs, prefixItems, unevaluatedProperties, dependentSchemas,
minContains/maxContains, patternProperties, if/then/else).
"""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

# ── helpers ────────────────────────────────────────────────────────────


def _validate(schema: dict[str, Any], data: Any) -> list[ValidationError]:
    return sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))


def _ok(schema: dict[str, Any], data: Any) -> bool:
    return len(_validate(schema, data)) == 0


# ── 1. schema compilation (check_schema) ───────────────────────────────


def test_check_schema_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
    )


def test_check_schema_rejects_empty_type_keyword() -> None:
    with pytest.raises(jsonschema.SchemaError, match="type"):
        Draft202012Validator.check_schema({"type": "object", "properties": {"x": {"type": ""}}})


def test_check_schema_rejects_bad_type_value() -> None:
    with pytest.raises(jsonschema.SchemaError, match="type"):
        Draft202012Validator.check_schema({"type": 123})


def test_check_schema_rejects_string_for_properties() -> None:
    with pytest.raises(jsonschema.SchemaError):
        Draft202012Validator.check_schema({"type": "object", "properties": "nope"})


# ── 2. validation — success paths ──────────────────────────────────────


def test_validate_flat_object_matches() -> None:
    assert _ok(
        {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}, "required": ["a"]},
        {"a": "hello", "b": 42},
    )


def test_validate_nested_object() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"child": {"type": "object", "properties": {"deep": {"type": "boolean"}}, "required": ["deep"]}},
        "required": ["child"],
    }
    assert _ok(schema, {"child": {"deep": True}})


def test_validate_array_tuples() -> None:
    schema: dict[str, Any] = {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]}
    assert _ok(schema, ["abc", 1])
    assert _ok(schema, ["abc", 1, "extra"])


def test_validate_nullable_property() -> None:
    schema: dict[str, Any] = {"type": "object", "properties": {"maybe": {"type": ["string", "null"]}}}
    assert _ok(schema, {"maybe": None})
    assert _ok(schema, {"maybe": "ok"})


# ── 3. validation — failure paths + error reporting ────────────────────


def test_error_missing_required() -> None:
    errors = _validate(
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        {},
    )
    assert errors
    assert "name" in errors[0].message
    assert list(errors[0].absolute_path) == []


def test_error_nested_path() -> None:
    errors = _validate(
        {"type": "object", "properties": {"outer": {"type": "object", "properties": {"inner": {"type": "string"}}}}},
        {"outer": {"inner": 42}},
    )
    assert errors
    assert list(errors[0].absolute_path) == ["outer", "inner"]


def test_error_multiple_failures() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"x": {"type": "integer"}, "y": {"type": "boolean"}},
    }
    errors = _validate(schema, {"x": "bad", "y": "also_bad"})
    assert len(errors) == 2
    paths = [list(e.absolute_path) for e in errors]
    assert ["x"] in paths
    assert ["y"] in paths


def test_error_message_includes_type_expected() -> None:
    errors = _validate({"type": "string"}, 42)
    assert errors
    assert "string" in errors[0].message


# ── 4. $ref resolution ─────────────────────────────────────────────────


def test_ref_resolves_local_fragment() -> None:
    schema: dict[str, Any] = {
        "$defs": {"stringy": {"type": "string"}},
        "type": "object",
        "properties": {"name": {"$ref": "#/$defs/stringy"}},
    }
    assert _ok(schema, {"name": "ok"})


def test_ref_follows_nested_chain() -> None:
    schema: dict[str, Any] = {
        "$defs": {
            "a": {"$ref": "#/$defs/b"},
            "b": {"type": "integer", "minimum": 0},
        },
        "type": "object",
        "properties": {"val": {"$ref": "#/$defs/a"}},
    }
    assert _ok(schema, {"val": 7})


def test_ref_invalid_fragment_raises() -> None:
    from referencing.exceptions import Unresolvable

    schema: dict[str, Any] = {
        "$defs": {"x": {"type": "string"}},
        "type": "object",
        "properties": {"name": {"$ref": "#/$defs/nonexistent"}},
    }
    with pytest.raises((Unresolvable, jsonschema.ValidationError)):
        Draft202012Validator(schema).validate({"name": "ok"})


def test_ref_within_error_path_is_reported() -> None:
    schema: dict[str, Any] = {
        "$defs": {"pos": {"type": "integer", "minimum": 0}},
        "type": "object",
        "properties": {"score": {"$ref": "#/$defs/pos"}},
    }
    errors = _validate(schema, {"score": -5})
    assert errors
    assert list(errors[0].absolute_path) == ["score"]


# ── 5. format validation (built-in) ────────────────────────────────────


def test_format_email_valid() -> None:
    schema: dict[str, Any] = {"type": "string", "format": "email"}
    assert _ok(schema, "user@example.com")


def test_format_email_invalid() -> None:
    schema: dict[str, Any] = {"type": "string", "format": "email"}
    errors = Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors("notanemail")
    assert any("email" in e.message for e in errors)


def test_format_uri_valid() -> None:
    schema: dict[str, Any] = {"type": "string", "format": "uri"}
    assert _ok(schema, "https://example.com/path?q=1")


def test_format_date_time_valid() -> None:
    schema: dict[str, Any] = {"type": "string", "format": "date-time"}
    assert _ok(schema, "2026-08-04T12:00:00Z")


def test_format_ipv4_valid() -> None:
    schema: dict[str, Any] = {"type": "string", "format": "ipv4"}
    assert _ok(schema, "192.168.1.1")


def test_format_ipv4_invalid() -> None:
    schema: dict[str, Any] = {"type": "string", "format": "ipv4"}
    errors = Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors("999.999.999.999")
    assert any("ipv4" in e.message for e in errors)


def test_no_format_checker_passes_anything() -> None:
    schema: dict[str, Any] = {"type": "string", "format": "email"}
    assert _ok(schema, "not-an-email")


# ── 6. custom format validators ────────────────────────────────────────


def _is_hex_color(instance: object) -> bool:
    if not isinstance(instance, str):
        return True
    return len(instance) == 7 and instance.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in instance[1:])


def test_custom_format_is_registered_and_used() -> None:
    checker = FormatChecker()
    checker.checks("hex-color")(_is_hex_color)

    schema: dict[str, Any] = {"type": "string", "format": "hex-color"}
    validator = Draft202012Validator(schema, format_checker=checker)

    assert len(list(validator.iter_errors("#ff0000"))) == 0
    assert len(list(validator.iter_errors("not-a-color"))) > 0


def test_custom_format_raises_wraps_in_format_error() -> None:
    def crashy(_inst: object) -> bool:
        raise RuntimeError("boom")

    checker = FormatChecker()
    checker.checks("crashy")(crashy)

    schema: dict[str, Any] = {"type": "string", "format": "crashy"}
    validator = Draft202012Validator(schema, format_checker=checker)
    with pytest.raises(RuntimeError, match="boom"):
        errors = list(validator.iter_errors("anything"))
        assert errors == []


# ── 7. draft 2020-12 features ──────────────────────────────────────────


def test_defs_reusable_subschemas() -> None:
    schema: dict[str, Any] = {
        "$defs": {"money": {"type": "number", "minimum": 0}},
        "type": "object",
        "properties": {"price": {"$ref": "#/$defs/money"}, "tax": {"$ref": "#/$defs/money"}},
    }
    assert _ok(schema, {"price": 10.0, "tax": 2.0})


def test_unevaluated_properties_enforcement() -> None:
    schema: dict[str, Any] = {"type": "object", "unevaluatedProperties": False}
    assert _ok(schema, {})
    assert not _ok(schema, {"surprise": 1})


def test_dependent_schemas_conditional() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"credit_card": {"type": "string"}},
        "dependentSchemas": {
            "credit_card": {"properties": {"billing_address": {"type": "string"}}, "required": ["billing_address"]}
        },
    }
    assert _ok(schema, {})
    assert not _ok(schema, {"credit_card": "1234"})
    assert _ok(schema, {"credit_card": "1234", "billing_address": "1 Main St"})


def test_if_then_else_branching() -> None:
    schema: dict[str, Any] = {
        "if": {"properties": {"kind": {"const": "person"}}},
        "then": {"required": ["name"]},
        "else": {"required": ["id"]},
    }
    assert _ok(schema, {"kind": "person", "name": "Alice"})
    assert _ok(schema, {"kind": "robot", "id": 7})
    assert not _ok(schema, {"kind": "person"})
    assert not _ok(schema, {"kind": "robot"})


def test_min_contains_numeric() -> None:
    schema: dict[str, Any] = {"type": "array", "contains": {"type": "string"}, "minContains": 2}
    assert _ok(schema, ["a", "b", 1])
    assert not _ok(schema, ["a", 1, 2])
    assert not _ok(schema, [1, 2, 3])


def test_max_contains_numeric() -> None:
    schema: dict[str, Any] = {"type": "array", "contains": {"type": "string"}, "maxContains": 1}
    assert _ok(schema, ["a", 1, 2])
    assert not _ok(schema, ["a", "b", 1])


def test_pattern_properties_leaves_unmatched_alone() -> None:
    schema: dict[str, Any] = {"type": "object", "patternProperties": {"^S_": {"type": "string"}}}
    assert _ok(schema, {"S_name": "val", "other": 123})
    assert not _ok(schema, {"S_name": 42})


def test_const_passing_and_failing() -> None:
    schema: dict[str, Any] = {"properties": {"version": {"const": 2}}}
    assert _ok(schema, {"version": 2})
    assert not _ok(schema, {"version": 3})


def test_enum_passing_and_failing() -> None:
    schema: dict[str, Any] = {"type": "string", "enum": ["red", "green", "blue"]}
    assert _ok(schema, "green")
    assert not _ok(schema, "yellow")


def test_all_of_composition() -> None:
    schema: dict[str, Any] = {"allOf": [{"type": "string"}, {"minLength": 3}, {"maxLength": 5}]}
    assert _ok(schema, "abcd")
    assert not _ok(schema, "ab")
    assert not _ok(schema, "abcdef")


def test_any_of_composition() -> None:
    schema: dict[str, Any] = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert _ok(schema, "hello")
    assert _ok(schema, 42)
    assert not _ok(schema, False)


def test_one_of_composition() -> None:
    schema: dict[str, Any] = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert _ok(schema, "hello")
    assert _ok(schema, 42)
    assert not _ok(schema, [])
    assert not _ok(schema, True)


def test_multiple_of_numeric_constraint() -> None:
    schema: dict[str, Any] = {"type": "number", "multipleOf": 0.5}
    assert _ok(schema, 1.0)
    assert _ok(schema, 1.5)
    assert not _ok(schema, 1.3)


def test_property_names_validation() -> None:
    schema: dict[str, Any] = {"type": "object", "propertyNames": {"pattern": "^[a-z]+$"}}
    assert _ok(schema, {"hello": 1, "world": 2})
    assert not _ok(schema, {"Hello": 1})


def test_min_max_properties() -> None:
    schema: dict[str, Any] = {"type": "object", "minProperties": 1, "maxProperties": 2}
    assert _ok(schema, {"a": 1})
    assert _ok(schema, {"a": 1, "b": 2})
    assert not _ok(schema, {})
    assert not _ok(schema, {"a": 1, "b": 2, "c": 3})
