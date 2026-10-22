"""Summarize durable pytest trace events without discarding failure evidence."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DEFAULT_TRACE_LOG = "/tmp/gludd-xdist-progress.log"
LEGACY_RSS_BYTES_THRESHOLD = 16 * 1024 * 1024
LegacyRssUnit = Literal["auto", "bytes", "kib"]

_MEMORY_KEYS = (
    "legacy_rss_input_unit",
    "memory_by_worker",
    "largest_rss_increases",
)
_RUN_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)


def _validated_run_id(value: str) -> str:
    """Accept only the safe path-component format emitted by the observer."""
    if (
        not value
        or len(value) > 200
        or value[0] in ".-"
        or any(character not in _RUN_ID_CHARS for character in value)
    ):
        raise argparse.ArgumentTypeError("run ID must be a safe path component")
    return value


def _events(path: Path, *, run_id: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    parsed: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if run_id is None:
                parsed.append({"event": "PARSE_ERROR", "line": line[:500]})
            continue
        if isinstance(value, dict) and (
            run_id is None or value.get("run_id") == run_id
        ):
            parsed.append(value)
    return parsed


def _legacy_rss_input_unit(
    events: list[dict[str, Any]], requested_unit: LegacyRssUnit
) -> Literal["bytes", "kib"] | None:
    """Resolve the platform-dependent unit used by the legacy ``rss_kb`` field."""
    if requested_unit != "auto":
        return requested_unit
    values = [
        value
        for event in events
        if isinstance((value := event.get("rss_kb")), int) and value >= 0
    ]
    if not values:
        return None
    # ``ru_maxrss`` is bytes on macOS and KiB on Linux. Existing trace events did
    # not record the platform, so a value above 16M is overwhelmingly likely to
    # be macOS bytes. Callers can override this inference for exceptional logs.
    return "bytes" if max(values) >= LEGACY_RSS_BYTES_THRESHOLD else "kib"


def _rss_bytes(
    event: dict[str, Any], legacy_unit: Literal["bytes", "kib"] | None
) -> int | None:
    rss_bytes = event.get("rss_bytes")
    if isinstance(rss_bytes, int) and rss_bytes >= 0:
        return rss_bytes
    rss_kib = event.get("rss_kib")
    if isinstance(rss_kib, int) and rss_kib >= 0:
        return rss_kib * 1024
    legacy_rss = event.get("rss_kb")
    if not isinstance(legacy_rss, int) or legacy_rss < 0:
        return None
    return legacy_rss if legacy_unit == "bytes" else legacy_rss * 1024


def _as_kib(value: int) -> int:
    return value // 1024


@dataclass
class _PendingTiming:
    timestamp: float | None
    outstanding_occurrences: int = 1


@dataclass(frozen=True)
class _ProtocolSpan:
    stream: tuple[str, str, int]
    nodeid: str
    seconds: float


@dataclass
class _TimingTotal:
    paired_count: int = 0
    seconds: float = 0.0

    def add(self, span: _ProtocolSpan) -> None:
        self.paired_count += 1
        self.seconds += span.seconds


def _timing_stream(event: Mapping[str, object]) -> tuple[str, str, int] | None:
    run_id, worker, pid = event.get("run_id"), event.get("worker"), event.get("pid")
    if (
        isinstance(run_id, str) and run_id
        and isinstance(worker, str) and worker
        and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
    ):
        return run_id, worker, pid
    return None


def _timing_timestamp(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        timestamp = float(value)
    except OverflowError:
        return None
    return timestamp if math.isfinite(timestamp) and timestamp >= 0 else None


def _timing_rows(spans: Sequence[_ProtocolSpan], limit: int) -> dict[str, object]:
    modules: dict[tuple[str, str, int, str], _TimingTotal] = {}
    workers: dict[tuple[str, str, int], _TimingTotal] = {}
    for span in spans:
        module = (*span.stream, span.nodeid.partition("::")[0])
        modules.setdefault(module, _TimingTotal()).add(span)
        workers.setdefault(span.stream, _TimingTotal()).add(span)
    return {
        "slowest_tests": [
            {
                "run_id": span.stream[0], "worker": span.stream[1],
                "pid": span.stream[2], "nodeid": span.nodeid,
                "test_protocol_seconds": round(span.seconds, 6),
            }
            for span in sorted(
                spans, key=lambda span: (-span.seconds, span.stream, span.nodeid)
            )[:limit]
        ],
        "modules": [
            {
                "run_id": module[0], "worker": module[1], "pid": module[2],
                "module": module[3], "paired_count": total.paired_count,
                "test_protocol_seconds": round(total.seconds, 6),
            }
            for module, total in sorted(
                modules.items(), key=lambda item: (-item[1].seconds, item[0])
            )[:limit]
        ],
        "workers": [
            {
                "run_id": stream[0], "worker": stream[1], "pid": stream[2],
                "paired_count": total.paired_count,
                "test_protocol_seconds": round(total.seconds, 6),
            }
            for stream, total in sorted(
                workers.items(), key=lambda item: (-item[1].seconds, item[0])
            )[:limit]
        ],
    }


def _trace_timings(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Pair serial test protocols; wall spans include fixtures and child waits."""
    pending: dict[tuple[str, str, int], dict[str, _PendingTiming]] = {}
    last_finish: dict[tuple[str, str, int], float] = {}
    spans: list[_ProtocolSpan] = []
    issues: list[dict[str, object]] = []

    def reject(
        reason: str, event: Mapping[str, object], *, outstanding_occurrences: int | None = None
    ) -> None:
        issue = {
            "reason": reason,
            **{key: event.get(key) for key in ("run_id", "worker", "pid", "nodeid")},
        }
        if outstanding_occurrences is not None:
            issue["outstanding_occurrences"] = outstanding_occurrences
        issues.append(issue)

    for event in events:
        kind = event.get("event")
        if kind == "PARSE_ERROR":
            reject("parse_error", event)
        if kind not in ("START", "FINISH"):
            continue
        stream, nodeid = _timing_stream(event), event.get("nodeid")
        if stream is None or not isinstance(nodeid, str) or not nodeid:
            reject("invalid_identity", event)
            continue
        timestamp = _timing_timestamp(event.get("timestamp"))
        if timestamp is None:
            reject("invalid_timestamp", event)
        active = pending.setdefault(stream, {})
        if kind == "START":
            if active:
                reject("overlapping_start", event)
                # Neither overlapping protocol has an unambiguous serial span.
                for occurrence in active.values():
                    occurrence.timestamp = None
                timestamp = None
            elif timestamp is not None and timestamp < last_finish.get(stream, 0):
                reject("overlapping_interval", event)
                timestamp = None
            if nodeid in active:
                active[nodeid].outstanding_occurrences += 1
            else:
                active[nodeid] = _PendingTiming(timestamp)
        elif nodeid not in active:
            reject("unmatched_finish", event)
        else:
            occurrence = active[nodeid]
            started = occurrence.timestamp
            occurrence.outstanding_occurrences -= 1
            if occurrence.outstanding_occurrences == 0:
                del active[nodeid]
            if timestamp is not None:
                if started is not None:
                    if timestamp < started:
                        reject("negative_duration", event)
                    else:
                        spans.append(_ProtocolSpan(stream, nodeid, timestamp - started))
                last_finish[stream] = max(last_finish.get(stream, 0), timestamp)
    for stream, active in pending.items():
        for nodeid, occurrence in active.items():
            reject("unfinished_start", {
                "run_id": stream[0], "worker": stream[1],
                "pid": stream[2], "nodeid": nodeid,
            }, outstanding_occurrences=occurrence.outstanding_occurrences)
    limit = 25
    return {
        "authority": "diagnostic-only", "gate_evidence": False, "release_ready": False,
        "clock": "wall", "scope": "test-protocol",
        "aggregation": "sum-of-paired-test-protocol-wall-seconds",
        "paired_count": len(spans), "issue_count": len(issues),
        "limit": limit, "issues": issues[:limit], **_timing_rows(spans, limit),
    }


def summarize_log(
    path: Path,
    *,
    legacy_rss_unit: LegacyRssUnit = "auto",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Summarize all events, or only events matching one validated run ID."""
    events = _events(path, run_id=run_id)
    resolved_legacy_unit = _legacy_rss_input_unit(events, legacy_rss_unit)
    unfinished: dict[tuple[str, str], dict[str, str]] = {}
    failures: list[dict[str, Any]] = []
    failure_nodeids: list[str] = []
    seen_failure_nodeids: set[str] = set()
    last_by_worker: dict[str, str] = {}
    memory_by_worker: dict[str, dict[str, Any]] = {}
    largest_rss_increases: list[dict[str, Any]] = []
    started = 0
    finished = 0

    for event in events:
        event_name = str(event.get("event", ""))
        worker = str(event.get("worker") or "controller")
        nodeid = event.get("nodeid")
        rss_bytes = _rss_bytes(event, resolved_legacy_unit)
        if rss_bytes is not None:
            memory = memory_by_worker.setdefault(
                worker,
                {
                    "first_rss_bytes": rss_bytes,
                    "first_rss_kib": _as_kib(rss_bytes),
                    "peak_rss_bytes": rss_bytes,
                    "peak_rss_kib": _as_kib(rss_bytes),
                    "growth_rss_bytes": 0,
                    "growth_rss_kib": 0,
                    "peak_nodeid": nodeid,
                    "_previous_rss_bytes": rss_bytes,
                },
            )
            previous_rss_bytes = int(memory["_previous_rss_bytes"])
            increase_rss_bytes = rss_bytes - previous_rss_bytes
            if increase_rss_bytes > 0:
                largest_rss_increases.append(
                    {
                        "worker": worker,
                        "nodeid": nodeid,
                        "event": event_name,
                        "rss_bytes": rss_bytes,
                        "rss_kib": _as_kib(rss_bytes),
                        "increase_rss_bytes": increase_rss_bytes,
                        "increase_rss_kib": _as_kib(increase_rss_bytes),
                    }
                )
            memory["_previous_rss_bytes"] = rss_bytes
            if rss_bytes > int(memory["peak_rss_bytes"]):
                memory["peak_rss_bytes"] = rss_bytes
                memory["peak_rss_kib"] = _as_kib(rss_bytes)
                memory["peak_nodeid"] = nodeid
            growth_rss_bytes = int(memory["peak_rss_bytes"]) - int(
                memory["first_rss_bytes"]
            )
            memory["growth_rss_bytes"] = growth_rss_bytes
            memory["growth_rss_kib"] = _as_kib(growth_rss_bytes)
        if event_name == "START" and isinstance(nodeid, str):
            started += 1
            unfinished[(worker, nodeid)] = {"worker": worker, "nodeid": nodeid}
            last_by_worker[worker] = nodeid
        elif event_name == "FINISH" and isinstance(nodeid, str):
            finished += 1
            unfinished.pop((worker, nodeid), None)
        elif event_name == "REPORT" and event.get("outcome") == "failed":
            failures.append(
                {
                    "worker": worker,
                    "nodeid": nodeid,
                    "when": event.get("when"),
                    "duration": event.get("duration"),
                    "longrepr": event.get("longrepr"),
                }
            )
            if isinstance(nodeid, str) and nodeid not in seen_failure_nodeids:
                seen_failure_nodeids.add(nodeid)
                failure_nodeids.append(nodeid)

    for memory in memory_by_worker.values():
        memory.pop("_previous_rss_bytes", None)
    largest_rss_increases.sort(
        key=lambda value: int(value["increase_rss_bytes"]), reverse=True
    )

    summary = {
        "path": str(path),
        "events": len(events),
        "started": started,
        "finished": finished,
        "unfinished": sorted(unfinished.values(), key=lambda value: (value["worker"], value["nodeid"])),
        "last_by_worker": dict(sorted(last_by_worker.items())),
        "failure_report_count": len(failures),
        "failure_nodeid_count": len(failure_nodeids),
        "failure_nodeids": failure_nodeids,
        "failures": failures[:50],
        "legacy_rss_input_unit": resolved_legacy_unit,
        "memory_by_worker": dict(sorted(memory_by_worker.items())),
        "largest_rss_increases": largest_rss_increases[:25],
        "timings": _trace_timings(events),
    }
    if run_id is not None:
        summary["run_id"] = run_id
    return summary


def _selected_summary(
    summary: dict[str, Any], keys: tuple[str, ...], *,
    include_memory: bool, include_timings: bool,
) -> dict[str, Any]:
    selected = {key: summary[key] for key in keys}
    if "run_id" in summary:
        selected["run_id"] = summary["run_id"]
    if include_memory:
        selected.update({key: summary[key] for key in _MEMORY_KEYS})
    if include_timings:
        selected["timings"] = summary["timings"]
    return selected


def compact_summary(
    summary: dict[str, Any], *, include_memory: bool = False,
    include_timings: bool = False,
) -> dict[str, Any]:
    """Return complete failure/crash evidence without verbose tracebacks."""
    keys = (
        "path",
        "events",
        "started",
        "finished",
        "failure_report_count",
        "failure_nodeid_count",
        "failure_nodeids",
        "unfinished",
        "last_by_worker",
    )
    return _selected_summary(
        summary, keys, include_memory=include_memory, include_timings=include_timings
    )


def failures_only_summary(
    summary: dict[str, Any], *, include_memory: bool = False,
    include_timings: bool = False,
) -> dict[str, Any]:
    """Return concise failure and incomplete-test evidence for long trace runs."""
    keys = (
        "path",
        "failure_report_count",
        "failure_nodeid_count",
        "failure_nodeids",
        "unfinished",
    )
    return _selected_summary(
        summary, keys, include_memory=include_memory, include_timings=include_timings
    )


def _cli_guidance(
    *, events: int, paired_count: int, issue_count: int,
    unfinished_count: int, failure_count: int,
) -> str:
    """Explain the selected diagnostic result without changing its authority."""
    if events == 0:
        outcome = "empty"
        detail = (
            "No selected trace events; timing, cause and performance remain unknown. "
            "Preserve the receipt/raw trace and inspect the producer and exact LOG/RUN_ID."
        )
    elif issue_count or unfinished_count:
        outcome = "partial"
        detail = (
            "Preserve failures, unfinished nodes and timing issues; "
            "only accepted paired spans are available and missing intervals remain unknown."
        )
    elif paired_count == 0:
        outcome = "no-paired-timings"
        detail = (
            "Selected events contain no accepted paired timing spans; "
            "preserve diagnostic evidence without inferring timing, cause or performance."
        )
    else:
        outcome = "populated-with-failures" if failure_count else "populated"
        detail = (
            "Inspect accepted exact run/worker/PID spans and retain recorded failures; "
            "wall spans include fixtures and child waits, not CPU or suite elapsed time. "
            "These rows alone establish no timeout cause or performance improvement."
        )
    return (
        "AGENT_NEXT target=test-xdist-trace-summary "
        f"outcome={outcome} events={events} paired_count={paired_count} "
        f"issue_count={issue_count} unfinished={unfinished_count} "
        f"failure_reports={failure_count} gate_evidence=false release_ready=false: "
        f"Exit 0 means summary generation only. {detail} "
        "Original acceptance, full gate and exact-SHA release proofs remain required."
    )


def main(argv: list[str] | None = None) -> int:
    """Print the selected compact, failure-only, or verbose JSON summary."""
    parser = argparse.ArgumentParser(description="Summarize a Gludd xdist JSONL trace")
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument("--verbose", action="store_true")
    output_mode.add_argument("--failures-only", action="store_true")
    parser.add_argument("--include-memory", action="store_true")
    parser.add_argument("--include-timings", action="store_true")
    parser.add_argument(
        "--run-id",
        type=_validated_run_id,
        help="include only events from this observed-command run",
    )
    parser.add_argument(
        "--legacy-rss-unit",
        choices=("auto", "bytes", "kib"),
        default="auto",
        help="unit of legacy rss_kb events (default: infer safely)",
    )
    parser.add_argument("path", nargs="?", default=DEFAULT_TRACE_LOG)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    path = Path(args.path)
    summary = summarize_log(
        path,
        legacy_rss_unit=args.legacy_rss_unit,
        run_id=args.run_id,
    )
    if args.verbose:
        output = summary
    elif args.failures_only:
        output = failures_only_summary(
            summary, include_memory=args.include_memory, include_timings=args.include_timings
        )
    else:
        output = compact_summary(
            summary, include_memory=args.include_memory, include_timings=args.include_timings
        )
    print(json.dumps(output, indent=2, sort_keys=True))
    timings = summary["timings"]
    print(
        _cli_guidance(
            events=summary["events"], paired_count=timings["paired_count"],
            issue_count=timings["issue_count"], unfinished_count=len(summary["unfinished"]),
            failure_count=summary["failure_report_count"],
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
