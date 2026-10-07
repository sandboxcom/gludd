"""Executable watchdog command dispatch and polling lifecycle."""

from __future__ import annotations

from typing import Any


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
    if argv is None:
        argv = runtime.sys.argv[1:]
    if argv and any(a.startswith("--") or not a.startswith("-") for a in argv):
        return int(runtime._cli_classification(argv))
    lease = runtime.acquire_watchdog_lock()
    if lease is None:
        runtime._log(
            "watchdog already running for namespace "
            f"{runtime.project_namespace(runtime._WORKSPACE)}; refusing duplicate"
        )
        return 0
    try:
        runtime._log(f"watchdog started — poll={runtime.POLL_SECS}s, threshold={runtime.STREAK_THRESHOLD}")
        runtime._check_plugin_liveness_on_startup()
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
        runtime.release_watchdog_lock(lease)
