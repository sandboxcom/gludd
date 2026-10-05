"""Deep renderer and formatter tests.

Covers table formatting, chart rendering, markdown generation, metric grid
rendering, HTML template integration, error pages, schema form rendering,
and RenderDocument validation edge cases.
"""

from __future__ import annotations

import json
import re

import pytest
from jinja2 import DictLoader
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ValidationError

from general_ludd.renderers.schema import (
    ChartData,
    ChartSection,
    ChartSeries,
    MarkdownSection,
    Metric,
    MetricGridSection,
    RawHtmlSection,
    RenderDocument,
    RenderMetadata,
    TableSection,
)


@pytest.fixture
def env():
    loader = DictLoader({})
    env = SandboxedEnvironment(
        loader=loader,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["dump_json"] = lambda v: json.dumps(
        v.model_dump() if hasattr(v, "model_dump") else v,
        indent=2,
        default=str,
    )
    return env


@pytest.fixture
def doc_fixture():
    return RenderDocument(
        title="Test Report",
        sections=[
            MarkdownSection(content="# Heading"),
            MetricGridSection(
                metrics=[
                    Metric(label="CPU", value=42.7, unit="%"),
                    Metric(label="Memory", value=8192, unit="MB"),
                    Metric(label="Uptime", value="3 days"),
                ]
            ),
            TableSection(
                title="Processes",
                columns=["Name", "PID", "Status"],
                rows=[
                    ["nginx", 1234, "running"],
                    ["postgres", 5678, "running"],
                    ["redis", 9012, "stopped"],
                ],
            ),
            ChartSection(
                title="CPU Usage",
                chart_type="line",
                data=ChartData(
                    labels=["Mon", "Tue", "Wed"],
                    series=[
                        ChartSeries(name="cpu_avg", values=[12.0, 45.0, 33.0]),
                        ChartSeries(name="cpu_max", values=[45.0, 92.0, 67.0]),
                    ],
                ),
            ),
        ],
        metadata=RenderMetadata(
            generated_at="2026-08-03T00:00:00Z",
            playbook="system_report.yml",
            execution_ms=150,
        ),
    )


# ── MarkdownSection rendering ──────────────────────────────────────────


class TestMarkdownRendering:
    def test_basic_markdown_section_renders(self, env, doc_fixture):
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% if s.type == 'markdown' %}"
            '<section class="mk">'
            "<pre>{{ s.content }}</pre>"
            "</section>"
            "{% endif %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc_fixture)
        assert "<pre># Heading</pre>" in html

    def test_markdown_escapes_html(self, env):
        doc = RenderDocument(
            title="X",
            sections=[MarkdownSection(content="<script>alert(1)</script>")],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = "{% for s in doc.sections %}<pre>{{ s.content }}</pre>{% endfor %}"
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_empty_markdown_section(self, env):
        doc = RenderDocument(
            title="X",
            sections=[MarkdownSection(content="")],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = "{% for s in doc.sections %}<pre>{{ s.content }}</pre>{% endfor %}"
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "<pre></pre>" in html


# ── MetricGridSection rendering ────────────────────────────────────────


class TestMetricGridRendering:
    def test_metric_grid_with_units(self, env, doc_fixture):
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% if s.type == 'metric_grid' %}"
            "{% for m in s.metrics %}"
            "<div><span>{{ m.label }}</span>"
            "<span>{{ m.value }}{% if m.unit %}{{ m.unit }}{% endif %}</span></div>"
            "{% endfor %}"
            "{% endif %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc_fixture)
        assert "CPU" in html
        assert "42.7%" in html
        assert "8192MB" in html

    def test_metric_without_unit(self, env):
        doc = RenderDocument(
            title="X",
            sections=[MetricGridSection(metrics=[Metric(label="Count", value=99)])],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% for m in s.metrics %}"
            "{{ m.label }}:{{ m.value }}{% if m.unit %} {{ m.unit }}{% endif %}|"
            "{% endfor %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "Count:99|" in html

    def test_empty_metric_grid(self, env):
        doc = RenderDocument(
            title="X",
            sections=[MetricGridSection(metrics=[])],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}GRID{% for m in s.metrics %}{{ m.label }}{% endfor %}GRID{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "GRIDGRID" in html


# ── TableSection rendering ─────────────────────────────────────────────


class TestTableRendering:
    def test_table_with_title_and_rows(self, env, doc_fixture):
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% if s.type == 'table' %}"
            "<h2>{{ s.title }}</h2>"
            "<table>"
            "<thead><tr>{% for c in s.columns %}<th>{{ c }}</th>{% endfor %}</tr></thead>"
            "<tbody>"
            "{% for row in s.rows %}"
            "<tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>"
            "{% endfor %}"
            "</tbody></table>"
            "{% endif %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc_fixture)
        assert "<h2>Processes</h2>" in html
        assert "<th>Name</th>" in html
        assert "<td>nginx</td>" in html
        assert "<td>5678</td>" in html

    def test_table_without_title(self, env):
        doc = RenderDocument(
            title="X",
            sections=[TableSection(columns=["A", "B"], rows=[[1, 2]])],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% if s.type == 'table' %}"
            "{% if s.title %}<h2>{{ s.title }}</h2>{% endif %}"
            "{% endif %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "<h2>" not in html

    def test_table_empty_rows(self, env):
        doc = RenderDocument(
            title="X",
            sections=[TableSection(columns=["Col1"], rows=[])],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}{% if s.type == 'table' %}ROWS:{{ s.rows | length }}{% endif %}{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "ROWS:0" in html

    def test_table_escapes_html_cells(self, env):
        doc = RenderDocument(
            title="X",
            sections=[TableSection(columns=["Col"], rows=[["<b>bold</b>"]])],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% for row in s.rows %}"
            "{% for cell in row %}<td>{{ cell }}</td>{% endfor %}"
            "{% endfor %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "&lt;b&gt;" in html
        assert "<b>bold</b>" not in html


# ── ChartSection rendering ─────────────────────────────────────────────


class TestChartRendering:
    def test_chart_with_labels_and_series(self, env, doc_fixture):
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% if s.type == 'chart' %}"
            "<section><h2>{{ s.title }}</h2>"
            "<span>{{ s.chart_type }}</span>"
            "<table>"
            "<thead><tr><th>label</th>"
            "{% for ser in s.data.series %}<th>{{ ser.name }}</th>{% endfor %}"
            "</tr></thead>"
            "<tbody>"
            "{% for lbl in s.data.labels %}"
            "<tr><td>{{ lbl }}</td>"
            "{% for ser in s.data.series %}<td>{{ ser.values[loop.index0] }}</td>{% endfor %}"
            "</tr>{% endfor %}"
            "</tbody></table></section>"
            "{% endif %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc_fixture)
        assert "<h2>CPU Usage</h2>" in html
        assert "<span>line</span>" in html
        assert "<th>cpu_avg</th>" in html
        assert "<th>cpu_max</th>" in html
        assert "<td>12.0</td>" in html
        assert "<td>92.0</td>" in html

    def test_chart_type_bar(self, env):
        doc = RenderDocument(
            title="X",
            sections=[
                ChartSection(
                    title="Bar Chart",
                    chart_type="bar",
                    data=ChartData(
                        labels=["A"],
                        series=[ChartSeries(name="val", values=[1])],
                    ),
                )
            ],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = "{% for s in doc.sections %}{{ s.chart_type }}{% endfor %}"
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "bar" in html

    def test_chart_type_pie(self, env):
        doc = RenderDocument(
            title="X",
            sections=[
                ChartSection(
                    title="Pie Chart",
                    chart_type="pie",
                    data=ChartData(
                        labels=["X"],
                        series=[ChartSeries(name="s", values=[1])],
                    ),
                )
            ],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = "{% for s in doc.sections %}{{ s.chart_type }}{% endfor %}"
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "pie" in html

    def test_chart_without_title(self, env):
        doc = RenderDocument(
            title="X",
            sections=[
                ChartSection(
                    chart_type="line",
                    data=ChartData(
                        labels=[],
                        series=[ChartSeries(name="s", values=[])],
                    ),
                )
            ],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}{% if s.title %}{{ s.title }}{% else %}NO_TITLE{% endif %}{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "NO_TITLE" in html


# ── RawHtmlSection rendering ───────────────────────────────────────────


class TestRawHtmlRendering:
    def test_raw_html_disabled_escapes(self, env):
        doc = RenderDocument(
            title="X",
            sections=[RawHtmlSection(html='<div class="alert">hi</div>')],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% if allow_raw_html %}{{ s.html | safe }}{% else %}"
            "<pre>{{ s.html }}</pre>"
            "{% endif %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc, allow_raw_html=False)
        assert "&lt;div" in html
        assert '<div class="alert">hi</div>' not in html

    def test_raw_html_enabled_outputs_raw(self, env):
        doc = RenderDocument(
            title="X",
            sections=[RawHtmlSection(html="<strong>bold</strong>")],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = (
            "{% for s in doc.sections %}"
            "{% if allow_raw_html %}{{ s.html | safe }}{% else %}"
            "<pre>{{ s.html }}</pre>"
            "{% endif %}"
            "{% endfor %}"
        )
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc, allow_raw_html=True)
        assert "<strong>bold</strong>" in html

    def test_empty_raw_html(self, env):
        doc = RenderDocument(
            title="X",
            sections=[RawHtmlSection(html="")],
            metadata=RenderMetadata(),
        )
        env.loader.mapping["page.html.j2"] = "{% for s in doc.sections %}<pre>{{ s.html }}</pre>{% endfor %}"
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "<pre></pre>" in html


# ── Full page integration ──────────────────────────────────────────────


class TestFullPageIntegration:
    _PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{{ doc.title }}</title></head>
<body>
<h1>{{ doc.title }}</h1>
{% for s in doc.sections %}
  {% if s.type == "markdown" %}<section class="mk"><pre>{{ s.content }}</pre></section>
  {% elif s.type == "metric_grid" %}
  <section class="metrics">{% for m in s.metrics %}
     <div class="metric"><span class="label">{{ m.label }}</span>
     <span class="value">{{ m.value }}{% if m.unit %}<span class="unit">{{ m.unit }}</span>
     {% endif %}</span></div>
  {% endfor %}</section>
  {% elif s.type == "table" %}
  <section class="table">{% if s.title %}<h2>{{ s.title }}</h2>{% endif %}
  <table><thead><tr>{% for c in s.columns %}<th>{{ c }}</th>{% endfor %}</tr></thead>
   <tbody>{% for row in s.rows %}<tr>
   {% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}</tbody></table></section>
  {% elif s.type == "chart" %}
  <section class="chart">{% if s.title %}<h2>{{ s.title }}</h2>{% endif %}
  <div class="chart-type">{{ s.chart_type }}</div>
  <table><thead><tr><th>Label</th>{% for ser in s.data.series %}<th>{{ ser.name }}</th>{% endfor %}</tr></thead>
   <tbody>{% for lbl in s.data.labels %}<tr><td>{{ lbl }}</td>
   {% for ser in s.data.series %}<td>{{ ser.values[loop.index0] }}</td>
   {% endfor %}</tr>{% endfor %}</tbody></table></section>
  {% elif s.type == "raw_html" %}
   <section class="raw-html">{% if allow_raw_html %}{{ s.html | safe }}
   {% else %}<pre>{{ s.html }}</pre>{% endif %}</section>
  {% endif %}
{% endfor %}
<footer>
{% if doc.metadata %}
<pre class="metadata">{{ doc.metadata | dump_json }}</pre>
{% endif %}
</footer>
</body>
</html>"""

    def test_full_page_renders_all_sections(self, env, doc_fixture):
        env.loader.mapping["page.html.j2"] = self._PAGE_TEMPLATE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc_fixture, allow_raw_html=False)
        assert "<h1>Test Report</h1>" in html
        assert "<pre># Heading</pre>" in html
        assert "CPU" in html
        assert "<td>nginx</td>" in html
        assert "<h2>CPU Usage</h2>" in html
        assert "execution_ms" in html

    def test_full_page_metadata_renders(self, env):
        doc = RenderDocument(
            title="Meta Test",
            sections=[],
            metadata=RenderMetadata(
                generated_at="2026-08-03Z",
                playbook="test.yml",
                execution_ms=42,
            ),
        )
        env.loader.mapping["page.html.j2"] = self._PAGE_TEMPLATE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc, allow_raw_html=False)
        assert "test.yml" in html
        assert "42" in html

    def test_document_with_no_sections(self, env):
        doc = RenderDocument(title="Empty", sections=[], metadata=RenderMetadata())
        env.loader.mapping["page.html.j2"] = "<h1>{{ doc.title }}</h1>{% for s in doc.sections %}SECTION{% endfor %}END"
        tpl = env.get_template("page.html.j2")
        html = tpl.render(doc=doc)
        assert "<h1>Empty</h1>" in html
        assert "SECTION" not in html
        assert "END" in html


# ── Error page rendering ───────────────────────────────────────────────


class TestErrorPageRendering:
    _ERROR_TEMPLATE = """{% extends "base.html.j2" %}
{% block title %}Renderer error{% endblock %}
{% block body %}<section class="error">
<h2>{{ title }}</h2>
<p><strong>Renderer:</strong> {{ name }}</p>
{% if detail %}<p><strong>Detail:</strong> {{ detail }}</p>{% endif %}
{% if timeout_s is not none %}<p><strong>Timeout:</strong> {{ timeout_s }}s</p>{% endif %}
{% if stdout %}<details><summary>stdout</summary><pre>{{ stdout }}</pre></details>{% endif %}
{% if stderr %}<details><summary>stderr</summary><pre>{{ stderr }}</pre></details>{% endif %}
</section>{% endblock %}"""

    _BASE_TEMPLATE = """<!DOCTYPE html><html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}{{ title | default("Render") }}{% endblock %}</title></head>
<body><header><nav><a href="/">home</a></nav></header>
<main>{% block body %}{% endblock %}</main>
<footer>Rendered by general_ludd. {% block footer %}{% endblock %}</footer>
</body></html>"""

    def test_error_renders_with_all_fields(self, env):
        env.loader.mapping["base.html.j2"] = self._BASE_TEMPLATE
        env.loader.mapping["error.html.j2"] = self._ERROR_TEMPLATE
        tpl = env.get_template("error.html.j2")
        html = tpl.render(
            title="Renderer failed",
            name="gpu_report",
            detail="playbook exited status='failed' rc=1",
            timeout_s=None,
            stdout="some output",
            stderr="ERROR: oom",
        )
        assert "<h2>Renderer failed</h2>" in html
        assert "Renderer:</strong> gpu_report" in html
        assert "Detail:</strong> playbook exited" in html
        assert "<summary>stdout</summary>" in html
        assert "<summary>stderr</summary>" in html

    def test_error_with_timeout(self, env):
        env.loader.mapping["base.html.j2"] = self._BASE_TEMPLATE
        env.loader.mapping["error.html.j2"] = self._ERROR_TEMPLATE
        tpl = env.get_template("error.html.j2")
        html = tpl.render(
            title="Renderer timeout",
            name="slow_render",
            timeout_s=30.0,
            detail="",
            stdout="",
            stderr="",
        )
        assert "<strong>Timeout:</strong> 30.0s" in html

    def test_error_minimal(self, env):
        env.loader.mapping["base.html.j2"] = self._BASE_TEMPLATE
        env.loader.mapping["error.html.j2"] = self._ERROR_TEMPLATE
        tpl = env.get_template("error.html.j2")
        html = tpl.render(title="Oops", name="bad", detail="", timeout_s=None, stdout="", stderr="")
        assert "Oops" in html
        assert "Renderer:</strong> bad" in html
        assert "Detail:" not in html
        assert "Timeout:" not in html


# ── Schema error page rendering ────────────────────────────────────────


class TestSchemaErrorRendering:
    _SCHEMA_ERROR_TEMPLATE = """{% extends "base.html.j2" %}
{% block title %}Schema validation error{% endblock %}
{% block body %}<section class="error schema-error">
<h2>Schema validation error{% if schema is defined and schema.title is defined %}: {{ schema.title }}{% endif %}</h2>
{% if errors is defined and errors %}
<ul>{% for err in errors %}<li><p><strong>Path:</strong><code>{{ err.path | default("(root)") }}</code></p>
{% if err.message is defined and err.message %}
<p><strong>Message:</strong> {{ err.message }}</p>{% endif %}</li>{% endfor %}</ul>
{% else %}<p>No error details available.</p>{% endif %}
</section>{% endblock %}"""

    _BASE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>{% block title %}{{ title | default("Render") }}{% endblock %}</title></head>
<body>{% block body %}{% endblock %}</body></html>"""

    def test_schema_error_with_multiple_errors(self, env):
        env.loader.mapping["base.html.j2"] = self._BASE
        env.loader.mapping["schema_error.html.j2"] = self._SCHEMA_ERROR_TEMPLATE
        tpl = env.get_template("schema_error.html.j2")
        html = tpl.render(
            name="gpu",
            errors=[
                {"path": "/gpus/0/name", "message": "must be string"},
                {"path": "/gpus/0/memory", "message": "must be integer"},
            ],
            schema={"title": "GPU Report"},
        )
        assert "Schema validation error: GPU Report" in html
        assert "/gpus/0/name" in html
        assert "must be string" in html

    def test_schema_error_with_no_errors(self, env):
        env.loader.mapping["base.html.j2"] = self._BASE
        env.loader.mapping["schema_error.html.j2"] = self._SCHEMA_ERROR_TEMPLATE
        tpl = env.get_template("schema_error.html.j2")
        html = tpl.render(name="test", errors=[], schema={})
        assert "Schema validation error" in html

    def test_schema_error_no_title(self, env):
        env.loader.mapping["base.html.j2"] = self._BASE
        env.loader.mapping["schema_error.html.j2"] = self._SCHEMA_ERROR_TEMPLATE
        tpl = env.get_template("schema_error.html.j2")
        html = tpl.render(name="anon", errors=[{"message": "bad"}], schema={"type": "object"})
        assert "Schema validation error" in html
        assert "bad" in html


# ── RenderDocument validation (Pydantic schema edge cases) ─────────────


class TestRenderDocumentValidation:
    def test_minimal_document(self):
        doc = RenderDocument(title="Min")
        assert doc.title == "Min"
        assert doc.sections == []
        assert isinstance(doc.metadata, RenderMetadata)

    def test_title_must_be_non_empty(self):
        with pytest.raises(ValidationError):
            RenderDocument(title="")

    def test_mixed_sections_discriminated(self):
        raw = {
            "title": "T",
            "sections": [
                {"type": "markdown", "content": "hi"},
                {"type": "table", "columns": ["A"], "rows": [[1]]},
                {"type": "metric_grid", "metrics": [{"label": "X", "value": 1}]},
                {"type": "chart", "chart_type": "line", "data": {"labels": [], "series": []}},
                {"type": "raw_html", "html": "<p>ok</p>"},
            ],
        }
        doc = RenderDocument.model_validate(raw)
        assert len(doc.sections) == 5
        assert isinstance(doc.sections[0], MarkdownSection)
        assert isinstance(doc.sections[1], TableSection)
        assert isinstance(doc.sections[2], MetricGridSection)
        assert isinstance(doc.sections[3], ChartSection)
        assert isinstance(doc.sections[4], RawHtmlSection)

    def test_unknown_section_type_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RenderDocument.model_validate(
                {
                    "title": "T",
                    "sections": [{"type": "unknown_type"}],
                }
            )

    def test_chart_data_series_default(self):
        cd = ChartData(labels=[], series=[])
        assert cd.series == []

    def test_metric_value_can_be_any(self):
        m = Metric(label="L", value={"nested": True})
        assert m.value == {"nested": True}

    def test_table_rows_mixed_types(self):
        section = TableSection(
            columns=["A", "B"],
            rows=[[1, "str"], [True, None], [3.14, [1, 2]]],
        )
        assert len(section.rows) == 3
        assert section.rows[2][1] == [1, 2]


# ── Schema form field rendering (_schema_field.html.j2 macro) ──────────


class TestSchemaFieldRendering:
    _FIELD_MACRO = """{% macro render_field(name, prop_schema, value, required=false) %}
{% set field_type = prop_schema.type | default("null") %}
{% set label = prop_schema.title | default(name) %}
<dt class="label{% if required %} required{% endif %}"><label>{{ label }}{% if required %} *{% endif %}</label></dt>
<dd class="field{% if required %} required{% endif %}">
{% if field_type == "string" %}
  {% if prop_schema.enum is defined %}
     <select name="{{ name }}" disabled>
     {% for option in prop_schema.enum %}<option value="{{ option }}"
     {{ ' selected' if option == value else '' }}>{{ option }}</option>
     {% endfor %}</select>
  {% elif prop_schema.format is defined and prop_schema.format == "uri" %}
    <a href="{{ value }}">{{ value }}</a>
  {% else %}
    <span class="value">{{ value }}</span>
  {% endif %}
{% elif field_type == "number" or field_type == "integer" %}
  <span class="number">{{ value }}{% if prop_schema.unit is defined %} {{ prop_schema.unit }}{% endif %}</span>
{% elif field_type == "boolean" %}
  <input type="checkbox" disabled{{ ' checked' if value else '' }}>
{% elif field_type == "array" %}
   <ul>{% if value is iterable and value is not string %}
   {% for item in value %}<li>{{ item }}</li>{% endfor %}{% endif %}</ul>
{% elif field_type == "null" and value is none %}
  <em>null</em>
{% else %}
  <span class="value">{{ value }}</span>
{% endif %}
</dd>{% endmacro %}"""

    _PAGE = (
        "{% from 'field.html.j2' import render_field %}"
        + "<dl>{% for name, ps in schema.properties.items() %}"
        + "{% set v = data.get(name) %}"
        + "{{ render_field(name, ps, v, required=(name in (schema.required or []))) }}"
        + "{% endfor %}</dl>"
    )

    def test_string_field(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"name": {"type": "string", "title": "Name"}},
            },
            data={"name": "Alice"},
        )
        assert "<label>Name</label>" in html
        assert "Alice" in html

    def test_enum_field_selected(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["a", "b", "c"]}},
            },
            data={"status": "b"},
        )
        assert re.search(r'<option value="b"\s+selected', html)

    def test_integer_field(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"count": {"type": "integer", "title": "Count"}},
            },
            data={"count": 42},
        )
        assert "42" in html

    def test_boolean_field_checked(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
            },
            data={"enabled": True},
        )
        assert "checked" in html

    def test_boolean_field_unchecked(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
            },
            data={"enabled": False},
        )
        assert "checked" not in html

    def test_uri_field_renders_link(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"homepage": {"type": "string", "format": "uri"}},
            },
            data={"homepage": "https://example.com"},
        )
        assert '<a href="https://example.com">' in html

    def test_required_field_marker(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
            },
            data={"email": "x@y.com"},
        )
        assert "required" in html
        assert " *</label>" in html

    def test_number_with_unit(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"memory": {"type": "number", "unit": "GB"}},
            },
            data={"memory": 32},
        )
        assert "32 GB" in html

    def test_array_scalar_items(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            },
            data={"tags": ["fast", "reliable"]},
        )
        assert "<li>fast</li>" in html
        assert "<li>reliable</li>" in html

    def test_null_type(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"empty": {"type": "null"}},
            },
            data={"empty": None},
        )
        assert "<em>null</em>" in html

    def test_missing_type_defaults_to_value(self, env):
        env.loader.mapping["field.html.j2"] = self._FIELD_MACRO
        env.loader.mapping["page.html.j2"] = self._PAGE
        tpl = env.get_template("page.html.j2")
        html = tpl.render(
            schema={
                "type": "object",
                "properties": {"custom": {"title": "Custom"}},
            },
            data={"custom": "anything"},
        )
        assert "anything" in html


# ── Router render functions (unit-level via render.py exports) ─────────


class TestRouterRenderFunctions:
    """Test the public render functions exported by routers/render.py."""

    def test_render_document_basic(self):
        from general_ludd.routers.render import render_document

        doc = RenderDocument(
            title="Hello",
            sections=[MarkdownSection(content="world")],
            metadata=RenderMetadata(
                generated_at="2026-08-03T00:00:00Z",
                playbook="test.yml",
                execution_ms=10,
            ),
        )
        html = render_document(doc, allow_raw_html=False)
        assert "<h1>Hello</h1>" in html
        assert "<pre>world</pre>" in html

    def test_render_document_with_raw_html_allowed(self):
        from general_ludd.routers.render import render_document

        doc = RenderDocument(
            title="Raw",
            sections=[RawHtmlSection(html="<em>italic</em>")],
            metadata=RenderMetadata(),
        )
        html_allowed = render_document(doc, allow_raw_html=True)
        assert "<em>italic</em>" in html_allowed

    def test_render_document_with_raw_html_disallowed(self):
        from general_ludd.routers.render import render_document

        doc = RenderDocument(
            title="Raw",
            sections=[RawHtmlSection(html="<em>italic</em>")],
            metadata=RenderMetadata(),
        )
        html_blocked = render_document(doc, allow_raw_html=False)
        assert "<em>italic</em>" not in html_blocked
        assert "&lt;em&gt;" in html_blocked

    def test_render_error(self):
        from general_ludd.routers.render import render_error

        html = render_error(
            title="Renderer failed",
            name="bad_render",
            detail="out of memory",
            timeout_s=30.0,
            stdout="line1\nline2",
            stderr="ERROR",
        )
        assert "Renderer failed" in html
        assert "bad_render" in html
        assert "out of memory" in html

    def test_render_schema_error(self):
        from general_ludd.routers.render import render_schema_error

        html = render_schema_error(
            name="gpu",
            errors=[
                {"path": "/x", "message": "bad type", "schema_snippet": '{"type":"string"}'},
                {"path": "/y", "message": "required"},
            ],
            schema={"title": "GPU", "type": "object"},
        )
        assert "Schema validation error: GPU" in html
        assert "/x" in html
        assert "bad type" in html

    def test_render_schema_page_basic(self):
        from general_ludd.routers.render import render_schema_page

        schema = {
            "title": "System Info",
            "type": "object",
            "properties": {
                "hostname": {"type": "string", "title": "Hostname"},
                "cpu_count": {"type": "integer", "title": "CPU Cores"},
            },
        }
        data = {"hostname": "server01", "cpu_count": 16}
        from general_ludd.renderers.schema_loader import extract_field_metadata

        fm = extract_field_metadata(schema)
        html = render_schema_page(
            schema=schema,
            data=data,
            field_metadata=fm,
        )
        assert "<h1>System Info</h1>" in html
        assert "Hostname" in html
        assert "server01" in html
        assert "16" in html

    def test_render_schema_page_no_data(self):
        from general_ludd.routers.render import render_schema_page

        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        data: dict[str, object] = {}
        html = render_schema_page(schema=schema, data=data, field_metadata=None)
        assert "<h1>Rendered Page</h1>" in html

    def test_dump_json_filter(self):
        from general_ludd.routers.render import render_document

        doc = RenderDocument(
            title="Filter Test",
            sections=[MarkdownSection(content="body")],
            metadata=RenderMetadata(
                generated_at="2026-08-03T00:00:00Z",
                playbook="test.yml",
                execution_ms=42,
            ),
        )
        html = render_document(doc)
        assert "execution_ms" in html
        assert "42" in html


# ── RendererCache edge cases ───────────────────────────────────────────


class TestRendererCacheEdgeCases:
    def test_cache_get_missing_key_returns_none(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        assert cache.get("nonexistent") is None

    def test_cache_contains_checks(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        cache.set("a", 1)
        assert "a" in cache
        assert "b" not in cache

    def test_cache_len(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        assert len(cache) == 0
        cache.set("a", 1)
        cache.set("b", 2)
        assert len(cache) == 2

    def test_cache_clear_returns_count(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cleared = cache.clear_all()
        assert cleared == 2
        assert len(cache) == 0

    def test_cache_clear_key(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        cache.set("a", 1)
        assert cache.clear("a") is True
        assert cache.clear("a") is False

    def test_cache_ttl_zero_skips(self):
        from general_ludd.renderers.cache import RendererCache

        cache = RendererCache()
        cache.set("a", "val", ttl=0)
        assert cache.get("a") is None
        assert "a" not in cache
