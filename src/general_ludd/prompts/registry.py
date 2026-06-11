"""Prompt registry for loading and rendering prompt templates.

KEEP LIST (V3.8): Thin, correct use of jinja2 for template rendering.
Not replaceable — the registry wraps jinja2 with project-specific template
discovery and profile-to-template mapping logic (15 LOC of domain code).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, FileSystemLoader

from general_ludd.events.types import TemplateUpdatedEvent

logger = logging.getLogger(__name__)


class PromptRegistry:
    def __init__(
        self,
        template_dir: str | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._templates: dict[str, str] = {}
        self._in_memory: set[str] = set()
        self._template_dir = template_dir
        self._loader: BaseLoader = (
            FileSystemLoader(template_dir) if template_dir else BaseLoader()
        )
        self._env = Environment(loader=self._loader, autoescape=False)
        self._event_bus = event_bus

    def register(self, name: str, template_text: str) -> None:
        self._templates[name] = template_text
        self._in_memory.add(name)

    def render(self, template_name: str, **kwargs: object) -> str:
        if template_name in self._templates:
            template = self._env.from_string(self._templates[template_name])
            return template.render(**kwargs)
        template = self._env.get_template(template_name)
        return template.render(**kwargs)

    def list_templates(self) -> list[str]:
        return list(self._templates.keys())

    def refresh(self) -> dict[str, Any]:
        if not self._template_dir:
            return {"templates": list(self._templates.keys()), "refreshed": False}
        templates_path = Path(self._template_dir)
        discovered: list[str] = []
        if templates_path.is_dir():
            for f in sorted(templates_path.glob("*.j2")):
                name = f.name
                self._templates[name] = f.read_text()
                discovered.append(name)
        disk_names = set(discovered)
        to_remove = [
            n for n in list(self._templates.keys())
            if n not in disk_names and n not in self._in_memory
        ]
        for name in to_remove:
            del self._templates[name]
        self._loader = FileSystemLoader(self._template_dir) if self._template_dir else BaseLoader()
        self._env = Environment(loader=self._loader, autoescape=False)
        if self._event_bus:
            self._event_bus.publish(TemplateUpdatedEvent(templates=discovered))
        return {"templates": discovered, "refreshed": True}


_WORK_TYPE_TEMPLATE_MAP: dict[str, str] = {
    "code": "implementation.md.j2",
    "test": "test.md.j2",
    "analysis": "gap_analysis.md.j2",
    "audit": "audit.md.j2",
    "prompt": "prompt_eval.md.j2",
    "review": "code_review.md.j2",
    "dependency": "dependency.md.j2",
    "bug_fix": "implementation.md.j2",
    "refactor": "implementation.md.j2",
    "feature": "implementation.md.j2",
    "docs": "documentation.md.j2",
    "security": "security.md.j2",
    "self_improvement": "self_improvement.md.j2",
}


def get_template_name_for_work_type(work_type: str) -> str:
    if work_type in _WORK_TYPE_TEMPLATE_MAP:
        return _WORK_TYPE_TEMPLATE_MAP[work_type]
    return _WORK_TYPE_TEMPLATE_MAP.get(work_type, "implementation.md.j2")
