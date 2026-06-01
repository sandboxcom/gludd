"""Ansible templating exposed for skills and prompts.

Wraps CoreAnsibleRunner to provide Jinja2 rendering with Ansible's
variable resolution and filters.
"""

from __future__ import annotations

from typing import Any

from general_ludd.ansible.core_runner import CoreAnsibleRunner


class AnsibleTemplater:
    def __init__(self, extra_vars: dict[str, Any] | None = None) -> None:
        self._extra_vars = extra_vars or {}
        self._runner = CoreAnsibleRunner()

    def render(self, template: str, **kwargs: Any) -> str:
        merged = dict(self._extra_vars)
        merged.update(kwargs)
        return self._runner.render_template(template, variables=merged)

    def resolve_fact(self, fact_name: str, host: str = "localhost") -> Any:
        return self._runner.resolve_variable(fact_name, host=host)
