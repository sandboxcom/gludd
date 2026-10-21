"""Executable watchdog command dispatch and polling lifecycle."""

from __future__ import annotations

from types import FrameType
from typing import Any

from . import lifecycle


def _cli_classification(runtime: Any, argv: list[str]) -> int:
    """Handle --once, --count-stalled, --list-stalled, --all flags."""
    tasks_dir = runtime.Path(argv[0]) if argv and (not argv[0].startswith("--")) else runtime.Path("/tmp/gludd-tasks")
    results = runtime.scan_tasks_dir(tasks_dir)
    if "--stop" in argv:
        stopped = runtime.stop_watchdog()
        print("watchdog stop requested" if stopped else "no watchdog owner")
        return 0
    if "--once" in argv:
        result = runtime.check_and_reset()
        print(runtime.json.dumps(result, indent=2))
        return 0
    if "--count-stalled" in argv:
        count = sum((1 for _, s, _ in results if s == runtime.State.LIKELY_STALLED_INCOMPLETE))
        print(count)
        return 0
    if "--list-stalled" in argv:
        for name, state, _reason in results:
            if state == runtime.State.LIKELY_STALLED_INCOMPLETE:
                print(f"{name}  {state.value}")
        return 0
    if "--all" in argv:
        for name, state, reason in results:
            print(f"{name}  {state.value}  ({reason})")
        return 0
    return 0


def main(runtime: Any, argv: list[str] | None = None) -> int:
    """Defer shutdown through lease acquisition, then run the owned watchdog."""
    if argv is None:
        argv = runtime.sys.argv[1:]
    if any(arg.split("=", 1)[0] in {"--start", "--stop", "--validate-only"} for arg in argv):
        return lifecycle.dispatch(runtime, argv)
    if argv and any(a.startswith("--") or not a.startswith("-") for a in argv):
        return int(runtime._cli_classification(argv))
    pointer = None
    lease = None
    previous_handler = None
    termination_requested = False

    def defer_termination(_signum: int, _frame: FrameType | None) -> None:
        """Retain shutdown until acquisition returns into the cleanup boundary."""
        nonlocal termination_requested
        termination_requested = True

    try:
        previous_handler = runtime.signal.signal(runtime.signal.SIGTERM, defer_termination)
        lease = runtime.acquire_watchdog_lock()
        if lease is None:
            runtime._log(
                "watchdog already running for namespace "
                f"{runtime.project_namespace(runtime._WORKSPACE)}; refusing duplicate"
            )
            return 0
        lifecycle.install_termination_handler()
        if termination_requested:
            return 0
        runtime._log(f"watchdog started — poll={runtime.POLL_SECS}s, threshold={runtime.STREAK_THRESHOLD}")
        runtime._check_plugin_liveness_on_startup()
        pointer = lifecycle.configured_pointer(runtime)
        lifecycle.publish_ready(runtime, lease)
        while True:
            if runtime.HIBERNATION_MARKER.exists():
                runtime._log("hibernation marker present — sleeping")
                runtime.time.sleep(runtime.POLL_SECS)
                continue
            try:
                runtime.check_and_reset()
                runtime._check_force_dispatch()
                runtime.check_running_tasks()
                runtime.check_push_status()
                runtime._check_gate_background()
                runtime._check_load_average()
                runtime._check_plugin_liveness_periodic()
                runtime._rotate_watchdog_logs()
            except Exception as exc:
                runtime._log(f"error: {exc}")
            runtime.time.sleep(runtime.POLL_SECS)
    finally:
        try:
            lifecycle.release_pointer(pointer)
        finally:
            try:
                runtime.release_watchdog_lock(lease)
            finally:
                if previous_handler is not None:
                    runtime.signal.signal(runtime.signal.SIGTERM, previous_handler)
