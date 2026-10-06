"""Continuation, liveness, and reset orchestration for one watchdog cycle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from scripts.watchdog_components.types import ContinueDirective

SecretsCheck = Callable[[], dict[str, object] | None]


def _check_under_floor_dispatch(runtime: Any) -> None:
    state = runtime._read_multitask_state()
    if not state:
        return
    dispatch_count = runtime._as_int(state.get("thisMessageDispatches"))
    zero_streak = runtime._as_int(state.get("zeroStreak"))
    estimated_in_flight = runtime._as_int(state.get("estimatedInFlight"))
    if dispatch_count >= 10:
        if zero_streak > 0:
            runtime._log(
                f"DISPATCH OK: {dispatch_count} dispatches this wave, "
                f"{estimated_in_flight} estimated in flight — floor satisfied"
            )
        return
    if not runtime._pending_work_exists():
        return
    pipeline_dry = estimated_in_flight <= 2
    if dispatch_count > 0 and dispatch_count < 10:
        runtime._log(
            f"UNDER-FLOOR DETECTED: only {dispatch_count} dispatches this wave "
            f"(floor=10, zero_streak={zero_streak}, in_flight={estimated_in_flight})"
        )
        directive = (
            f"[{runtime._now()}] UNDER-FLOOR DETECTED: only {dispatch_count} "
            "dispatch(es) in current wave.\nFloor is 10. pending work exists. "
            f"Dispatch {10 - dispatch_count} more subagents NOW.\n"
            f"zero_streak={zero_streak}, estimated_in_flight={estimated_in_flight}\n"
        )
        runtime._write_prioritized_plain_directive(directive)
    elif pipeline_dry and zero_streak > 0:
        runtime._log(
            "UNDER-FLOOR DETECTED: pipeline dry — zero dispatch "
            f"streak={zero_streak}, only {estimated_in_flight} estimated in flight "
            "(floor=10)"
        )
        directive = (
            f"[{runtime._now()}] UNDER-FLOOR DETECTED: zero dispatch "
            f"streak={zero_streak}.\nEstimated in flight: {estimated_in_flight}. "
            "Floor is 10. pending work exists.\n"
            "DISPATCH A FULL WAVE OF 10 SUBAGENTS NOW.\n"
        )
        runtime._write_prioritized_plain_directive(directive)


def _auto_reengage_enforcement(runtime: Any, mtime_age: float | None) -> None:
    """Auto-re-engage enforcement after push completes when disengage is active.

    Called every poll cycle from check_and_reset(). Re-engages under three rules:
    1. Push completed + agent active + CI green → re-engage immediately.
    2. Disengage >2 min + agent active (<60s mtime) → re-engage regardless of push.
    3. Hard cap: >5 min → re-engage regardless of agent state.
    Also reads block-counter.json directly so a stale disengage file alone does
    not block re-engagement.
    """
    block_disengage_active = False
    block_file_age_s = 0.0
    try:
        bp = runtime.Path(runtime.BLOCK_COUNTER_FILE)
        if bp.exists():
            block_data = runtime._read_json_record(bp)
            du = runtime._as_float(block_data.get("disengageUntil"))
            if du > runtime.time.time() * 1000:
                block_disengage_active = True
            block_file_age_s = runtime.time.time() - bp.stat().st_mtime
    except Exception:
        pass
    if not runtime._is_disengage_active() and (not block_disengage_active):
        return
    p = runtime.Path(runtime.DISENGAGE_FILE)
    try:
        file_age_ms = (runtime.time.time() - p.stat().st_mtime) * 1000 if p.exists() else 0
    except Exception:
        file_age_ms = 0
    effective_age_ms = file_age_ms if file_age_ms > 0 else block_file_age_s * 1000
    ci_pending, ci_run_id = runtime._ci_is_pending_or_red()
    agent_active = mtime_age is not None and mtime_age < runtime.AUTO_REENGAGE_AGENT_ACTIVE_SECS
    push_running = runtime._is_push_running()
    should_reengage = False
    reason = ""
    if not push_running and agent_active:
        rc = runtime._ci_is_pending_or_red()
        if not rc[0]:
            should_reengage = True
            reason = "push completed, CI green, agent active"
        elif effective_age_ms > runtime.DISENGAGE_MAX_SECS_CI_NOT_GREEN * 1000:
            should_reengage = True
            reason = f"push completed, disengage capped at {runtime.DISENGAGE_MAX_SECS_CI_NOT_GREEN}s (CI pending/red)"
    if not should_reengage and agent_active and (effective_age_ms > runtime.AUTO_REENGAGE_DISENGAGE_AGE_SECS * 1000):
        should_reengage = True
        ci_state = "pending/red" if ci_pending else "green"
        push_state = "running" if push_running else "done"
        reason = (
            f"disengage >{runtime.AUTO_REENGAGE_DISENGAGE_AGE_SECS}s, "
            f"agent active (mtime_age={mtime_age:.0f}s, ci={ci_state}, "
            f"push={push_state})"
        )
    if not should_reengage and effective_age_ms > runtime.DISENGAGE_MAX_SECS_CI_NOT_GREEN * 1000:
        should_reengage = True
        ci_state = "pending/red" if ci_pending else "green"
        agent_state = "active" if agent_active else "idle"
        reason = f"disengage_cap: {runtime.DISENGAGE_MAX_SECS_CI_NOT_GREEN}s max (ci={ci_state}, agent={agent_state})"
    if not should_reengage:
        return
    with runtime.suppress(Exception):
        runtime.Path(runtime.BLOCK_COUNTER_FILE).write_text(
            runtime.json.dumps({"consecutiveBlocks": 0, "totalBlocks": 0, "lastBlockTs": 0, "disengageUntil": 0})
        )
    try:
        if p.exists():
            p.unlink(missing_ok=True)
    except Exception:
        pass
    runtime._log(f"watchdog: auto-re-engaged enforcement — {reason}")
    if ci_pending:
        runtime._log(f"watchdog: CI still pending (run {ci_run_id}) — enforcement re-engaged; agent must fix CI")


def _check_plugin_hashes(runtime: Any) -> None:
    """Run check_plugin_hashes.py --quiet to detect stale plugin code.

    Called every 100 watchdog cycles (~17 min). If plugin .ts files have been
    modified since the last manifest write, the script writes the disengage
    signal — the same effect as `make disengage-enforcement`.
    """
    try:
        manifest = runtime._WORKSPACE / ".opencode" / "plugin-hashes.json"
        plugin_dir = runtime._WORKSPACE / ".opencode" / "plugin"
        current = {}
        if plugin_dir.is_dir():
            for f in sorted(plugin_dir.glob("*.ts")):
                with runtime.suppress(Exception):
                    current[f.name] = runtime.hashlib.sha256(f.read_bytes()).hexdigest()
        plugins_dir = runtime._WORKSPACE / ".opencode" / "plugins"
        if plugins_dir.is_dir():
            for f in sorted(plugins_dir.glob("*.ts")):
                with runtime.suppress(Exception):
                    current[f"plugins/{f.name}"] = runtime.hashlib.sha256(f.read_bytes()).hexdigest()
        stored = {}
        if manifest.is_file():
            try:
                data = runtime.json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    stored = {k: v for k, v in data.items() if isinstance(v, str)}
            except Exception:
                pass
        if not current:
            return
        if not stored:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(runtime.json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return
        if current == stored:
            return
        changed = [f for f in set(current) & set(stored) if current[f] != stored[f]]
        new_f = sorted(set(current) - set(stored))
        removed = sorted(set(stored) - set(current))
        details = []
        if new_f:
            details.append(f"new: {', '.join(new_f)}")
        if removed:
            details.append(f"removed: {', '.join(removed)}")
        if changed:
            details.append(f"changed: {', '.join(changed)}")
        reason = " | ".join(details) if details else "plugin hashes changed"
        runtime._write_disengage_signal(minutes=60, reason=f"plugin_version_mismatch: {reason}")
        with runtime.suppress(Exception):
            runtime.Path(runtime.BLOCK_COUNTER_FILE).write_text(
                runtime.json.dumps(
                    {"consecutiveBlocks": 0, "totalBlocks": 0, "lastBlockTs": 0, "disengageUntil": 9999999999999}
                )
            )
        runtime._log(f"PLUGIN VERSION CHANGED: {reason} — disengage signal written")
    except Exception:
        pass


def _write_continue_directive(
    runtime: Any,
    work_sources: list[str],
    stop_count: int,
    tasks_unchecked: bool,
    ratchet_count: int,
    gate_red: bool,
    ci_pending: bool,
    ci_run_id: str | None = None,
    work_hint: str = "",
    extra_message: str = "",
) -> None:
    """Write the continue directive to BOTH JSON (for plugins) and plain-text (for visibility)."""
    directive = runtime._build_continue_directive(
        work_sources=work_sources,
        stop_count=stop_count,
        tasks_unchecked=tasks_unchecked,
        ratchet_count=ratchet_count,
        gate_red=gate_red,
        ci_pending=ci_pending,
        ci_run_id=ci_run_id,
        work_hint=work_hint,
        extra_message=extra_message,
    )
    try:
        runtime.Path(runtime.CONTINUE_DIRECTIVE).write_text(runtime.json.dumps(directive, indent=2))
        runtime._log(f"directive written to {runtime.CONTINUE_DIRECTIVE} (stop_count={stop_count})")
    except Exception as e:
        runtime._log(f"ERROR writing JSON directive: {e}")
    pending_items_str = "\n".join(f"  - {item}" for item in directive["pending_items"])
    txt = (
        "\n======================================================================\n"
        "⛔⛔⛔ WATCHDOG CONTINUE DIRECTIVE ⛔⛔⛔\n"
        "======================================================================\n"
        f"ACTION: {directive['action']}\n"
        f"STOP COUNT: {stop_count} "
        f"(escalation threshold: {runtime.STOP_ESCALATE_THRESHOLD})\n"
        f"REQUIRED TOOL: {directive['required_tool']}\n"
        f"SOURCE: {directive['source']}\n"
        f"TS: {directive['ts']}\n"
        "----------------------------------------------------------------------\n"
        f"PENDING WORK:\n{pending_items_str}\n"
        "----------------------------------------------------------------------\n"
        f"MESSAGE:\n  {directive['message']}\n"
    )
    if extra_message:
        txt += (
            f"\n----------------------------------------------------------------------\nESCALATION: {extra_message}\n"
        )
    txt += (
        "======================================================================\n"
        "YOU MUST DISPATCH SUBAGENTS NOW. DO NOT SEND TEXT-ONLY RESPONSES.\n"
        "======================================================================\n"
    )
    try:
        if runtime._write_prioritized_plain_directive(txt):
            runtime._log(f"loud directive written to {runtime.PURE_IDLE_DIRECTIVE}")
    except Exception as e:
        runtime._log(f"ERROR writing plain-text directive: {e}")


def _write_disengage_signal(runtime: Any, minutes: int = 5, reason: str = "") -> None:
    try:
        disengage_until = int(runtime.time.time() * 1000 + minutes * 60 * 1000)
        runtime.Path(runtime.DISENGAGE_FILE).write_text(
            runtime.json.dumps(
                {
                    "disengage_until": disengage_until,
                    "disengage_until_epoch_ms": disengage_until,
                    "reason": reason,
                    "ts": runtime._now(),
                }
            )
        )
        runtime._log(f"DISENGAGE: sent signal for {minutes}min — {reason}")
    except Exception:
        pass


def _check_force_dispatch(runtime: Any) -> bool:
    """Read /tmp/gludd-force-dispatch.json from enforce-stop.ts escalation level 3+.

    Builds specific task dispatch commands for each unchecked TASKS.md item,
    ratchet entry, and red gate.  Writes to CONTINUE_DIRECTIVE with
    action=FORCE_DISPATCH.

    Returns True if force-dispatch is active (lower idle threshold).
    """
    p = runtime.Path(runtime.FORCE_DISPATCH_FILE)
    if not p.exists():
        return False
    try:
        mtime = p.stat().st_mtime
        age = runtime.time.time() - mtime
        if age > runtime.FORCE_DISPATCH_MAX_AGE:
            p.unlink(missing_ok=True)
            return False
        data = runtime._read_json_record(p)
        level = runtime._as_int(data.get("level"), 3)
        tasks_unchecked = runtime._tasks_md_has_unchecked()
        ratchet_count = runtime._ratchet_has_entries()
        gate_red = runtime._gate_status_is_red()
        dispatch_commands: list[dict[str, object]] = []
        task_index = 1
        if tasks_unchecked and runtime._TASKS_MD.exists():
            content = runtime._TASKS_MD.read_text(encoding="utf-8")
            for line in content.splitlines():
                if runtime._UNCHECKED_PATTERN.search(line):
                    item_text = line.strip()
                    dispatch_commands.append(
                        {
                            "index": task_index,
                            "task_item": item_text,
                            "tool": "task",
                            "command": f"dispatch subagent: {item_text}",
                        }
                    )
                    task_index += 1
        if ratchet_count > 0:
            dispatch_commands.append(
                {
                    "index": task_index,
                    "task_item": f"ratchet: {ratchet_count} entries",
                    "tool": "task",
                    "command": f"dispatch subagents to fix {ratchet_count} ratchet entries",
                }
            )
        if gate_red:
            dispatch_commands.append(
                {
                    "index": task_index + 1,
                    "task_item": "gate: red — fix failures",
                    "tool": "task",
                    "command": "dispatch subagent to investigate and fix red gate",
                }
            )
        if dispatch_commands:
            directive = {
                "action": "FORCE_DISPATCH",
                "level": level,
                "dispatch_count": len(dispatch_commands),
                "dispatch_commands": dispatch_commands,
                "message": (
                    f"FORCE DISPATCH (level {level}): Dispatch "
                    f"{len(dispatch_commands)} subagents NOW. "
                    "Do NOT send text-only responses."
                ),
                "ts": runtime._now(),
            }
            runtime.Path(runtime.CONTINUE_DIRECTIVE).write_text(runtime.json.dumps(directive, indent=2))
            runtime._log(f"FORCE DISPATCH: level={level}, {len(dispatch_commands)} commands written")
        else:
            p.unlink(missing_ok=True)
            runtime._log("FORCE DISPATCH: flag cleared — no pending work found")
        return bool(dispatch_commands)
    except Exception as e:
        runtime._log(f"FORCE DISPATCH: error processing flag: {e}")
        return False


def _check_plugin_liveness_on_startup(runtime: Any) -> None:
    """Run the plugin liveness check once at startup and log the result.

    Skips the check if it was already run within LIVENESS_STARTUP_BACKOFF_SECS
    (file-based backoff persists across watchdog restarts).
    """
    if runtime._liveness_startup_in_backoff():
        runtime._log(
            "plugin-liveness: backoff active — skipping startup check "
            f"(last check <{runtime.LIVENESS_STARTUP_BACKOFF_SECS}s ago)"
        )
        runtime._last_liveness_check = runtime.time.time()
        return
    runtime._log("plugin-liveness: running startup check...")
    try:
        result = runtime.subprocess.run(
            ["make", "check-plugin-liveness"], capture_output=True, text=True, timeout=30, cwd=str(runtime._WORKSPACE)
        )
        if result.returncode == 0:
            runtime._log("plugin-liveness: PASSED — enforce-stop.ts structurally intact and firing")
        else:
            runtime._log(
                f"plugin-liveness: FAILED (exit={result.returncode}) — enforce-stop.ts may be dead or silently disabled"
            )
            runtime._log(f"  stderr: {result.stderr.strip()[:300]}")
        runtime._last_liveness_check = runtime.time.time()
        runtime._liveness_write_backoff_ts()
    except runtime.subprocess.TimeoutExpired:
        runtime._log("plugin-liveness: TIMEOUT — check took >30s")
        runtime._liveness_write_backoff_ts()
    except Exception as e:
        runtime._log(f"plugin-liveness: ERROR running check: {e}")
        runtime._liveness_write_backoff_ts()


def _liveness_write_backoff_ts(runtime: Any) -> None:
    with runtime.suppress(Exception):
        runtime.Path(runtime.LIVENESS_STARTUP_BACKOFF_FILE).write_text(
            runtime.json.dumps({"last_check_ts": runtime.time.time()})
        )


def _is_push_running(runtime: Any) -> bool:
    push_lock = runtime._WORKSPACE / ".git" / "push.lock"
    if push_lock.exists():
        return True
    try:
        result = runtime.subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "git push" in line and "grep" not in line and ("ps -eo" not in line):
                return True
    except Exception:
        pass
    return False


def _is_force_dispatch_active(runtime: Any) -> bool:
    p = runtime.Path(runtime.FORCE_DISPATCH_FILE)
    if not p.exists():
        return False
    try:
        age = runtime.time.time() - p.stat().st_mtime
        return bool(age <= runtime.FORCE_DISPATCH_MAX_AGE)
    except Exception:
        return False


def _liveness_startup_in_backoff(runtime: Any) -> bool:
    """Check if startup liveness check should be skipped due to recent run.

    File-based backoff persists across watchdog restarts. If liveness was
    checked in the last LIVENESS_STARTUP_BACKOFF_SECS, skip the check to
    prevent a tight crash-restart loop from hammering make check-plugin-liveness.
    """
    try:
        p = runtime.Path(runtime.LIVENESS_STARTUP_BACKOFF_FILE)
        if p.exists():
            data = runtime.json.loads(p.read_text())
            last_ts = float(data.get("last_check_ts", 0))
            if runtime.time.time() - last_ts < runtime.LIVENESS_STARTUP_BACKOFF_SECS:
                return True
    except Exception:
        pass
    return False


def _clear_disengage_signal(runtime: Any) -> None:
    try:
        p = runtime.Path(runtime.DISENGAGE_FILE)
        if p.exists():
            data = runtime.json.loads(p.read_text())
            if data.get("disengage_until", 0) < runtime.time.time() * 1000:
                p.unlink()
    except Exception:
        pass


def _build_continue_directive(
    runtime: Any,
    work_sources: list[str],
    stop_count: int,
    tasks_unchecked: bool,
    ratchet_count: int,
    gate_red: bool,
    ci_pending: bool,
    ci_run_id: str | None = None,
    work_hint: str = "",
    extra_message: str = "",
) -> ContinueDirective:
    pending_items: list[str] = []
    if tasks_unchecked:
        pending_items.append("TASKS.md has unchecked items")
    if ratchet_count > 0:
        pending_items.append(f"{ratchet_count} ratchet entries")
    if gate_red:
        pending_items.append(".gate-status is red")
    if ci_pending:
        suffix = f" (run {ci_run_id})" if ci_run_id else ""
        pending_items.append(f"CI pending{suffix}")
    dispatch_commands: list[dict[str, object]] = []
    task_index = 1
    if tasks_unchecked and runtime._TASKS_MD.exists():
        try:
            content = runtime._TASKS_MD.read_text(encoding="utf-8")
            for line in content.splitlines():
                if runtime._UNCHECKED_PATTERN.search(line):
                    item_text = line.strip()
                    dispatch_commands.append(
                        {
                            "index": task_index,
                            "task_item": item_text,
                            "tool": "task",
                            "command": f"dispatch subagent: {item_text}",
                        }
                    )
                    task_index += 1
        except Exception:
            pass
    if ratchet_count > 0:
        dispatch_commands.append(
            {
                "index": task_index,
                "task_item": f"ratchet: {ratchet_count} entries",
                "tool": "task",
                "command": f"dispatch subagents to fix {ratchet_count} ratchet entries",
            }
        )
        task_index += 1
    if gate_red:
        dispatch_commands.append(
            {
                "index": task_index,
                "task_item": "gate: red — fix failures",
                "tool": "task",
                "command": "dispatch subagent to investigate and fix red gate",
            }
        )
        task_index += 1
    msg_parts = [f"FORCE DISPATCH: {len(dispatch_commands)} specific tasks below. Dispatch ALL of them NOW."]
    if work_hint.strip():
        msg_parts.append(work_hint.strip())
    if extra_message.strip():
        msg_parts.append(extra_message.strip())
    return {
        "action": "FORCE_DISPATCH",
        "pending_items": pending_items,
        "required_tool": "task",
        "dispatch_count": len(dispatch_commands),
        "dispatch_commands": dispatch_commands,
        "message": " ".join(msg_parts),
        "stop_count": stop_count,
        "source": ", ".join(work_sources) if work_sources else "unknown",
        "ts": runtime._now(),
    }


def _is_disengage_active(runtime: Any) -> bool:
    try:
        p = runtime.Path(runtime.DISENGAGE_FILE)
        if not p.exists():
            return False
        data = runtime._read_json_record(p)
        return bool(runtime._as_float(data.get("disengage_until")) > runtime.time.time() * 1000)
    except Exception:
        return False


def _read_multitask_state(runtime: Any) -> dict[str, object]:
    try:
        p = runtime.Path(runtime.MULTITASK_STATE_FILE)
        if not p.exists():
            return {}
        return cast(dict[str, object], runtime._read_json_record(p))
    except Exception:
        return {}


def _write_orchestrator_state(
    runtime: Any,
    tasks_unchecked: bool,
    ratchet_count: int,
    gate_red: bool,
    ci_pending: bool,
    repo_pending: bool,
    agent_active: bool,
    ci_run_id: str | None = None,
    stop_detected: bool = False,
) -> None:
    try:
        health = runtime._compute_health_score(
            tasks_unchecked, ratchet_count, gate_red, ci_pending, repo_pending, agent_active
        )
        ci_loop = runtime._detect_ci_loop()
        ci_stall = runtime._detect_ci_true_stall()
        state = {
            "ts": runtime._now(),
            "epoch": runtime.time.time(),
            "health_score": health,
            "tasks_md_unchecked": tasks_unchecked,
            "ratchet_entries": ratchet_count,
            "gate_status_red": gate_red,
            "ci_pending_or_red": ci_pending,
            "ci_run_id": ci_run_id,
            "repo_pending": repo_pending,
            "agent_active": agent_active,
            "ci_loop_detected": ci_loop,
            "ci_true_stall": ci_stall,
            "stop_detected": stop_detected,
        }
        runtime.Path(runtime.ORCHESTRATOR_STATE_FILE).write_text(runtime.json.dumps(state, indent=2))
        runtime.Path(runtime.HEALTH_SCORE_FILE).write_text(runtime.json.dumps({"score": health, "ts": runtime._now()}))
    except Exception:
        pass


def _check_plugin_liveness_periodic(runtime: Any) -> None:
    """Run plugin liveness check every LIVENESS_CHECK_COOLDOWN_SECS."""
    now = runtime.time.time()
    if now - runtime._last_liveness_check < runtime.LIVENESS_CHECK_COOLDOWN_SECS:
        return
    try:
        result = runtime.subprocess.run(
            ["make", "check-plugin-liveness"], capture_output=True, text=True, timeout=30, cwd=str(runtime._WORKSPACE)
        )
        if result.returncode != 0:
            runtime._log(f"plugin-liveness: periodic check FAILED (exit={result.returncode})")
    except Exception:
        pass
    runtime._last_liveness_check = now


def check_and_reset(runtime: Any, *, secrets_check: SecretsCheck | None = None) -> dict[str, object]:
    """Run one watchdog cycle.

    ``secrets_check`` is an explicit dependency seam for deterministic unit
    cycles. Production callers omit it and retain the fail-closed, periodic
    repository-wide scan.
    """
    streak = runtime._read_streak()
    result: dict[str, object] = {
        "ts": runtime._now(),
        "streak": streak,
        "pending_todos": [],
        "reset_applied": False,
        "hibernating": runtime.HIBERNATION_MARKER.exists(),
        "stop_detected": False,
    }
    pending = runtime._pending_todos()
    result["pending_todos"] = pending
    reset_needed = False
    reason = ""
    tasks_unchecked = runtime._tasks_md_has_unchecked()
    ratchet_count = runtime._ratchet_has_entries()
    gate_red = runtime._gate_status_is_red()
    ci_pending, ci_run_id = runtime._ci_is_pending_or_red()
    has_pending_work = tasks_unchecked or ratchet_count > 0 or gate_red or ci_pending
    has_any_work = has_pending_work or ci_pending
    if ci_pending and ci_run_id is not None:
        try:
            stamp = runtime.datetime.now(runtime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            newline = chr(10)
            runtime._CI_STATUS.parent.mkdir(parents=True, exist_ok=True)
            runtime._CI_STATUS.write_text(
                f"=== CI {stamp} ==="
                + newline
                + f"CI FAIL pending (run {ci_run_id})"
                + newline
                + "suggested_action: wait_for_ci"
                + newline
            )
            has_pending_work = True
        except Exception:
            pass
    mtime_age = runtime._streak_mtime_age_seconds()
    with runtime.suppress(Exception):
        runtime.Path(runtime.HEARTBEAT_FILE).write_text(
            runtime.json.dumps(
                {
                    "ts": runtime._now(),
                    "epoch": runtime.time.time(),
                    "poll_cycle": runtime._POLL_CYCLE_COUNT + 1,
                    "streak": streak,
                    "mtime_age_s": round(mtime_age, 1) if mtime_age else None,
                    "has_pending_work": has_pending_work,
                    "tasks_md_unchecked": tasks_unchecked,
                    "ratchet_entries": ratchet_count,
                    "gate_status_red": gate_red,
                    "ci_pending_or_red": ci_pending,
                    "ci_run_id": ci_run_id,
                    "pending_todo_count": len(pending),
                    "stop_count": runtime._read_stop_count(),
                },
                indent=2,
            )
        )
    if mtime_age is not None and mtime_age < runtime.PURE_IDLE_SECS:
        runtime._write_watchdog_activity()
    idle_threshold = runtime.FORCE_DISPATCH_IDLE_SECS if runtime._is_force_dispatch_active() else runtime.STOP_IDLE_SECS
    if runtime.HEARTBEAT_VERBOSE and has_any_work:
        sources = []
        if tasks_unchecked:
            sources.append("TASKS.md")
        if ratchet_count > 0:
            sources.append("ratchet")
        if gate_red:
            sources.append("gate")
        if ci_pending:
            sources.append(f"CI(run={ci_run_id})")
        runtime._log(
            f"watchdog: pending work detected — sources={sources} mtime_age={mtime_age:.0f}s"
            if mtime_age
            else f"watchdog: pending work detected — sources={sources}"
        )
    if has_any_work and (streak == 0 or streak is None) and (mtime_age is not None) and (mtime_age > idle_threshold):
        reset_needed = True
        work_sources = []
        if has_pending_work:
            work_sources.append("local")
        if ci_pending:
            work_sources.append(f"CI (run {ci_run_id})")
        reason = (
            "STOP DETECTED: agent idle with pending work "
            f"({', '.join(work_sources)}) — {mtime_age:.0f}s since last tool "
            f"(threshold={idle_threshold}s)"
        )
        result["stop_detected"] = True
        runtime._log(reason)
        stop_count = runtime._increment_stop_count()
        extra_message = ""
        if stop_count >= runtime.STOP_ESCALATE_THRESHOLD:
            extra_message = f"REPEATED STOP DETECTED ({stop_count}x) — WORK OR FACE RESTART"
        work_hint = ""
        if ci_pending and (not has_pending_work):
            ci_minutes = runtime._ci_pending_for_too_long_minutes()
            if ci_minutes and ci_minutes > 10:
                work_hint = (
                    f"CI pending >{ci_minutes:.0f}min. Stop pushing new commits — "
                    "they reset CI. Work on wiring/coding gaps while waiting."
                )
            else:
                work_hint = (
                    "CI pending. Work on wiring/coding gaps while waiting. Do NOT push new commits until CI is green."
                )
        runtime._write_continue_directive(
            work_sources=work_sources,
            stop_count=stop_count,
            tasks_unchecked=tasks_unchecked,
            ratchet_count=ratchet_count,
            gate_red=gate_red,
            ci_pending=ci_pending,
            ci_run_id=ci_run_id,
            work_hint=work_hint,
            extra_message=extra_message,
        )
        sp = runtime.Path(runtime.STOP_STATE)
        if sp.exists():
            try:
                sp.unlink()
                runtime._log(f"cleared stop-state: {sp}")
            except Exception:
                pass
    elif (
        has_pending_work
        and streak is not None
        and (streak > 0)
        and (mtime_age is not None)
        and (mtime_age > runtime.STOP_IDLE_SECS * 2)
    ):
        reset_needed = True
        work_sources = ["local"]
        reason = (
            f"STOP DETECTED (grinding): agent has streak={streak} but "
            f"mtime_age={mtime_age:.0f}s > {runtime.STOP_IDLE_SECS * 2}s "
            "with pending work — likely stuck in a loop"
        )
        result["stop_detected"] = True
        runtime._log(reason)
        stop_count = runtime._increment_stop_count()
        extra_message = ""
        if stop_count >= runtime.STOP_ESCALATE_THRESHOLD:
            extra_message = f"REPEATED STOP DETECTED ({stop_count}x) — AGENT MAY BE LOOPING"
        runtime._write_continue_directive(
            work_sources=work_sources,
            stop_count=stop_count,
            tasks_unchecked=tasks_unchecked,
            ratchet_count=ratchet_count,
            gate_red=gate_red,
            ci_pending=ci_pending,
            ci_run_id=ci_run_id,
            work_hint=(
                "Agent has streak but mtime is stale — likely grinding in a loop. Dispatch subagents to break out."
            ),
            extra_message=extra_message,
        )
    elif streak is not None and streak >= runtime.STREAK_THRESHOLD:
        reset_needed = True
        reason = f"streak={streak} >= threshold={runtime.STREAK_THRESHOLD}"
    elif runtime.check_agent_stalled():
        reset_needed = True
        reason = "agent stalled on stop enforcement"
    elif pending and streak is not None and (streak > 0):
        if mtime_age is not None and mtime_age < runtime.POLL_SECS:
            reset_needed = True
            reason = "text-only response with pending todos"
    task_result = runtime.check_task_anomalies()
    if task_result["anomalies"]:
        result["task_anomalies"] = task_result["anomalies"]
    if task_result["stalled"]:
        result["task_stalled"] = task_result["stalled"]
        runtime.Path(runtime.EX_STALLED_TASKS_FILE).write_text(
            runtime.json.dumps({"ts": runtime._now(), "stalled": task_result["stalled"]}, indent=2)
        )
        runtime._log(
            f"STALLED TASK DETECTED: {len(task_result['stalled'])} task(s) — writing {runtime.EX_STALLED_TASKS_FILE}"
        )
    runtime._check_push_stalled()
    runtime._check_task_anomaly_300s()
    runtime._check_ci_pending_stall()
    if (
        ci_pending and (not has_pending_work) and (mtime_age is not None) and (mtime_age < runtime.PURE_IDLE_SECS)
    ) or has_pending_work:
        runtime._max_out_false_done()
    if not reset_needed and mtime_age is not None and (mtime_age > runtime.PURE_IDLE_SECS):
        last_flag = runtime._read_last_flag_time()
        now = runtime.time.time()
        if now - last_flag > runtime.FLAG_COOLDOWN_SECS:
            runtime._log(f"IDLE DETECTED: agent idle >{runtime.PURE_IDLE_SECS}s ({mtime_age:.0f}s since last tool)")
            runtime._write_last_flag_time(now)
            runtime._write_continue_directive(
                work_sources=["pure_idle"],
                stop_count=runtime._read_stop_count(),
                tasks_unchecked=tasks_unchecked,
                ratchet_count=ratchet_count,
                gate_red=gate_red,
                ci_pending=ci_pending,
                ci_run_id=ci_run_id,
                work_hint="",
                extra_message=f"Pure idle detected — agent silent for {mtime_age:.0f}s",
            )
            reset_needed = True
            reason = f"pure idle detected ({mtime_age:.0f}s)"
            result["stop_detected"] = True
    deadlines = runtime._read_deadlines()
    runtime._update_task_history(deadlines)
    history_anomalies = runtime._detect_history_anomalies(deadlines)
    if history_anomalies:
        result["history_anomalies"] = history_anomalies
        with runtime.suppress(Exception):
            runtime.Path(runtime.TASK_ANOMALIES_FILE).write_text(
                runtime.json.dumps({"ts": runtime._now(), "anomalies": history_anomalies}, indent=2)
            )
        for a in history_anomalies:
            rolling_info = f", rolling_avg={a['rolling_avg_s']}s" if a.get("rolling_avg_s") else ""
            runtime._log(
                f"TASK ANOMALY: task {a['id']} ({a['type']}) "
                f"{a['description']} running {a['elapsed_s']}s — "
                f"reason={a['reason']}{rolling_info}"
            )
    runtime._check_ci_stall()
    runtime._check_push_health()
    runtime.check_task_timings()
    timing_anomalies = runtime._check_timing_anomalies()
    push_anomaly = runtime._detect_stalled_push()
    if push_anomaly:
        timing_anomalies.append("git-push")
    if timing_anomalies:
        result["timing_anomalies"] = timing_anomalies
        anchored_messages: list[str] = []
        for op in timing_anomalies:
            expected = runtime.EXPECTED_DURATIONS.get(op, 300)
            timing = runtime._read_timing_data()
            actual = timing[op]["duration"] if op in timing else 0.0
            anchored_messages.append(
                f"⛔ TIMING ANOMALY: {op} running for {actual:.0f}s (expected {expected}s). Check for network issues."
            )
        try:
            existing = ""
            directive_p = runtime.Path(runtime.PURE_IDLE_DIRECTIVE)
            if directive_p.exists():
                existing = directive_p.read_text()
            directive_p.write_text(existing + "\n".join(anchored_messages) + "\n")
        except Exception:
            pass
    runtime._POLL_CYCLE_COUNT += 1
    if runtime._POLL_CYCLE_COUNT % runtime._POLL_CYCLE_PRUNE_INTERVAL == 0:
        runtime._prune_alerted_anomalies()
        runtime._check_plugin_hashes()
    if reset_needed:
        runtime._reset_streak()
        result["reset_applied"] = True
        if not result.get("stop_detected") and runtime.check_agent_stalled():
            runtime._write_continue_directive(
                work_sources=["agent_stalled"],
                stop_count=runtime._read_stop_count(),
                tasks_unchecked=tasks_unchecked,
                ratchet_count=ratchet_count,
                gate_red=gate_red,
                ci_pending=False,
                extra_message=f"agent stalled on stop enforcement, pending={len(pending)} todos",
            )
        if pending:
            runtime._log(f"UNJAMMED: {reason}, pending={len(pending)} todos: {pending[:3]}")
        else:
            runtime._log(f"UNJAMMED: {reason}, no pending todos detected but resetting anyway")
    elif not has_any_work and mtime_age is not None and (mtime_age < runtime.POLL_SECS):
        runtime._clear_stop_count()
    elif ci_pending and (not has_pending_work):
        ci_minutes = runtime._ci_pending_for_too_long_minutes()
        if ci_minutes is not None and ci_minutes > 30:
            runtime._log(f"CI STALLED: pending >30min (run {ci_run_id}) — may need investigation")
        elif ci_minutes is not None and ci_minutes > 10:
            runtime._log(f"CI NOTE: pending {ci_minutes:.0f}min (run {ci_run_id}) — stop pushing new commits")
        else:
            runtime._log(f"CI pending (run {ci_run_id}) — work locally while waiting")
    elif streak is not None:
        pass
    else:
        runtime._log("streak file missing — enforcement may not be tracking")
    if mtime_age is not None and mtime_age > 20:
        task_state_path = runtime.Path(runtime.TASK_STATE_FILE)
        if task_state_path.exists():
            try:
                tasks = runtime.json.loads(task_state_path.read_text())
                if isinstance(tasks, dict):
                    tasks = [tasks]
                if isinstance(tasks, list):
                    now = runtime.time.time()
                    for task in tasks:
                        if not isinstance(task, dict):
                            continue
                        started = task.get("started", 0)
                        name = task.get("name", "unknown")
                        pid = task.get("pid")
                        if not started:
                            continue
                        elapsed = now - started
                        if elapsed > 60:
                            runtime._log(f"STALLED TASK: {name} running {elapsed:.0f}s")
                            if pid:
                                runtime.kill_stalled_task(pid)
                            runtime._reset_streak()
                            result["reset_applied"] = True
                            result["stop_detected"] = True
            except Exception:
                pass
    ci_loop = runtime._detect_ci_loop()
    ci_true_stall = runtime._detect_ci_true_stall()
    if ci_loop:
        runtime._log(
            f"CI LOOP DETECTED: >{runtime.CI_LOOP_THRESHOLD_PUSHES} pushes in "
            f"<{runtime.CI_LOOP_THRESHOLD_MINUTES}min while CI pending. "
            "STOP PUSHING."
        )
        runtime._write_disengage_signal(minutes=10, reason="ci_loop")
    if ci_true_stall:
        runtime._log(
            f"CI TRUE STALL: pending >{runtime.CI_TRUE_STALL_MINUTES}min "
            f"with no pushes for {runtime.CI_TRUE_STALL_NO_PUSH_MINUTES}min. "
            "CI may be broken."
        )
    runtime._check_under_floor_dispatch()
    ci_red_after_tag = runtime._check_ci_red_after_tag_push()
    if ci_red_after_tag:
        result["ci_red_after_tag"] = ci_red_after_tag
    release_status = runtime._check_release_completeness()
    if release_status:
        result["release_incomplete"] = release_status
    selected_secrets_check = runtime._check_secrets_committed if secrets_check is None else secrets_check
    secrets_violation = selected_secrets_check()
    if secrets_violation:
        result["secrets_violation"] = secrets_violation
    stale_release = runtime._check_stale_release()
    if stale_release:
        result["stale_release"] = stale_release
    agent_active = mtime_age is not None and mtime_age < runtime.PURE_IDLE_SECS
    runtime._write_orchestrator_state(
        tasks_unchecked=tasks_unchecked,
        ratchet_count=ratchet_count,
        gate_red=gate_red,
        ci_pending=ci_pending,
        repo_pending=False,
        agent_active=agent_active,
        ci_run_id=ci_run_id,
        stop_detected=bool(result.get("stop_detected", False)),
    )
    runtime._clear_disengage_signal()
    runtime._auto_reengage_enforcement(mtime_age)
    return result
