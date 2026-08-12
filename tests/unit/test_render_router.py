"""Deep tests for routers/render.py — route handlers, helpers, and error paths.

Covers _dump_json, _get_registry, _get_cache, render_schema_error,
render_error, api_list_renderers, get_renderer_schema, render_by_name,
and the register wiring.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from general_ludd.renderers.cache import RendererCache
from general_ludd.renderers.registry import RendererRegistry, RendererSpec
from general_ludd.renderers.runner import (
    RendererFailure,
    RendererTimeout,
    SchemaValidationError,
)
from general_ludd.renderers.schema import (
    MarkdownSection,
    RenderDocument,
    RenderMetadata,
)
from general_ludd.routers import render as render_module
from general_ludd.routers.render import (
    register,
    render_document,
    render_error,
    render_schema_error,
    render_schema_page,
)

_dump_json = render_module._dump_json
_get_registry = render_module._get_registry
_get_cache = render_module._get_cache
_env = render_module._env


# ═══════════════════════════════════════════════════════════════════════════
# _dump_json
# ═══════════════════════════════════════════════════════════════════════════


class FakePydanticModel(BaseModel):
    x: int = 1
    y: str = "hello"


class TestDumpJson:
    def test_dumps_plain_dict(self):
        result = _dump_json({"a": 1, "b": [2, 3]})
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": [2, 3]}

    def test_dumps_pydantic_model(self):
        obj = FakePydanticModel(x=42, y="z")
        result = _dump_json(obj)
        parsed = json.loads(result)
        assert parsed == {"x": 42, "y": "z"}

    def test_dumps_object_with_model_dump_method(self):
        class HasDump:
            def model_dump(self) -> dict[str, object]:
                return {"value": "from_dump"}

        result = _dump_json(HasDump())
        parsed = json.loads(result)
        assert parsed == {"value": "from_dump"}

    def test_non_serializable_falls_back_to_str(self):
        class Weird:
            def __str__(self) -> str:
                return "weird-str"

        result = _dump_json({"item": Weird()})
        parsed = json.loads(result)
        assert parsed == {"item": "weird-str"}

    def test_nested_pydantic_models(self):
        class Inner(BaseModel):
            name: str = "inner"

        class Outer(BaseModel):
            items: list[Inner] = [Inner()]

        result = _dump_json(Outer())
        parsed = json.loads(result)
        assert parsed == {"items": [{"name": "inner"}]}

    def test_returns_valid_json_indent_2(self):
        result = _dump_json({"k": "v"})
        assert result == '{\n  "k": "v"\n}'


# ═══════════════════════════════════════════════════════════════════════════
# _get_registry / _get_cache
# ═══════════════════════════════════════════════════════════════════════════


class TestGetRegistry:
    def test_returns_none_when_attr_missing(self):
        app = FastAPI()
        assert _get_registry(app) is None

    def test_returns_none_when_attr_is_wrong_type(self):
        app = FastAPI()
        app.state._renderer_registry = "not-a-registry"
        assert _get_registry(app) is None

    def test_returns_none_when_attr_is_none(self):
        app = FastAPI()
        app.state._renderer_registry = None
        assert _get_registry(app) is None

    def test_returns_registry_when_correct_type(self):
        app = FastAPI()
        reg = RendererRegistry()
        app.state._renderer_registry = reg
        assert _get_registry(app) is reg


class TestGetCache:
    def test_returns_none_when_attr_missing(self):
        app = FastAPI()
        assert _get_cache(app) is None

    def test_returns_none_when_attr_is_wrong_type(self):
        app = FastAPI()
        app.state._renderer_cache = {"not": "a cache"}
        assert _get_cache(app) is None

    def test_returns_cache_when_correct_type(self):
        app = FastAPI()
        cache = RendererCache()
        app.state._renderer_cache = cache
        assert _get_cache(app) is cache


# ═══════════════════════════════════════════════════════════════════════════
# render_schema_error  (template uses schema.title, not name param)
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderSchemaError:
    def test_includes_error_path_and_message(self):
        html = render_schema_error(
            name="gpu",
            errors=[{"path": "/x", "message": "bad type"}],
        )
        assert "/x" in html
        assert "bad type" in html

    def test_none_schema_renders_without_title(self):
        html = render_schema_error(
            name="test",
            errors=[{"path": "/a", "message": "missing"}],
            schema=None,
        )
        assert "/a" in html
        assert "missing" in html

    def test_empty_errors_list_shows_no_errors(self):
        html = render_schema_error(name="empty", errors=[])
        assert "No error details available" in html

    def test_mixed_error_objects(self):
        html = render_schema_error(
            name="mixed",
            errors=[
                {"path": "/p", "message": "msg1"},
                {"error": "raw"},
            ],
        )
        assert "/p" in html
        assert "msg1" in html

    def test_schema_title_propagates_into_heading(self):
        html = render_schema_error(
            name="t",
            errors=[],
            schema={"title": "My Schema", "type": "object"},
        )
        assert "My Schema" in html

    def test_error_with_schema_snippet_included(self):
        html = render_schema_error(
            name="s",
            errors=[
                {
                    "path": "/z",
                    "message": "wrong type",
                    "schema_snippet": '{"type":"integer"}',
                },
            ],
        )
        assert "{&#34;type&#34;:&#34;integer&#34;}" in html
        assert '{"type":"integer"}' not in html


# ═══════════════════════════════════════════════════════════════════════════
# render_error
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderError:
    def test_basic_title_name_detail(self):
        html = render_error(title="Bang", name="x", detail="boom")
        assert "Bang" in html
        assert "x" in html
        assert "boom" in html

    def test_no_timeout_renders_fine(self):
        html = render_error(title="Timeout?", name="y")
        assert "Timeout?" in html
        assert "y" in html

    def test_timeout_zero_renders_fine(self):
        html = render_error(title="T0", name="z", timeout_s=0.0)
        assert "T0" in html

    def test_empty_stdout_stderr(self):
        html = render_error(title="Silent", name="s", stdout="", stderr="")
        assert "Silent" in html

    def test_all_fields_populated(self):
        html = render_error(
            title="Full Error",
            name="crash",
            detail="OOM at 2.1 GB",
            timeout_s=120.0,
            stdout="output line\nsecond line",
            stderr="ERROR: panic",
        )
        assert "Full Error" in html
        assert "crash" in html
        assert "OOM at 2.1 GB" in html
        assert "output line" in html
        assert "ERROR: panic" in html


# ═══════════════════════════════════════════════════════════════════════════
# render_document (additional edge cases beyond test_renderers_deep.py)
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderDocumentEdgeCases:
    def test_minimal_document_renders(self):
        doc = RenderDocument(title="X", sections=[])
        html = render_document(doc)
        assert isinstance(html, str)
        assert len(html) > 0
        assert "X" in html

    def test_metadata_empty_strings_and_zero(self):
        doc = RenderDocument(
            title="M",
            sections=[MarkdownSection(content="body")],
            metadata=RenderMetadata(
                generated_at="",
                playbook="",
                execution_ms=0,
            ),
        )
        html = render_document(doc)
        assert "M" in html
        assert "body" in html

    def test_allow_raw_html_false_by_default(self):
        doc = RenderDocument(title="Safe", sections=[])
        html = render_document(doc)
        assert "Safe" in html

    def test_markdown_section_renders_pre(self):
        doc = RenderDocument(
            title="MD",
            sections=[MarkdownSection(content="# h1\nparagraph")],
        )
        html = render_document(doc)
        assert "MD" in html
        assert "paragraph" in html


# ═══════════════════════════════════════════════════════════════════════════
# render_schema_page
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderSchemaPageEdgeCases:
    def test_empty_schema_and_data(self):
        html = render_schema_page(
            schema={"type": "object"},
            data={},
            field_metadata=None,
        )
        assert isinstance(html, str)
        assert "Rendered Page" in html

    def test_field_metadata_empty_list(self):
        schema = {
            "title": "Test",
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        html = render_schema_page(
            schema=schema,
            data={"name": "val"},
            field_metadata=[],
        )
        assert "Test" in html
        assert "val" in html

    def test_nested_objects(self):
        schema = {
            "title": "Nested",
            "type": "object",
            "properties": {
                "inner": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                }
            },
        }
        html = render_schema_page(
            schema=schema,
            data={"inner": {"key": "v"}},
            field_metadata=None,
        )
        assert "Nested" in html
        assert "v" in html

    def test_schema_without_title_uses_default(self):
        html = render_schema_page(
            schema={"type": "object", "properties": {"x": {"type": "integer"}}},
            data={"x": 7},
            field_metadata=None,
        )
        assert "Rendered Page" in html
        assert "7" in html


# ═══════════════════════════════════════════════════════════════════════════
# Jinja2 environment
# ═══════════════════════════════════════════════════════════════════════════


class TestJinja2Environment:
    def test_env_is_sandboxed(self):
        from jinja2.sandbox import SandboxedEnvironment

        assert isinstance(_env, SandboxedEnvironment)

    def test_dump_json_filter_registered(self):
        assert "dump_json" in _env.filters
        assert callable(_env.filters["dump_json"])

    def test_autoescape_is_callable_not_bool(self):
        assert callable(_env.autoescape)

    def test_trim_blocks_enabled(self):
        assert _env.trim_blocks is True

    def test_lstrip_blocks_enabled(self):
        assert _env.lstrip_blocks is True

    def test_autoescape_actually_escapes_html(self):
        tpl = _env.from_string("{{ value }}")
        out = tpl.render(value="<script>alert(1)</script>")
        assert "&lt;script&gt;" in out
        assert "<script>" not in out


# ═══════════════════════════════════════════════════════════════════════════
# Route-handler behavioral tests (via TestClient)
# ═══════════════════════════════════════════════════════════════════════════


def _make_app(
    *,
    registry: RendererRegistry | None = None,
    cache: RendererCache | None = None,
    runner: object = None,
) -> FastAPI:
    app = FastAPI()
    if registry is not None:
        app.state._renderer_registry = registry
    if cache is not None:
        app.state._renderer_cache = cache
    if runner is not None:
        app.state._renderer_runner = runner
    register(app, {})
    return app


def _make_spec(name: str = "test_render", schema_path: str | None = None) -> RendererSpec:
    return RendererSpec(
        name=name,
        path=__import__("pathlib").Path(f"/fake/{name}.yml"),
        description="A test renderer",
        timeout_seconds=30,
        cache_ttl_seconds=60,
        schema_path=__import__("pathlib").Path(schema_path) if schema_path else None,
    )


def _make_registry(*specs: RendererSpec) -> RendererRegistry:
    reg = RendererRegistry()
    for s in specs:
        reg._specs[s.name] = s
    return reg


# stub.run(spec) is called with a single arg by run_renderer;
# it must return a RenderDocument (canonical) or dict (schema-driven).
def _canonical_doc(title: str = "T", content: str = "body") -> RenderDocument:
    return RenderDocument(
        title=title,
        sections=[MarkdownSection(content=content)],
        metadata=RenderMetadata(),
    )


class TestApiListRenderers:
    def test_empty_registry_returns_empty_list(self):
        reg = _make_registry()
        app = _make_app(registry=reg)
        client = TestClient(app)
        resp = client.get("/api/renderers")
        assert resp.status_code == 200
        assert resp.json() == {"renderers": [], "count": 0}

    def test_single_renderer_in_list(self):
        reg = _make_registry(_make_spec("hello"))
        app = _make_app(registry=reg)
        client = TestClient(app)
        resp = client.get("/api/renderers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["renderers"][0]["name"] == "hello"
        assert body["renderers"][0]["has_schema"] is False

    def test_renderer_with_schema_has_schema_true(self):
        reg = _make_registry(_make_spec("gpu", schema_path="/tmp/gpu.schema.json"))
        app = _make_app(registry=reg)
        client = TestClient(app)
        resp = client.get("/api/renderers")
        assert resp.status_code == 200
        assert resp.json()["renderers"][0]["has_schema"] is True

    def test_multiple_renderers_count(self):
        reg = _make_registry(_make_spec("a"), _make_spec("b"), _make_spec("c"))
        app = _make_app(registry=reg)
        client = TestClient(app)
        resp = client.get("/api/renderers")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

    def test_registry_none_returns_empty_list(self):
        app = _make_app(registry=None)
        client = TestClient(app)
        resp = client.get("/api/renderers")
        assert resp.status_code == 200
        assert resp.json() == {"renderers": [], "count": 0}


class TestGetRendererSchema:
    def test_registry_none_returns_404(self):
        app = _make_app(registry=None)
        client = TestClient(app)
        resp = client.get("/render/anything/schema")
        assert resp.status_code == 404

    def test_unknown_renderer_returns_404(self):
        app = _make_app(registry=_make_registry())
        client = TestClient(app)
        resp = client.get("/render/nonexistent/schema")
        assert resp.status_code == 404

    def test_renderer_without_schema_returns_404(self):
        reg = _make_registry(_make_spec("no_schema"))
        app = _make_app(registry=reg)
        client = TestClient(app)
        resp = client.get("/render/no_schema/schema")
        assert resp.status_code == 404

    def test_schema_content_type_is_correct(self, tmp_path):
        schema_path = tmp_path / "has_schema.schema.json"
        schema_path.write_text(json.dumps({"type": "object", "properties": {}}))
        reg = _make_registry(_make_spec("has_schema", schema_path=str(schema_path)))
        app = _make_app(registry=reg)
        client = TestClient(app)
        resp = client.get("/render/has_schema/schema")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(render_module._SCHEMA_CONTENT_TYPE)

    def test_unreadable_schema_returns_500(self):
        reg = _make_registry(_make_spec("bad", schema_path="/nonexistent/path.json"))
        app = _make_app(registry=reg)
        client = TestClient(app)
        resp = client.get("/render/bad/schema")
        assert resp.status_code == 500


class TestRenderByName:
    def test_registry_none_returns_503_service_unavailable(self):
        app = _make_app(registry=None)
        client = TestClient(app)
        resp = client.get("/render/anything")
        assert resp.status_code == 503
        assert "Renderer not configured" in resp.text

    def test_unknown_renderer_returns_404(self):
        app = _make_app(registry=_make_registry())
        client = TestClient(app)
        resp = client.get("/render/nonexistent")
        assert resp.status_code == 404

    def test_renderer_timeout_returns_504(self):
        class TimeoutRunner:
            async def run(self, spec):
                raise RendererTimeout(spec.name, 30.0)

        reg = _make_registry(_make_spec("slow"))
        app = _make_app(registry=reg, runner=TimeoutRunner())
        client = TestClient(app)
        resp = client.get("/render/slow")
        assert resp.status_code == 504
        assert "timeout" in resp.text.lower()

    def test_renderer_failure_returns_500(self):
        class FailingRunner:
            async def run(self, spec):
                raise RendererFailure(spec.name, "disk full", stdout="out", stderr="err")

        reg = _make_registry(_make_spec("boom"))
        app = _make_app(registry=reg, runner=FailingRunner())
        client = TestClient(app)
        resp = client.get("/render/boom")
        assert resp.status_code == 500
        assert "Renderer failed" in resp.text
        assert "disk full" in resp.text

    def test_schema_validation_error_returns_422(self):
        class SchemaFailRunner:
            async def run(self, spec):
                raise SchemaValidationError(spec.name, ["/hostname: must be string"])

        reg = _make_registry(_make_spec("bad_data"))
        app = _make_app(registry=reg, runner=SchemaFailRunner())
        client = TestClient(app)
        resp = client.get("/render/bad_data")
        assert resp.status_code == 422
        assert "Schema validation error" in resp.text
        assert "hostname" in resp.text

    def test_schema_validation_error_mixed_messages(self):
        class MixedValRunner:
            async def run(self, spec):
                raise SchemaValidationError(
                    spec.name,
                    ["/a: wrong type", "bare message without colon"],
                )

        reg = _make_registry(_make_spec("mixed"))
        app = _make_app(registry=reg, runner=MixedValRunner())
        client = TestClient(app)
        resp = client.get("/render/mixed")
        assert resp.status_code == 422
        assert "wrong type" in resp.text
        assert "bare message" in resp.text

    def test_canonical_mode_returns_200(self):
        class CanonicalRunner:
            async def run(self, spec):
                return _canonical_doc("Canonical Page", "hello")

        reg = _make_registry(_make_spec("canon"))
        app = _make_app(registry=reg, runner=CanonicalRunner())
        client = TestClient(app)
        resp = client.get("/render/canon")
        assert resp.status_code == 200
        assert "Canonical Page" in resp.text
        assert "hello" in resp.text

    def test_schema_driven_mode_returns_200(self, tmp_path):
        schema_path = tmp_path / "sd.schema.json"
        schema_path.write_text(json.dumps({"type": "object", "title": "My Page", "properties": {}}))

        class SchemaRunner:
            async def run(self, spec):
                return {"key": "val"}

        reg = _make_registry(_make_spec("schema_driven", schema_path=str(schema_path)))
        app = _make_app(registry=reg, runner=SchemaRunner())
        client = TestClient(app)
        resp = client.get("/render/schema_driven")
        assert resp.status_code == 200
        assert "My Page" in resp.text
        assert "val" in resp.text

    def test_cache_hit_avoids_runner(self):
        class NeverCallRunner:
            async def run(self, spec):
                raise RuntimeError("must not be called")

        reg = _make_registry(_make_spec("cached"))
        cache = RendererCache()
        cache.set("cached", "<html>Cached!</html>")
        app = _make_app(registry=reg, cache=cache, runner=NeverCallRunner())
        client = TestClient(app)
        resp = client.get("/render/cached")
        assert resp.status_code == 200
        assert "Cached!" in resp.text

    def test_cache_miss_runs_runner_and_populates_cache(self):
        call_count = 0

        class CountingRunner:
            async def run(self, spec):
                nonlocal call_count
                call_count += 1
                return _canonical_doc("Fresh", "content")

        reg = _make_registry(_make_spec("fresh"))
        cache = RendererCache()
        app = _make_app(registry=reg, cache=cache, runner=CountingRunner())
        client = TestClient(app)

        resp1 = client.get("/render/fresh")
        assert resp1.status_code == 200
        assert call_count == 1

        resp2 = client.get("/render/fresh")
        assert resp2.status_code == 200
        assert call_count == 1  # cached, runner not called again

    def test_no_cache_runner_called_every_time(self):
        call_count = 0

        class CountingRunner:
            async def run(self, spec):
                nonlocal call_count
                call_count += 1
                return _canonical_doc("NoCache", "body")

        reg = _make_registry(_make_spec("nocache"))
        app = _make_app(registry=reg, cache=None, runner=CountingRunner())
        client = TestClient(app)

        resp1 = client.get("/render/nocache")
        assert resp1.status_code == 200
        resp2 = client.get("/render/nocache")
        assert resp2.status_code == 200
        assert call_count == 2  # no cache, runner called twice


class TestRendererAllowRawHtml:
    def test_allow_raw_html_false_in_spec_passes_through(self):
        class CaptureRunner:
            async def run(self, spec):
                return _canonical_doc("T", "body")

        reg = _make_registry(_make_spec("safe_render"))
        app = _make_app(registry=reg, runner=CaptureRunner())
        client = TestClient(app)
        resp = client.get("/render/safe_render")
        assert resp.status_code == 200

    def test_allow_raw_html_true_in_spec_passes_through(self):
        spec = _make_spec("raw_ok")
        spec.allow_raw_html = True
        reg = _make_registry(spec)

        class CaptureRunner:
            async def run(self, spec):
                return _canonical_doc("T", "body")

        app = _make_app(registry=reg, runner=CaptureRunner())
        client = TestClient(app)
        resp = client.get("/render/raw_ok")
        assert resp.status_code == 200


class TestSchemaValidationErrorWithCompanionSchema:
    def test_loads_companion_schema_on_validation_error(self, tmp_path):
        schema_path = tmp_path / "with_schema.schema.json"
        schema_path.write_text(json.dumps({"type": "object", "title": "Gadget Schema", "properties": {}}))

        class SchemaFailRunner:
            async def run(self, spec):
                raise SchemaValidationError(spec.name, ["/widget: must be a number"])

        reg = _make_registry(_make_spec("with_schema", schema_path=str(schema_path)))
        app = _make_app(registry=reg, runner=SchemaFailRunner())
        client = TestClient(app)
        resp = client.get("/render/with_schema")
        assert resp.status_code == 422
        assert "Gadget Schema" in resp.text
        assert "widget" in resp.text


class TestRendererExceptionSafety:
    def test_unhandled_exception_during_render(self):
        class CrashRunner:
            async def run(self, spec):
                raise ValueError("unexpected runner crash")

        reg = _make_registry(_make_spec("crash"))
        app = _make_app(registry=reg, runner=CrashRunner())
        client = TestClient(app)
        resp = client.get("/render/crash")
        assert resp.status_code == 500
        assert "Renderer failed" in resp.text
        assert "unexpected runner crash" not in resp.text


class TestRenderByNameHtmlContentType:
    def test_response_is_html(self):
        class QuickRunner:
            async def run(self, spec):
                return _canonical_doc("T", "body")

        reg = _make_registry(_make_spec("html"))
        app = _make_app(registry=reg, runner=QuickRunner())
        client = TestClient(app)
        resp = client.get("/render/html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ═══════════════════════════════════════════════════════════════════════════
# Schema error structured-message parsing
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaValidationErrorParsing:
    def test_colon_separated_message_parsed_to_path_and_message(self):
        class Runner:
            async def run(self, spec):
                raise SchemaValidationError(spec.name, ["/cpu/cores: must be integer"])

        reg = _make_registry(_make_spec("parsed"))
        app = _make_app(registry=reg, runner=Runner())
        client = TestClient(app)
        resp = client.get("/render/parsed")
        assert resp.status_code == 422
        assert "/cpu/cores" in resp.text
        assert "must be integer" in resp.text

    def test_message_without_colon_gets_root_path(self):
        class Runner:
            async def run(self, spec):
                raise SchemaValidationError(spec.name, ["document is too large"])

        reg = _make_registry(_make_spec("root"))
        app = _make_app(registry=reg, runner=Runner())
        client = TestClient(app)
        resp = client.get("/render/root")
        assert resp.status_code == 422
        assert "document is too large" in resp.text
        assert "(root)" in resp.text

    def test_message_with_multiple_colons_splits_on_first_only(self):
        class Runner:
            async def run(self, spec):
                raise SchemaValidationError(
                    spec.name,
                    ["/path: value: contains colon in message"],
                )

        reg = _make_registry(_make_spec("multi_colon"))
        app = _make_app(registry=reg, runner=Runner())
        client = TestClient(app)
        resp = client.get("/render/multi_colon")
        assert resp.status_code == 422
        assert "/path" in resp.text
        assert "value: contains colon in message" in resp.text
