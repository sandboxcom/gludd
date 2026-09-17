"""Killable process boundary for approval-bound managed self-improvement."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import math
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan
from general_ludd.util.owned_process import (
    OwnedProcessCancelled,
    OwnedProcessPolicy,
    run_owned_process,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 1800.0
_MAX_TIMEOUT_SECONDS = 7200.0


def managed_execution_timeout_seconds(config: Mapping[str, object] | None) -> float:
    """Return one finite configured deadline for a complete managed run."""
    raw: object = (
        _DEFAULT_TIMEOUT_SECONDS
        if config is None
        else config.get(
            "managed_execution_timeout_seconds",
            _DEFAULT_TIMEOUT_SECONDS,
        )
    )
    if isinstance(raw, bool):
        raise ValueError("managed_execution_timeout_seconds must be a finite number")
    if not isinstance(raw, (int, float, str)):
        raise ValueError("managed_execution_timeout_seconds must be a finite number")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            "managed_execution_timeout_seconds must be a finite number"
        ) from None
    if not math.isfinite(value) or not 0 < value <= _MAX_TIMEOUT_SECONDS:
        raise ValueError(
            "managed_execution_timeout_seconds must be greater than 0 and no more than 7200"
        )
    return value


@dataclass(frozen=True, slots=True)
class ConfiguredManagedRunnerFactory:
    """Picklable repository-runner factory with an immutable config snapshot."""

    config: dict[str, Any]

    def __call__(self, repo_root: Path) -> Any:
        """Build one repository runner from an isolated copy of the snapshot."""
        from general_ludd.self_improve.runtime import (
            build_managed_self_improve_runner,
        )

        return build_managed_self_improve_runner(
            repo_root,
            self_improve_config=copy.deepcopy(self.config),
        )


def _run_managed_runner(
    runner_factory: object,
    repo_root: str,
    plan_json: str,
) -> object:
    """Rebuild and execute the approved runner inside the owned child."""
    if not callable(runner_factory):
        raise TypeError("managed runner factory must be callable")
    canonical_root = Path(repo_root).resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("managed execution repository must be a directory")
    plan = ApprovedSelfImprovePlan.from_json(plan_json)
    if plan.repo_root != canonical_root:
        raise ValueError("approved plan belongs to a different repository")
    runner = runner_factory(canonical_root)
    run = getattr(runner, "run", None)
    if not callable(run):
        raise TypeError("managed runner must expose run(plan)")
    return run(plan)


class ManagedSelfImproveProcessExecutor:
    """Supervise one full managed run in an exactly owned process group."""

    def __init__(
        self,
        *,
        runner_factory: object,
        timeout_seconds: float,
        event_sink: Callable[[str], None] | None = None,
        operation: Callable[[object, str, str], object] | None = None,
    ) -> None:
        """Bind the immutable factory, finite policy, and observable sink."""
        self._runner_factory = runner_factory
        self._policy = OwnedProcessPolicy(timeout_seconds=timeout_seconds)
        self._event_sink = event_sink
        self._operation: Callable[[object, str, str], object] = (
            operation if operation is not None else _run_managed_runner
        )

    @staticmethod
    def _process_name(plan: ApprovedSelfImprovePlan) -> str:
        identity = hashlib.sha256(
            plan.attempt_identity_digest.encode("ascii")
        ).hexdigest()[:16]
        return f"gludd-self-improve-{identity}"

    def run(
        self,
        repo_root: Path,
        plan: ApprovedSelfImprovePlan,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> object:
        """Execute once and return only after the owned child is reaped."""
        if not isinstance(plan, ApprovedSelfImprovePlan):
            raise TypeError("plan must be an ApprovedSelfImprovePlan")
        canonical_root = repo_root.resolve(strict=True)
        if not canonical_root.is_dir() or plan.repo_root != canonical_root:
            raise ValueError("approved plan repository does not match execution repository")
        return run_owned_process(
            self._operation,
            (self._runner_factory, str(canonical_root), plan.to_json()),
            policy=self._policy,
            process_name=self._process_name(plan),
            cancel_requested=cancel_requested,
            event_sink=self._event_sink,
        )

    async def run_async(
        self,
        repo_root: Path,
        plan: ApprovedSelfImprovePlan,
    ) -> object:
        """Propagate coroutine cancellation only after child termination."""
        cancel = threading.Event()
        thread_task: asyncio.Task[object] = asyncio.create_task(
            asyncio.to_thread(
                self.run,
                repo_root,
                plan,
                cancel_requested=cancel.is_set,
            )
        )
        try:
            return await asyncio.shield(thread_task)
        except asyncio.CancelledError:
            cancel.set()
            try:
                await asyncio.shield(thread_task)
            except OwnedProcessCancelled:
                pass
            except Exception:
                logger.warning(
                    "Managed child failed while cancellation was being drained",
                    exc_info=True,
                )
            raise


__all__ = (
    "ConfiguredManagedRunnerFactory",
    "ManagedSelfImproveProcessExecutor",
    "managed_execution_timeout_seconds",
)
