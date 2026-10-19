"""Parse gate background logs into phases, batches, and heartbeat samples."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from general_ludd.gate._types import GateAdmission, GateEvent, GateRun, Heartbeat

(
    _PHASE_RE,
    _SHARD_RE,
    _BATCH_SETUP_RE,
    _BATCH_RESULT_RE,
    _BATCH_PASS_RE,
    _BATCH_FAIL_RE,
    _SHARD_PASS_RE,
    _SHARD_FAIL_RE,
    _TERMINAL_RE,
    _RESUME_RECOVER_RE,
) = (
    re.compile(r"^=== GATE PHASE: (\S+) ===$"),
    re.compile(r"^=== GATE TEST SHARD \d+/\d+: (\S+) files=\d+ batches=(\d+) max-files=\d+ ===$"),
    re.compile(r"^SERIAL-SHARD-PHASE phase=(\S+):batch-(\d+):setup result=\d+$"),
    re.compile(r"^OWNED-PYTEST-RESULT label=(\S+):batch-(\d+) rc=(\d+)$"),
    re.compile(r"^SHARD-BATCH-PASS shard=(\S+) batch=(\d+) rc=\d+$"),
    re.compile(r"^SHARD-BATCH-FAIL shard=(\S+) batch=(\d+).*$"),
    re.compile(r"^SHARD-PASS shard=(\S+) rc=\d+$"),
    re.compile(r"^SHARD-FAIL shard=(\S+) batch=(\d+).*$"),
    re.compile(r"^=== GATE: (PASSED|FAILED) ===$"),
    re.compile(r"^RESUME-RECOVER candidate_sha=\S+ batches=(\d+)$"),
)
_HEARTBEAT_RE = re.compile(
    r"^\[gate-background-watch\] heartbeat run_id=\S+ pid=\d+ "
    r"elapsed=([\d.]+)s remaining=([\d.]+)s$"
)
_LOG_NAME_RE = re.compile(r"gate-(\d{14})-[0-9a-f]{8}\.log")


def _parse_filename_timestamp(name: str) -> float | None:
    """Derive a log start time from the gate-<timestamp>-<hash>.log name."""
    match = _LOG_NAME_RE.search(name)
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        return dt.timestamp()
    except ValueError:
        return None


def _batch_label(shard: str, number: str) -> str:
    return f"{shard}:batch-{int(number):03d}"


def parse_gate_log(path: Path) -> GateRun:
    """Parse a gate log into phases, batches, heartbeats, and terminal state."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start_time = _parse_filename_timestamp(path.name) or os.path.getmtime(path)

    events: list[GateEvent] = []
    heartbeats: list[Heartbeat] = []
    shards: list[tuple[str, int]] = []
    terminal: str | None = None
    resume_batches = 0
    admission: GateAdmission | None = None
    open_phase: GateEvent | None = None
    open_batch: GateEvent | None = None

    def _close_phase(end_line: int) -> None:
        nonlocal open_phase
        if open_phase is not None:
            events.append(
                GateEvent(
                    kind="phase",
                    label=open_phase.label,
                    start_line=open_phase.start_line,
                    end_line=end_line,
                )
            )
            open_phase = None

    def _close_batch(end_line: int) -> None:
        nonlocal open_batch
        if open_batch is not None:
            events.append(
                GateEvent(
                    kind="batch",
                    label=open_batch.label,
                    start_line=open_batch.start_line,
                    end_line=end_line,
                )
            )
            open_batch = None

    for line_no, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")

        if admission is not None:
            owner = re.match(r"^PROOF-ADMISSION-START phase=(\S+)$", line)
            blocked = re.match(r"^PROOF-ADMISSION-BLOCKED phase=(\S+)(?: .*)?$", line)
            observed = re.match(r"^\[observed [^\]]+\] start run_id=(\S+) log=(.+)$", line)
            if owner:
                admission.owner = owner.group(1)[:128]
                admission.reason = None
                admission.node = None
            elif blocked:
                admission.state = "blocked"
                admission.owner = blocked.group(1)[:128]
            elif line.startswith("PROOF-ADMISSION-PASS "):
                admission.state = "passed"
            elif admission.state == "running":
                if observed:
                    admission.run_id = observed.group(1)[:128]
                    admission.log_path = observed.group(2)[:512]
                elif line.startswith("FAILED "):
                    admission.node = line.split(" ", 2)[1][:256]
                elif re.match(
                    r"^(?:ERROR:|BLOCKED:|E |observed command error:|observed-command:|work-item:"
                    r"|phase=(?:inspect|cleanup|recheck) status=failed)", line,
                ):
                    admission.reason = line[:256]
                elif re.match(r"^\[observed [^\]]+\] result state=(?:timed_out|failed|interrupted) ", line):
                    if admission.reason is None or "state=timed_out " in line:
                        admission.reason = line[:256]
            if line.startswith("admission FAIL ") and admission.log_path is None:
                pointer = re.search(r" log=(\S+)", line)
                if pointer:
                    admission.log_path = pointer.group(1)[:512]

        heartbeat_match = _HEARTBEAT_RE.match(line)
        if heartbeat_match:
            heartbeats.append(Heartbeat(line=line_no, elapsed=float(heartbeat_match.group(1))))
            continue

        resume_match = _RESUME_RECOVER_RE.match(line)
        if resume_match:
            resume_batches = int(resume_match.group(1))
            continue

        terminal_match = _TERMINAL_RE.match(line)
        if terminal_match:
            terminal = terminal_match.group(1)
            _close_phase(line_no)
            _close_batch(line_no)
            continue

        phase_match = _PHASE_RE.match(line)
        if phase_match:
            _close_phase(line_no - 1)
            _close_batch(line_no - 1)
            if phase_match.group(1) == "admission":
                admission = GateAdmission()
                continue
            open_phase = GateEvent(kind="phase", label=phase_match.group(1), start_line=line_no, end_line=line_no)
            continue

        shard_match = _SHARD_RE.match(line)
        if shard_match:
            _close_phase(line_no - 1)
            _close_batch(line_no - 1)
            shards.append((shard_match.group(1), int(shard_match.group(2))))
            if open_phase is None or open_phase.label != "test":
                open_phase = GateEvent(kind="phase", label="test", start_line=line_no, end_line=line_no)
            continue

        batch_setup_match = _BATCH_SETUP_RE.match(line)
        if batch_setup_match:
            _close_batch(line_no - 1)
            label = _batch_label(batch_setup_match.group(1), batch_setup_match.group(2))
            open_batch = GateEvent(kind="batch", label=label, start_line=line_no, end_line=line_no)
            continue

        result_match = _BATCH_RESULT_RE.match(line)
        if result_match and open_batch is not None:
            label = _batch_label(result_match.group(1), result_match.group(2))
            if open_batch.label == label:
                open_batch = GateEvent(
                    kind=open_batch.kind,
                    label=open_batch.label,
                    start_line=open_batch.start_line,
                    end_line=line_no,
                )
            continue

        pass_match = _BATCH_PASS_RE.match(line)
        fail_match = _BATCH_FAIL_RE.match(line)
        if (pass_match or fail_match) and open_batch is not None:
            _close_batch(line_no)
            continue

        if _SHARD_PASS_RE.match(line) or _SHARD_FAIL_RE.match(line):
            _close_batch(line_no)
            continue

    _close_phase(len(lines))
    _close_batch(len(lines))

    end_time = os.path.getmtime(path) if terminal is not None else None

    return GateRun(
        path=path,
        events=events,
        total_lines=len(lines),
        start_time=start_time,
        end_time=end_time,
        heartbeats=heartbeats,
        shards=shards,
        lines=lines,
        terminal=terminal,
        resume_batches=resume_batches,
        admission=admission,
    )


def discover_gate_logs(log_dir: Path, max_historical: int = 10) -> list[Path]:
    """Return recent gate logs sorted newest-first."""
    if not log_dir.exists():
        return []
    candidates = [p for p in log_dir.iterdir() if p.is_file() and _LOG_NAME_RE.fullmatch(p.name)]
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[:max_historical]


def elapsed_at_line(run: GateRun, line: int, total_elapsed: float) -> float:
    """Return the wall-clock elapsed seconds at a log line.

    Uses recorded heartbeat samples when present; otherwise falls back to
    proportional line-position interpolation within the run duration.
    """
    if run.heartbeats:
        prev: Heartbeat | None = None
        nxt: Heartbeat | None = None
        for hb in run.heartbeats:
            if hb.line <= line:
                prev = hb
            elif nxt is None:
                nxt = hb
                break
        if prev is not None and nxt is not None:
            span = nxt.line - prev.line
            if span > 0:
                frac = (line - prev.line) / span
                return prev.elapsed + frac * (nxt.elapsed - prev.elapsed)
        if prev is not None:
            return prev.elapsed
        return 0.0
    if run.total_lines == 0:
        return 0.0
    return (line - 1) / run.total_lines * total_elapsed
