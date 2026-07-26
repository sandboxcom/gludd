"""E2E tests for renderers and notifications subsystems — full workflows.

Scenarios:
  Renderers:
   1. RendererSpec model_dump and backward-compat properties
   2. RendererRegistry discovery pipeline — bundled + operator dirs
   3. Schema models — MarkdownSection, Metric, MetricGridSection, TableSection,
      ChartSection, ChartData, ChartSeries, RawHtmlSection, RenderDocument
   4. Schema discriminator — Section union validates each type correctly
   5. schema_loader.load_schema — file exists, missing, non-dict
   6. schema_loader.validate_against_schema — draft 2020-12, legacy draft,
       success, failure, error messages
   7. schema_loader.extract_field_metadata — flat, nested objects, arrays,
       required markers, enum/format, no-properties, malformed
   8. RendererCache — TTL set/get, expiry, clear, clear_all, containment, ttl=0
   9. RendererRunner — async execution, stub injection, canonical validation,
       schema-driven validation, timeout, failure modes, size capping
  10. RendererResult — data, schema, field_metadata, doc population
  11. RunRenderer — backward-compat executor re-exports

  Notifications:
  12. NotificationDispatcher config — enabled/disabled, min_priority, backends
  13. NotificationDispatcher dispatch — stdout, slack (source missing/ok),
       webhook (missing url, missing transport, success/failure HTTP status)
  14. NotificationDispatcher priority threshold — low/medium/high/urgent
  15. NotificationDispatcher message formatting — template rendering
  16. NotificationDispatcher unknown backend
  17. NotificationDispatcher test() method
  18. NotificationDispatcher fallback config
  19. NotificationDispatcher error resilience — exceptions per backend
  20. HttpTransport Protocol — runtime_checkable behavior

  Cross-subsystem:
  21. RendererFailure → notification dispatch integration
  22. RendererTimeout → notification dispatch integration
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from general_ludd.notifications.dispatcher import (
    BACKEND_NAMES,
    FALLBACK_NOTIFICATION_CONFIG,
    NOTIFICATION_TEMPLATE,
    PRIORITY_LEVELS,
    HttpTransport,
    NotificationDispatcher,
)
from general_ludd.renderers.cache import RendererCache
from general_ludd.renderers.executor import (
    RendererFailure as ExecutorRendererFailure,
)
from general_ludd.renderers.executor import (
    RendererTimeout as ExecutorRendererTimeout,
)
from general_ludd.renderers.executor import (
    run_renderer as executor_run_renderer,
)
from general_ludd.renderers.registry import (
    RendererRegistry,
    RendererSpec,
)
from general_ludd.renderers.runner import (
    RendererFailure,
    RendererResult,
    RendererTimeout,
    SchemaValidationError,
    _coerce_stub_output,
    _max_bytes,
    _validate_canonical,
    _validate_with_schema,
    run_renderer,
)
from general_ludd.renderers.schema import (
    ChartData,
    ChartSection,
    ChartSeries,
    MarkdownSection,
    Metric,
    MetricGridSection,
    RawHtmlSection,
    RenderDocument,
    RendererOutput,
    RenderMetadata,
    TableSection,
)
from general_ludd.renderers.schema_loader import (
    FieldMeta,
    extract_field_metadata,
    load_schema,
    validate_against_schema,
)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. RendererSpec model_dump and backward-compat
# ═══════════════════════════════════════════════════════════════════════════════


class TestRendererSpecModelDump:
    def test_model_dump_serializes_paths_as_strings(self):
        spec = RendererSpec(
            name="test-renderer",
            path=Path("/tmp/test.yml"),
            description="A test renderer",
            timeout_seconds=60,
            cache_ttl_seconds=120,
            allow_raw_html=True,
            schema_path=Path("/tmp/test.schema.json"),
        )
        d = spec.model_dump()
        assert d["name"] == "test-renderer"
        assert d["path"] == "/tmp/test.yml"
        assert d["schema_path"] == "/tmp/test.schema.json"
        assert d["timeout_seconds"] == 60
        assert d["allow_raw_html"] is True

    def test_model_dump_null_schema_path(self):
        spec = RendererSpec(name="no-schema", path=Path("/tmp/x.yml"))
        d = spec.model_dump()
        assert d["schema_path"] is None

    def test_playbook_path_property(self):
        spec = RendererSpec(name="pb", path=Path("/a/b/playbook.yml"))
        assert spec.playbook_path == "/a/b/playbook.yml"

    def test_timeout_s_property(self):
        spec = RendererSpec(name="t", path=Path("/t.yml"), timeout_seconds=45)
        assert spec.timeout_s == 45.0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RendererRegistry discovery pipeline
# ═══════════════════════════════════════════════════════════════════════════════


_2_EXAMPLE_PLAYBOOK = [
    {
        "hosts": "localhost",
        "vars": {
            "renderer": True,
            "renderer_description": "Example renderer",
            "renderer_timeout_seconds": 60,
            "renderer_cache_ttl_seconds": 30,
            "renderer_allow_raw_html": True,
        },
        "tasks": [],
    }
]


class TestRendererRegistryDiscovery:
    def test_discovers_playbook_in_bundled_dir(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "my_render.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))

        reg = RendererRegistry(bundled_dir=bundled, operator_dir=None)
        reg.discover()
        assert "my_render" in reg
        spec = reg.get("my_render")
        assert spec is not None
        assert spec.description == "Example renderer"
        assert spec.timeout_seconds == 60
        assert spec.cache_ttl_seconds == 30
        assert spec.allow_raw_html is True

    def test_operator_overrides_bundled_on_name_clash(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        operator = tmp_path / "operator"
        bundled.mkdir()
        operator.mkdir()
        (bundled / "shared.yml").write_text(
            yaml.dump([{**dict(_2_EXAMPLE_PLAYBOOK[0]), "vars": {"renderer": True, "renderer_description": "bundled"}}])
        )
        (operator / "shared.yml").write_text(
            yaml.dump(
                [
                    {
                        **dict(_2_EXAMPLE_PLAYBOOK[0]),
                        "vars": {
                            "renderer": True,
                            "renderer_description": "operator-override",
                        },
                    }
                ]
            )
        )

        reg = RendererRegistry(bundled_dir=bundled, operator_dir=operator)
        reg.discover()
        spec = reg.get("shared")
        assert spec is not None
        assert spec.description == "operator-override"

    def test_names_returns_sorted_list(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "z.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))
        (bundled / "a.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))
        reg = RendererRegistry(bundled_dir=bundled)
        reg.discover()
        assert reg.names() == ["a", "z"]

    def test_metadata_returns_list_of_dicts(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "r1.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))
        reg = RendererRegistry(bundled_dir=bundled)
        reg.discover()
        meta = reg.metadata()
        assert len(meta) == 1
        assert meta[0]["name"] == "r1"
        assert isinstance(meta[0]["path"], str)

    def test_list_all_and_iteration_agree(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "r1.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))
        (bundled / "r2.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))
        reg = RendererRegistry(bundled_dir=bundled)
        reg.discover()
        assert len(reg) == 2
        assert len(reg.list_all()) == 2
        names = [s.name for s in reg]
        assert set(names) == {"r1", "r2"}

    def test_skips_non_renderer_playbooks(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "renderer.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))
        (bundled / "not_a_renderer.yml").write_text(
            yaml.dump([{"hosts": "localhost", "vars": {"other": True}, "tasks": []}])
        )
        reg = RendererRegistry(bundled_dir=bundled)
        reg.discover()
        assert len(reg) == 1
        assert "not_a_renderer" not in reg

    def test_get_nonexistent(self):
        reg = RendererRegistry()
        assert reg.get("nonexistent") is None

    def test_contains(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "r.yml").write_text(yaml.dump(_2_EXAMPLE_PLAYBOOK))
        reg = RendererRegistry(bundled_dir=bundled)
        reg.discover()
        assert "r" in reg
        assert "x" not in reg

    def test_unparseable_yaml_skipped(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "broken.yml").write_text(": invalid yaml :")
        reg = RendererRegistry(bundled_dir=bundled)
        reg.discover()
        assert len(reg) == 0

    def test_non_list_yaml_skipped(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "not_list.yml").write_text(yaml.dump({"key": "val"}))
        reg = RendererRegistry(bundled_dir=bundled)
        reg.discover()
        assert len(reg) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Schema models — construction and validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestSchemaModels:
    def test_markdown_section_roundtrip(self):
        md = MarkdownSection(content="# Hello")
        assert md.type == "markdown"
        assert md.content == "# Hello"
        d = md.model_dump()
        assert d["type"] == "markdown"
        MarkdownSection.model_validate(d)

    def test_metric(self):
        m = Metric(label="CPU", value=85.5, unit="%")
        assert m.label == "CPU"
        assert m.value == 85.5
        assert m.unit == "%"
        assert m.model_dump()["unit"] == "%"

    def test_metric_without_unit(self):
        m = Metric(label="Count", value=42)
        assert m.unit is None

    def test_metric_grid_section(self):
        grid = MetricGridSection(metrics=[Metric(label="A", value=1), Metric(label="B", value=2)])
        assert grid.type == "metric_grid"
        assert len(grid.metrics) == 2

    def test_table_section(self):
        tbl = TableSection(title="My Table", columns=["Name", "Score"], rows=[["Alice", 10], ["Bob", 20]])
        assert tbl.type == "table"
        assert tbl.title == "My Table"
        assert tbl.rows == [["Alice", 10], ["Bob", 20]]

    def test_table_section_no_title(self):
        tbl = TableSection(columns=["c1"], rows=[])
        assert tbl.title is None

    def test_chart_data_and_series(self):
        series = ChartSeries(name="Series 1", values=[1, 2, 3])
        data = ChartData(labels=["A", "B", "C"], series=[series])
        assert data.labels == ["A", "B", "C"]
        assert data.series[0].name == "Series 1"

    def test_chart_section_pie(self):
        section = ChartSection(
            title="Pie Chart",
            chart_type="pie",
            data=ChartData(labels=["X"], series=[ChartSeries(name="X", values=[100])]),
        )
        assert section.chart_type == "pie"

    def test_raw_html_section(self):
        s = RawHtmlSection(html="<div>hello</div>")
        assert s.type == "raw_html"

    def test_render_metadata_defaults(self):
        meta = RenderMetadata()
        assert meta.generated_at is None
        assert meta.execution_ms is None

    def test_render_document_minimal(self):
        doc = RenderDocument(title="Minimal")
        assert doc.title == "Minimal"
        assert doc.sections == []
        assert doc.metadata.generated_at is None

    def test_render_document_with_sections(self):
        doc = RenderDocument(
            title="Full",
            sections=[
                MarkdownSection(content="intro"),
                TableSection(columns=["K"], rows=[["V"]]),
            ],
        )
        assert len(doc.sections) == 2

    def test_renderer_output_alias(self):
        assert RendererOutput is RenderDocument

    def test_strict_forbids_extra(self):
        with pytest.raises(ValueError):
            MarkdownSection(content="ok", extra_field="no")  # type: ignore[call-arg]

    def test_empty_title_raises(self):
        with pytest.raises(ValueError):
            RenderDocument(title="")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Schema discriminator — union validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestSchemaDiscriminator:
    def test_validates_mixed_sections(self):
        doc_data = {
            "title": "Mixed",
            "sections": [
                {"type": "markdown", "content": "text"},
                {"type": "metric_grid", "metrics": [{"label": "L", "value": 1}]},
                {"type": "table", "columns": ["A"], "rows": [["B"]]},
                {
                    "type": "chart",
                    "title": "C",
                    "chart_type": "bar",
                    "data": {"labels": ["X"], "series": [{"name": "S", "values": [1]}]},
                },
                {"type": "raw_html", "html": "<p>x</p>"},
            ],
        }
        doc = RenderDocument.model_validate(doc_data)
        assert len(doc.sections) == 5
        assert isinstance(doc.sections[0], MarkdownSection)
        assert isinstance(doc.sections[1], MetricGridSection)
        assert isinstance(doc.sections[2], TableSection)
        assert isinstance(doc.sections[3], ChartSection)
        assert isinstance(doc.sections[4], RawHtmlSection)

    def test_unknown_section_type_raises(self):
        with pytest.raises(ValueError):
            RenderDocument.model_validate({"title": "X", "sections": [{"type": "unknown"}]})

    def test_chart_allows_line_bar_pie(self):
        for ct in ("line", "bar", "pie"):
            doc = RenderDocument.model_validate(
                {
                    "title": "T",
                    "sections": [
                        {
                            "type": "chart",
                            "chart_type": ct,
                            "data": {"labels": [], "series": []},
                        }
                    ],
                }
            )
            assert doc.sections[0].chart_type == ct

    def test_chart_rejects_invalid_type(self):
        with pytest.raises(ValueError):
            ChartSection(
                chart_type="scatter",  # type: ignore[arg-type]
                data=ChartData(labels=[], series=[]),
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. schema_loader.load_schema
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadSchema:
    def test_load_existing_file(self, tmp_path: Path):
        schema_file = tmp_path / "test.schema.json"
        schema_file.write_text(json.dumps({"type": "object"}))
        result = load_schema(schema_file)
        assert result == {"type": "object"}

    def test_load_missing_file_returns_none(self, tmp_path: Path):
        result = load_schema(tmp_path / "nonexistent.json")
        assert result is None

    def test_load_non_json_object_raises(self, tmp_path: Path):
        schema_file = tmp_path / "arr.schema.json"
        schema_file.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="JSON object"):
            load_schema(schema_file)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. schema_loader.validate_against_schema
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateAgainstSchema:
    def test_valid_data_passes(self):
        schema: dict[str, object] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        ok, errors = validate_against_schema({"name": "test"}, schema)
        assert ok is True
        assert errors == []

    def test_missing_required_field_fails(self):
        schema: dict[str, object] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        ok, errors = validate_against_schema({}, schema)
        assert ok is False
        assert len(errors) >= 1
        assert "name" in errors[0].lower() or "'name'" in errors[0]

    def test_wrong_type_fails(self):
        schema: dict[str, object] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        ok, _errors = validate_against_schema({"count": "not-an-int"}, schema)
        assert ok is False

    def test_legacy_draft_07_accepted_with_warning(self):
        schema: dict[str, object] = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
        }
        with pytest.warns(DeprecationWarning, match="legacy dialect"):
            ok, _ = validate_against_schema({}, schema)
        assert ok is True

    def test_unknown_draft_defaults_to_2020_12(self):
        schema: dict[str, object] = {
            "$schema": "https://example.com/unknown-draft",
            "type": "object",
        }
        ok, _ = validate_against_schema({}, schema)
        assert ok is True

    def test_no_dollar_schema_defaults_to_2020_12(self):
        schema: dict[str, object] = {"type": "object"}
        ok, _ = validate_against_schema({}, schema)
        assert ok is True

    def test_nested_path_in_error_message(self):
        schema: dict[str, object] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {"inner": {"type": "string"}},
                    "required": ["inner"],
                }
            },
        }
        ok, errors = validate_against_schema({"outer": {}}, schema)
        assert ok is False
        assert len(errors) >= 1
        assert "inner" in " ".join(errors).lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. schema_loader.extract_field_metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractFieldMetadata:
    def test_flat_properties(self):
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "title": "Name", "description": "The name"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        fields = extract_field_metadata(schema)
        assert len(fields) == 2
        name_field = next(f for f in fields if f.name == "name")
        assert name_field.type == "string"
        assert name_field.title == "Name"
        assert name_field.required is True
        age_field = next(f for f in fields if f.name == "age")
        assert age_field.required is False

    def test_nested_object_fields(self):
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "person": {
                    "type": "object",
                    "properties": {
                        "first": {"type": "string"},
                        "last": {"type": "string"},
                    },
                    "required": ["first"],
                }
            },
        }
        fields = extract_field_metadata(schema)
        assert len(fields) == 1
        person = fields[0]
        assert person.type == "object"
        assert person.children is not None
        assert len(person.children) == 2
        first_child = next(c for c in person.children if c.name == "first")
        assert first_child.required is True
        last_child = next(c for c in person.children if c.name == "last")
        assert last_child.required is False

    def test_array_of_objects_with_items(self):
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "col1": {"type": "string"},
                            "col2": {"type": "integer"},
                        },
                        "required": ["col1"],
                    },
                }
            },
        }
        fields = extract_field_metadata(schema)
        assert len(fields) == 1
        row_field = fields[0]
        assert row_field.type == "array"
        assert row_field.items is not None
        assert row_field.items.type == "object"
        assert row_field.items.children is not None
        assert len(row_field.items.children) == 2

    def test_array_of_scalar_items(self):
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        }
        fields = extract_field_metadata(schema)
        assert fields[0].items is not None
        assert fields[0].items.type == "string"

    def test_enum_and_format(self):
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "inactive"]},
                "email": {"type": "string", "format": "email"},
            },
        }
        fields = extract_field_metadata(schema)
        status_f = next(f for f in fields if f.name == "status")
        assert status_f.enum == ["active", "inactive"]
        email_f = next(f for f in fields if f.name == "email")
        assert email_f.format == "email"

    def test_no_properties_returns_empty(self):
        schema: dict[str, object] = {"type": "object"}
        fields = extract_field_metadata(schema)
        assert fields == []

    def test_null_properties_returns_empty(self):
        schema: dict[str, object] = {"type": "object", "properties": None}  # type: ignore[arg-type]
        fields = extract_field_metadata(schema)
        assert fields == []

    def test_non_dict_properties_raises(self):
        schema: dict[str, object] = {
            "type": "object",
            "properties": "not-a-dict",
        }
        with pytest.raises(ValueError, match="must be a dict"):
            extract_field_metadata(schema)

    def test_non_dict_schema_raises(self):
        with pytest.raises(ValueError, match="Expected schema to be a dict"):
            extract_field_metadata("not-a-dict")  # type: ignore[arg-type]

    def test_field_meta_to_dict(self):
        fm = FieldMeta(
            name="test",
            title="Test",
            description="desc",
            type="string",
            required=True,
            enum=["a", "b"],
            format="email",
            children=None,
            items=None,
        )
        d = fm.to_dict()
        assert d["name"] == "test"
        assert d["type"] == "string"
        assert d["required"] is True
        assert d["enum"] == ["a", "b"]
        assert d["format"] == "email"
        assert d["children"] is None
        assert d["items"] is None

    def test_field_meta_to_dict_with_children_and_items(self):
        child = FieldMeta(name="c", title="", description="", type="int", required=False)
        items = FieldMeta(name="i", title="", description="", type="str", required=False)
        fm = FieldMeta(
            name="parent",
            title="Parent",
            description="",
            type="object",
            required=False,
            children=[child],
            items=items,
        )
        d = fm.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["name"] == "c"
        assert d["items"]["name"] == "i"

    def test_required_from_schema_level(self):
        schema: dict[str, object] = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"],
        }
        fields = extract_field_metadata(schema)
        assert all(f.required for f in fields)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. RendererCache — TTL operations
# ═══════════════════════════════════════════════════════════════════════════════


class TestRendererCache:
    def test_set_and_get(self):
        cache = RendererCache()
        cache.set("key1", {"data": 42})
        assert cache.get("key1") == {"data": 42}

    def test_get_missing_returns_none(self):
        cache = RendererCache()
        assert cache.get("missing") is None

    def test_ttl_expiry(self):
        cache = RendererCache()
        cache.set("key", "val", ttl=0.001)
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_default_ttl(self):
        cache = RendererCache(ttl_default=0.001)
        cache.set("key", "val")
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_ttl_zero_skips_cache(self):
        cache = RendererCache()
        cache.set("key", "val", ttl=0)
        assert cache.get("key") is None
        assert len(cache) == 0

    def test_clear_single(self):
        cache = RendererCache()
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.clear("a") is True
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.clear("missing") is False

    def test_clear_all(self):
        cache = RendererCache()
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.clear_all() == 2
        assert len(cache) == 0

    def test_contains(self):
        cache = RendererCache()
        cache.set("x", "value")
        assert "x" in cache
        assert "y" not in cache

    def test_contains_expired(self):
        cache = RendererCache()
        cache.set("x", "value", ttl=0.001)
        time.sleep(0.01)
        assert "x" not in cache

    def test_len_counts_active(self):
        cache = RendererCache()
        cache.set("a", 1)
        cache.set("b", 2)
        assert len(cache) == 2

    def test_overwrite_replaces(self):
        cache = RendererCache()
        cache.set("k", "old")
        cache.set("k", "new")
        assert cache.get("k") == "new"

    def test_expired_entry_lazy_deleted(self):
        cache = RendererCache()
        cache.set("k", "v", ttl=0.001)
        time.sleep(0.01)
        cache.get("k")
        assert "k" not in cache
        assert "k" not in cache._entries


# ═══════════════════════════════════════════════════════════════════════════════
# 9. RendererRunner — execution and validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestRendererRunnerErrors:
    def test_renderer_timeout_str(self):
        exc = RendererTimeout("my-renderer", 30.0)
        assert "my-renderer" in str(exc)
        assert "30" in str(exc)
        assert exc.name == "my-renderer"
        assert exc.timeout_s == 30.0

    def test_renderer_failure_str(self):
        exc = RendererFailure("broken", "something went wrong", stdout="out", stderr="err")
        assert "broken" in str(exc)
        assert "something went wrong" in str(exc)
        assert exc.stdout == "out"
        assert exc.stderr == "err"

    def test_renderer_failure_defaults(self):
        exc = RendererFailure("x", "msg")
        assert exc.stdout == ""
        assert exc.stderr == ""

    def test_schema_validation_error_str(self):
        exc = SchemaValidationError("my-schema", ["err1", "err2"])
        assert "my-schema" in str(exc)
        assert "2 error" in str(exc)
        assert exc.errors == ["err1", "err2"]

    def test_max_bytes_default(self, monkeypatch):
        monkeypatch.delenv("GLUDD_RENDER_MAX_BYTES", raising=False)
        assert _max_bytes() == 1024 * 1024

    def test_max_bytes_from_env(self, monkeypatch):
        monkeypatch.setenv("GLUDD_RENDER_MAX_BYTES", "512")
        assert _max_bytes() == 512

    def test_max_bytes_invalid_env(self, monkeypatch):
        monkeypatch.setenv("GLUDD_RENDER_MAX_BYTES", "not-a-number")
        assert _max_bytes() == 1024 * 1024

    def test_renderer_result_no_schema_no_doc(self):
        result = RendererResult(data={"x": 1}, schema=None, field_metadata=None, doc=None)
        assert result.data == {"x": 1}
        assert result.schema is None
        assert result.field_metadata is None
        assert result.doc is None


class TestValidateCanonical:
    def test_valid_document_passes(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        raw: dict[str, object] = {
            "title": "Hello",
            "sections": [],
            "metadata": {},
        }
        result = _validate_canonical(spec, raw, start=0.0)
        assert result.doc is not None
        assert result.doc.title == "Hello"
        assert result.doc.metadata.playbook is not None
        assert result.doc.metadata.execution_ms is not None

    def test_invalid_document_raises(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        with pytest.raises(RendererFailure, match="schema validation"):
            _validate_canonical(spec, {"title": "", "sections": []}, start=0.0)

    def test_metadata_overwritten_by_runner(self):
        spec = RendererSpec(name="runner", path=Path("/data/runner.yml"))
        raw: dict[str, object] = {
            "title": "T",
            "sections": [],
            "metadata": {"generated_at": "2024-01-01", "playbook": "evil.yml"},
        }
        result = _validate_canonical(spec, raw, start=time.monotonic())
        assert result.doc is not None
        assert result.doc.metadata.playbook == "runner.yml"
        assert result.doc.metadata.execution_ms is not None


class TestCoerceStubOutput:
    def test_render_document_to_dict(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        doc = RenderDocument(title="Stub")
        coerced = _coerce_stub_output(doc, spec)
        assert coerced["title"] == "Stub"
        assert "sections" in coerced

    def test_raw_dict_passthrough(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        assert _coerce_stub_output({"a": 1}, spec) == {"a": 1}

    def test_unexpected_type_raises(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        with pytest.raises(RendererFailure, match="int"):
            _coerce_stub_output(42, spec)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. RendererRunner — stub injection + FastAPI integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunRendererStub:
    @pytest.mark.asyncio
    async def test_stub_returns_render_document(self):
        from fastapi import FastAPI

        app = FastAPI()

        class StubRunner:
            async def run(self, spec):
                return RenderDocument(title="From Stub")

        app.state._renderer_runner = StubRunner()
        spec = RendererSpec(name="stub-test", path=Path("/tmp/stub.yml"))
        result = await run_renderer(app, spec)
        assert result.doc is not None
        assert result.doc.title == "From Stub"

    @pytest.mark.asyncio
    async def test_stub_returns_raw_dict(self):
        from unittest.mock import AsyncMock

        from fastapi import FastAPI

        app = FastAPI()
        stub = MagicMock()
        stub.run = AsyncMock(return_value={"title": "Raw Dict", "sections": [], "metadata": {}})
        app.state._renderer_runner = stub

        spec = RendererSpec(name="raw-test", path=Path("/tmp/raw.yml"))
        result = await run_renderer(app, spec)
        assert result.doc is not None
        assert result.doc.title == "Raw Dict"

    @pytest.mark.asyncio
    async def test_no_runner_on_app_state_raises(self):
        from fastapi import FastAPI

        app = FastAPI()
        spec = RendererSpec(name="no-runner", path=Path("/tmp/nr.yml"))
        with pytest.raises(RendererFailure, match="no AnsibleRunnerAdapter"):
            await run_renderer(app, spec)


# ═══════════════════════════════════════════════════════════════════════════════
# 11. RendererRunner — schema-driven validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestSchemaDrivenValidation:
    def test_validate_with_schema_success(self, tmp_path: Path):
        schema_file = tmp_path / "test.schema.json"
        schema_file.write_text(json.dumps({"type": "object", "properties": {"key": {"type": "string"}}}))
        spec = RendererSpec(name="s", path=Path("/tmp/s.yml"), schema_path=schema_file)
        raw: dict[str, object] = {"key": "value"}
        result = _validate_with_schema(spec, raw)
        assert result.doc is None
        assert result.schema is not None
        assert result.field_metadata is not None
        assert len(result.field_metadata) == 1

    def test_validate_with_schema_failure(self, tmp_path: Path):
        schema_file = tmp_path / "fail.schema.json"
        schema_file.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"key": {"type": "number"}},
                    "required": ["key"],
                }
            )
        )
        spec = RendererSpec(name="sf", path=Path("/tmp/sf.yml"), schema_path=schema_file)
        with pytest.raises(SchemaValidationError):
            _validate_with_schema(spec, {"wrong": "data"})

    def test_validate_with_schema_missing_file(self, tmp_path: Path):
        spec = RendererSpec(
            name="missing",
            path=Path("/tmp/m.yml"),
            schema_path=tmp_path / "nonexistent.schema.json",
        )
        with pytest.raises(RendererFailure, match="not found at run time"):
            _validate_with_schema(spec, {})


# ═══════════════════════════════════════════════════════════════════════════════
# 12. RendererExecutor backward-compat re-exports
# ═══════════════════════════════════════════════════════════════════════════════


class TestRendererExecutorBackwardCompat:
    def test_executor_re_exports_run_renderer(self):
        assert executor_run_renderer is run_renderer

    def test_executor_re_exports_renderer_failure(self):
        assert ExecutorRendererFailure is RendererFailure

    def test_executor_re_exports_renderer_timeout(self):
        assert ExecutorRendererTimeout is RendererTimeout


# ═══════════════════════════════════════════════════════════════════════════════
# 13. NotificationDispatcher config
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationDispatcherConfig:
    def test_enabled_true(self):
        nd = NotificationDispatcher({"enabled": True})
        assert nd._enabled is True

    def test_enabled_false(self):
        nd = NotificationDispatcher({"enabled": False})
        assert nd._enabled is False

    def test_fallback_config_applied_on_empty(self):
        nd = NotificationDispatcher({})
        assert nd._enabled == FALLBACK_NOTIFICATION_CONFIG["enabled"]
        assert nd._min_priority == FALLBACK_NOTIFICATION_CONFIG["min_priority"]

    def test_custom_min_priority(self):
        nd = NotificationDispatcher({"enabled": True, "min_priority": "low"})
        assert nd._min_priority_val == 0

    def test_default_min_priority_is_high(self):
        nd = NotificationDispatcher({"enabled": True})
        assert nd._min_priority == "high"
        assert nd._min_priority_val == 2

    def test_backends_passed_through(self):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}, "webhook": {"url": "http://x"}}})
        assert "stdout" in nd._backends
        assert "webhook" in nd._backends


# ═══════════════════════════════════════════════════════════════════════════════
# 14. NotificationDispatcher dispatch — stdout
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationDispatchStdout:
    def test_stdout_dispatch_succeeds(self, capsys):
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"}
        )
        todo: dict[str, object] = {
            "id": 1,
            "title": "Test Todo",
            "priority": "high",
            "category": "blocker",
            "agent_id": "agent-1",
            "body": "Please do this",
        }
        result = nd.dispatch(todo)
        captured = capsys.readouterr()
        assert result["ok"] is True
        assert "[gludd]" in captured.out
        assert "Test Todo" in captured.out


# ═══════════════════════════════════════════════════════════════════════════════
# 15. NotificationDispatcher dispatch — slack
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationDispatchSlack:
    def test_slack_source_not_found(self):
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"slack": {"source": "missing-source"}}, "min_priority": "low"},
            slack_sources={},
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["slack"]["ok"] is False
        assert "not found" in str(result["results"]["slack"]["error"])

    def test_slack_send_notification_called(self):
        mock_source = MagicMock()
        mock_source.send_notification.return_value = {"ok": True, "backend": "slack"}
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"slack": {"source": "myslack"}}, "min_priority": "low"},
            slack_sources={"myslack": mock_source},
        )
        todo: dict[str, object] = {"id": 2, "title": "Slack Todo", "priority": "urgent"}
        result = nd.dispatch(todo)
        mock_source.send_notification.assert_called_once()
        assert result["results"]["slack"]["ok"] is True

    def test_slack_exception_caught(self):
        mock_source = MagicMock()
        mock_source.send_notification.side_effect = RuntimeError("boom")
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"slack": {"source": "bad-slack"}}, "min_priority": "low"},
            slack_sources={"bad-slack": mock_source},
        )
        todo: dict[str, object] = {"id": 3, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["slack"]["ok"] is False
        assert "boom" in str(result["results"]["slack"]["error"])


# ═══════════════════════════════════════════════════════════════════════════════
# 16. NotificationDispatcher dispatch — webhook
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationDispatchWebhook:
    def test_webhook_no_url(self):
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {}}, "min_priority": "low"}
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["webhook"]["ok"] is False
        assert "requires url" in str(result["results"]["webhook"]["error"])

    def test_webhook_no_transport(self):
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {"url": "http://example.com"}}, "min_priority": "low"}
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["webhook"]["ok"] is False
        assert "no HTTP transport" in str(result["results"]["webhook"]["error"])

    def test_webhook_successful_post(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_transport = MagicMock()
        mock_transport.post.return_value = mock_resp
        nd = NotificationDispatcher(
            {
                "enabled": True,
                "backends": {"webhook": {"url": "http://hook.local", "headers": {"X-Custom": "v"}}},
                "min_priority": "low",
            },
            transport=mock_transport,
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["webhook"]["ok"] is True
        assert result["results"]["webhook"]["status_code"] == 200
        mock_transport.post.assert_called_once()
        call_kwargs = mock_transport.post.call_args.kwargs
        assert "Content-Type" in call_kwargs["headers"]

    def test_webhook_non_200_status(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_transport = MagicMock()
        mock_transport.post.return_value = mock_resp
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {"url": "http://hook.local"}}, "min_priority": "low"},
            transport=mock_transport,
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["webhook"]["ok"] is False
        assert result["results"]["webhook"]["status_code"] == 500

    def test_webhook_transport_exception(self):
        mock_transport = MagicMock()
        mock_transport.post.side_effect = ConnectionError("refused")
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {"url": "http://dead.local"}}, "min_priority": "low"},
            transport=mock_transport,
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["webhook"]["ok"] is False
        assert "refused" in str(result["results"]["webhook"]["error"])

    def test_webhook_without_status_code_attribute(self):
        mock_resp = MagicMock()
        del mock_resp.status_code
        mock_transport = MagicMock()
        mock_transport.post.return_value = mock_resp
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {"url": "http://h"}}, "min_priority": "low"},
            transport=mock_transport,
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["webhook"]["ok"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 17. NotificationDispatcher priority threshold
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationPriorityThreshold:
    def test_low_below_high_threshold(self):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "high"})
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "low"}
        result = nd.dispatch(todo)
        assert result["ok"] is False
        assert "below" in str(result.get("reason", ""))

    def test_high_meets_high_threshold(self):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "high"})
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "high"}
        result = nd.dispatch(todo)
        assert result["ok"] is True

    def test_urgent_always_passes(self):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "urgent"})
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["ok"] is True

    def test_medium_meets_low_threshold(self):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"})
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "medium"}
        result = nd.dispatch(todo)
        assert result["ok"] is True

    def test_unknown_priority_defaults_to_zero(self):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"})
        assert nd._priority_meets_threshold("unknown") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 18. NotificationDispatcher message formatting
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationFormatting:
    def test_full_template_renders(self):
        nd = NotificationDispatcher({"enabled": True})
        todo: dict[str, object] = {
            "id": 42,
            "title": "Fix Bug",
            "priority": "high",
            "category": "security",
            "agent_id": "agent-007",
            "body": "Critical vulnerability found",
        }
        msg = nd._format_message(todo)
        assert "[gludd]" in msg
        assert "high" in msg
        assert "#42" in msg
        assert "Fix Bug" in msg
        assert "security" in msg
        assert "agent-007" in msg
        assert "Critical vulnerability found" in msg

    def test_missing_fields_get_defaults(self):
        nd = NotificationDispatcher({"enabled": True})
        msg = nd._format_message({})
        assert "?" in msg
        assert "untitled" in msg
        assert "medium" in msg
        assert "unknown" in msg

    def test_notification_template_constant(self):
        assert "{priority}" in NOTIFICATION_TEMPLATE
        assert "{id}" in NOTIFICATION_TEMPLATE
        assert "{title}" in NOTIFICATION_TEMPLATE
        assert "{category}" in NOTIFICATION_TEMPLATE
        assert "{agent_id}" in NOTIFICATION_TEMPLATE
        assert "{body}" in NOTIFICATION_TEMPLATE


# ═══════════════════════════════════════════════════════════════════════════════
# 19. NotificationDispatcher unknown backend
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnknownBackend:
    def test_unknown_backend_returns_error(self):
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"nonexistent": {}}, "min_priority": "low"}
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["results"]["nonexistent"]["ok"] is False
        assert "unknown" in str(result["results"]["nonexistent"]["error"])


# ═══════════════════════════════════════════════════════════════════════════════
# 20. NotificationDispatcher test() method
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationTestMethod:
    def test_method_sends_test_notification(self, capsys):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"})
        result = nd.test()
        assert result["ok"] is True
        captured = capsys.readouterr()
        assert "test-1" in captured.out
        assert "Test notification" in captured.out

    def test_disabled_dispatcher_test_returns_false(self):
        nd = NotificationDispatcher({"enabled": False})
        result = nd.test()
        assert result["ok"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 21. NotificationDispatcher error resilience
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotificationErrorResilience:
    def test_multi_backend_one_fails_others_succeed(self, capsys):
        nd = NotificationDispatcher(
            {
                "enabled": True,
                "backends": {
                    "stdout": {},
                    "webhook": {"url": "http://hf"},
                    "slack": {"source": "missing-src"},
                },
                "min_priority": "low",
            },
            slack_sources={},
            transport=None,
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["ok"] is True
        assert result["results"]["stdout"]["ok"] is True
        assert result["results"]["webhook"]["ok"] is False
        assert result["results"]["slack"]["ok"] is False

    def test_all_backends_fail_returns_false(self):
        nd = NotificationDispatcher(
            {"enabled": True, "backends": {"webhook": {"url": "http://x"}}, "min_priority": "low"},
            transport=None,
        )
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result["ok"] is False

    def test_disabled_returns_immediately(self):
        nd = NotificationDispatcher({"enabled": False, "backends": {"stdout": {}}})
        todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
        result = nd.dispatch(todo)
        assert result == {"ok": False, "reason": "notifications disabled"}

    def test_handler_exception_caught_and_logged(self, capsys):
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"})
        original = nd._dispatch_stdout
        nd._dispatch_stdout = lambda msg: (_ for _ in ()).throw(RuntimeError("stdout crash"))  # type: ignore[assignment]
        try:
            todo: dict[str, object] = {"id": 1, "title": "T", "priority": "urgent"}
            result = nd.dispatch(todo)
            assert result["results"]["stdout"]["ok"] is False
            assert "stdout crash" in str(result["results"]["stdout"]["error"])
        finally:
            nd._dispatch_stdout = original


# ═══════════════════════════════════════════════════════════════════════════════
# 22. HttpTransport Protocol
# ═══════════════════════════════════════════════════════════════════════════════


class TestHttpTransportProtocol:
    def test_protocol_is_runtime_checkable(self):
        assert hasattr(HttpTransport, "_is_runtime_protocol")
        # Verify it was decorated with @runtime_checkable
        assert isinstance(object(), HttpTransport) is False

    def test_object_with_post_method_passes(self):
        class FakeTransport:
            def post(self, url, *, headers, data=None, json=None, timeout=10):
                pass

        assert isinstance(FakeTransport(), HttpTransport)

    def test_object_without_post_fails(self):
        class BadTransport:
            def get(self, url):
                pass

        assert not isinstance(BadTransport(), HttpTransport)


# ═══════════════════════════════════════════════════════════════════════════════
# 23. Cross-subsystem: renderer + notification integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestRendererNotificationIntegration:
    def test_renderer_failure_can_trigger_notification_dispatch(self, capsys):
        exc = RendererFailure("playbook-x", "crash", stdout="exit 1", stderr="segfault")
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"})
        todo: dict[str, object] = {
            "id": "render-fail-1",
            "title": f"Renderer failure: {exc.name}",
            "priority": "urgent",
            "category": "renderer_error",
            "agent_id": "renderer-runner",
            "body": f"Detail: {exc.detail}\nstdout: {exc.stdout}\nstderr: {exc.stderr}",
        }
        result = nd.dispatch(todo)
        assert result["ok"] is True
        captured = capsys.readouterr()
        assert "playbook-x" in captured.out
        assert "segfault" in captured.out

    def test_renderer_timeout_can_trigger_notification(self, capsys):
        exc = RendererTimeout("slow-playbook", 30.0)
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"})
        todo: dict[str, object] = {
            "id": "render-timeout-1",
            "title": f"Renderer timeout: {exc.name}",
            "priority": "urgent",
            "category": "renderer_timeout",
            "agent_id": "renderer-runner",
            "body": str(exc),
        }
        result = nd.dispatch(todo)
        assert result["ok"] is True
        captured = capsys.readouterr()
        assert "slow-playbook" in captured.out
        assert "30.0" in captured.out

    def test_schema_validation_error_notification_includes_error_list(self, capsys):
        errors = [
            "<root>: 'name' is a required property",
            "age: 5 is not of type 'string'",
        ]
        exc = SchemaValidationError("schema-renderer", errors)
        nd = NotificationDispatcher({"enabled": True, "backends": {"stdout": {}}, "min_priority": "low"})
        todo: dict[str, object] = {
            "id": "schema-fail-1",
            "title": f"Schema validation error: {exc.name}",
            "priority": "high",
            "category": "schema_validation",
            "agent_id": "schema-validator",
            "body": "\n".join(exc.errors),
        }
        result = nd.dispatch(todo)
        assert result["ok"] is True
        captured = capsys.readouterr()
        assert "'name' is a required property" in captured.out
        assert "not of type 'string'" in captured.out

    def test_priorities_directly_mapped(self):
        assert PRIORITY_LEVELS["low"] == 0
        assert PRIORITY_LEVELS["medium"] == 1
        assert PRIORITY_LEVELS["high"] == 2
        assert PRIORITY_LEVELS["urgent"] == 3

    def test_backend_names_frozenset(self):
        assert "slack" in BACKEND_NAMES
        assert "stdout" in BACKEND_NAMES
        assert "webhook" in BACKEND_NAMES
        assert len(BACKEND_NAMES) == 3

    def test_notification_import(self):
        from general_ludd.notifications import NotificationDispatcher as ND
        assert ND is NotificationDispatcher

    def test_renderers_import(self):
        from general_ludd.renderers import registry
        assert registry is not None
