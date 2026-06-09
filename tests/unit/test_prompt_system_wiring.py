"""Tests for prompt system wiring: registry -> EventLoop -> template rendering.

GAP 1: PromptRegistry wired into EventLoop
GAP 2: self_improvement work type mapped
GAP 3: PromptRegistry wired into HotReloader
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app
from general_ludd.prompts.registry import (
    PromptRegistry,
    get_template_name_for_work_type,
)

TEMPLATES_DIR = str(Path(__file__).parent.parent.parent / "templates" / "prompts")


class TestPromptRegistryWiredToEventLoop:
    @pytest.fixture
    def app_with_templates(self):
        return create_daemon_app(
            tick_interval=0.01,
            templates_dir=TEMPLATES_DIR,
        )

    def test_event_loop_receives_prompt_registry(self, app_with_templates):
        with TestClient(app_with_templates):
            event_loop = app_with_templates.state.event_loop
            assert event_loop is not None
            assert event_loop._prompt_registry is not None

    def test_prompt_registry_loaded_templates(self, app_with_templates):
        with TestClient(app_with_templates):
            registry = app_with_templates.state._prompt_registry
            assert registry is not None
            templates = registry.list_templates()
            assert len(templates) > 0
            assert "implementation.md.j2" in templates

    def test_prompt_registry_renders_implementation_template(self, app_with_templates):
        with TestClient(app_with_templates):
            registry = app_with_templates.state._prompt_registry
            rendered = registry.render(
                "implementation.md.j2",
                todo={"title": "Fix bug", "work_type": "code"},
                config={},
            )
            assert isinstance(rendered, str)
            assert len(rendered) > 0


class TestSelfImprovementTemplateMap:
    def test_self_improvement_mapped(self):
        result = get_template_name_for_work_type("self_improvement")
        assert result == "self_improvement.md.j2"

    def test_all_known_work_types_mapped(self):
        for wt in [
            "code", "test", "review", "refactor", "docs", "infra",
            "prompt", "analysis", "audit", "release", "dependency",
            "security", "model", "self_improvement", "unknown",
        ]:
            assert get_template_name_for_work_type(wt) is not None, f"{wt} not mapped"


class TestHotReloaderGetsPromptRegistry:
    def test_reload_endpoint_wires_prompt_registry(self):
        app = create_daemon_app(
            tick_interval=0.01,
            templates_dir=TEMPLATES_DIR,
        )
        with TestClient(app) as client:
            resp = client.post("/admin/reload", json={"scope": "templates"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True


class TestTemplatesEndpoint:
    def test_templates_list_after_refresh(self):
        app = create_daemon_app(
            tick_interval=0.01,
            templates_dir=TEMPLATES_DIR,
        )
        with TestClient(app) as client:
            refresh_resp = client.post("/admin/templates/refresh")
            assert refresh_resp.status_code == 200
            list_resp = client.get("/admin/templates")
            assert list_resp.status_code == 200
            templates = list_resp.json()["templates"]
            assert len(templates) > 0

    def test_templates_list_auto_populated_at_startup(self):
        app = create_daemon_app(
            tick_interval=0.01,
            templates_dir=TEMPLATES_DIR,
        )
        with TestClient(app) as client:
            list_resp = client.get("/admin/templates")
            assert list_resp.status_code == 200
            templates = list_resp.json()["templates"]
            assert len(templates) > 0


class TestPromptRegistryUnit:
    def test_register_and_render_in_memory(self):
        registry = PromptRegistry()
        registry.register("test.md.j2", "Hello {{ name }}!")
        rendered = registry.render("test.md.j2", name="world")
        assert rendered == "Hello world!"

    def test_refresh_discovers_disk_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "example.md.j2").write_text("Content: {{ x }}")
            registry = PromptRegistry(template_dir=tmpdir)
            result = registry.refresh()
            assert "example.md.j2" in result["templates"]
            assert registry.list_templates() == ["example.md.j2"]

    def test_refresh_removes_stale_disk_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir, "old.md.j2")
            f.write_text("old")
            registry = PromptRegistry(template_dir=tmpdir)
            registry.refresh()
            assert "old.md.j2" in registry.list_templates()
            f.unlink()
            registry.refresh()
            assert "old.md.j2" not in registry.list_templates()

    def test_refresh_preserves_in_memory_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = PromptRegistry(template_dir=tmpdir)
            registry.register("mem.md.j2", "in-memory")
            Path(tmpdir, "disk.md.j2").write_text("on-disk")
            registry.refresh()
            names = registry.list_templates()
            assert "mem.md.j2" in names
            assert "disk.md.j2" in names
            Path(tmpdir, "disk.md.j2").unlink()
            registry.refresh()
            assert "mem.md.j2" in registry.list_templates()
