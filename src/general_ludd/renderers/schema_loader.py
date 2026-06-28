"""JSON Schema loader + validator for renderer playbooks.

Spec: ``docs/design/PLAYBOOK_WEB_RENDERER.md`` §3.3 (b). A renderer playbook may
ship a companion ``<name>.schema.json`` file; this module loads it, validates
the playbook's output against it, and walks the schema to extract per-field
metadata that a Jinja2 template can render generically — without being limited
to the closed canonical section set in :mod:`general_ludd.renderers.schema`.

Design notes:

* ``jsonschema`` is imported lazily inside each public function so the daemon
  still boots if the library is somehow absent (defensive — the dependency is
  declared in ``pyproject.toml``).
* Validation uses ``Draft202012Validator``. Older drafts (draft-07 and earlier)
  are still accepted but emit a ``DeprecationWarning`` nudging authors to
  upgrade — they are a well-known superseded dialect.
* ``extract_field_metadata`` walks ``schema["properties"]`` recursively, so a
  renderer template can build forms/tables/tables-of-tables from any schema
  without knowing its shape ahead of time.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_DRAFT_07 = "http://json-schema.org/draft-07/schema#"
_LEGACY_DRAFT_PREFIXES = (
    "http://json-schema.org/draft-07",
    "http://json-schema.org/draft-06",
    "http://json-schema.org/draft-04",
    "http://json-schema.org/draft-03",
)


@dataclass
class FieldMeta:
    """Metadata for a single property in a JSON Schema.

    Consumed by Jinja2 templates via :meth:`to_dict`.
    """

    name: str
    title: str
    description: str
    type: str
    required: bool = False
    enum: list[Any] | None = None
    format: str | None = None
    children: list[FieldMeta] | None = None
    items: FieldMeta | None = None

    def to_dict(self) -> dict[str, Any]:
        """Flatten to a plain dict for template consumption."""

        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "type": self.type,
            "required": self.required,
            "enum": self.enum,
            "format": self.format,
            "children": [c.to_dict() for c in self.children] if self.children is not None else None,
            "items": self.items.to_dict() if self.items is not None else None,
        }


# ---------------------------------------------------------------------------
# load_schema
# ---------------------------------------------------------------------------


def load_schema(path: Path) -> dict[str, Any] | None:
    """Load a JSON Schema from ``path``.

    Returns the parsed schema dict, or ``None`` if the file does not exist
    (no raise — companion schemas are optional).
    """

    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Schema file {path} did not parse to a JSON object")
    return data


# ---------------------------------------------------------------------------
# validate_against_schema
# ---------------------------------------------------------------------------


def validate_against_schema(data: Any, schema: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate ``data`` against ``schema`` using ``jsonschema``.

    Returns ``(True, [])`` on success, ``(False, [messages...])`` on failure.
    Older drafts (draft-07 and earlier) emit a ``DeprecationWarning`` but are
    still accepted.
    """

    try:
        from jsonschema import (
            Draft7Validator,
            Draft202012Validator,
        )
    except ImportError as exc:  # pragma: no cover - defensive; dep is declared
        raise RuntimeError(
            "jsonschema is not installed but is required for schema-driven rendering. "
            "Run `make sync` to install declared dependencies."
        ) from exc

    declared = schema.get("$schema") if isinstance(schema, dict) else None

    validator: Any
    if declared == _DRAFT_2020_12 or declared is None:
        validator = Draft202012Validator(schema)
    elif isinstance(declared, str) and declared.startswith(_LEGACY_DRAFT_PREFIXES):
        warnings.warn(
            f"JSON Schema declares legacy dialect {declared!r}; "
            f"upgrade to {_DRAFT_2020_12!r}. Validation will proceed with the "
            "legacy validator but support may be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        validator = Draft7Validator(schema)
    else:
        # Unknown / newer draft — default to 2020-12 (forward-compatible).
        validator = Draft202012Validator(schema)

    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if not errors:
        return True, []
    messages = [_format_validation_error(e) for e in errors]
    return False, messages


def _format_validation_error(err: Any) -> str:
    """Render a jsonschema ValidationError into a compact human-readable string."""

    path = "/".join(str(p) for p in err.absolute_path) if err.absolute_path else "<root>"
    return f"{path}: {err.message}"


# ---------------------------------------------------------------------------
# extract_field_metadata
# ---------------------------------------------------------------------------


def extract_field_metadata(schema: dict[str, Any]) -> list[FieldMeta]:
    """Walk ``schema["properties"]`` and return :class:`FieldMeta` per property.

    * Nested ``object`` properties populate :attr:`FieldMeta.children`.
    * ``array`` properties with ``items`` of type ``object`` populate
      :attr:`FieldMeta.items` (a single :class:`FieldMeta` describing the row).
    * A schema with no ``properties`` returns ``[]`` (graceful, not an error).
    * Malformed schemas (e.g. ``properties`` is not a dict) raise ``ValueError``
      so authors see a clear signal rather than a silent empty list.
    """

    if not isinstance(schema, dict):
        raise ValueError(f"Expected schema to be a dict, got {type(schema).__name__}")

    raw_props = schema.get("properties")
    if raw_props is None:
        return []
    if not isinstance(raw_props, dict):
        raise ValueError(
            f"schema['properties'] must be a dict, got {type(raw_props).__name__}"
        )

    required_set: set[str] = set()
    raw_required = schema.get("required")
    if isinstance(raw_required, list):
        required_set = {str(r) for r in raw_required}

    fields: list[FieldMeta] = []
    for prop_name, prop_schema in raw_props.items():
        if not isinstance(prop_schema, dict):
            # Skip malformed property definitions gracefully.
            fields.append(
                FieldMeta(
                    name=str(prop_name),
                    title=str(prop_name),
                    description="",
                    type="any",
                    required=str(prop_name) in required_set,
                )
            )
            continue
        fields.append(_build_field_meta(str(prop_name), prop_schema, required_set))
    return fields


def _build_field_meta(name: str, prop_schema: dict[str, Any], required_set: set[str]) -> FieldMeta:
    """Build a single FieldMeta, recursing into nested objects / arrays."""

    title = str(prop_schema.get("title") or name)
    description = str(prop_schema.get("description") or "")
    ftype = str(prop_schema.get("type") or "any")
    enum = prop_schema.get("enum")
    fmt = prop_schema.get("format")
    fmt_str = str(fmt) if fmt is not None else None
    enum_list = list(enum) if isinstance(enum, list) else None

    children: list[FieldMeta] | None = None
    items: FieldMeta | None = None

    if ftype == "object" and isinstance(prop_schema.get("properties"), dict):
        nested_required: set[str] = set()
        nested_req = prop_schema.get("required")
        if isinstance(nested_req, list):
            nested_required = {str(r) for r in nested_req}
        nested_props = prop_schema["properties"]
        if isinstance(nested_props, dict):
            children = []
            for child_name, child_schema in nested_props.items():
                if isinstance(child_schema, dict):
                    children.append(_build_field_meta(str(child_name), child_schema, nested_required))
                else:
                    children.append(
                        FieldMeta(
                            name=str(child_name),
                            title=str(child_name),
                            description="",
                            type="any",
                            required=str(child_name) in nested_required,
                        )
                    )

    if ftype == "array":
        raw_items = prop_schema.get("items")
        if isinstance(raw_items, dict):
            # Required list inside array items applies to item-level fields.
            item_required: set[str] = set()
            if isinstance(raw_items.get("required"), list):
                item_required = {str(r) for r in raw_items["required"]}
            if raw_items.get("type") == "object" and isinstance(raw_items.get("properties"), dict):
                # Build a synthetic FieldMeta describing the row.
                item_children = []
                for child_name, child_schema in raw_items["properties"].items():
                    if isinstance(child_schema, dict):
                        item_children.append(
                            _build_field_meta(str(child_name), child_schema, item_required)
                        )
                    else:
                        item_children.append(
                            FieldMeta(
                                name=str(child_name),
                                title=str(child_name),
                                description="",
                                type="any",
                                required=str(child_name) in item_required,
                            )
                        )
                items = FieldMeta(
                    name=name,
                    title=title,
                    description=description,
                    type="object",
                    enum=None,
                    format=None,
                    children=item_children,
                    items=None,
                )
            else:
                # Items are scalars — describe the scalar type.
                items = FieldMeta(
                    name=name,
                    title=title,
                    description=description,
                    type=str(raw_items.get("type") or "any"),
                    enum=None,
                    format=None,
                    children=None,
                    items=None,
                )

    return FieldMeta(
        name=name,
        title=title,
        description=description,
        type=ftype,
        required=name in required_set,
        enum=enum_list,
        format=fmt_str,
        children=children,
        items=items,
    )
