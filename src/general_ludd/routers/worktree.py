from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from general_ludd.daemon import _get_or_create_extended_subsystems


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/worktree/scan")
    async def admin_worktree_scan(
        watch_paths: str | None = None,
    ) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        monitor = ext.get("worktree_monitor")
        if monitor is None:
            from general_ludd.worktree import WorktreeMonitor, WorktreeMonitorConfig

            cfg = WorktreeMonitorConfig(
                watch_paths=watch_paths.split(",") if watch_paths else [],
                abandoned_after_hours=0,
            )
            monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        return {
            "todos": todos,
            "tracked_count": len(monitor.tracked_worktrees),
        }

    @app.get("/admin/worktree/status")
    async def admin_worktree_status() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        monitor = ext.get("worktree_monitor")
        if monitor is None:
            return {"tracked_worktrees": [], "tracked_count": 0}
        tracked = [
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
