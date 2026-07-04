"""Prompt registry for loading and rendering prompt templates.

KEEP LIST (V3.8): Thin, correct use of jinja2 for template rendering.
Not replaceable — the registry wraps jinja2 with project-specific template
discovery and profile-to-template mapping logic (15 LOC of domain code).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, FileSystemLoader, select_autoescape

from general_ludd.events.types import TemplateUpdatedEvent

logger = logging.getLogger(__name__)


class PromptRegistry:
    def __init__(
        self,
        template_dir: str | None = None,
        event_bus: Any | None = None,
        extra_template_dirs: list[str] | None = None,
        version: str = "0.1.0",
    ) -> None:
        self._templates: dict[str, str] = {}
        self._in_memory: set[str] = set()
        self._template_dir = template_dir
        self._extra_template_dirs: list[str] = list(extra_template_dirs or [])
        self._loader: BaseLoader = self._make_loader()
        self._env = Environment(loader=self._loader, autoescape=select_autoescape())
        self._event_bus = event_bus
        self.version = version

    def _make_loader(self) -> BaseLoader:
        """Build a Jinja2 FileSystemLoader with extras-first then default ordering.

        Extra template dirs shadow the default dir — a template name present in
        an extra dir wins over the same name in ``_template_dir``.  Falls back to
        a no-op :class:`BaseLoader` when no dirs are configured at all.
        """
        dirs: list[str] = list(self._extra_template_dirs)
        if self._template_dir:
            dirs.append(self._template_dir)
        return FileSystemLoader(dirs) if dirs else BaseLoader()

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
        # Build the ordered search list: extra dirs first (they shadow the default),
        # then the default template dir.  First-name-wins across dirs.
        all_dirs: list[Path] = [Path(d) for d in self._extra_template_dirs]
        if self._template_dir:
            all_dirs.append(Path(self._template_dir))
        if not all_dirs:
            return {"templates": list(self._templates.keys()), "refreshed": False}

        discovered: list[str] = []
        seen_names: set[str] = set()
        for tdir in all_dirs:
            if tdir.is_dir():
                for f in sorted(tdir.glob("*.j2")):
                    name = f.name
                    if name not in seen_names:
                        seen_names.add(name)
                        self._templates[name] = f.read_text()
                        discovered.append(name)
        disk_names = set(discovered)
        to_remove = [
            n for n in list(self._templates.keys())
            if n not in disk_names and n not in self._in_memory
        ]
        for name in to_remove:
            del self._templates[name]
        self._loader = self._make_loader()
        self._env = Environment(loader=self._loader, autoescape=select_autoescape())
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


def render_message_queue_section(
    role: str,
    unread_count: int = 0,
    senders: list[str] | None = None,
    enabled: bool = False,
) -> str:
    """Render the message-queue + facts availability blurb for a dispatched agent.

    Part 4 of the facts/MQ backbone: when an agent/model is dispatched, its
    prompt is told the message queue and live facts are available, and how many
    messages are waiting. Gated behind ``enabled`` — when False, returns the
    empty string so prompts without MQ context are byte-for-byte unchanged.

    Parameters
    ----------
    role:
        The agent/role name the prompt is addressed to (e.g. ``coder``).
    unread_count:
        Number of unread messages waiting for this role.
    senders:
        Optional distinct sender names contributing to those unread messages.
    enabled:
        Feature flag. When False (default) this returns ``""``.
    """
    if not enabled:
        return ""
    mail = (
        "You have 1 unread message"
        if unread_count == 1
        else f"You have {unread_count} unread message(s)"
    )
    from_clause = ""
    if senders:
        uniq = sorted({s for s in senders if s})
        if uniq:
            from_clause = " from " + ", ".join(uniq)
    return (
        f"Message queue: you are agent '{role}'. {mail}{from_clause}. "
        "To read them, the playbook runs gludd_message(receive). To coordinate, "
        "you may send messages to other agents/roles via the message queue. "
        "Live facts (work/todo/model/history stats) are available via gludd_facts."
    )
