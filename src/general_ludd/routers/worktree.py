from __future__ import annotations

from fastapi import FastAPI, HTTPException

from general_ludd.daemon import _get_or_create_extended_subsystems
from general_ludd.worktree import WorktreeMonitor, WorktreeMonitorConfig

# DoS cap: a caller-supplied comma-separated watch_paths string is split and
# iterated unbounded. Reject early when the split yields more paths than this.
_MAX_WATCH_PATHS = 100


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/admin/worktree/scan")
    async def admin_worktree_scan(
        watch_paths: str | None = None,
    ) -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        monitor_obj = ext.get("worktree_monitor")
        monitor = monitor_obj if isinstance(monitor_obj, WorktreeMonitor) else None
        if monitor is None:
            paths = watch_paths.split(",") if watch_paths else []
            if len(paths) > _MAX_WATCH_PATHS:
                raise HTTPException(
                    status_code=413,
                    detail="watch_paths exceeds maximum allowed count",
                )
            cfg = WorktreeMonitorConfig(
                watch_paths=paths,
                abandoned_after_hours=0,
            )
            monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        return {
            "todos": todos,
            "tracked_count": len(monitor.tracked_worktrees),
        }

    @app.get("/admin/worktree/status")
    async def admin_worktree_status() -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        monitor_obj = ext.get("worktree_monitor")
        monitor = monitor_obj if isinstance(monitor_obj, WorktreeMonitor) else None
        if monitor is None:
            return {"tracked_worktrees": [], "tracked_count": 0}
        tracked: list[dict[str, object]] = [
            {
                "path": wt.path,
                "todo_id": wt.todo_id,
                "has_agents_md": wt.agents_md is not None,
                "last_scanned": wt.last_scanned.isoformat() if wt.last_scanned else None,
                "last_activity": wt.last_activity.isoformat() if wt.last_activity else None,
            }
            for wt in monitor.tracked_worktrees.values()
        ]
        return {
            "tracked_worktrees": tracked,
            "tracked_count": len(tracked),
        }
