from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/api/replays")
    async def api_list_replays() -> list[str]:
        recorder = getattr(app.state, "_run_recorder", None)
        if recorder is None:
            return []
        return cast("list[str]", recorder.list_runs())
