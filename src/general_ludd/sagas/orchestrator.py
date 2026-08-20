"""Saga orchestrator — steps, compensation, persistence, timeout.

Implements the Saga pattern: a sequence of steps each with a forward action
and a compensating action.  On failure the orchestrator runs compensations
for all previously-successful steps in LIFO order.  Supports timeout per step,
retry with backoff, and pluggable persistence.
"""

from __future__ import annotations

import dataclasses
import enum
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Protocols / type aliases
# ---------------------------------------------------------------------------

StepAction = Callable[[], None]
StepCompensation = Callable[[], None]


class SagaStore(Protocol):
    """Pluggable persistence for saga execution state."""

    def save(self, saga_id: str, state: dict[str, Any]) -> None:
        """Persist the latest state for a saga identifier."""
        ...

    def load(self, saga_id: str) -> dict[str, Any] | None:
        """Load persisted state for a saga identifier, if present."""
        ...

    def delete(self, saga_id: str) -> None:
        """Delete persisted state for a saga identifier."""
        ...


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class StepResult:
    """Record the observable outcome of one saga step."""

    step_index: int
    step_name: str
    success: bool
    error: str | None = None
    duration_ms: float = 0.0


class SagaState(enum.Enum):
    """Enumerate durable saga lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPENSATING = "compensating"
    COMPLETED = "completed"
    COMPENSATED = "compensated"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclasses.dataclass
class SagaStep:
    """Describe a forward saga action and its optional compensation."""

    name: str
    action: StepAction
    compensation: StepCompensation | None = None
    timeout_seconds: float = 30.0
    retry_count: int = 0
    retry_delay_seconds: float = 0.1


@dataclasses.dataclass
class SagaConfig:
    """Configure retry, persistence, and compensation behavior."""

    max_retries: int = 3
    retry_backoff_multiplier: float = 2.0
    persist_on_each_step: bool = True
    compensation_timeout_seconds: float = 10.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SagaError(Exception):
    """Base saga error."""


class SagaStepFailure(SagaError):
    """Report a forward step that exhausted its retries."""

    def __init__(self, step_index: int, step_name: str, original: Exception) -> None:
        """Initialize failure context for a forward step."""
        self.step_index = step_index
        self.step_name = step_name
        self.original = original
        super().__init__(f"Step {step_index} ({step_name!r}) failed: {original}")


class SagaCompensationFailure(SagaError):
    """Report a compensation that could not complete."""

    def __init__(self, step_index: int, step_name: str, original: Exception) -> None:
        """Initialize failure context for a compensating step."""
        self.step_index = step_index
        self.step_name = step_name
        self.original = original
        super().__init__(f"Compensation for step {step_index} ({step_name!r}) failed: {original}")


class SagaTimeout(SagaError):
    """Report a saga step that exceeded its execution budget."""

    def __init__(self, step_index: int, step_name: str, timeout: float) -> None:
        """Initialize timeout context for a saga step."""
        self.step_index = step_index
        self.step_name = step_name
        self.timeout = timeout
        super().__init__(f"Step {step_index} ({step_name!r}) timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class SagaOrchestrator:
    """Execute saga steps with retries, compensation, and durable state."""

    def __init__(
        self,
        saga_id: str,
        steps: Sequence[SagaStep],
        *,
        config: SagaConfig | None = None,
        store: SagaStore | None = None,
    ) -> None:
        """Initialize an orchestrator for a uniquely identified saga."""
        self.saga_id = saga_id
        self._steps = list(steps)
        self._config = config or SagaConfig()
        self._store = store

        self._state: SagaState = SagaState.PENDING
        self._results: list[StepResult] = []
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._completed_at: float | None = None

    # ---- properties ---------------------------------------------------------

    @property
    def state(self) -> SagaState:
        """Return the current saga lifecycle state."""
        return self._state

    @property
    def results(self) -> list[StepResult]:
        """Return a defensive copy of recorded step outcomes."""
        return list(self._results)

    @property
    def step_count(self) -> int:
        """Return the number of configured forward steps."""
        return len(self._steps)

    @property
    def elapsed_seconds(self) -> float:
        """Return elapsed execution time using a monotonic clock."""
        if self._started_at is None:
            return 0.0
        end = self._completed_at or time.monotonic()
        return end - self._started_at

    # ---- public API ---------------------------------------------------------

    def run(self) -> list[StepResult]:
        """Run or safely resume the saga and return recorded outcomes."""
        with self._lock:
            if self._state != SagaState.PENDING:
                raise SagaError(f"Saga {self.saga_id!r} already started (state={self._state.value})")
            self._state = SagaState.RUNNING
            self._started_at = time.monotonic()

        self._restore()
        if self._state in (SagaState.COMPLETED, SagaState.COMPENSATED):
            return list(self._results)
        forward_idx = len(self._results)

        try:
            for idx in range(forward_idx, len(self._steps)):
                step = self._steps[idx]
                result = self._execute_step(idx, step)
                self._results.append(result)
                if self._config.persist_on_each_step:
                    self._persist()

                if not result.success:
                    self._compensate(idx)
                    self._completed_at = time.monotonic()
                    self._persist()
                    return list(self._results)

            self._set_state(SagaState.COMPLETED)
            self._completed_at = time.monotonic()
            self._persist()
            return list(self._results)

        except SagaTimeout as exc:
            self._results.append(
                StepResult(
                    step_index=exc.step_index,
                    step_name=exc.step_name,
                    success=False,
                    error=str(exc),
                )
            )
            self._compensate(
                exc.step_index,
                terminal_state=SagaState.TIMED_OUT,
            )
            self._completed_at = time.monotonic()
            self._persist()
            return list(self._results)
        except Exception as exc:
            self._set_state(SagaState.FAILED)
            self._completed_at = time.monotonic()
            self._results.append(
                StepResult(
                    step_index=-1,
                    step_name="<unhandled>",
                    success=False,
                    error=str(exc),
                )
            )
            self._persist()
            return list(self._results)

    # ---- internal -----------------------------------------------------------

    def _execute_step(self, idx: int, step: SagaStep) -> StepResult:
        attempt = 0
        max_attempts = min(1 + step.retry_count, 1 + self._config.max_retries)
        delay = step.retry_delay_seconds
        last_error: str | None = None

        while attempt < max_attempts:
            attempt += 1
            try:
                threading.Event()
                exc_holder: Exception | None = None

                def runner() -> None:
                    nonlocal exc_holder
                    try:
                        step.action()
                    except Exception as e:
                        exc_holder = e

                t = threading.Thread(target=runner, daemon=True)
                start = time.monotonic()
                t.start()
                t.join(timeout=step.timeout_seconds)

                if t.is_alive():
                    duration = (time.monotonic() - start) * 1000.0
                    self._set_state(SagaState.TIMED_OUT)
                    raise SagaTimeout(idx, step.name, step.timeout_seconds)

                duration = (time.monotonic() - start) * 1000.0

                if exc_holder is not None:
                    raise exc_holder

                return StepResult(
                    step_index=idx,
                    step_name=step.name,
                    success=True,
                    duration_ms=round(duration, 3),
                )

            except SagaTimeout:
                raise
            except Exception as e:
                last_error = str(e)
                if attempt < max_attempts:
                    time.sleep(delay)
                    delay *= self._config.retry_backoff_multiplier

        return StepResult(
            step_index=idx,
            step_name=step.name,
            success=False,
            error=last_error,
        )

    def _compensate(
        self,
        failed_idx: int,
        *,
        terminal_state: SagaState = SagaState.COMPENSATED,
    ) -> None:
        self._set_state(SagaState.COMPENSATING)
        for idx in range(failed_idx - 1, -1, -1):
            step = self._steps[idx]
            if step.compensation is None:
                continue
            _compensation: StepCompensation = step.compensation
            try:
                threading.Event()
                exc_holder: Exception | None = None

                def runner(comp: StepCompensation = _compensation) -> None:
                    nonlocal exc_holder
                    try:
                        comp()
                    except Exception as e:
                        exc_holder = e

                t = threading.Thread(target=runner, daemon=True)
                t.start()
                t.join(timeout=self._config.compensation_timeout_seconds)

                if exc_holder is not None:
                    raise exc_holder
            except Exception:
                pass
        self._set_state(terminal_state)

    def _set_state(self, new_state: SagaState) -> None:
        self._state = new_state

    def _persist(self) -> None:
        if self._store is None:
            return
        self._store.save(
            self.saga_id,
            {
                "saga_id": self.saga_id,
                "state": self._state.value,
                "results": [dataclasses.asdict(r) for r in self._results],
                "started_at": self._started_at,
                "completed_at": self._completed_at,
            },
        )

    def _restore(self) -> None:
        if self._store is None:
            return
        data = self._store.load(self.saga_id)
        if data is None:
            return
        stored_results = data.get("results", [])
        if isinstance(stored_results, list):
            for result_data in stored_results:
                if isinstance(result_data, dict):
                    self._results.append(StepResult(**result_data))
        stored_state = data.get("state")
        if stored_state == SagaState.COMPLETED.value:
            self._state = SagaState.COMPLETED
            self._completed_at = data.get("completed_at")
        elif stored_state == SagaState.COMPENSATED.value:
            self._state = SagaState.COMPENSATED
            self._completed_at = data.get("completed_at")


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class SagaBuilder:
    """Build a configured saga orchestrator through a fluent API."""

    def __init__(self) -> None:
        """Initialize an empty builder with safe default configuration."""
        self._steps: list[SagaStep] = []
        self._config = SagaConfig()
        self._store: SagaStore | None = None

    def step(
        self,
        name: str,
        action: StepAction,
        *,
        compensation: StepCompensation | None = None,
        timeout_seconds: float = 30.0,
        retry_count: int = 0,
        retry_delay_seconds: float = 0.1,
    ) -> SagaBuilder:
        """Append a configured forward step and optional compensation."""
        self._steps.append(
            SagaStep(
                name=name,
                action=action,
                compensation=compensation,
                timeout_seconds=timeout_seconds,
                retry_count=retry_count,
                retry_delay_seconds=retry_delay_seconds,
            )
        )
        return self

    def with_max_retries(self, n: int) -> SagaBuilder:
        """Set the maximum forward-action retry count."""
        self._config.max_retries = n
        return self

    def with_retry_backoff(self, multiplier: float) -> SagaBuilder:
        """Set the exponential retry backoff multiplier."""
        self._config.retry_backoff_multiplier = multiplier
        return self

    def with_persistence(self, store: SagaStore) -> SagaBuilder:
        """Attach a durable state store to the saga."""
        self._store = store
        return self

    def without_step_persistence(self) -> SagaBuilder:
        """Persist only terminal state instead of every successful step."""
        self._config.persist_on_each_step = False
        return self

    def with_compensation_timeout(self, seconds: float) -> SagaBuilder:
        """Set the execution budget for each compensation."""
        self._config.compensation_timeout_seconds = seconds
        return self

    def build(self, saga_id: str) -> SagaOrchestrator:
        """Build an orchestrator for the supplied saga identifier."""
        return SagaOrchestrator(saga_id, self._steps, config=self._config, store=self._store)
