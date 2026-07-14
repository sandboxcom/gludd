"""Structural tests for renderers/registry.py — renderer playbook discovery."""

import tempfile
from pathlib import Path

from general_ludd.renderers.registry import (
    RendererMeta,
    RendererRegistry,
    RendererSpec,
    _coerce_int,
    _companion_schema,
    _resolve_default_playbooks_dir,
)


class TestRendererSpec:
    def test_construct_minimal(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        assert spec.name == "test"
        assert spec.description == ""
        assert spec.timeout_seconds == 30
        assert spec.cache_ttl_seconds == 30
        assert spec.allow_raw_html is False
        assert spec.schema_path is None

    def test_construct_full(self):
        spec = RendererSpec(
            name="test",
            path=Path("/tmp/test.yml"),
            description="a renderer",
            timeout_seconds=60,
            cache_ttl_seconds=120,
            allow_raw_html=True,
            schema_path=Path("/tmp/test.schema.json"),
        )
        assert spec.allow_raw_html is True
        assert spec.timeout_seconds == 60
        assert spec.schema_path == Path("/tmp/test.schema.json")

    def test_model_dump(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        d = spec.model_dump()
        assert d["name"] == "test"
        assert d["path"] == "/tmp/test.yml"
        assert d["schema_path"] is None

    def test_playbook_path_property(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"))
        assert spec.playbook_path == "/tmp/test.yml"

    def test_timeout_s_property(self):
        spec = RendererSpec(name="test", path=Path("/tmp/test.yml"), timeout_seconds=45)
        assert spec.timeout_s == 45.0
        assert isinstance(spec.timeout_s, float)


class TestRendererMeta:
    def test_renderer_meta_is_renderer_spec(self):
        assert RendererMeta is RendererSpec


class TestCoerceInt:
    def test_valid_int(self):
        assert _coerce_int("42", 99) == 42

    def test_invalid_string(self):
        assert _coerce_int("abc", 99) == 99

    def test_none(self):
        assert _coerce_int(None, 99) == 99

    def test_float_string(self):
        assert _coerce_int("3.14", 99) == 99

    def test_int(self):
        assert _coerce_int(42, 99) == 42


class TestResolveDefaultPlaybooksDir:
    def test_returns_path(self):
        result = _resolve_default_playbooks_dir()
        assert isinstance(result, Path)


class TestCompanionSchema:
    def test_nonexistent(self):
        result = _companion_schema(Path("/nonexistent/foo.yml"))
        assert result is None

    def test_existing_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            yml = Path(tmp) / "test.yml"
            schema = Path(tmp) / "test.schema.json"
            yml.touch()
            schema.touch()
            result = _companion_schema(yml)
            assert result == schema


class TestRendererRegistry:
    def test_construct_defaults(self):
        registry = RendererRegistry()
        assert registry.bundled_dir is not None
        assert isinstance(registry.bundled_dir, Path)

    def test_construct_explicit_dirs(self):
        registry = RendererRegistry(
            bundled_dir=Path("/tmp/bundled"),
            operator_dir=Path("/tmp/operator"),
        )
        assert registry.bundled_dir == Path("/tmp/bundled")

    def test_discover_empty_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            assert registry.names() == []

    def test_discover_finds_renderer_playbooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            playbook = bundled / "my_renderer.yml"
            playbook.write_text("""
- name: test renderer
  vars:
    renderer: true
    renderer_description: a test renderer
    renderer_timeout_seconds: 45
          """.strip())
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            assert "my_renderer" in registry.names()

    def test_discover_skips_non_renderer_playbooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            playbook = bundled / "not_renderer.yml"
            playbook.write_text("- hosts: all\n  tasks: []\n")
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            assert "not_renderer" not in registry.names()

    def test_get_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            playbook = bundled / "foo.yml"
            playbook.write_text("""
- vars:
    renderer: true
            """.strip())
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            spec = registry.get("foo")
            assert spec is not None
            assert spec.name == "foo"

    def test_get_missing(self):
        registry = RendererRegistry(bundled_dir=Path("/tmp/bundled"))
        assert registry.get("nonexistent") is None

    def test_metadata_returns_list_of_dicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            playbook = bundled / "r.yml"
            playbook.write_text("""
- vars:
    renderer: true
            """.strip())
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            meta = registry.metadata()
            assert isinstance(meta, list)
            assert len(meta) == 1
            assert meta[0]["name"] == "r"

    def test_list_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            for name in ("a", "b"):
                (bundled / f"{name}.yml").write_text("- vars:\n    renderer: true\n")
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            specs = registry.list_all()
            assert len(specs) == 2
            names = {s.name for s in specs}
            assert names == {"a", "b"}

    def test_iter(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            (bundled / "x.yml").write_text("- vars:\n    renderer: true\n")
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            items = list(registry)
            assert len(items) == 1

    def test_len(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            (bundled / "a.yml").write_text("- vars:\n    renderer: true\n")
            (bundled / "b.yml").write_text("- vars:\n    renderer: true\n")
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            assert len(registry) == 2

    def test_contains(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            (bundled / "z.yml").write_text("- vars:\n    renderer: true\n")
            registry = RendererRegistry(bundled_dir=bundled)
            registry.discover()
            assert "z" in registry
            assert "missing" not in registry
