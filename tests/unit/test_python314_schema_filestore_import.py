"""Regression for Python 3.14 JSON-Schema → filestore import ordering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from general_ludd.renderers.registry import RendererSpec
from general_ludd.renderers.runner import SchemaValidationError, _validate_with_schema

pytestmark = pytest.mark.xdist_group(name="python314_filestore_import")


def test_01_schema_validation_failure_does_not_poison_imports(
    tmp_path: Path,
) -> None:
    """Exercise the JSON-Schema failure path before a later test imports fs."""
    schema_path = tmp_path / "renderer.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        ),
        encoding="utf-8",
    )
    spec = RendererSpec(
        name="python314-order",
        path=tmp_path / "renderer.yml",
        description="import-order regression",
        schema_path=schema_path,
    )
    with pytest.raises(SchemaValidationError):
        _validate_with_schema(spec, {})


def test_02_event_loop_filestore_remains_importable() -> None:
    """A prior test's schema failure must not poison the EventLoop import."""
    from general_ludd.event_loop.loop import PHASE_ORDER

    assert "check_service_credits" in PHASE_ORDER
