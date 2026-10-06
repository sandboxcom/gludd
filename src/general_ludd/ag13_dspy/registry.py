"""Thread-safe prompt registry and typed prompt specification."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptSpec:
    """Typed input/output specification for a prompt (analogous to ``dspy.Signature``)."""

    name: str
    inputs: dict[str, type] = field(default_factory=dict)
    output: type = str
    description: str = ""


@dataclass
class PromptTemplate:
    """Parametrized jinja2-compatible template wrapping a :class:`PromptSpec`."""

    spec: PromptSpec
    template: str
    version: int = 1
    score: float | None = None

    def call(self, **kwargs: Any) -> str:
        """Render the template with the supplied keyword arguments."""
        from jinja2 import Template

        rendered = Template(self.template).render(**kwargs)
        if not isinstance(rendered, str):
            raise TypeError("prompt renderer must return a string")
        return rendered


class PromptRegistry:
    """Thread-safe store mapping ``(name, version)`` → :class:`PromptTemplate`.

    Supports ``put``, ``get``, ``list_versions``, ``latest``, and ``get_best``
    (highest-scoring version for a name).
    """

    def __init__(self) -> None:
        """Initialize an empty prompt registry."""
        self._store: dict[tuple[str, int], PromptTemplate] = {}
        self._lock = threading.Lock()

    def put(self, name: str, version: int, template: PromptTemplate, score: float | None = None) -> None:
        """Store a prompt template at the named version."""
        with self._lock:
            template.version = version
            if score is not None:
                template.score = score
            self._store[(name, version)] = template

    def get(self, name: str, version: int) -> PromptTemplate | None:
        """Return a prompt template by name and version, if present."""
        with self._lock:
            return self._store.get((name, version))

    def latest(self, name: str) -> PromptTemplate | None:
        """Return the highest-versioned template for a name, if present."""
        with self._lock:
            versions = [v for (n, v) in self._store if n == name]
            if not versions:
                return None
            return self._store[(name, max(versions))]

    def get_best(self, name: str) -> PromptTemplate | None:
        """Return the highest-scoring template for a name, if present."""
        with self._lock:
            entries = [
                (t.score if t.score is not None else -1.0, t)
                for (n, _), t in self._store.items()
                if n == name
            ]
            if not entries:
                return None
            return max(entries, key=lambda e: e[0])[1]

    def list_versions(self, name: str) -> list[int]:
        """Return the stored versions for a name in ascending order."""
        with self._lock:
            return sorted(
                [v for (n, v) in self._store if n == name],
            )

    def list_names(self) -> list[str]:
        """Return all stored prompt names in ascending order."""
        with self._lock:
            return sorted({n for (n, _) in self._store})

    def remove(self, name: str, version: int) -> None:
        """Remove a prompt version when it exists."""
        with self._lock:
            self._store.pop((name, version), None)

    def __len__(self) -> int:
        """Return the number of stored prompt versions."""
        with self._lock:
            return len(self._store)
