"""PassRegistry — ordered collection of named pipeline passes with execution.

Each pass has a name, phase, priority, and required flag. The registry
enforces priority-based ordering, fail-fast on required passes, and tracks
per-pass result (status, duration, error).
"""

from __future__ import annotations

import copy
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum


class PassStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class NamedPass:
    """A single pipeline pass definition (immutable value object)."""

    name: str
    phase: str
    priority: int
    required: bool


@dataclass
class PassResult:
    """Mutable result container updated during execution."""

    name: str
    status: PassStatus = PassStatus.PENDING
    duration_s: float = 0.0
    error: str = ""


PassFn = Callable[[], Awaitable[bool]]

BUILTIN_PASSES: tuple[NamedPass, ...] = (
    NamedPass(name="lint", phase="validation", priority=10, required=True),
    NamedPass(name="typecheck", phase="validation", priority=20, required=True),
    NamedPass(name="collect", phase="validation", priority=30, required=True),
    NamedPass(name="test-unit", phase="test", priority=40, required=True),
    NamedPass(name="test-integration", phase="test", priority=50, required=False),
    NamedPass(name="bundle-binaries", phase="build", priority=60, required=False),
    NamedPass(name="verify-release-artifact", phase="build", priority=70, required=False),
)


class PassRegistry:
    """Ordered registry of named pipeline passes with execution engine."""

    def __init__(self) -> None:
        self._passes: dict[str, NamedPass] = {}
        self._fns: dict[str, PassFn] = {}
        self._results: dict[str, PassResult] = {}

    @property
    def passes(self) -> tuple[NamedPass, ...]:
        return tuple(self._passes.values())

    @property
    def results(self) -> dict[str, PassResult]:
        return copy.deepcopy(self._results)

    def register(self, pass_def: NamedPass) -> None:
        existing = self._passes.get(pass_def.name)
        if existing is not None:
            if existing == pass_def:
                return
            raise ValueError(
                f"Conflicting pass definition for '{pass_def.name}': "
                f"existing={existing}, new={pass_def}"
            )
        self._passes[pass_def.name] = pass_def
        self._results[pass_def.name] = PassResult(name=pass_def.name)

    def register_many(self, pass_defs: tuple[NamedPass, ...]) -> None:
        for p in pass_defs:
            self.register(p)

    def bind(self, name: str, fn: PassFn) -> None:
        if name not in self._passes:
            raise KeyError(f"Pass '{name}' is not registered")
        self._fns[name] = fn

    def _ordered_passes(self) -> list[NamedPass]:
        return sorted(self._passes.values(), key=lambda p: (p.priority, p.name))

    async def execute_all(self) -> dict[str, PassResult]:
        failed_phase: str | None = None

        for p in self._ordered_passes():
            if failed_phase is not None and p.phase != failed_phase:
                failed_phase = None

            if failed_phase is not None:
                self._results[p.name].status = PassStatus.SKIPPED
                continue

            fn = self._fns.get(p.name)
            if fn is None:
                self._results[p.name].status = PassStatus.SKIPPED
                self._results[p.name].error = "No function bound"
                continue

            self._results[p.name].status = PassStatus.RUNNING
            start = time.monotonic()
            try:
                success = await fn()
                self._results[p.name].duration_s = time.monotonic() - start
                if success:
                    self._results[p.name].status = PassStatus.PASSED
                else:
                    self._results[p.name].status = PassStatus.FAILED
                    if p.required:
                        failed_phase = p.phase
            except Exception as exc:
                self._results[p.name].duration_s = time.monotonic() - start
                self._results[p.name].status = PassStatus.FAILED
                self._results[p.name].error = str(exc)
                if p.required:
                    failed_phase = p.phase

        return dict(self._results)
