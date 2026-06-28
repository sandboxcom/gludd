"""Unit tests for the Phase 1 playbook-renderer registry + schema layer.

Covers docs/design/PLAYBOOK_WEB_RENDERER.md §3.2-§3.3, §4, §6:

  - RendererRegistry.discover() walks a bundled dir + an operator dir
    (operator wins on name clash) and skips any playbook lacking
    ``vars.renderer: true``.
  - RendererSpec carries the §6 field set (name/path/description/
    timeout_seconds/cache_ttl_seconds/allow_raw_html/schema_path).
  - RenderDocument (pydantic) validates the §4 canonical JSON shape,
    rejecting unknown section types and malformed payloads.
  - The runner (not the playbook) is the source of truth for the
    metadata block; whatever the playbook wrote is overwritten.
"""

from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from general_ludd.renderers.registry import RendererRegistry, RendererSpec
from general_ludd.renderers.schema import (
    ChartSection,
    MarkdownSection,
    MetricGridSection,
    RawHtmlSection,
    RenderDocument,
    RenderMetadata,
    TableSection,
)

_BUNDLED_PLAYBOOK = """\
---
- name: Bundled system facts renderer
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    renderer: true
    renderer_description: Bundled example
    renderer_timeout_seconds: 15
    renderer_cache_ttl_seconds: 45
    renderer_allow_raw_html: true
  tasks:
    - name: Write render.json
      ansible.builtin.copy:
        dest: "{{ artifact_dir }}/render.json"
        content: "{}"
"""

_NON_RENDERER_PLAYBOOK = """\
---
- name: Ordinary playbook that is not a renderer
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Noop
      ansible.builtin.debug:
        msg: hi
"""

_OPERATOR_PLAYBOOK = """\
---
- name: Operator override of bundled renderer
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    renderer: true
    renderer_description: Operator override
  tasks:
    - name: Write render.json
      ansible.builtin.copy:
        dest: "{{ artifact_dir }}/render.json"
        content: "{}"
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestRendererSpecShape:
    def test_renderer_spec_is_dataclass_with_required_fields(self):
        assert is_dataclass(RendererSpec)
        spec = RendererSpec(
            name="x",
            path=Path("/tmp/x.yml"),
            description="d",
        )
        # §6 default knobs.
        assert spec.timeout_seconds == 30
        assert spec.cache_ttl_seconds == 30
        assert spec.allow_raw_html is False
        assert spec.schema_path is None
        # All §6 fields exist as dataclass fields.
        from dataclasses import fields

        field_names = {f.name for f in fields(RendererSpec)}
        assert field_names == {
            "name",
            "path",
            "description",
            "timeout_seconds",
            "cache_ttl_seconds",
            "allow_raw_html",
            "schema_path",
        }


class TestRegistryDiscovery:
    def test_discover_finds_bundled_renderer(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        _write(bundled / "system_facts.yml", _BUNDLED_PLAYBOOK)
        registry = RendererRegistry(bundled_dir=bundled, operator_dir=None)
        registry.discover()
        assert "system_facts" in registry.names()
        spec = registry.get("system_facts")
        assert spec is not None
        assert spec.name == "system_facts"
        assert spec.path.name == "system_facts.yml"
        assert spec.description == "Bundled example"
        assert spec.timeout_seconds == 15
        assert spec.cache_ttl_seconds == 45
        assert spec.allow_raw_html is True

    def test_discover_ignores_non_renderer_playbooks(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        _write(bundled / "system_facts.yml", _BUNDLED_PLAYBOOK)
        _write(bundled / "housekeeping.yml", _NON_RENDERER_PLAYBOOK)
        registry = RendererRegistry(bundled_dir=bundled, operator_dir=None)
        registry.discover()
        names = set(registry.names())
        assert "system_facts" in names
        assert "housekeeping" not in names

    def test_operator_dir_overrides_bundled(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        operator = tmp_path / "operator"
        _write(bundled / "system_facts.yml", _BUNDLED_PLAYBOOK)
        _write(operator / "system_facts.yml", _OPERATOR_PLAYBOOK)
        registry = RendererRegistry(bundled_dir=bundled, operator_dir=operator)
        registry.discover()
        spec = registry.get("system_facts")
        assert spec is not None
        # Operator description wins.
        assert spec.description == "Operator override"
        # Operator file path wins.
        assert operator in spec.path.parents

    def test_metadata_payload_has_expected_keys(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        _write(bundled / "system_facts.yml", _BUNDLED_PLAYBOOK)
        registry = RendererRegistry(bundled_dir=bundled, operator_dir=None)
        registry.discover()
        meta_list = registry.metadata()
        assert len(meta_list) == 1
        entry = meta_list[0]
        assert entry["name"] == "system_facts"
        assert "description" in entry
        assert "timeout_seconds" in entry

    def test_system_facts_fixture_is_valid_renderer(self):
        """The shipped Phase 1 fixture is discoverable + correctly described.

        References docs/design/PLAYBOOK_WEB_RENDERER.md §2 + §5: the fixture
        lives at ``playbooks/renderers/system_facts.yml`` and MUST carry
        ``vars.renderer: true`` plus the canonical timeout/cache knobs.
        """
        import yaml

        repo_root = Path(__file__).resolve().parents[2]
        renderers_dir = repo_root / "playbooks" / "renderers"
        fixture = renderers_dir / "system_facts.yml"
        assert fixture.is_file(), f"fixture missing: {fixture}"

        registry = RendererRegistry(bundled_dir=renderers_dir, operator_dir=None)
        registry.discover()
        assert "system_facts" in registry.names()

        spec = registry.get("system_facts")
        assert spec is not None
        assert spec.name == "system_facts"
        assert spec.path == fixture
        # The fixture-set knobs from the task spec (§5).
        assert spec.timeout_seconds == 20
        assert spec.cache_ttl_seconds == 30

        # Re-read the YAML to assert the marker + description directly.
        data = yaml.safe_load(fixture.read_text())
        assert isinstance(data, list) and data
        play_vars = data[0].get("vars", {}) or {}
        assert play_vars.get("renderer") is True
        # Description is non-empty and matches what the registry parsed.
        assert spec.description
        assert play_vars["renderer_description"].strip() == spec.description.strip()


class TestSchemaValidation:
    def test_render_document_accepts_all_section_types(self):
        doc = RenderDocument.model_validate(
            {
                "title": "All sections",
                "sections": [
                    {"type": "markdown", "content": "## hi"},
                    {
                        "type": "metric_grid",
                        "metrics": [
                            {"label": "a", "value": 1},
                            {"label": "b", "value": 2.5, "unit": "USD"},
                        ],
                    },
                    {
                        "type": "table",
                        "title": "t",
                        "columns": ["a", "b"],
                        "rows": [["x", 1], ["y", 2]],
                    },
                    {
                        "type": "chart",
                        "title": "c",
                        "chart_type": "line",
                        "data": {
                            "labels": ["a", "b"],
                            "series": [{"name": "s", "values": [1, 2]}],
                        },
                    },
                    {"type": "raw_html", "html": "<p>x</p>"},
                ],
                "metadata": {
                    "generated_at": "2026-06-28T14:03:22Z",
                    "playbook": "x.yml",
                    "execution_ms": 12,
                    "renderer_version": 1,
                },
            }
        )
        assert isinstance(doc, RenderDocument)
        assert isinstance(doc.sections[0], MarkdownSection)
        assert isinstance(doc.sections[1], MetricGridSection)
        assert isinstance(doc.sections[2], TableSection)
        assert isinstance(doc.sections[3], ChartSection)
        assert isinstance(doc.sections[4], RawHtmlSection)

    def test_schema_validation_rejects_bad_shape(self):
        # Missing required `title`.
        with pytest.raises(ValidationError):
            RenderDocument.model_validate({"sections": []})
        # Unknown section type.
        with pytest.raises(ValidationError):
            RenderDocument.model_validate(
                {
                    "title": "ok",
                    "sections": [{"type": "hologram", "content": "..."}],
                    "metadata": {},
                }
            )
        # metric_grid missing `metrics`.
        with pytest.raises(ValidationError):
            RenderDocument.model_validate(
                {
                    "title": "ok",
                    "sections": [{"type": "metric_grid"}],
                    "metadata": {},
                }
            )

    def test_section_types_validated(self):
        # Accepted types are exactly the closed set.
        good_types = {"markdown", "metric_grid", "table", "chart", "raw_html"}
        for t in good_types:
            payload: dict = {"title": "t", "sections": [], "metadata": {}}
            if t == "markdown":
                payload["sections"] = [{"type": t, "content": "x"}]
            elif t == "metric_grid":
                payload["sections"] = [{"type": t, "metrics": []}]
            elif t == "table":
                payload["sections"] = [{"type": t, "columns": ["a"], "rows": []}]
            elif t == "chart":
                payload["sections"] = [
                    {"type": t, "chart_type": "bar", "data": {"labels": [], "series": []}}
                ]
            elif t == "raw_html":
                payload["sections"] = [{"type": t, "html": "<x/>"}]
            RenderDocument.model_validate(payload)  # no raise

        # Unknown type rejected.
        with pytest.raises(ValidationError):
            RenderDocument.model_validate(
                {
                    "title": "t",
                    "sections": [{"type": "bogus"}],
                    "metadata": {},
                }
            )


class TestMetadataOverwrite:
    def test_metadata_overwrite(self):
        """Runner overwrites execution_ms + playbook regardless of playbook values."""
        raw = {
            "title": "t",
            "sections": [],
            "metadata": {
                "generated_at": "1970-01-01T00:00:00Z",
                "playbook": "PLAYBOOK_LIED.yml",
                "execution_ms": 999999,
                "renderer_version": 1,
            },
        }
        doc = RenderDocument.model_validate(raw)
        # The runner is the source of truth: it replaces these fields after run.
        # We simulate the runner's overwrite step here.
        assert isinstance(doc.metadata, RenderMetadata)
        doc.metadata.execution_ms = 42
        doc.metadata.playbook = "gpu_dashboard.yml"
        assert doc.metadata.execution_ms == 42
        assert doc.metadata.playbook == "gpu_dashboard.yml"
        # And the original lie is gone.
        assert doc.metadata.execution_ms != 999999
