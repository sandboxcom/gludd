"""Router for POST /admin/make — run make targets via MakeRunner."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/make")
    async def admin_run_make(body: dict[str, Any]) -> dict[str, Any]:
        target: str = body["target"]
        extra_args: list[str] | None = body.get("extra_args")
        cwd: str | None = body.get("cwd")
        timeout_s: int | None = body.get("timeout_s")
        env_extra: dict[str, str] | None = body.get("env_extra")
        stream: bool = body.get("stream", False)

        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner(cwd=cwd, default_timeout_s=timeout_s or 300)

        if stream:
            phases_seen: list[str] = []

            def _cb(phase: str) -> None:
                phases_seen.append(phase)

            result = runner.run(
                target,
                extra_args=extra_args,
                timeout_s=timeout_s,
                env_extra=env_extra,
                stream=True,
                stream_callback=_cb,
            )
        else:
            result = runner.run(
                target,
                extra_args=extra_args,
                timeout_s=timeout_s,
                env_extra=env_extra,
            )

        return {
            "target": result.target,
            "exit_code": result.exit_code,
            "success": result.success,
            "duration_s": result.duration_s,
            "stdout_tail": result.stdout_tail,
            "stderr_tail": result.stderr_tail,
            "timed_out": result.timed_out,
            "oom_killed": result.oom_killed,
            "error": result.error,
            "phases": result.phases,
        }
