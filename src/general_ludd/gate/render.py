"""Render gate progress reports and derive the next action to take."""

from __future__ import annotations

import re

from general_ludd.gate._types import GateRun, ProgressReport

_FAILED_ID_RE = re.compile(r"^FAILED (\S+::\S+)")
_ERROR_LINE_RE = re.compile(r"^E\s+((?:AssertionError|[A-Za-z_][A-Za-z0-9_.]*Error).*)$")


def _format_eta(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def failure_summary(run: GateRun) -> str:
    """Extract failing test ids and the first error line from a failed log."""
    failed_ids: list[str] = []
    seen: set[str] = set()
    error_message: str | None = None
    for line in run.lines:
        id_match = _FAILED_ID_RE.match(line)
        if id_match and id_match.group(1) not in seen:
            seen.add(id_match.group(1))
            failed_ids.append(id_match.group(1))
            if len(failed_ids) >= 3:
                break
    for line in run.lines:
        error_match = _ERROR_LINE_RE.match(line)
        if error_match:
            error_message = error_match.group(1).strip()
            if len(error_message) > 90:
                error_message = error_message[:90] + "..."
            break
    if not failed_ids:
        return ""
    ids = ", ".join(failed_ids)
    if error_message:
        return f"{ids} ({error_message})"
    return ids


def _next_action(status: str | None, failure: str, next_poll_secs: float | None = None) -> str:
    """Suggest the immediate next step for the gate state."""
    if status is None:
        return ""
    if status.startswith("RUNNING"):
        if next_poll_secs is not None and next_poll_secs > 0:
            return f"next: keep working; next poll in ~{int(next_poll_secs)}s"
        return "next: keep working; poll at natural breaks"
    if status.startswith("FINISHED: PASSED"):
        return "next: commit green work, push, verify remote"
    if status.startswith("FINISHED: FAILED"):
        if failure:
            return f"next: fix {failure} then make gate-resume"
        return "next: gate-tail the failure, fix root cause, gate-resume"
    if status.startswith("IDLE"):
        return "next: gate-background (or gate-resume if a prior gate failed)"
    return ""


def render_progress(
    report: ProgressReport,
    status: str | None = None,
    failure: str | None = None,
    next_poll_secs: float | None = None,
) -> str:
    """Render a progress report as a one-line summary."""
    label = report.current_label or "finished"
    eta = "unknown" if not report.remaining_known else _format_eta(report.estimated_remaining_seconds)
    total = _format_eta(report.estimated_total_seconds)
    segment_eta = _format_eta(report.segment_remaining_seconds)
    prefix = f"gate: {status} | " if status else "gate: "
    next_step = _next_action(status, failure or "", next_poll_secs)
    next_suffix = f" | {next_step}" if next_step else ""
    return (
        f"{prefix}{label} | {report.percent_complete:.1f}% of gate complete"
        f" ({report.segment_percent_complete:.1f}% of segment) | "
        f"elapsed {_format_eta(report.elapsed_seconds)} | "
        f"ETA {eta} (segment ETA {segment_eta}; total ~{total}) | "
        f"segments {report.completed_segments}/{report.total_segments}{next_suffix}"
    )
