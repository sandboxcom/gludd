"""Execution tracer — lightweight span-based trace system for task lifecycle observability.

Inspired by Arize Phoenix / OpenTelemetry span tracing, adapted for gludd's
agentic task pipeline. Records timing, tokens, and costs for each phase of
task execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ExecutionSpan:
    """A single span in an execution trace, representing one phase of a task."""

    trace_id: str
    span_id: str
    name: str
    phase: str
    started_at: datetime

    status: str = "running"
    ended_at: datetime | None = None
    duration_ms: float = 0.0

    model_profile_id: str | None = None
    prompt_profile_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error_message: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def complete(
        self,
        status: str = "success",
        ended_at: datetime | None = None,
        output_tokens: int = 0,
        input_tokens: int = 0,
        cost_usd: float = 0.0,
        model_profile_id: str | None = None,
        prompt_profile_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.status = status
        self.ended_at = ended_at or datetime.now(UTC)
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        self.output_tokens = output_tokens
        self.input_tokens = input_tokens
        self.cost_usd = cost_usd
        if model_profile_id:
            self.model_profile_id = model_profile_id
        if prompt_profile_id:
            self.prompt_profile_id = prompt_profile_id
        if error_message:
            self.error_message = error_message

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "name": self.name,
            "phase": self.phase,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "model_profile_id": self.model_profile_id,
            "prompt_profile_id": self.prompt_profile_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "error_message": self.error_message,
        }


@dataclass
class ExecutionTrace:
    """A complete trace of a task execution, containing multiple spans."""

    trace_id: str
    todo_id: str
    work_type: str
    started_at: datetime
    spans: list[ExecutionSpan] = field(default_factory=list)

    def __init__(
        self,
        todo_id: str = "",
        work_type: str = "code",
        trace_id: str | None = None,
    ) -> None:
        self.trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        self.todo_id = todo_id
        self.work_type = work_type
        self.started_at = datetime.now(UTC)
        self.spans = []

    def start_span(self, name: str, phase: str = "generate") -> ExecutionSpan:
        span = ExecutionSpan(
            trace_id=self.trace_id,
            span_id=f"span-{uuid.uuid4().hex[:8]}",
            name=name,
            phase=phase,
            started_at=datetime.now(UTC),
        )
        self.spans.append(span)
        return span

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.spans)

    @property
    def total_tokens(self) -> int:
        return sum(s.output_tokens for s in self.spans)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.spans)

    @property
    def success_rate(self) -> float:
        if not self.spans:
            return 0.0
        successful = sum(1 for s in self.spans if s.status == "success")
        return successful / len(self.spans)

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "todo_id": self.todo_id,
            "work_type": self.work_type,
            "started_at": self.started_at.isoformat(),
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "success_rate": self.success_rate,
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
        }
