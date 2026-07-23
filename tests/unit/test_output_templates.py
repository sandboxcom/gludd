from __future__ import annotations

import json

import pytest
from jinja2 import TemplateError
from jinja2.exceptions import SecurityError

from general_ludd.output_templates import (
    OutputTemplateRegistry,
    compile_default_output_templates,
    render_smoke_report,
)


def test_output_registry_compiles_and_renders_user_ordered_template(tmp_path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "smoke.custom.txt.j2").write_text(
        "{{ report.provider }}|{{ report.status }}|{{ report.metrics.checks_failed }}"
        "{% if report.status != \"pass\" %}|show-failure{% endif %}",
        encoding="utf-8",
    )

    registry = OutputTemplateRegistry(template_dirs=[template_dir])
    summary = registry.compile()

    assert summary["count"] == 1
    assert summary["templates"] == ["smoke.custom.txt.j2"]
    assert registry.render(
        "smoke.custom.txt.j2",
        report={"provider": "aws", "status": "fail", "metrics": {"checks_failed": 2}},
    ) == "aws|fail|2|show-failure"


def test_output_registry_prefers_operator_template_directory(tmp_path) -> None:
    default_dir = tmp_path / "default"
    override_dir = tmp_path / "override"
    default_dir.mkdir()
    override_dir.mkdir()
    (default_dir / "smoke.report.text.j2").write_text("default {{ report.provider }}", encoding="utf-8")
    (override_dir / "smoke.report.text.j2").write_text("override {{ report.provider }}", encoding="utf-8")

    registry = OutputTemplateRegistry(template_dirs=[override_dir, default_dir])
    registry.compile()

    assert registry.render("smoke.report.text.j2", report={"provider": "openrouter"}) == "override openrouter"


def test_output_templates_block_python_runtime_access(tmp_path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "unsafe.j2").write_text("{{ report.__class__.__mro__ }}", encoding="utf-8")
    registry = OutputTemplateRegistry(template_dirs=[template_dir])
    registry.compile()

    with pytest.raises((SecurityError, TemplateError)):
        registry.render("unsafe.j2", report={"provider": "aws"})


def test_default_smoke_json_template_round_trips_report() -> None:
    registry = compile_default_output_templates()
    report = {
        "provider": "aws",
        "test": "ec2-a100",
        "status": "pass",
        "metrics": {"checks_failed": 0},
        "logs": [{"level": "info", "message": "ok", "fields": {}}],
        "events": [],
        "trace": [],
    }

    rendered = render_smoke_report(report, json_output=True, registry=registry)

    parsed = json.loads(rendered)
    assert parsed["provider"] == "aws"
    assert parsed["status"] == "pass"
    assert "smoke.report.json.j2" in registry.list_templates()
    assert "smoke.report.text.j2" in registry.list_templates()


def test_default_smoke_text_template_includes_logs_and_metrics() -> None:
    report = {
        "run_id": "smoke-1",
        "provider": "aws",
        "test": "ec2-a100",
        "status": "fail",
        "mode": "dry-run",
        "coverage_depth": "preflight",
        "functional_scope": ["configuration"],
        "estimated_cost_usd": 0.0,
        "trace_id": "trace-1",
        "metrics": {"checks_total": 2, "checks_failed": 1, "duration_ms": 7},
        "logs": [{"level": "error", "message": "credential variable check", "fields": {"missing": ["AWS_KEY"]}}],
        "events": [],
        "trace": [],
        "analysis_prompt": "Analyze this report.",
    }

    rendered = render_smoke_report(report, json_output=False)

    assert "provider: aws" in rendered
    assert "status: fail" in rendered
    assert "credential variable check" in rendered
    assert "AWS_KEY" in rendered
