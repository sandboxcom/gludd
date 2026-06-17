"""FastAPI worker application for General Ludd Agent."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.job_invocation import (
    _GENERATION_WORK_TYPES,
    invoke_model_for_generation,
    is_generation_work_type,
)
from general_ludd.schemas.job import JobSpec

__all__ = [
    "_GENERATION_WORK_TYPES",
    "create_app",
    "invoke_model_for_generation",
    "is_generation_work_type",
]

logger = logging.getLogger(__name__)

_runner: AnsibleRunnerAdapter | None = None


def get_runner() -> AnsibleRunnerAdapter:
    global _runner
    if _runner is None:
        _runner = AnsibleRunnerAdapter()
    return _runner


def get_playbook_registry() -> set[str]:
    return set(get_runner().list_playbooks())


def build_gateway_from_config() -> ModelGateway | None:
    """Build a ModelGateway from the worker's config, or None when unconfigured.

    The worker is stateless; model profiles come from the same user config the
    daemon reads. When no profiles are configured the worker simply does not
    perform model calls (the playbook still runs).
    """
    try:
        from general_ludd.config.loader import load_user_config

        uc = load_user_config()
        raw_profiles = getattr(uc, "model_profiles", {}) or {}
        profiles: list[ModelProfile] = []
        for key, val in raw_profiles.items():
            if isinstance(val, ModelProfile):
                profiles.append(val)
            elif isinstance(val, dict):
                data = dict(val)
                data.setdefault("model_profile_id", key)
                profiles.append(ModelProfile(**data))
        if not profiles:
            return None
        from general_ludd.secrets.env import EnvSecretsManager

        return ModelGateway(profiles=profiles, secrets_manager=EnvSecretsManager())
    except Exception as exc:  # pragma: no cover - defensive config path
        logger.warning("Worker gateway construction failed: %s", exc)
        return None


def _redact_secrets(message: str, refs: list[str]) -> str:
    for ref in refs:
        message = message.replace(ref, "***REDACTED***")
    return message


_UNSET: Any = object()


def _invoke_gateway_for_job(
    gateway: ModelGateway, job: JobSpec
) -> str | None:
    """Call the model for a generation job. Returns the generated text or None."""
    return invoke_model_for_generation(
        gateway,
        job_id=job.job_id,
        work_type=job.work_type,
        model_profile=job.model_profile,
        prompt_text=job.prompt_text,
        skill_body=job.skill_body,
    )


def create_app(gateway: ModelGateway | None = _UNSET) -> FastAPI:
    application = FastAPI(
        title="General Ludd Worker",
        version="0.1.0",
    )
    # ``gateway`` omitted → build from config; explicit None → no model calls.
    if gateway is _UNSET:
        gateway = build_gateway_from_config()
    application.state.gateway = gateway

    # W5.6 (AUTH blocker): the worker runs arbitrary registered playbooks for any
    # caller who can reach the port. Enforce the same pre-shared-key the daemon
    # uses (GLUDD_PSK), via the SHARED security.auth helper so the two surfaces
    # cannot drift. Fixes: (1) the old `token != _psk` was a timing oracle — the
    # helper uses hmac.compare_digest; (2) the worker ignored GLUDD_REQUIRE_AUTH
    # (fail-open) — it now mirrors the daemon's A-3 fail-closed 503 branch and
    # emits the LOUD no-PSK startup warning. Default (no PSK, no require) stays
    # OPEN so local/dev callers and existing no-PSK tests keep working.
    from general_ludd.security.auth import check_bearer_token, load_auth_posture

    _posture = load_auth_posture("worker")
    _psk = _posture.psk
    application.state._psk = _psk
    application.state._require_auth = _posture.require_auth
    application.state._no_auth = _posture.no_auth
    _public_paths = {"/healthz", "/docs", "/openapi.json", "/redoc"}

    def _worker_is_public(path: str) -> bool:
        return path in _public_paths or path.startswith("/docs")

    @application.middleware("http")
    async def _psk_auth_middleware(request: Any, call_next: Any) -> Any:
        path = request.url.path
        if not _worker_is_public(path):
            if _posture.no_auth and _posture.require_auth:
                # Fail-closed: auth required but no PSK configured.
                return JSONResponse(
                    status_code=503,
                    content={"error": "auth_required", "reason": "no PSK configured"},
                )
            if _psk:
                auth = request.headers.get("Authorization", "")
                if not check_bearer_token(auth, _psk):
                    return JSONResponse(
                        status_code=401, content={"error": "unauthorized"}
                    )
        return await call_next(request)

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "healthy"}

    @application.post("/ping")
    async def ping() -> dict[str, Any]:
        # Liveness over the wire: a ping in, a correlated pong out. Uses the
        # WorkerPingEvent/WorkerPongEvent taxonomy so peers can match request
        # to response by correlation_id.
        from general_ludd.worker.heartbeat import handle_ping, make_ping

        ping_event = make_ping()
        worker_id = os.environ.get("GLUDD_WORKER_ID", "worker")
        pong = handle_ping(ping_event, worker_id=worker_id)
        return {
            "type": str(pong.type),
            "worker_id": pong.payload["worker_id"],
            "correlation_id": pong.correlation_id,
            "ping_id": ping_event.event_id,
        }

    @application.post("/jobs/execute")
    async def execute_job(job: JobSpec) -> dict[str, Any]:
        registry = get_playbook_registry()
        if job.playbook not in registry:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown playbook: {job.playbook}",
            )
        redacted_vars = _redact_secrets(
            f"Executing job vars for {job.job_id}",
            job.vars_namespace_refs,
        )
        logger.info(
            "Executing job_id=%s todo_id=%s project_id=%s playbook=%s %s",
            job.job_id,
            job.todo_id,
            getattr(job, "project_id", None),
            job.playbook,
            redacted_vars,
        )
        # C1 (W3.1): for generation work types, invoke the model gateway and
        # feed its output into the playbook extravars and the job result.
        model_response: str | None = None
        gw = application.state.gateway
        if gw is not None and is_generation_work_type(job.work_type):
            model_response = _invoke_gateway_for_job(gw, job)

        runner = get_runner()
        dirs = runner.prepare_job_dirs(job.job_id)
        runner.write_vars(
            job.job_id,
            job_vars={
                "job_id": job.job_id,
                "todo_id": job.todo_id,
                "queue": job.queue,
                "work_type": job.work_type,
                "project_id": getattr(job, "project_id", None),
                "model_profile": job.model_profile,
                "prompt_text": job.prompt_text,
                "skill_body": job.skill_body,
                "model_response": model_response,
                **job.budget_context,
            },
            shared_vars=None,
        )
        runner_result = await asyncio.to_thread(
            runner.run_playbook,
            playbook_name=job.playbook,
            private_data_dir=dirs["root"],
            extravars={"model_response": model_response} if model_response is not None else None,
        )
        return {
            "status": "created",
            "return_id": f"RET-{job.job_id}",
            "todo_id": job.todo_id,
            "job_id": job.job_id,
            "playbook": job.playbook,
            "model_response": model_response,
            "exit_code": runner_result.get("rc", runner_result.get("exit_code", 0)),
            "result_summary": runner_result.get("output", runner_result.get("result_summary", "")),
            "artifacts": runner_result.get("artifacts", []),
            "events": runner_result.get("events", []),
        }

    @application.post("/jobs/return-review")
    async def return_review_job(job: JobSpec) -> dict[str, Any]:
        return {"status": "ack", "job_id": job.job_id, "detail": "Return review queued for daemon reviewer"}

    @application.post("/jobs/validate")
    async def validate_job(job: JobSpec) -> dict[str, Any]:
        # H3 (W3.8): returning a silent ack made callers believe validation
        # had run.  Until a real validation playbook is wired, return 501 so
        # callers know this path is unimplemented.
        raise HTTPException(
            status_code=501,
            detail={
                "reason": "not_implemented",
                "description": (
                    "/jobs/validate has no backing playbook yet. "
                    "POST to /jobs/execute with work_type='validation' to run a real validation job."
                ),
                "job_id": job.job_id,
            },
        )

    @application.post("/jobs/policy-validate")
    async def policy_validate_job(job: JobSpec) -> dict[str, Any]:
        # H3 (W3.8): same as above — return 501 instead of silent ack.
        raise HTTPException(
            status_code=501,
            detail={
                "reason": "not_implemented",
                "description": (
                    "/jobs/policy-validate has no backing policy engine yet. "
                    "Silent ack was removed to prevent callers from assuming validation ran."
                ),
                "job_id": job.job_id,
            },
        )

    @application.post("/jobs/reload-request")
    async def reload_request_job(job: JobSpec) -> dict[str, Any]:
        # H3 (W3.8): reload routing is not wired through the worker yet.
        raise HTTPException(
            status_code=501,
            detail={
                "reason": "not_implemented",
                "description": (
                    "/jobs/reload-request is not connected to the worker's reload path. "
                    "Use the daemon's /admin/reload endpoint instead."
                ),
                "job_id": job.job_id,
            },
        )

    return application
