"""Canonical JSON shape emitted by renderer playbooks (Phase 1).

Spec: docs/design/PLAYBOOK_WEB_RENDERER.md §4. A renderer playbook writes
``{{ artifact_dir }}/render.json`` matching :class:`RenderDocument`; the
runner validates it at execution time and overwrites the ``metadata``
block so the playbook cannot lie about timing.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarkdownSection(_Strict):
    type: Literal["markdown"] = "markdown"
    content: str = Field(min_length=0)


class Metric(_Strict):
    label: str
    value: Any
    unit: str | None = None


class MetricGridSection(_Strict):
    type: Literal["metric_grid"] = "metric_grid"
    metrics: list[Metric]


class TableSection(_Strict):
    type: Literal["table"] = "table"
    title: str | None = None
    columns: list[str]
    rows: list[list[Any]]


class ChartSeries(_Strict):
    name: str
    values: list[Any] = Field(default_factory=list)


class ChartData(_Strict):
    labels: list[Any] = Field(default_factory=list)
    series: list[ChartSeries]


class ChartSection(_Strict):
    type: Literal["chart"] = "chart"
    title: str | None = None
    chart_type: Literal["line", "bar", "pie"]
    data: ChartData


class RawHtmlSection(_Strict):
    type: Literal["raw_html"] = "raw_html"
    html: str = Field(min_length=0)


# Discriminated union keyed on `type` — the closed set of §4.
Section = Annotated[
    MarkdownSection
    | MetricGridSection
    | TableSection
    | ChartSection
    | RawHtmlSection,
    Field(discriminator="type"),
]


class RenderMetadata(_Strict):
    generated_at: str | None = None
    playbook: str | None = None
    execution_ms: int | None = None
    renderer_version: int | None = None


class RenderDocument(_Strict):
    title: str = Field(min_length=1)
    sections: list[Section] = Field(default_factory=list)
    metadata: RenderMetadata = Field(default_factory=RenderMetadata)


# Backward-compat aliases for prior partial work that referenced the older
# `RendererOutput` name. The canonical name per the design doc is
# `RenderDocument`; new code should use that.
RendererOutput = RenderDocument
