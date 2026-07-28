"""E2E: Renderers, approval, collections workflow tests — batch 1 of coverage push."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# renderers.registry
# ---------------------------------------------------------------------------

class TestRendererRegistry:
    def test_registry_import(self):
        from general_ludd.renderers.registry import RendererRegistry, RendererSpec

        assert RendererRegistry is not None
        assert RendererSpec is not None

    def test_registry_default_bundled_dir(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry()
        assert reg.bundled_dir is not None

    def test_registry_custom_dirs(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        assert reg.bundled_dir == Path("/tmp/nonexistent")

    def test_registry_discover_empty(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        reg.discover()
        assert len(reg) == 0
        assert reg.names() == []

    def test_registry_names_empty(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        assert reg.names() == []

    def test_registry_get_missing(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        assert reg.get("nonexistent") is None

    def test_registry_metadata_empty(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        assert reg.metadata() == []

    def test_registry_list_all_empty(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        assert reg.list_all() == []

    def test_registry_iter_empty(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        assert list(reg) == []

    def test_registry_contains_false(self):
        from general_ludd.renderers.registry import RendererRegistry

        reg = RendererRegistry(bundled_dir=Path("/tmp/nonexistent"))
        assert "foo" not in reg

    def test_renderer_spec_defaults(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        assert spec.name == "test"
        assert spec.path == Path("/tmp/test.yml")
        assert spec.description == ""
        assert spec.timeout_seconds == 30
        assert spec.cache_ttl_seconds == 30
        assert not spec.allow_raw_html
        assert spec.schema_path is None

    def test_renderer_spec_custom(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(
            name="custom",
            path=Path("/tmp/custom.yml"),
            description="desc",
            timeout_seconds=60,
            cache_ttl_seconds=10,
            allow_raw_html=True,
            schema_path=Path("/tmp/custom.schema.json"),
        )
        assert spec.description == "desc"
        assert spec.timeout_seconds == 60
        assert spec.cache_ttl_seconds == 10
        assert spec.allow_raw_html
        assert spec.schema_path == Path("/tmp/custom.schema.json")

    def test_renderer_spec_model_dump(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        dump = spec.model_dump()
        assert dump["name"] == "test"
        assert dump["path"] == "/tmp/test.yml"

    def test_renderer_spec_playbook_path(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        assert spec.playbook_path == "/tmp/test.yml"

    def test_renderer_spec_timeout_s(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"), timeout_seconds=45)
        assert spec.timeout_s == 45.0

    def test_renderer_spec_model_dump_schema_none(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        dump = spec.model_dump()
        assert dump["schema_path"] is None

    def test_parse_renderer_playbook_basic(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("""[{"vars": {"renderer": true}, "hosts": "localhost", "tasks": []}]""")
            f.flush()
            spec = RendererRegistry._parse(Path(f.name))
        Path(f.name).unlink()
        assert spec is not None
        assert spec.name == Path(f.name).stem

    def test_parse_renderer_not_a_renderer(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("[{'hosts': 'localhost', 'tasks': []}]")
            f.flush()
            spec = RendererRegistry._parse(Path(f.name))
        Path(f.name).unlink()
        assert spec is None

    def test_parse_renderer_invalid_yaml(self):
        from general_ludd.renderers.registry import RendererRegistry

        spec = RendererRegistry._parse(Path("/tmp/nonexistent_never.yml"))
        assert spec is None

    def test_parse_renderer_empty_list(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("[]")
            f.flush()
            spec = RendererRegistry._parse(Path(f.name))
        Path(f.name).unlink()
        assert spec is None

    def test_registry_meta_alias(self):
        from general_ludd.renderers.registry import RendererMeta, RendererSpec

        assert RendererMeta is RendererSpec

    def test_registry_discover_with_yml(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.TemporaryDirectory() as d:
            playbook = Path(d) / "test_renderer.yml"
            playbook.write_text("""[{"vars": {"renderer": true}, "hosts": "localhost", "tasks": []}]""")
            reg = RendererRegistry(bundled_dir=Path(d))
            reg.discover()
            assert len(reg) == 1
            assert "test_renderer" in reg

    def test_registry_operator_dir_override(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.TemporaryDirectory() as bundled, tempfile.TemporaryDirectory() as operator:
            (Path(bundled) / "render.yml").write_text(
                '[{"vars": {"renderer": true, "renderer_description": "bundled"}, "hosts": "localhost", "tasks": []}]'
            )
            (Path(operator) / "render.yml").write_text(
                '[{"vars": {"renderer": true, "renderer_description": "operator"}, "hosts": "localhost", "tasks": []}]'
            )
            reg = RendererRegistry(bundled_dir=Path(bundled), operator_dir=Path(operator))
            reg.discover()
            spec = reg.get("render")
            assert spec is not None
            assert spec.description == "operator"

    def test_registry_discover_with_schema_companion(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.TemporaryDirectory() as d:
            playbook = Path(d) / "myrender.yml"
            playbook.write_text("""[{"vars": {"renderer": true}, "hosts": "localhost", "tasks": []}]""")
            schema_path = Path(d) / "myrender.schema.json"
            schema_path.write_text("{}")
            reg = RendererRegistry(bundled_dir=Path(d))
            reg.discover()
            spec = reg.get("myrender")
            assert spec is not None
            assert spec.schema_path is not None

    def test_registry_discover_playbook_with_full_vars(self):
        from general_ludd.renderers.registry import RendererRegistry

        with tempfile.TemporaryDirectory() as d:
            playbook = Path(d) / "full.yml"
            playbook.write_text(
                yaml.dump([{
                    "vars": {
                        "renderer": True,
                        "renderer_description": "Full renderer",
                        "renderer_timeout_seconds": 120,
                        "renderer_cache_ttl_seconds": 60,
                        "renderer_allow_raw_html": True,
                    },
                    "hosts": "localhost",
                    "tasks": [],
                }])
            )
            reg = RendererRegistry(bundled_dir=Path(d))
            reg.discover()
            spec = reg.get("full")
            assert spec is not None
            assert spec.description == "Full renderer"
            assert spec.timeout_seconds == 120
            assert spec.cache_ttl_seconds == 60
            assert spec.allow_raw_html is True


# ---------------------------------------------------------------------------
# renderers.schema (RenderDocument + sections)
# ---------------------------------------------------------------------------

class TestRenderDocument:
    def test_import_schema(self):
        from general_ludd.renderers.schema import (
            RenderDocument,
        )

        assert RenderDocument is not None

    def test_render_document_minimal(self):
        from general_ludd.renderers.schema import RenderDocument

        doc = RenderDocument(title="Test")
        assert doc.title == "Test"
        assert doc.sections == []
        assert doc.metadata is not None

    def test_render_document_extra_forbidden(self):
        from general_ludd.renderers.schema import RenderDocument

        with pytest.raises(ValidationError):
            RenderDocument(title="Test", extra_field="bad")

    def test_markdown_section(self):
        from general_ludd.renderers.schema import MarkdownSection

        sec = MarkdownSection(content="# Hello")
        assert sec.type == "markdown"
        assert sec.content == "# Hello"

    def test_markdown_section_empty_content(self):
        from general_ludd.renderers.schema import MarkdownSection

        sec = MarkdownSection(content="")
        assert sec.content == ""

    def test_metric_defaults(self):
        from general_ludd.renderers.schema import Metric

        m = Metric(label="CPU", value=42)
        assert m.label == "CPU"
        assert m.value == 42
        assert m.unit is None

    def test_metric_grid_section(self):
        from general_ludd.renderers.schema import Metric, MetricGridSection

        sec = MetricGridSection(metrics=[Metric(label="CPU", value=90)])
        assert sec.type == "metric_grid"
        assert len(sec.metrics) == 1

    def test_table_section(self):
        from general_ludd.renderers.schema import TableSection

        sec = TableSection(columns=["Name", "Value"], rows=[["A", 1]])
        assert sec.type == "table"
        assert sec.columns == ["Name", "Value"]

    def test_chart_section(self):
        from general_ludd.renderers.schema import ChartData, ChartSection, ChartSeries

        sec = ChartSection(
            chart_type="line",
            data=ChartData(labels=["a", "b"], series=[ChartSeries(name="S1", values=[1, 2])]),
        )
        assert sec.type == "chart"
        assert sec.chart_type == "line"

    def test_raw_html_section(self):
        from general_ludd.renderers.schema import RawHtmlSection

        sec = RawHtmlSection(html="<p>hi</p>")
        assert sec.type == "raw_html"

    def test_render_metadata_defaults(self):
        from general_ludd.renderers.schema import RenderMetadata

        meta = RenderMetadata()
        assert meta.generated_at is None
        assert meta.playbook is None

    def test_render_document_full(self):
        from general_ludd.renderers.schema import (
            MarkdownSection,
            Metric,
            MetricGridSection,
            RenderDocument,
            TableSection,
        )

        doc = RenderDocument(
            title="Full Doc",
            sections=[
                MarkdownSection(content="Intro"),
                MetricGridSection(metrics=[Metric(label="CPU", value=99)]),
                TableSection(columns=["Col1"], rows=[["val1"]]),
            ],
        )
        assert len(doc.sections) == 3

    def test_renderer_output_alias(self):
        from general_ludd.renderers.schema import RenderDocument, RendererOutput

        assert RendererOutput is RenderDocument


# ---------------------------------------------------------------------------
# renderers.schema_loader
# ---------------------------------------------------------------------------

class TestSchemaLoader:
    def test_import(self):
        from general_ludd.renderers.schema_loader import (
            load_schema,
        )

        assert load_schema is not None

    def test_field_meta_defaults(self):
        from general_ludd.renderers.schema_loader import FieldMeta

        fm = FieldMeta(name="test", title="T", description="", type="string")
        assert fm.name == "test"
        assert fm.children is None

    def test_field_meta_to_dict(self):
        from general_ludd.renderers.schema_loader import FieldMeta

        fm = FieldMeta(name="test", title="T", description="", type="string", required=True)
        d = fm.to_dict()
        assert d["name"] == "test"
        assert d["required"] is True

    def test_load_schema_missing_file(self):
        from general_ludd.renderers.schema_loader import load_schema

        result = load_schema(Path("/tmp/nonexistent_schema.json"))
        assert result is None

    def test_load_schema_valid(self):
        from general_ludd.renderers.schema_loader import load_schema

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"type": "object", "properties": {}}))
            f.flush()
            result = load_schema(Path(f.name))
        Path(f.name).unlink()
        assert result is not None
        assert result["type"] == "object"

    def test_load_schema_not_dict(self):
        from general_ludd.renderers.schema_loader import load_schema

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("42")
            f.flush()
            with pytest.raises(ValueError):
                load_schema(Path(f.name))
        Path(f.name).unlink()

    def test_extract_field_metadata_not_dict(self):
        from general_ludd.renderers.schema_loader import extract_field_metadata

        with pytest.raises(ValueError):
            extract_field_metadata("not a dict")  # type: ignore[arg-type]

    def test_extract_field_metadata_no_properties(self):
        from general_ludd.renderers.schema_loader import extract_field_metadata

        result = extract_field_metadata({"type": "object"})
        assert result == []

    def test_extract_field_metadata_properties_not_dict(self):
        from general_ludd.renderers.schema_loader import extract_field_metadata

        with pytest.raises(ValueError):
            extract_field_metadata({"type": "object", "properties": "bad"})

    def test_extract_field_metadata_simple(self):
        from general_ludd.renderers.schema_loader import extract_field_metadata

        schema = {"type": "object", "properties": {"name": {"type": "string", "title": "Name"}}}
        result = extract_field_metadata(schema)
        assert len(result) == 1
        assert result[0].name == "name"
        assert result[0].type == "string"

    def test_extract_field_metadata_required(self):
        from general_ludd.renderers.schema_loader import extract_field_metadata

        schema = {
            "type": "object",
            "required": ["email"],
            "properties": {"email": {"type": "string"}, "name": {"type": "string"}},
        }
        result = extract_field_metadata(schema)
        assert result[0].required is True

    def test_extract_field_metadata_nested_object(self):
        from general_ludd.renderers.schema_loader import extract_field_metadata

        schema = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                }
            },
        }
        result = extract_field_metadata(schema)
        assert result[0].children is not None
        assert len(result[0].children) == 1

    def test_extract_field_metadata_array_items_object(self):
        from general_ludd.renderers.schema_loader import extract_field_metadata

        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
                }
            },
        }
        result = extract_field_metadata(schema)
        assert result[0].items is not None
        assert result[0].items.type == "object"

    def test_field_meta_children_to_dict(self):
        from general_ludd.renderers.schema_loader import FieldMeta

        child = FieldMeta(name="c", title="C", description="", type="string")
        parent = FieldMeta(name="p", title="P", description="", type="object", children=[child])
        d = parent.to_dict()
        assert d["children"] is not None
        assert len(d["children"]) == 1

    def test_field_meta_enum(self):
        from general_ludd.renderers.schema_loader import FieldMeta

        fm = FieldMeta(name="color", title="Color", description="", type="string", enum=["red", "blue"])
        d = fm.to_dict()
        assert d["enum"] == ["red", "blue"]


# ---------------------------------------------------------------------------
# renderers.cache
# ---------------------------------------------------------------------------

class TestRendererCache:
    def test_import(self):
        from general_ludd.renderers.cache import RendererCache

        assert RendererCache is not None

    def test_cache_default_ttl(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("k", "v")
        assert c.get("k") == "v"
        assert len(c) == 1

    def test_cache_expires(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("k", "v", ttl=0)
        assert c.get("k") is None
        assert len(c) == 0

    def test_cache_missing(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        assert c.get("nope") is None

    def test_cache_clear_one(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("k", "v")
        assert c.clear("k") is True
        assert c.get("k") is None

    def test_cache_clear_missing(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        assert c.clear("nope") is False

    def test_cache_clear_all(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("a", 1)
        c.set("b", 2)
        assert c.clear_all() == 2
        assert len(c) == 0

    def test_cache_contains(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("k", "v")
        assert "k" in c
        assert "nope" not in c

    def test_cache_overwrite(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache()
        c.set("k", "v1")
        c.set("k", "v2")
        assert c.get("k") == "v2"

    def test_cache_custom_default_ttl(self):
        from general_ludd.renderers.cache import RendererCache

        c = RendererCache(ttl_default=0.001)
        c.set("k", "v")
        time.sleep(0.01)
        assert c.get("k") is None


# ---------------------------------------------------------------------------
# renderers.runner (dataclasses + exceptions)
# ---------------------------------------------------------------------------

class TestRendererRunner:
    def test_import(self):
        from general_ludd.renderers.runner import (
            RendererResult,
        )

        assert RendererResult is not None

    def test_renderer_result_defaults(self):
        from general_ludd.renderers.runner import RendererResult

        r = RendererResult()
        assert r.data == {}
        assert r.schema is None
        assert r.field_metadata is None
        assert r.doc is None

    def test_renderer_timeout(self):
        from general_ludd.renderers.runner import RendererTimeout

        exc = RendererTimeout("test", 30.0)
        assert exc.name == "test"
        assert "test" in str(exc)

    def test_renderer_failure(self):
        from general_ludd.renderers.runner import RendererFailure

        exc = RendererFailure("test", "details", stdout="out", stderr="err")
        assert exc.name == "test"
        assert exc.detail == "details"

    def test_schema_validation_error(self):
        from general_ludd.renderers.runner import SchemaValidationError

        exc = SchemaValidationError("test", ["error 1", "error 2"])
        assert len(exc.errors) == 2


# ---------------------------------------------------------------------------
# renderers.executor (re-exports)
# ---------------------------------------------------------------------------

class TestRendererExecutor:
    def test_executor_re_exports(self):
        from general_ludd.renderers.executor import (
            run_renderer,
        )

        assert run_renderer is not None

    def test_executor_run_renderer_is_runner_renderer(self):
        from general_ludd.renderers.executor import run_renderer as re_run
        from general_ludd.renderers.runner import run_renderer as rn_run

        assert re_run is rn_run


# ---------------------------------------------------------------------------
# renderers.runner — _max_bytes env handling
# ---------------------------------------------------------------------------

class TestRendererMaxBytes:
    def test_max_bytes_default(self):
        from general_ludd.renderers.runner import _max_bytes

        assert _max_bytes() == 1024 * 1024

    @patch.dict(os.environ, {"GLUDD_RENDER_MAX_BYTES": ""}, clear=True)
    def test_max_bytes_env_not_set(self):
        from general_ludd.renderers.runner import _max_bytes

        assert _max_bytes() == 1024 * 1024

    def test_max_bytes_env_set_valid(self, monkeypatch):
        from general_ludd.renderers.runner import _max_bytes

        monkeypatch.setenv("GLUDD_RENDER_MAX_BYTES", "512")
        try:
            assert _max_bytes() == 512
        finally:
            del os.environ["GLUDD_RENDER_MAX_BYTES"]


# ---------------------------------------------------------------------------
# approval.gate
# ---------------------------------------------------------------------------

class TestApprovalGate:
    def test_import_gate(self):
        from general_ludd.approval.gate import (
            ApprovalGate,
            ApprovalRequest,
            ApprovalResult,
        )

        assert ApprovalGate is not None
        assert ApprovalRequest is not None
        assert ApprovalResult is not None

    def test_approval_request_creation(self):
        from general_ludd.approval.gate import ApprovalRequest

        req = ApprovalRequest(action="deploy", target="production", by="agent-1")
        assert req.action == "deploy"
        assert req.target == "production"

    def test_approval_result_default(self):
        from general_ludd.approval.gate import ApprovalResult

        result = ApprovalResult(allowed=False, reason="test")
        assert result.allowed is False
        assert result.reason == "test"


# ---------------------------------------------------------------------------
# collections.importer
# ---------------------------------------------------------------------------

class TestCollectionsImporter:
    def test_import_issue(self):
        from general_ludd.collections.importer import ImportIssue

        issue = ImportIssue(severity="error", message="bad")
        assert issue.severity == "error"
        assert issue.message == "bad"

    def test_import_issue_warn(self):
        from general_ludd.collections.importer import ImportIssue

        issue = ImportIssue(severity="warn", message="warning")
        assert issue.severity == "warn"

    def test_importer_constructor(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        importer = TerraformCollectionImporter(Path("/tmp/nonexistent_collection"))
        assert importer.collection_path == Path("/tmp/nonexistent_collection")

    def test_importer_no_terraform_dir(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        with tempfile.TemporaryDirectory() as d:
            collection = Path(d) / "test_collection"
            collection.mkdir()
            importer = TerraformCollectionImporter(collection)
            issues = importer._validate_terraform_dirs()
            assert len(issues) == 1
            assert issues[0].severity == "warn"

    def test_importer_no_rego_policies(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        with tempfile.TemporaryDirectory() as d:
            importer = TerraformCollectionImporter(Path(d))
            issues = importer._validate_rego_policies()
            assert issues == []

    def test_importer_no_providers(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        with tempfile.TemporaryDirectory() as d:
            collection = Path(d) / "test"
            collection.mkdir()
            (collection / "galaxy.yml").write_text("namespace: test\nname: test\nversion: 1.0.0\n")
            importer = TerraformCollectionImporter(
                collection, operator_trust_data_path=Path("/tmp/nonexistent_trust.json")
            )
            with suppress(RuntimeError):
                importer._load_operator_trust_list()

    def test_importer_run_optional_binary_missing(self):
        from general_ludd.collections.importer import _run_optional_binary

        issues = _run_optional_binary(
            binary="nonexistent_binary_xyz",
            argv=["nonexistent_binary_xyz", "validate"],
            cwd=Path("/tmp"),
            relabel="test",
        )
        assert len(issues) == 1
        assert issues[0].severity == "warn"

    def test_parse_variable_names(self):
        from general_ludd.collections.importer import _parse_variable_names

        names = _parse_variable_names('variable "foo" {\n  type = string\n}\nvariable "bar" {\n}')
        assert "foo" in names
        assert "bar" in names

    def test_parse_tfvars_keys(self):
        from general_ludd.collections.importer import _parse_tfvars_keys

        keys = _parse_tfvars_keys("foo = 123\nbar = \"hello\"\n# comment = ignored\n")
        assert "foo" in keys
        assert "bar" in keys

    def test_is_floating_version_true(self):
        from general_ludd.collections.importer import _is_floating_version

        assert _is_floating_version(">= 2.0")
        assert _is_floating_version("> 1.5")
        assert _is_floating_version("< 3.0")

    def test_is_floating_version_false(self):
        from general_ludd.collections.importer import _is_floating_version

        assert not _is_floating_version("~> 2.8")
        assert not _is_floating_version("= 2.8.0")

    def test_provider_in_trust_list_exact(self):
        from general_ludd.collections.importer import _provider_in_trust_list

        assert _provider_in_trust_list("hashicorp/aws", ["hashicorp/aws"])

    def test_provider_in_trust_list_suffix(self):
        from general_ludd.collections.importer import _provider_in_trust_list

        assert _provider_in_trust_list("aws", ["hashicorp/aws"])

    def test_rego_deny_reassignment_detection(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        with tempfile.TemporaryDirectory() as d:
            collection = Path(d) / "test"
            collection.mkdir()
            policies = collection / "plugins" / "terraform" / "policies"
            policies.mkdir(parents=True)
            (policies / "bad.rego").write_text("deny = true")
            importer = TerraformCollectionImporter(collection)
            issues = importer._validate_rego_policies()
            assert len(issues) == 1
            assert issues[0].severity == "error"

    def test_importer_import_collection_aggregates(self):
        from general_ludd.collections.importer import TerraformCollectionImporter

        with tempfile.TemporaryDirectory() as d:
            collection = Path(d) / "test"
            collection.mkdir()
            importer = TerraformCollectionImporter(collection)
            issues = importer.import_collection()
            assert isinstance(issues, list)


# ---------------------------------------------------------------------------
# renderers.registry — backward-compat properties
# ---------------------------------------------------------------------------

class TestRendererSpecCompat:
    def test_playbook_path_str(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/a/b/c.yml"))
        assert spec.playbook_path == "/a/b/c.yml"

    def test_timeout_s_float(self):
        from general_ludd.renderers.registry import RendererSpec

        spec = RendererSpec(name="test", path=Path("/tmp/t.yml"), timeout_seconds=42)
        assert spec.timeout_s == 42.0
