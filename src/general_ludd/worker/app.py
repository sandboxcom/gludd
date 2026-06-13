"""FastAPI worker application for General Ludd Agent."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.schemas.job import JobSpec

logger = logging.getLogger(__name__)

_runner: AnsibleRunnerAdapter | None = None

# Work types whose execute job is a model-driven generation task. For these the
# worker invokes the ModelGateway and feeds the generated output into the
# playbook (and the job result) before running the playbook.
_GENERATION_WORK_TYPES: frozenset[str] = frozenset(
    {"code", "bug_fix", "test", "refactor", "docs", "prompt", "analysis", "security"}
)


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
    if not job.prompt_text:
        logger.info(
            "Generation job %s has no prompt_text; skipping model call", job.job_id
        )
        return None
    profile_id = job.model_profile or "default"
    messages = []
    if job.skill_body:
        messages.append({"role": "system", "content": job.skill_body})
    messages.append({"role": "user", "content": job.prompt_text})
    try:
        response = gateway.call_model(profile_id, messages=messages)
        return response.content
    except Exception as exc:
        logger.warning(
            "Model call failed for job %s (profile=%s): %s",
            job.job_id,
            profile_id,
            exc,
        )
        return None


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
    # uses (GLUDD_PSK). When the env var is unset, auth is disabled (matching the
    # daemon's behavior) so local/dev callers and existing tests still work.
    _psk = os.environ.get("GLUDD_PSK", "")
    application.state._psk = _psk
    _public_paths = {"/healthz", "/docs", "/openapi.json", "/redoc"}

    @application.middleware("http")
    async def _psk_auth_middleware(request: Any, call_next: Any) -> Any:
        if _psk:
            path = request.url.path
            if path not in _public_paths and not path.startswith("/docs"):
                auth = request.headers.get("Authorization", "")
                token = (
                    auth.removeprefix("Bearer ").strip()
                    if auth.startswith("Bearer ")
                    else ""
                )
                if not token or token != _psk:
                    return JSONResponse(
                        status_code=401, content={"error": "unauthorized"}
                    )
        return await call_next(request)

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "healthy"}

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
        if gw is not None and job.work_type in _GENERATION_WORK_TYPES:
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
        runner_result = runner.run_playbook(
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
