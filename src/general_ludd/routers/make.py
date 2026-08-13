"""Router for POST /admin/make — run make targets via MakeRunner."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from general_ludd.commands import make as make_commands

# Public dependency seam retained for integrations that patch the router while
# the command-module seam remains live for callers that patch before register().
MakeRunner = make_commands.MakeRunner
_DEFAULT_MAKE_RUNNER = MakeRunner


class MakeRequest(BaseModel):
    """Strict administrative make request.

    Unknown keys remain ignored for wire compatibility, while known fields are
    never coerced across JSON types before reaching the command boundary.
    """

    model_config = ConfigDict(extra="ignore")

    target: StrictStr
    extra_args: list[StrictStr] | None = None
    cwd: StrictStr | None = None
    timeout_s: StrictInt | None = None
    env_extra: dict[StrictStr, StrictStr] | None = None
    stream: StrictBool = False


def _runner_class() -> type[make_commands.MakeRunner]:
    """Resolve both established MakeRunner patch/injection seams."""
    if MakeRunner is not _DEFAULT_MAKE_RUNNER:
        return MakeRunner
    return make_commands.MakeRunner


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/make")
    async def admin_run_make(body: MakeRequest) -> dict[str, Any]:
        timeout = body.timeout_s if body.timeout_s is not None else 300
        runner = _runner_class()(cwd=body.cwd, default_timeout_s=timeout)

        if body.stream:
            phases_seen: list[str] = []

            def _cb(phase: str) -> None:
                phases_seen.append(phase)

            result = runner.run(
                body.target,
                extra_args=body.extra_args,
                timeout_s=body.timeout_s,
                env_extra=body.env_extra,
                stream=True,
                stream_callback=_cb,
            )
        else:
            result = runner.run(
                body.target,
                extra_args=body.extra_args,
                timeout_s=body.timeout_s,
                env_extra=body.env_extra,
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
