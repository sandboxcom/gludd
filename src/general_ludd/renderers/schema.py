"""Canonical JSON shape emitted by renderer playbooks.

A renderer playbook writes ``render.json`` to its ``artifact_dir`` with the
shape::

    {
      "title": "Human-readable page title",
      "sections": [
        {"type": "markdown",    "body": "## ..."},
        {"type": "metric_grid", "metrics": [{"label": "...", "value": "..."}]},
        {"type": "raw_html",    "html": "<iframe .../>"}
      ],
      "metadata": {"renderer": "system_facts", ...}
    }

Phase 1 supports ``markdown`` + ``metric_grid`` + ``raw_html``. ``chart`` and
``table`` are reserved for Phase 2 (declared in the union so a future playbook
that emits them fails validation with a clear error rather than silently
passing through).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class MarkdownSection(BaseModel):
    """Free-form markdown body. Autoescaped by the template."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["markdown"] = "markdown"
    body: str = Field(min_length=0)


class MetricGridSection(BaseModel):
    """Grid of label/value metric cards. Autoescaped by the template."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["metric_grid"] = "metric_grid"
    metrics: list[dict[str, str]] = Field(default_factory=list)


class RawHtmlSection(BaseModel):
    """Pre-rendered HTML. The template emits this UNescaped (``| safe``).

    Only this section type bypasses Jinja2 autoescaping — markdown and
    metric_grid values are always escaped. Renderer playbooks are trusted
    (operator-authored, PSK-gated execution path), so raw_html is the
    escape hatch for embedded widgets / iframes.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["raw_html"] = "raw_html"
    html: str = Field(min_length=0)


# Discriminated union keyed on `type`. Add chart/table subtypes in Phase 2.
RendererSection = Annotated[
    MarkdownSection | MetricGridSection | RawHtmlSection,
    Field(discriminator="type"),
]


class RendererOutput(BaseModel):
    """Top-level canonical shape validated against the playbook's render.json."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    sections: list[RendererSection] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class RendererMeta(BaseModel):
    """Catalog entry for a discovered renderer playbook."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    playbook_path: str
    timeout_s: float = 30.0
