"""Compiled output templates for logs and reports.

The templates are Jinja2 files compatible with Ansible-style syntax, but this
module renders them in a restricted sandbox. They format already-built report
objects only; template rendering is not a hook for running Python or Ansible.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from jinja2 import FileSystemLoader, StrictUndefined, Template
from jinja2.sandbox import SandboxedEnvironment

DEFAULT_OUTPUT_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "log_output"
OUTPUT_TEMPLATE_ENV_VAR = "GLUDD_OUTPUT_TEMPLATES_DIR"

_ALLOWED_FILTERS = frozenset(
    {
        "abs",
        "batch",
        "capitalize",
        "center",
        "count",
        "default",
        "dictsort",
        "escape",
        "filesizeformat",
        "first",
        "float",
        "forceescape",
        "format",
        "indent",
        "int",
        "items",
        "join",
        "last",
        "length",
        "list",
        "lower",
        "map",
        "max",
        "min",
        "pprint",
        "reject",
        "rejectattr",
        "replace",
        "reverse",
        "round",
        "safe",
        "select",
        "selectattr",
        "slice",
        "sort",
        "string",
        "striptags",
        "sum",
        "title",
        "trim",
        "truncate",
        "unique",
        "upper",
        "urlencode",
        "wordcount",
        "wordwrap",
    }
)


def _json_filter(value: object, indent: int | None = 2) -> str:
    return json.dumps(value, indent=indent, sort_keys=True, default=str)


class OutputTemplateRegistry:
    """Compile and render report/log output templates."""

    def __init__(self, template_dirs: Iterable[str | Path] | None = None) -> None:
        self._template_dirs = _dedupe_paths(template_dirs or [])
        self._env = self._make_environment()
        self._compiled: dict[str, Template] = {}
        self._summary: dict[str, Any] = {
            "count": 0,
            "templates": [],
            "template_dirs": [str(p) for p in self._template_dirs],
        }

    @classmethod
    def default(
        cls,
        extra_template_dirs: Iterable[str | Path] | None = None,
    ) -> OutputTemplateRegistry:
        dirs: list[str | Path] = []
        env_dirs = os.environ.get(OUTPUT_TEMPLATE_ENV_VAR, "")
        if env_dirs:
            dirs.extend(part for part in env_dirs.split(os.pathsep) if part)
        if extra_template_dirs:
            dirs.extend(extra_template_dirs)
        dirs.append(DEFAULT_OUTPUT_TEMPLATE_DIR)
        return cls(dirs)

    def compile(self) -> dict[str, Any]:
        self._env = self._make_environment()
        self._compiled = {}
        for name in self._discover_template_names():
            self._compiled[name] = self._env.get_template(name)
        self._summary = {
            "count": len(self._compiled),
            "templates": sorted(self._compiled),
            "template_dirs": [str(path) for path in self._template_dirs],
        }
        return dict(self._summary)

    def list_templates(self) -> list[str]:
        return sorted(self._compiled)

    def render(self, template_name: str, **context: object) -> str:
        if not self._compiled:
            self.compile()
        template = self._compiled[template_name]
        return template.render(**context)

    @property
    def summary(self) -> dict[str, Any]:
        return dict(self._summary)

    def _discover_template_names(self) -> list[str]:
        discovered: list[str] = []
        seen: set[str] = set()
        for template_dir in self._template_dirs:
            if not template_dir.is_dir():
                continue
            for path in sorted(template_dir.rglob("*.j2")):
                name = path.relative_to(template_dir).as_posix()
                if name in seen:
                    continue
                seen.add(name)
                discovered.append(name)
        return discovered

    def _make_environment(self) -> SandboxedEnvironment:
        env = SandboxedEnvironment(
            loader=FileSystemLoader([str(path) for path in self._template_dirs]),
            autoescape=False,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        allowed_filters = {name: env.filters[name] for name in _ALLOWED_FILTERS if name in env.filters}
        allowed_filters["tojson"] = _json_filter
        env.filters.clear()
        env.filters.update(allowed_filters)
        env.globals.clear()
        return env


def compile_default_output_templates(
    extra_template_dirs: Iterable[str | Path] | None = None,
) -> OutputTemplateRegistry:
    registry = OutputTemplateRegistry.default(extra_template_dirs=extra_template_dirs)
    registry.compile()
    return registry


def render_smoke_report(
    report: Mapping[str, Any],
    *,
    json_output: bool,
    template_name: str | None = None,
    registry: OutputTemplateRegistry | None = None,
) -> str:
    template = template_name or ("smoke.report.json.j2" if json_output else "smoke.report.text.j2")
    active_registry = registry or compile_default_output_templates()
    return active_registry.render(
        template,
        report=report,
        metrics=report.get("metrics", {}),
        logs=report.get("logs", []),
        events=report.get("events", []),
        trace=report.get("trace", []),
    )


def render_smoke_list(
    smoke_tests: Sequence[Mapping[str, Any]],
    *,
    json_output: bool,
    template_name: str | None = None,
    registry: OutputTemplateRegistry | None = None,
) -> str:
    template = template_name or ("smoke.list.json.j2" if json_output else "smoke.list.text.j2")
    active_registry = registry or compile_default_output_templates()
    return active_registry.render(template, smoke_tests=smoke_tests)


def _dedupe_paths(paths: Iterable[str | Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result
