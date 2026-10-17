"""Estimate gate completion percentage and ETA from parsed gate logs."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import mean

from general_ludd.gate._types import GateEvent, GateRun, ProgressReport
from general_ludd.gate.parsing import elapsed_at_line, parse_gate_log


def _event_duration(run: GateRun, event: GateEvent) -> float:
    """Estimate event duration from recorded or interpolated wall time."""
    if run.duration <= 0:
        return 0.0
    start = elapsed_at_line(run, event.start_line, run.duration)
    end = elapsed_at_line(run, event.end_line, run.duration)
    return max(0.0, end - start)


def _average_durations(runs: Iterable[GateRun]) -> dict[str, float]:
    """Compute average duration per segment label across historical runs."""
    samples: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        for event in run.events:
            samples[event.label].append(_event_duration(run, event))
    averages: dict[str, float] = {}
    for label, values in samples.items():
        averages[label] = mean(values) if values else 0.0
    return averages


def _template_sequence(runs: Iterable[GateRun]) -> list[str]:
    """Build the expected segment sequence from the most complete runs.

    Runs are ordered by event count so a full run contributes the spine;
    labels missing from it are filled in from shorter runs.
    """
    labels: list[str] = []
    seen: set[str] = set()
    for run in sorted(runs, key=lambda r: len(r.events), reverse=True):
        for event in run.events:
            if event.label not in seen:
                seen.add(event.label)
                labels.append(event.label)
    return labels


def _shard_batch_counts(runs: Iterable[GateRun]) -> dict[str, int]:
    """Return the declared batch count per shard, taking the maximum seen."""
    counts: dict[str, int] = {}
    for run in runs:
        for name, batches in run.shards:
            counts[name] = max(counts.get(name, 0), batches)
    return counts


def _longest_run_shards(runs: Iterable[GateRun]) -> list[tuple[str, int]]:
    """Return the shard declaration list from the run that declared the most."""
    best: list[tuple[str, int]] = []
    for run in runs:
        if len(run.shards) > len(best):
            best = list(run.shards)
    return best


def _fallback_average(label: str, averages: dict[str, float]) -> float:
    """Fall back to the shard's average, then the global batch average."""
    if ":" not in label:
        return 0.0
    shard = label.rsplit(":", 1)[0]
    shard_values = [avg for key, avg in averages.items() if key.startswith(shard + ":")]
    if shard_values:
        return mean(shard_values)
    batch_values = [avg for key, avg in averages.items() if ":" in key]
    if batch_values:
        return mean(batch_values)
    return 60.0


def _elapsed_seconds(current: GateRun, now: float) -> float:
    """Current wall-clock elapsed: heartbeat max vs wall time, whichever wins."""
    heartbeat_elapsed = max(hb.elapsed for hb in current.heartbeats) if current.heartbeats else 0.0
    return max(heartbeat_elapsed, max(0.0, now - current.start_time))


def _terminal_report(current: GateRun, elapsed: float) -> ProgressReport:
    return ProgressReport(
        current_label=None,
        elapsed_seconds=elapsed,
        estimated_total_seconds=elapsed,
        estimated_remaining_seconds=0.0,
        percent_complete=100.0,
        completed_segments=len(current.events),
        total_segments=len(current.events),
        remaining_known=True,
    )


def _starting_report(elapsed: float, has_template: bool) -> ProgressReport:
    return ProgressReport(
        current_label="starting",
        elapsed_seconds=elapsed,
        estimated_total_seconds=elapsed,
        estimated_remaining_seconds=0.0,
        percent_complete=0.0,
        completed_segments=0,
        total_segments=0,
        remaining_known=has_template,
    )


def estimate_progress(
    current_log: Path,
    historical_logs: Iterable[Path],
    now: float | None = None,
) -> ProgressReport:
    """Estimate completion percentage and ETA for a running gate."""
    current = parse_gate_log(current_log)
    historical = [parse_gate_log(p) for p in historical_logs if p != current_log]
    averages = _average_durations(historical)
    template = _template_sequence(historical)
    shard_counts = _shard_batch_counts([current, *historical])
    shard_order = [name for name, _batches in current.shards] or [
        name for name, _batches in _longest_run_shards(historical)
    ]

    elapsed = _elapsed_seconds(current, now or time.time())

    if current.terminal is not None:
        return _terminal_report(current, elapsed)

    if not current.events:
        return _starting_report(elapsed, bool(template))

    last_line = current.total_lines
    completed_events = [e for e in current.events if e.end_line < last_line]
    active_events = [e for e in current.events if e.end_line == last_line]
    active = active_events[-1] if active_events else current.events[-1]

    active_start_elapsed = elapsed_at_line(current, active.start_line, elapsed)
    elapsed_in_active = max(0.0, elapsed - active_start_elapsed)
    active_avg = averages.get(active.label) or _fallback_average(active.label, averages)
    active_remaining = max(0.0, active_avg - elapsed_in_active)

    completed_labels = {e.label for e in completed_events}
    remaining_labels = [label for label in template if label not in completed_labels and label != active.label]

    def _add_batch_labels(shard: str, from_batch: int, to_batch: int) -> None:
        for number in range(from_batch, to_batch + 1):
            label = f"{shard}:batch-{number:03d}"
            if label not in completed_labels and label != active.label and label not in remaining_labels:
                remaining_labels.append(label)

    # Synthesize the remaining batches of the active shard: the current log
    # declares the shard's batch count even though later batches never appear
    # in any historical template (they only run if the gate gets there).
    active_shard: str | None = None
    if ":" in active.label:
        active_shard, active_batch_text = active.label.rsplit(":", 1)
        if active_batch_text.startswith("batch-"):
            active_batch = int(active_batch_text.split("-", 1)[1])
            declared = shard_counts.get(active_shard, active_batch)
            _add_batch_labels(active_shard, active_batch + 1, declared)

    # Shards that follow the active one contribute their full declared range.
    for shard in shard_order:
        declared = shard_counts.get(shard, 0)
        if declared <= 0:
            continue
        if shard == active_shard and active.label.startswith(shard + ":batch-"):
            continue
        if any(e.label.startswith(shard + ":batch-") for e in [*completed_events, active]):
            continue
        _add_batch_labels(shard, 1, declared)

    # A resume run skips batches already recorded in its resume state; drop
    # the first N pending batch labels so the estimate reflects real work.
    skipped_segments = 0
    if current.resume_batches > 0:
        pending_batches = [label for label in remaining_labels if ":" in label]
        skipped_segments = min(current.resume_batches, len(pending_batches))
        if skipped_segments:
            remaining_labels = [label for label in remaining_labels if ":" not in label] + pending_batches[
                skipped_segments:
            ]

    remaining_seconds = active_remaining + sum(
        averages.get(label) or _fallback_average(label, averages) for label in remaining_labels
    )

    remaining_known = remaining_seconds > 0 and (bool(template) or active_avg > 0)
    estimated_total = elapsed + remaining_seconds
    if remaining_known and estimated_total > 0:
        percent = min(100.0, elapsed / estimated_total * 100.0)
    else:
        known_total = len(completed_events) + 1
        percent = min(100.0, len(completed_events) / known_total * 100.0)

    completed_display = len(completed_events) + skipped_segments
    return ProgressReport(
        current_label=active.label,
        elapsed_seconds=elapsed,
        estimated_total_seconds=estimated_total,
        estimated_remaining_seconds=remaining_seconds,
        percent_complete=percent,
        completed_segments=completed_display,
        total_segments=completed_display + 1 + len(remaining_labels),
        remaining_known=remaining_known,
    )
