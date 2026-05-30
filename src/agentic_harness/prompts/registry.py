"""Prompt management module."""

from __future__ import annotations

from jinja2 import BaseLoader, Environment, FileSystemLoader


class PromptRegistry:
    def __init__(self, template_dir: str | None = None) -> None:
        self._templates: dict[str, str] = {}
        self._loader: BaseLoader = (
            FileSystemLoader(template_dir) if template_dir else BaseLoader()
        )
        self._env = Environment(loader=self._loader)

    def register(self, name: str, template_text: str) -> None:
        self._templates[name] = template_text

    def render(self, template_name: str, **kwargs: object) -> str:
        if template_name in self._templates:
            template = self._env.from_string(self._templates[template_name])
            return template.render(**kwargs)
        template = self._env.get_template(template_name)
        return template.render(**kwargs)

    def list_templates(self) -> list[str]:
        return list(self._templates.keys())
