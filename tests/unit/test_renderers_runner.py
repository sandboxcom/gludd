"""Unit tests for renderers/runner.py.

Covers the previously 28.1%-rated module by exercising:
  * run_renderer with stub injection (canonical + schema-driven)
  * run_renderer without runner (RendererFailure)
  * _read_render_json (missing, oversize, parse error, non-dict)
  * _validate_canonical (RenderDocument validation, metadata overwrite)
  * _validate_with_schema (missing schema, validation failure)
  * _coerce_stub_output
  * _max_bytes env var parsing
  * RendererTimeout, RendererFailure, SchemaValidationError construction
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from general_ludd.renderers.registry import RendererSpec
from general_ludd.renderers.runner import (
    RendererFailure,
    RendererResult,
    RendererTimeout,
    SchemaValidationError,
    _coerce_stub_output,
    _max_bytes,
    _read_render_json,
    _validate_canonical,
    _validate_with_schema,
    run_renderer,
)
from general_ludd.renderers.schema import (
    MarkdownSection,
    RenderDocument,
    RenderMetadata,
)


class TestMaxBytes:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("GLUDD_RENDER_MAX_BYTES", raising=False)
        assert _max_bytes() == 1024 * 1024

    def test_parses_integer(self, monkeypatch):
        monkeypatch.setenv("GLUDD_RENDER_MAX_BYTES", "4096")
        assert _max_bytes() == 4096

    def test_invalid_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("GLUDD_RENDER_MAX_BYTES", "not-a-number")
        with caplog.at_level("WARNING"):
            result = _max_bytes()
        assert result == 1024 * 1024


class TestReadRenderJson:
    def test_reads_valid_json(self, tmp_path):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "render.json").write_text('{"key": "value"}')
        data = _read_render_json(artifact_dir, "test-renderer")
        assert data == {"key": "value"}

    def test_missing_file_raises(self, tmp_path):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        with pytest.raises(RendererFailure, match="not written"):
            _read_render_json(artifact_dir, "test-renderer")

    def test_oversize_file_raises(self, tmp_path):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        payload = json.dumps({"x": "y" * 2000})
        (artifact_dir / "render.json").write_text(payload)
        with (
            patch("general_ludd.renderers.runner._max_bytes", return_value=10),
            pytest.raises(RendererFailure, match="cap="),
        ):
            _read_render_json(artifact_dir, "test-renderer")

    def test_non_dict_root_raises(self, tmp_path):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "render.json").write_text("[1, 2, 3]")
        with pytest.raises(RendererFailure, match="must be an object"):
            _read_render_json(artifact_dir, "test-renderer")

    def test_invalid_json_raises(self, tmp_path):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "render.json").write_text("not json {{")
        with pytest.raises(RendererFailure, match="cannot parse"):
            _read_render_json(artifact_dir, "test-renderer")


class TestCoerceStubOutput:
    def test_render_document_returns_dict(self):
        doc = RenderDocument(
            title="test",
            sections=[MarkdownSection(type="markdown", content="hi")],
            metadata=RenderMetadata(
                generated_at="2026-01-01T00:00:00Z",
                playbook="x.yml",
                execution_ms=0,
            ),
        )
        result = _coerce_stub_output(doc, RendererSpec(name="r", path=Path("/r.yml"), description="d"))
        assert result == doc.model_dump()

    def test_dict_passthrough(self):
        result = _coerce_stub_output({"k": "v"}, RendererSpec(name="r", path=Path("/r.yml"), description="d"))
        assert result == {"k": "v"}

    def test_unknown_type_raises(self):
        with pytest.raises(RendererFailure, match="expected dict"):
            _coerce_stub_output("not-a-dict", RendererSpec(name="r", path=Path("/r.yml"), description="d"))


class TestValidateCanonical:
    def test_valid_document_passes(self):
        raw = {
            "title": "Test Report",
            "sections": [{"type": "markdown", "content": "## hi"}],
            "metadata": {
                "generated_at": "2026-01-01T00:00:00Z",
                "playbook": "x.yml",
                "execution_ms": 0,
            },
        }
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"), description="d")
        result = _validate_canonical(spec, raw, 100.0)
        assert result.doc is not None
        assert result.doc.title == "Test Report"

    def test_metadata_overwritten(self):
        raw = {
            "title": "T",
            "sections": [],
            "metadata": {
                "generated_at": "2026-01-01T00:00:00Z",
                "playbook": "LIED.yml",
                "execution_ms": 999999,
            },
        }
        spec = RendererSpec(name="test", path=Path("/tmp/reports/gpu.yml"), description="d")
        result = _validate_canonical(spec, raw, 200.0)
        assert result.doc is not None
        assert result.doc.metadata.playbook == "gpu.yml"

    def test_invalid_document_raises(self):
        spec = RendererSpec(name="r", path=Path("/r.yml"), description="d")
        with pytest.raises(RendererFailure, match="schema validation failed"):
            _validate_canonical(spec, {"title": "only", "sections": [{"type": "bogus"}], "metadata": {}}, 0.0)


class TestValidateWithSchema:
    def test_valid_against_schema_passes(self, tmp_path):
        schema_path = tmp_path / "test.schema.json"
        schema_path.write_text(json.dumps({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }))
        spec = RendererSpec(
            name="test", path=Path("/r.yml"), description="d",
            schema_path=schema_path,
        )
        result = _validate_with_schema(spec, {"name": "value"})
        assert result.schema is not None
        assert result.doc is None

    def test_missing_schema_file_raises(self, tmp_path):
        schema_path = tmp_path / "nonexistent.schema.json"
        spec = RendererSpec(
            name="test", path=Path("/r.yml"), description="d",
            schema_path=schema_path,
        )
        with pytest.raises(RendererFailure, match="not found"):
            _validate_with_schema(spec, {"name": "value"})

    def test_validation_failure_raises(self, tmp_path):
        schema_path = tmp_path / "test.schema.json"
        schema_path.write_text(json.dumps({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }))
        spec = RendererSpec(
            name="test", path=Path("/r.yml"), description="d",
            schema_path=schema_path,
        )
        with pytest.raises(SchemaValidationError, match="companion schema validation"):
            _validate_with_schema(spec, {})


class TestRunRenderer:
    @pytest.mark.asyncio
    async def test_stub_canonical_mode(self):
        app = FastAPI()
        stub = MagicMock()
        doc = RenderDocument(
            title="Stub Result",
            sections=[MarkdownSection(type="markdown", content="body")],
            metadata=RenderMetadata(
                generated_at="2026-01-01T00:00:00Z",
                playbook="x.yml",
                execution_ms=0,
            ),
        )
        stub.run = AsyncMock(return_value=doc)
        app.state._renderer_runner = stub
        spec = RendererSpec(name="stub-test", path=Path("/r.yml"), description="d")
        result = await run_renderer(app, spec)
        assert result.doc is not None
        assert result.doc.title == "Stub Result"

    @pytest.mark.asyncio
    async def test_stub_schema_driven_mode(self, tmp_path):
        app = FastAPI()
        stub = MagicMock()
        stub.run = AsyncMock(return_value={"name": "schema-value"})
        app.state._renderer_runner = stub
        schema_path = tmp_path / "test.schema.json"
        schema_path.write_text(json.dumps({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }))
        spec = RendererSpec(
            name="schema-test", path=Path("/r.yml"), description="d",
            schema_path=schema_path,
        )
        result = await run_renderer(app, spec)
        assert result.schema is not None
        assert result.doc is None

    @pytest.mark.asyncio
    async def test_no_runner_raises(self):
        app = FastAPI()
        app.state._renderer_runner = None
        spec = RendererSpec(name="test", path=Path("/r.yml"), description="d")
        with pytest.raises(RendererFailure, match="no AnsibleRunnerAdapter"):
            await run_renderer(app, spec)


class TestExceptions:
    def test_renderer_timeout(self):
        exc = RendererTimeout("my-renderer", 30.0)
        assert exc.name == "my-renderer"
        assert exc.timeout_s == 30.0
        assert "my-renderer" in str(exc)
        assert str(30.0) in str(exc)

    def test_renderer_failure(self):
        exc = RendererFailure("r", "bad thing", stdout="out", stderr="err")
        assert exc.name == "r"
        assert exc.detail == "bad thing"
        assert exc.stdout == "out"
        assert exc.stderr == "err"
        assert "bad thing" in str(exc)

    def test_schema_validation_error(self):
        exc = SchemaValidationError("r", ["error 1", "error 2"])
        assert exc.name == "r"
        assert exc.errors == ["error 1", "error 2"]
        assert "2 error" in str(exc)
        assert "schema validation" in str(exc)


class TestRendererResult:
    def test_default_constructor(self):
        result = RendererResult()
        assert result.data == {}
        assert result.schema is None
        assert result.field_metadata is None
        assert result.doc is None

    def test_custom_data(self):
        result = RendererResult(data={"k": "v"}, doc=None)
        assert result.data == {"k": "v"}


class TestExecutePlaybook:
    @pytest.mark.asyncio
    async def test_execute_playbook_timeout_raises(self):
        import asyncio

        from general_ludd.renderers.runner import _execute_playbook

        mock_runner = MagicMock()
        mock_runner.register_playbook.return_value = None
        spec = RendererSpec(name="timeout-test", path=Path("/r.yml"), description="d")
        spec.timeout_seconds = 0.01

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(1)
            return {}

        with patch("asyncio.to_thread", side_effect=slow_run), pytest.raises(RendererTimeout):
            await _execute_playbook(spec, mock_runner)

    @pytest.mark.asyncio
    async def test_execute_playbook_playbook_failure_raises(self):
        from general_ludd.renderers.runner import _execute_playbook

        mock_runner = MagicMock()
        mock_runner.register_playbook.return_value = None

        async def fake_to_thread(fn, *args, **kwargs):
            return {"status": "failed", "rc": 1, "stdout": "", "stderr": "err msg"}

        spec = RendererSpec(name="fail-test", path=Path("/r.yml"), description="d")
        with (
            patch("asyncio.to_thread", side_effect=fake_to_thread),
            pytest.raises(RendererFailure, match="playbook exited"),
        ):
            await _execute_playbook(spec, mock_runner)

    @pytest.mark.asyncio
    async def test_execute_playbook_success_with_render_json(self, tmp_path):
        import tempfile

        from general_ludd.renderers.runner import _execute_playbook

        artifact_owner = tempfile.TemporaryDirectory(dir=tmp_path)
        artifact_root = Path(artifact_owner.name)
        artifact_dir = artifact_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "render.json").write_text('{"title": "ok", "sections": []}')

        mock_runner = MagicMock()
        mock_runner.register_playbook.return_value = None

        async def fake_to_thread(fn, *args, **kwargs):
            return {"status": "successful", "rc": 0}

        spec = RendererSpec(name="ok-test", path=Path("/r.yml"), description="d")
        with patch("asyncio.to_thread", side_effect=fake_to_thread), \
             patch("tempfile.TemporaryDirectory", return_value=artifact_owner):
            raw, _start = await _execute_playbook(spec, mock_runner)
        assert raw["title"] == "ok"
        assert not artifact_root.exists()


class TestRunRendererRealRunner:
    @pytest.mark.asyncio
    async def test_run_renderer_with_real_runner_success(self, tmp_path):
        from general_ludd.renderers.runner import run_renderer

        app = FastAPI()
        app.state._renderer_runner = None

        artifact_root = tmp_path / "render-job"
        artifact_dir = artifact_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "render.json").write_text(json.dumps({
            "title": "Report",
            "sections": [{"type": "markdown", "content": "body"}],
            "metadata": {
                "generated_at": "2026-01-01T00:00:00Z",
                "playbook": "x.yml",
                "execution_ms": 0,
            },
        }))

        mock_runner = MagicMock()
        mock_runner.register_playbook.return_value = None
        app.state._runner = mock_runner

        async def fake_to_thread(fn, *args, **kwargs):
            return {"status": "successful", "rc": 0}

        spec = RendererSpec(name="real-test", path=Path("/r.yml"), description="d")
        with patch("asyncio.to_thread", side_effect=fake_to_thread), \
             patch("tempfile.mkdtemp", return_value=str(artifact_root)):
            result = await run_renderer(app, spec)
        assert result.doc is not None
        assert result.doc.title == "Report"

    @pytest.mark.asyncio
    async def test_run_renderer_with_real_runner_schema_mode(self, tmp_path):
        from general_ludd.renderers.runner import run_renderer

        app = FastAPI()
        app.state._renderer_runner = None

        artifact_root = tmp_path / "render-schema-job"
        artifact_dir = artifact_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "render.json").write_text(json.dumps({"name": "value"}))

        schema_path = tmp_path / "test.schema.json"
        schema_path.write_text(json.dumps({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }))

        mock_runner = MagicMock()
        mock_runner.register_playbook.return_value = None
        app.state._runner = mock_runner

        async def fake_to_thread(fn, *args, **kwargs):
            return {"status": "ok", "rc": 0}

        spec = RendererSpec(
            name="real-schema-test", path=Path("/r.yml"), description="d",
            schema_path=schema_path,
        )
        with patch("asyncio.to_thread", side_effect=fake_to_thread), \
             patch("tempfile.mkdtemp", return_value=str(artifact_root)):
            result = await run_renderer(app, spec)
        assert result.schema is not None
        assert result.doc is None
