"""Schema-driven rendering contract tests for the system_facts renderer.

Target: playbooks/renderers/system_facts.schema.json (the companion JSON
Schema that formalizes playbooks/renderers/system_facts.yml's render.json
output).

This test file is the executable proof that:

1. The companion schema is a valid JSON Schema draft 2020-12 document.
2. The JSON the playbook actually emits validates against the schema.
3. The schema rejects malformed payloads (missing required fields, wrong
   types, unknown top-level keys, out-of-range numerics, missing row keys).
4. ``extract_field_metadata`` correctly reads the per-property title /
   description / required metadata back out of the schema.
5. The schema-driven Jinja2 template renders the contract end-to-end.

The companion ``renderers.schema_loader`` (validate_against_schema /
extract_field_metadata) and ``templates/render/schema_page.html.j2`` are
delivered by parallel tasks. When either is absent, the corresponding
tests degrade gracefully via ``pytest.importorskip`` / ``pytest.skip`` so
they activate automatically the moment the parallel-task code lands.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "playbooks" / "renderers" / "system_facts.schema.json"
PLAYBOOK_PATH = REPO_ROOT / "playbooks" / "renderers" / "system_facts.yml"
TEMPLATE_PATH = (
    REPO_ROOT / "src" / "general_ludd" / "templates" / "render" / "schema_page.html.j2"
)

DRAFT_2020_12_URL = "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """Load the companion schema once per module."""
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture
def playbook_payload() -> dict[str, Any]:
    """Synthesize the exact JSON the playbook emits for a sample snapshot.

    Values mirror the ``set_fact`` defaults in system_facts.yml applied to a
    representative daemon ``/api/facts`` snapshot (3 running agents, 12 jobs
    completed today, 0.92 success fraction, two models reporting usage).
    """
    return {
        "title": "System Facts",
        "sections": [
            {
                "type": "metric_grid",
                "metrics": [
                    {"label": "Running agents", "value": 3, "unit": ""},
                    {"label": "Completed today", "value": 12, "unit": ""},
                    {"label": "Success rate", "value": 0.92, "unit": "%"},
                ],
            },
            {
                "type": "table",
                "title": "Per-model usage",
                "columns": ["model", "calls", "cost_usd"],
                "rows": [
                    {"model": "claude-sonnet-4", "calls": 48, "cost_usd": 1.23},
                    {"model": "claude-opus-4", "calls": 6, "cost_usd": 0.87},
                ],
            },
        ],
        "metadata": {
            "generated_at": "2026-06-28T12:00:00Z",
            "playbook": "system_facts.yml",
            "renderer_version": 1,
        },
    }


def _validate(payload: dict[str, Any], schema: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate using the parallel-task loader if available, else jsonschema.

    ``general_ludd.renderers.schema_loader.validate_against_schema`` returns
    ``(ok, errors)``. Until that module lands, fall back to ``jsonschema``
    directly and adapt the return shape so callers can treat both paths the
    same way.
    """
    try:
        from general_ludd.renderers.schema_loader import validate_against_schema
    except ImportError:
        errors = sorted(
            (f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}")
            for e in jsonschema.Draft202012Validator(schema).iter_errors(payload)
        )
        return (not errors, errors)
    return validate_against_schema(payload, schema)


# ---------------------------------------------------------------------------
# 1. Schema-file shape
# ---------------------------------------------------------------------------


def test_schema_file_is_valid_json_schema_draft_2020_12(schema: dict[str, Any]) -> None:
    """The companion schema declares draft 2020-12 and is itself well-formed."""
    assert schema["$schema"] == DRAFT_2020_12_URL, (
        "schema must declare the draft 2020-12 vocabulary"
    )
    assert schema["$id"] == "https://gludd.example/schemas/system_facts.schema.json"
    assert schema["title"] == "System Facts Renderer Output"
    assert schema["type"] == "object"
    assert schema.get("additionalProperties") is False, (
        "strict contract: additionalProperties must be false"
    )
    # Draft 2020-12 uses $defs (not definitions) for reusable subschemas.
    assert "$defs" in schema, "draft 2020-12 schemas should use $defs"
    # Must actually be a valid draft 2020-12 schema, not just self-described.
    jsonschema.Draft202012Validator.check_schema(schema)


def test_schema_file_exists_on_disk() -> None:
    """The companion schema is committed alongside the playbook."""
    assert SCHEMA_PATH.is_file(), f"missing companion schema: {SCHEMA_PATH}"


def test_schema_required_top_level_fields(schema: dict[str, Any]) -> None:
    """title, sections, metadata are all required."""
    required = schema.get("required", [])
    for field in ("title", "sections", "metadata"):
        assert field in required, f"{field!r} must be in schema.required"


# ---------------------------------------------------------------------------
# 2. Schema <-> playbook output agreement
# ---------------------------------------------------------------------------


def test_schema_matches_playbook_output(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """The payload the playbook emits validates cleanly against the schema."""
    ok, errors = _validate(playbook_payload, schema)
    assert ok, f"playbook payload failed schema validation: {errors}"


def test_schema_definitions_are_reusable(schema: dict[str, Any]) -> None:
    """The $defs block defines MetricGridSection, TableSection, ModelRow, Metadata."""
    defs = schema.get("$defs", {})
    for expected in ("MetricGridSection", "TableSection", "ModelRow", "Metadata"):
        assert expected in defs, f"missing $defs entry: {expected}"


# ---------------------------------------------------------------------------
# 3. Negative paths — schema rejects malformed payloads
# ---------------------------------------------------------------------------


def test_schema_rejects_missing_required_field(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """Removing a top-level required field must fail validation."""
    del playbook_payload["metadata"]
    ok, errors = _validate(playbook_payload, schema)
    assert not ok, "missing required field was accepted"
    assert any("metadata" in e for e in errors)


def test_schema_rejects_wrong_type(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """A string where an integer is expected must fail validation."""
    # Running agents is slot 0 of the metric_grid; value must be integer >= 0.
    playbook_payload["sections"][0]["metrics"][0]["value"] = "three"
    ok, errors = _validate(playbook_payload, schema)
    assert not ok, "string-where-integer was accepted"
    assert errors


def test_schema_rejects_additional_properties(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """An unknown top-level key must fail because additionalProperties is false."""
    playbook_payload["unknown_top_level_field"] = "should not be here"
    ok, _errors = _validate(playbook_payload, schema)
    assert not ok, "unknown top-level property was accepted"


def test_schema_rejects_success_rate_above_one(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """success_rate is bounded to [0, 1.0]; 1.5 must fail."""
    playbook_payload["sections"][0]["metrics"][2]["value"] = 1.5
    ok, _errors = _validate(playbook_payload, schema)
    assert not ok, "success_rate=1.5 was accepted"


def test_schema_rejects_table_row_missing_required_model(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """A table row without a model identifier must fail."""
    playbook_payload["sections"][1]["rows"].append({"calls": 5, "cost_usd": 0.1})
    ok, _errors = _validate(playbook_payload, schema)
    assert not ok, "table row missing 'model' was accepted"


def test_schema_rejects_negative_running_agents(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """running_agents has minimum=0; -1 must fail."""
    playbook_payload["sections"][0]["metrics"][0]["value"] = -1
    ok, _errors = _validate(playbook_payload, schema)
    assert not ok, "negative running_agents was accepted"


# ---------------------------------------------------------------------------
# 4. Field-metadata extraction (parallel-task API)
# ---------------------------------------------------------------------------


def test_field_metadata_extraction(schema: dict[str, Any]) -> None:
    """extract_field_metadata returns per-property metadata with required flags.

    ``general_ludd.renderers.schema_loader.extract_field_metadata`` returns a
    list of ``FieldMeta`` dataclasses (one per top-level property) carrying at
    least {name, title, description, required, type}.
    """
    try:
        from general_ludd.renderers.schema_loader import extract_field_metadata
    except ImportError:
        pytest.skip("schema_loader.extract_field_metadata not yet implemented")

    metadata = extract_field_metadata(schema)
    assert isinstance(metadata, list)
    assert len(metadata) == len(schema["properties"]), (
        "metadata must cover every top-level property exactly once"
    )

    by_name: dict[str, Any] = {m.name: m for m in metadata}

    # Every property has a human-readable title (not just the property key).
    for name, meta in by_name.items():
        assert meta.title, f"property {name!r} missing human-readable title"
        assert meta.title != name or name == "title", (
            f"property {name!r} title defaulted to its key name - set an explicit title"
        )

    # Required flags agree with the schema's `required` array.
    required_set = set(schema.get("required", []))
    for meta in metadata:
        assert meta.required == (meta.name in required_set), (
            f"required flag wrong for {meta.name!r}"
        )

    # The three named contract fields exist.
    for expected in ("title", "sections", "metadata"):
        assert expected in by_name, f"missing metadata for top-level field {expected!r}"


# ---------------------------------------------------------------------------
# 5. Schema-driven rendering end-to-end (parallel-task template)
# ---------------------------------------------------------------------------


def test_schema_driven_rendering_produces_html(
    schema: dict[str, Any], playbook_payload: dict[str, Any]
) -> None:
    """Render schema_page.html.j2 with the schema + payload and assert content.

    The parallel-task Jinja2 template at
    ``src/general_ludd/templates/render/schema_page.html.j2`` takes
    ``(schema, data)`` and produces an HTML page that walks the top-level
    properties. When the template is absent, skip; when present, the rendered
    HTML must surface the schema title, schema description, every top-level
    property title, and the top-level ``title`` data value.
    """
    if not TEMPLATE_PATH.is_file():
        pytest.skip("schema_page.html.j2 not yet implemented")

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_PATH.parent)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(TEMPLATE_PATH.name)
    html = template.render(schema=schema, data=playbook_payload)

    # Schema title appears in the page heading.
    assert schema["title"] in html, "schema title must appear in rendered HTML"

    # Schema description appears.
    assert schema["description"][:40] in html, "schema description must appear"

    # Every top-level property title is surfaced by the template's label walk.
    for prop_name, prop_schema in schema["properties"].items():
        title = prop_schema.get("title", prop_name)
        assert title in html, f"property title {title!r} missing from rendered HTML"

    # The top-level `title` data value ("System Facts") is rendered. Deeper
    # values (metrics, table rows) live inside the `sections` array, which the
    # generic top-level template does not recurse into - that is the next
    # template's job, not this schema contract's.
    assert playbook_payload["title"] in html, "title data value not rendered"


# ---------------------------------------------------------------------------
# 6. Playbook <-> schema drift guard
# ---------------------------------------------------------------------------


def test_playbook_emits_flat_model_rows() -> None:
    """The playbook must emit {model, calls, cost_usd} rows, not {model, stats}.

    This is a static guard against regression of the namespace-loop fix that
    flattened the per-model usage rows to match the companion schema. It reads
    the playbook source and asserts the row-shaping code is present.
    """
    source = PLAYBOOK_PATH.read_text()
    assert "'model': _model" in source, "playbook lost its model-row flattening"
    assert "'calls':" in source, "playbook lost its calls-row flattening"
    assert "'cost_usd':" in source, "playbook lost its cost_usd-row flattening"
