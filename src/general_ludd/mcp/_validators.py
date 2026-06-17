"""Shared Pydantic field-validator factories for the mcp package.

Keeps boilerplate DRY without pulling in a heavy dependency.
"""
from __future__ import annotations

from typing import Any

from pydantic import field_validator


def strip_and_require_str(noun: str) -> classmethod:
    """Return a Pydantic ``field_validator`` that strips whitespace and rejects empty strings.

    Usage inside a Pydantic model::

        from general_ludd.mcp._validators import strip_and_require_str

        class MyModel(BaseModel):
            name: str
            _validate_name = strip_and_require_str("name")

    Args:
        noun: The human-readable field name used in the error message, e.g. ``"server_id"``.

    Returns:
        A ``classmethod``-wrapped Pydantic field validator that can be assigned as a class
        attribute on a ``BaseModel`` subclass.
    """

    def _validator(cls: Any, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError(f"{noun} must not be empty")
        return v

    # Wrap as a Pydantic field_validator for the given field name.
    # We use the noun as the field name — callers must ensure noun matches the field.
    return field_validator(noun, mode="before")(_validator)  # type: ignore[return-value]
