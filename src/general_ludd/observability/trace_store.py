"""Bounded in-memory recent-traces store — the honest persistence seam.

``ExecutionTrace`` objects are produced in-process (timing/tokens/cost per
phase) but were not previously retained anywhere queryable: the tracer builds a
trace, the ``AutoBenchmarkRecorder`` derives a benchmark row from it, and the
``OTelBridge`` (when an OTLP collector is configured) exports its spans — after
which the trace is dropped. To expose *genuinely-captured* traces as Ansible
dynamic facts WITHOUT fabricating a data source, this module adds a small
bounded ring buffer that the recorder path appends each completed trace to.

The buffer lives on ``app.state._recent_traces`` and is read by GET /api/facts
(``traces`` block) and GET /api/traces. It is deliberately in-process and
bounded: facts payloads must stay small enough to use in playbook ``when:``
conditions, and traces are observability telemetry, not durable records (the
durable derivative — benchmark results — already lands in the DB via the
recorder). Nothing here invents spans; ``recent()``/``snapshot()`` only ever
return traces that were actually recorded.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from general_ludd.observability.tracer import ExecutionTrace

DEFAULT_MAXLEN = 100
DEFAULT_RECENT_LIMIT = 20
DEFAULT_MAX_SPANS = 25


class RecentTracesBuffer:
    """A bounded ring of the most-recently-recorded execution traces.

    Newest traces are returned first. The ring drops the oldest trace once it
    exceeds ``maxlen``. ``total_recorded`` counts every trace ever recorded
    (including dropped ones) so the snapshot can be honest about truncation.
    """

    def __init__(self, maxlen: int = DEFAULT_MAXLEN) -> None:
        self._traces: deque[ExecutionTrace] = deque(maxlen=maxlen)
        self._total_recorded = 0

    def record(self, trace: ExecutionTrace) -> None:
        """Append a completed trace. Called from the recorder path."""
        self._traces.append(trace)
        self._total_recorded += 1

    @property
    def total_recorded(self) -> int:
        return self._total_recorded

    def recent(
        self,
        limit: int | None = None,
        todo_id: str | None = None,
    ) -> list[ExecutionTrace]:
        """Most-recent-first traces, optionally filtered by ``todo_id``."""
        items = list(reversed(self._traces))
        if todo_id is not None:
            items = [t for t in items if t.todo_id == todo_id]
        if limit is not None:
            items = items[:limit]
        return items

    def _phase_summary(self, traces: list[ExecutionTrace]) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for trace in traces:
            for span in trace.spans:
                bucket = summary.setdefault(
                    span.phase,
                    {
                        "span_count": 0,
                        "total_cost_usd": 0.0,
                        "total_tokens": 0,
                        "success_count": 0,
                    },
                )
                bucket["span_count"] += 1
                bucket["total_cost_usd"] += span.cost_usd
                bucket["total_tokens"] += span.output_tokens
                if span.status == "success":
                    bucket["success_count"] += 1
        return summary

    def snapshot(
        self,
        limit: int = DEFAULT_RECENT_LIMIT,
        max_spans: int = DEFAULT_MAX_SPANS,
        todo_id: str | None = None,
    ) -> dict[str, Any]:
        """Bounded JSON-safe view: recent traces + by-phase aggregate.

        Trace count is capped to ``limit`` and each trace's span list to
        ``max_spans`` so the payload stays usable inside playbooks. The phase
        aggregate is computed over the (possibly filtered) recent window.
        """
        traces = self.recent(limit=limit, todo_id=todo_id)
        recent: list[dict[str, Any]] = []
        for trace in traces:
            row = trace.to_dict()
            spans = row.get("spans", [])
            if isinstance(spans, list) and len(spans) > max_spans:
                row["spans"] = spans[:max_spans]
                row["spans_truncated"] = True
            recent.append(row)
        return {
            "count": len(recent),
            "total_recorded": self._total_recorded,
            "recent": recent,
            "by_phase": self._phase_summary(traces),
        }
