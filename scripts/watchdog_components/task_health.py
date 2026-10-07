"""Task state, duration, stall, and anomaly monitoring services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from scripts.watchdog_components.types import (
    AnomalyFindings,
    DurationFinding,
    TaskStateRecord,
    TaskTimingRecord,
)


def _read_task_deadlines(runtime: Any) -> dict[str, object]:
    return cast(dict[str, object], runtime._read_json_record(runtime.Path(runtime.TASK_DEADLINES_FILE)))


def _find_expected_duration(runtime: Any, command: str) -> int | None:
    cmd_lower = command.lower()
    if "git-push" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["git-push"])
    if "git-status" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["git-status"])
    if "ci-verdict" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["ci-verdict"])
    if "lint" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["lint"])
    if "typecheck" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["typecheck"])
    if "collect-check" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["collect-check"])
    if "test-unit" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["test-unit"])
    if "gate" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["gate"])
    if "test" in cmd_lower:
        return int(runtime.EXPECTED_DURATIONS["test-specific"])
    return None


def _load_stalled_tasks(runtime: Any) -> set[str]:
    try:
        p = runtime.Path(runtime.STALLED_TASKS_FILE)
        if not p.exists():
            return set()
        return set(p.read_text(encoding="utf-8").splitlines())
    except Exception:
        return set()


def _record_stalled(runtime: Any, task_id: str) -> None:
    already = runtime._load_stalled_tasks()
    already.add(task_id)
    runtime.Path(runtime.STALLED_TASKS_FILE).write_text("\n".join(sorted(already)) + "\n")


def kill_stalled_task(runtime: Any, pid: int) -> None:
    try:
        runtime.os.kill(pid, runtime.signal.SIGTERM)
        runtime._log(f"TASK KILL: sent SIGTERM to pid={pid}")
        runtime.time.sleep(5)
        runtime.os.kill(pid, runtime.signal.SIGKILL)
        runtime._log(f"TASK KILL: sent SIGKILL to pid={pid}")
    except ProcessLookupError:
        runtime._log(f"TASK KILL: pid={pid} already gone")
    except Exception as exc:
        runtime._log(f"TASK KILL: error killing pid={pid}: {exc}")


def _read_task_timings(runtime: Any) -> dict[str, TaskTimingRecord]:
    try:
        p = runtime.Path(runtime.TASK_TIMING_FILE)
        if not p.exists():
            return {}
        raw = runtime._read_json_record(p)
        timings: dict[str, TaskTimingRecord] = {}
        for task_name, value in raw.items():
            entry = runtime._as_record(value)
            if entry is None:
                continue
            timings[task_name] = {
                "average_duration_seconds": runtime._as_float(entry.get("average_duration_seconds")),
                "count": runtime._as_int(entry.get("count")),
            }
        return timings
    except Exception:
        return {}


def _write_task_timings(runtime: Any, data: dict[str, TaskTimingRecord]) -> None:
    runtime.Path(runtime.TASK_TIMING_FILE).write_text(runtime.json.dumps(data))


def _normalize_task_state(runtime: Any, value: object) -> TaskStateRecord | None:
    record = runtime._as_record(value)
    if record is None or "name" not in record or "started" not in record:
        return None
    state: TaskStateRecord = {
        "name": runtime._as_text(record["name"], "unknown"),
        "started": runtime._as_float(record["started"]),
    }
    if "ended" in record:
        state["ended"] = runtime._as_float(record["ended"])
    if "pid" in record:
        state["pid"] = runtime._as_int(record["pid"])
    return state


def _read_task_state(runtime: Any) -> list[TaskStateRecord]:
    try:
        p = runtime.Path(runtime.TASK_STATE_FILE)
        if not p.exists():
            return []
        data: object = runtime.json.loads(p.read_text())
        state = runtime._normalize_task_state(data)
        if state is not None and "ended" not in state:
            return [state]
        return []
    except Exception:
        return []


def _write_task_state(runtime: Any, data: list[TaskStateRecord]) -> None:
    runtime.Path(runtime.TASK_STATE_SNAPSHOT).write_text(runtime.json.dumps(data))


def _read_previous_state(runtime: Any) -> list[TaskStateRecord]:
    try:
        p = runtime.Path(runtime.TASK_STATE_SNAPSHOT)
        if not p.exists():
            return []
        data: object = runtime.json.loads(p.read_text())
        if isinstance(data, list):
            return [state for item in data if (state := runtime._normalize_task_state(item)) is not None]
        return []
    except Exception:
        return []


def _update_timing(runtime: Any, task_name: str, duration_secs: float) -> None:
    timings = runtime._read_task_timings()
    if task_name in timings:
        entry = timings[task_name]
        old_avg = entry["average_duration_seconds"]
        old_count = entry["count"]
        new_count = old_count + 1
        new_avg = (old_avg * old_count + duration_secs) / new_count
        entry["average_duration_seconds"] = new_avg
        entry["count"] = new_count
    else:
        timings[task_name] = {"average_duration_seconds": duration_secs, "count": 1}
    runtime._write_task_timings(timings)


def _flag_anomaly(runtime: Any, task_name: str, expected_secs: float, actual_secs: float) -> None:
    ratio = actual_secs / expected_secs if expected_secs > 0 else 0
    runtime._log(
        f"TASK TIMING ANOMALY: {task_name} took {actual_secs:.0f}s (expected ~{expected_secs:.0f}s, {ratio:.1f}x)"
    )
    directive_p = runtime.Path("/tmp/gludd-continue.txt")
    existing = ""
    if directive_p.exists():
        with runtime.suppress(Exception):
            existing = directive_p.read_text()
    directive_p.write_text(
        (
            existing + f"[{runtime._now()}] TIMING ANOMALY: {task_name} took "
            f"{actual_secs:.0f}s vs expected {expected_secs:.0f}s\n"
        ).strip()
        + "\n"
    )


def _kill_stalled_task(runtime: Any, task_name: str, pid: int | None) -> None:
    if pid is not None:
        try:
            runtime.os.kill(pid, runtime.signal.SIGTERM)
            runtime._log(f"KILLED STALLED TASK: {task_name} pid={pid} (SIGTERM)")
            runtime.time.sleep(2)
            runtime.os.kill(pid, runtime.signal.SIGKILL)
            runtime._log(f"KILLED STALLED TASK: {task_name} pid={pid} (SIGKILL)")
        except ProcessLookupError:
            runtime._log(f"STALLED TASK: {task_name} pid={pid} already exited")
        except Exception as exc:
            runtime._log(f"STALLED TASK: {task_name} pid={pid} kill error: {exc}")
    else:
        runtime._log(f"STALLED TASK KILLED: {task_name} (no pid available for kill)")


def check_task_timings(runtime: Any) -> None:
    running = runtime._read_task_state()
    previous = runtime._read_previous_state()
    now = runtime.time.time()
    prev_names = {t.get("name") for t in previous}
    curr_names = {t.get("name") for t in running}
    completed_names = prev_names - curr_names
    for task in previous:
        name = task.get("name")
        if name in completed_names:
            started = task.get("started", 0.0)
            if started > 0:
                duration = now - started
                runtime._update_timing(name or "unknown", duration)
    for task in running:
        name = task.get("name", "unknown")
        started = task.get("started", 0.0)
        pid = task.get("pid")
        if started <= 0:
            continue
        elapsed = now - started
        if elapsed > runtime.TASK_STALL_TIMEOUT:
            runtime._kill_stalled_task(name, pid)
            runtime._log(f"STALLED TASK KILLED: {name} running {elapsed:.0f}s")
        timings = runtime._read_task_timings()
        if name in timings:
            avg = timings[name]["average_duration_seconds"]
            if avg > 0 and elapsed > avg * runtime.ANOMALY_MULTIPLIER:
                runtime._flag_anomaly(name, avg, elapsed)
    runtime._write_task_state(running)


def check_agent_stalled(runtime: Any, stop_state_path: Path | None = None, false_done_path: Path | None = None) -> bool:
    sp = stop_state_path or runtime.Path(runtime.STOP_STATE)
    fp = false_done_path or runtime.Path(runtime.FALSE_DONE_BLOCKS)
    try:
        if sp.exists():
            data = runtime.json.loads(sp.read_text())
            if data.get("hasPendingWork"):
                return True
    except Exception:
        pass
    try:
        if fp.exists():
            data = runtime.json.loads(fp.read_text())
            if int(data.get("consecutive", 0)) > 0:
                return True
    except Exception:
        pass
    return False


def _guess_task_type(runtime: Any, task_id: str) -> str:
    tid = task_id.lower()
    if "push" in tid:
        return "git-push"
    if "test" in tid:
        return "test"
    if "commit" in tid:
        return "commit"
    if "gate" in tid:
        return "gate"
    return "general"


def _expected_duration(runtime: Any, task_id: str) -> int:
    return int(runtime.EXPECTED_DURATIONS.get(runtime._guess_task_type(task_id), 300))


def _read_anomaly_count(runtime: Any) -> int:
    try:
        p = runtime.Path(runtime.ANOMALY_COUNT_FILE)
        if not p.exists():
            return 0
        data = runtime.json.loads(p.read_text())
        return int(data.get("count", 0))
    except Exception:
        return 0


def _write_anomaly_count(runtime: Any, count: int) -> None:
    runtime.Path(runtime.ANOMALY_COUNT_FILE).write_text(runtime.json.dumps({"count": count}))


def _increment_anomaly_count(runtime: Any, key: str | None = None, counts: dict[str, int] | None = None) -> int:
    if key is not None:
        if counts is None:
            counts = runtime._read_anomaly_counts()
        new_val = counts.get(key, 0) + 1
        counts[key] = new_val
        runtime.Path(runtime.ANOMALY_COUNT_FILE).write_text(runtime.json.dumps(counts))
        return new_val
    new_count = runtime._read_anomaly_count() + 1
    runtime._write_anomaly_count(new_count)
    return int(new_count)


def _gate_pid_elapsed_seconds(runtime: Any) -> float | None:
    try:
        if not runtime.GATE_PID_FILE.exists():
            return None
        mtime = runtime.GATE_PID_FILE.stat().st_mtime
        return float(runtime.time.time() - mtime)
    except Exception:
        return None


def _detect_task_type(runtime: Any, task_id: str, tasks_dir: Path | None = None) -> str:
    tid = task_id.lower()
    if any(kw in tid for kw in ("gate", "marshal", "build")):
        return "gate"
    if any(kw in tid for kw in ("test", "pytest", "collect-check")):
        return "test"
    if any(kw in tid for kw in ("research", "read", "audit", "review", "explore", "find", "scan")):
        return "research"
    if any(kw in tid for kw in ("push", "ship")):
        return "push"
    return "default"


def check_task_anomalies(runtime: Any) -> AnomalyFindings:
    """Read task deadlines, detect duration anomalies against EXPECTED_DURATIONS.

    Supports two file formats:
      - {task_id: epoch_ms}  (plugin format, command deduced from task_id)
      - {task_id: {start_ts, command, pid}}  (enforce-deadline format)

    Thresholds: >2x expected = ANOMALY, >5x expected = STALLED.
    Returns findings dict for integration by check_and_reset().
    """
    findings: AnomalyFindings = {"tasks": [], "anomalies": [], "stalled": [], "ts": runtime._now()}
    dl_path = runtime.Path(runtime.TASK_DEADLINES_FILE)
    if dl_path.exists():
        try:
            decoded: object = runtime.json.loads(dl_path.read_text())
            raw = runtime._as_record(decoded)
            if raw is not None:
                now_epoch = runtime.time.time()
                stalled_set = runtime._load_stalled_tasks()
                for task_id, value in raw.items():
                    if isinstance(value, (int, float)):
                        start_ts = float(value / 1000.0 if value > 100000000000.0 else value)
                        command = ""
                    elif (details := runtime._as_record(value)) is not None:
                        start_ts = runtime._as_float(details.get("start_ts"))
                        if not start_ts:
                            continue
                        command = runtime._as_text(details.get("command"))
                    else:
                        continue
                    elapsed = now_epoch - start_ts
                    if command:
                        expected = runtime._find_expected_duration(command)
                    else:
                        task_type = runtime._detect_task_type(task_id)
                        expected = runtime.EXPECTED_DURATIONS.get(task_type, runtime.EXPECTED_DURATIONS["default"])
                    if expected is None:
                        continue
                    entry: DurationFinding = {
                        "task_id": task_id,
                        "elapsed_s": round(elapsed, 1),
                        "expected_s": expected,
                    }
                    findings["tasks"].append(entry)
                    if elapsed > expected * 5:
                        findings["stalled"].append(entry)
                        if task_id not in stalled_set:
                            runtime._log(
                                f"TASK STALLED: {task_id} ({command}) running {elapsed:.0f}s (expected {expected}s)"
                            )
                            runtime._record_stalled(task_id)
                    elif elapsed > expected * 2:
                        findings["anomalies"].append(entry)
                        if task_id not in runtime._alerted_anomalies:
                            runtime._log(
                                f"TASK ANOMALY: {task_id} ({command}) running {elapsed:.0f}s (expected {expected}s)"
                            )
                            runtime._alerted_anomalies[task_id] = now_epoch
                with runtime.suppress(Exception):
                    runtime.Path(runtime.EX_ANOMALIES_FILE).write_text(runtime.json.dumps(findings, indent=2))
        except Exception:
            pass
    try:
        gp = runtime.GATE_PID_FILE
        if gp.exists():
            gate_elapsed = runtime.time.time() - gp.stat().st_mtime
            if gate_elapsed > 45 * 60:
                runtime._log(f"GATE STALLED: background gate running {gate_elapsed:.0f}s (>45min)")
                findings.setdefault("stalled", []).append(
                    {"task_id": "gate-process", "elapsed_s": round(gate_elapsed, 1), "expected_s": 2700, "type": "gate"}
                )
    except Exception:
        pass
    for t in findings.get("tasks", []):
        task_id = t.get("task_id", "")
        elapsed_s = t.get("elapsed_s", 0.0)
        if "push" in task_id.lower() and elapsed_s > 60:
            runtime._log(f"PUSH STALLED — possible network issue: {task_id} elapsed={elapsed_s:.0f}s")
    for a in findings.get("anomalies", []):
        task_id = a.get("task_id", "unknown")
        cnt = runtime._increment_anomaly_count(f"anomaly:{task_id}")
        if cnt >= runtime.ANOMALY_ESCALATE_THRESHOLD:
            runtime._log(
                f"ANOMALY ESCALATION: {task_id} anomaly {cnt}x (threshold={runtime.ANOMALY_ESCALATE_THRESHOLD})"
            )
            findings["escalated"] = True
    for s in findings.get("stalled", []):
        task_id = s.get("task_id", "unknown")
        cnt = runtime._increment_anomaly_count(f"stalled:{task_id}")
        if cnt >= runtime.ANOMALY_ESCALATE_THRESHOLD:
            runtime._log(
                f"ANOMALY ESCALATION: {task_id} stalled {cnt}x (threshold={runtime.ANOMALY_ESCALATE_THRESHOLD})"
            )
            findings["escalated"] = True
    return findings
