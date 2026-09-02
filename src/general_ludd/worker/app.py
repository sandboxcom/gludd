"""FastAPI worker application for General Ludd Agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.job_invocation import (
    _GENERATION_WORK_TYPES,
    invoke_model_for_generation,
    is_generation_work_type,
)
from general_ludd.observability.timing import default_tracker
from general_ludd.schemas.job import JobSpec

if TYPE_CHECKING:
    from general_ludd.self_improve.managed_runner import (
        ApprovedSelfImprovePlan,
        ManagedRunResult,
    )

__all__ = [
    "_GENERATION_WORK_TYPES",
    "build_worker_self_improve_runner",
    "create_app",
    "invoke_model_for_generation",
    "is_generation_work_type",
    "resolve_worker_self_improve_repo_root",
]

logger = logging.getLogger(__name__)

_runner: AnsibleRunnerAdapter | None = None


class _ManagedSelfImproveService(Protocol):
    def run(self, plan: ApprovedSelfImprovePlan) -> ManagedRunResult:
        """Execute one approval-bound plan."""


class _ManagedSelfImproveFactory(Protocol):
    def __call__(self, repo_root: Path) -> _ManagedSelfImproveService:
        """Build one repository-bound managed service."""


class _SelfImproveRepoResolver(Protocol):
    def __call__(self, project_id: str) -> Path:
        """Resolve one trusted project identity to its canonical repository."""


def get_runner() -> AnsibleRunnerAdapter:
    global _runner
    if _runner is None:
        _runner = AnsibleRunnerAdapter()
    return _runner


def get_playbook_registry() -> set[str]:
    return set(get_runner().list_playbooks())


def build_worker_self_improve_runner(repo_root: Path) -> _ManagedSelfImproveService:
    """Build the installed approval-bound runtime for one canonical repository."""
    from general_ludd.self_improve import build_managed_self_improve_runner

    return build_managed_self_improve_runner(repo_root)


def resolve_worker_self_improve_repo_root(project_id: str) -> Path:
    """Resolve a project through the worker's shared workspace convention."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id must be non-empty text")
    from general_ludd.projects.workspace import ProjectWorkspace

    repo_root = ProjectWorkspace(project_id=project_id).repo_dir.resolve(strict=True)
    if not repo_root.is_dir():
        raise ValueError("project repository is not a directory")
    return repo_root


def build_gateway_from_config(permission_spec: Any = None) -> ModelGateway | None:
    """Build a ModelGateway from the worker's config, or None when unconfigured.

    The worker is stateless; model profiles come from the same user config the
    daemon reads. When no profiles are configured the worker simply does not
    perform model calls (the playbook still runs).

    ``permission_spec``: when provided, the worker's hvac-backed
    SecretsManager is scoped to this spec (the STS token's spec for this
    job). ``None`` means back-compat (no enforcement — admin PSK path).
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

        # Auto-config: synthesize a ModelProfile per provider whose credential
        # env var is set (MISTRAL_API_KEY, FIREWORKS_API_KEY, ...). Dedup by
        # model_profile_id so explicit config-supplied profiles always win.
        # Mirrors the daemon's auto-config step so workers share the same
        # operator env-var contract.
        try:
            from general_ludd.models.auto_configurator import AutoConfigurator

            _auto = AutoConfigurator().auto_configure_profiles()
            if _auto:
                _existing = {p.model_profile_id for p in profiles}
                for _p in _auto:
                    if _p.model_profile_id not in _existing:
                        profiles.append(_p)
                        _existing.add(_p.model_profile_id)
        except Exception:
            logger.warning(
                "Worker auto-config: env-var profile discovery failed; continuing with explicit config only",
                exc_info=True,
            )

        if not profiles:
            return None
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager

        # CI-1 fix: register providers so the worker's gateway can make live calls
        # (provider_registry omitted → None → "No provider registry configured").
        # Permission gate: when the caller (daemon dispatch, STS-bearing job)
        # passes a permission_spec, scope the SecretsManager to it so a narrow
        # STS token cannot read secrets outside its allow-list. ``None`` keeps
        # the historical EnvSecretsManager (no path gating, back-compat).
        secrets_manager: Any = EnvSecretsManager()
        if permission_spec is not None:
            try:
                from general_ludd.secrets.config import OpenBaoConfig
                from general_ludd.secrets.manager import SecretsManager

                secrets_manager = SecretsManager(
                    config=OpenBaoConfig(),
                    permission_spec=permission_spec,
                )
            except Exception:
                logger.warning(
                    "Worker: failed to build scoped SecretsManager; falling back to EnvSecretsManager (no path gating)",
                    exc_info=True,
                )
                secrets_manager = EnvSecretsManager()
        return ModelGateway(
            profiles=profiles,
            provider_registry=ProviderRegistry.from_profiles(profiles),
            secrets_manager=secrets_manager,
        )
    except Exception:  # pragma: no cover - defensive config path
        # E2: best-effort config fallback — the worker degrades to no model
        # calls rather than failing. Surface the full traceback (exc_info) so the
        # failure is OBSERVABLE instead of silently swallowed.
        logger.warning(
            "Worker gateway construction failed; falling back to no model calls",
            exc_info=True,
        )
        return None


def _redact_secrets(message: str, refs: list[str]) -> str:
    for ref in refs:
        message = message.replace(ref, "***REDACTED***")
    return message


_UNSET: Any = object()


def _invoke_gateway_for_job(gateway: ModelGateway, job: JobSpec) -> tuple[str | None, list[dict[str, Any]] | None]:
    """Call the model for a generation job.

    Returns a ``(content, tool_calls)`` tuple: the generated text and the
    model's STRUCTURED tool/function calls (OpenAI-nested shape), or ``None``
    for each when absent.
    """
    # #56: opt-in SLM context-compaction. Read the enable flag + aggression
    # level from UserConfig (the same load_user_config() surface this module
    # uses for model_profiles). Fail-soft to OFF so a config error never breaks
    # the generation call — the plain ContextCompactor path is used by default.
    use_slm_compaction, compaction_level = _resolve_compaction_config()
    return invoke_model_for_generation(
        gateway,
        job_id=job.job_id,
        work_type=job.work_type,
        model_profile=job.model_profile,
        prompt_text=job.prompt_text,
        skill_body=job.skill_body,
        # S-1 (task #25): scope secret resolution to the job's project so the
        # worker path also isolates per-project credentials (None → base).
        project_id=job.project_id,
        use_slm_compaction=use_slm_compaction,
        compaction_level=compaction_level,
    )


def _resolve_compaction_config() -> tuple[bool, Any]:
    """Read (enabled, CompactionLevel|None) from UserConfig, fail-soft to OFF.

    Any load/parse error → ``(False, None)`` so the generation path behaves
    exactly as before. ``level`` indexes ``compaction.aggressive.LEVELS``.
    """
    try:
        from general_ludd.compaction.aggressive import level_at
        from general_ludd.config.loader import load_user_config

        uc = load_user_config()
        block = getattr(uc, "compaction", None)
        enabled = bool(getattr(block, "enabled", False))
        if not enabled:
            return False, None
        return True, level_at(getattr(block, "level", 1))
    except Exception:  # pragma: no cover - config is best-effort here
        logger.warning("compaction config resolve failed; compaction OFF", exc_info=True)
        return False, None


def build_dispatcher_from_config() -> Any:
    """Build a DynamicDispatcher for the worker from user config, or None.

    Mirrors the daemon's ``build_event_loop_mcp_dispatcher()``: wires a
    DynamicDispatcher with a skill handler so model-emitted tool calls are
    EXECUTED rather than dropped. Returns None when no skills are configured
    so the worker keeps its detect-only fallback.
    """
    try:
        from general_ludd.daemon_wiring import make_skill_handler
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher
        from general_ludd.skills.loader import discover_skills
        from general_ludd.skills.registry import SkillRegistry

        registry = SkillRegistry()
        config_dir = os.environ.get("GLUDD_CONFIG_DIR")
        if config_dir:
            for skill in discover_skills(config_dir):
                registry.register(skill)
        if not registry.list_skills():
            return None
        return DynamicDispatcher(
            role="event_loop",
            skill_handler=make_skill_handler(registry),
        )
    except Exception:  # pragma: no cover - defensive config path
        # E2: best-effort config fallback — the worker keeps its detect-only
        # path rather than failing. Surface the full traceback (exc_info) so the
        # failure is OBSERVABLE instead of silently swallowed.
        logger.warning(
            "Worker dispatcher construction failed; falling back to detect-only",
            exc_info=True,
        )
        return None


def create_app(
    gateway: ModelGateway | None = _UNSET,
    dispatcher: Any = _UNSET,
    permission_spec: Any = None,
    self_improve_runner_factory: _ManagedSelfImproveFactory | None = None,
    self_improve_repo_resolver: _SelfImproveRepoResolver | None = None,
) -> FastAPI:
    """Create the worker FastAPI app with PSK auth and gateway/dispatcher wiring."""
    application = FastAPI(
        title="General Ludd Worker",
        version="0.1.0",
    )
    # ``gateway`` omitted → build from config; explicit None → no model calls.
    # ``permission_spec`` is forwarded only when the gateway is built here —
    # an explicit ``gateway`` argument is assumed already scoped by the caller.
    if gateway is _UNSET:
        gateway = build_gateway_from_config(permission_spec=permission_spec)
    application.state.gateway = gateway
    # ``dispatcher`` omitted → build from config; explicit None → detect-only.
    if dispatcher is _UNSET:
        dispatcher = build_dispatcher_from_config()
    application.state.dispatcher = dispatcher
    application.state.self_improve_runner_factory = (
        build_worker_self_improve_runner
        if self_improve_runner_factory is None
        else self_improve_runner_factory
    )
    application.state.self_improve_repo_resolver = (
        resolve_worker_self_improve_repo_root
        if self_improve_repo_resolver is None
        else self_improve_repo_resolver
    )
    application.state.self_improve_model_lock = asyncio.Lock()

    async def _shutdown_owned_model_resources() -> None:
        from general_ludd.models.job_invocation import drain_background_tasks

        await drain_background_tasks()
        owned_gateway = application.state.gateway
        if owned_gateway is not None:
            owned_gateway.close()

    application.router.add_event_handler("shutdown", _shutdown_owned_model_resources)

    # C20: worker auth fail-closed by default. The worker runs arbitrary
    # registered playbooks for any caller who can reach the port. Enforce the
    # same pre-shared-key the daemon uses (GLUDD_AUTH_PSK), via the SHARED
    # security.auth helper so the two surfaces cannot drift.
    from general_ludd.security.auth import check_bearer_token, load_auth_posture

    _posture = load_auth_posture("worker")
    _psk = _posture.psk
    application.state._psk = _psk
    application.state._require_auth = _posture.require_auth
    application.state._no_auth = _posture.no_auth
    _public_paths = {"/healthz", "/docs", "/openapi.json", "/redoc"}

    if _psk:
        logger.info("Worker auth: ON (GLUDD_AUTH_PSK configured, %s)", _posture.surface)
    elif _posture.require_auth and _posture.no_auth:
        logger.warning(
            "Worker auth: FAIL-CLOSED — no GLUDD_AUTH_PSK set, all non-public paths "
            "will return 403. Set GLUDD_AUTH_PSK to enable auth or "
            "GLUDD_PSK_DISABLE=1 to disable it."
        )
    else:
        logger.info("Worker auth: OFF (GLUDD_PSK_DISABLE=1, back-compat mode)")

    def _worker_is_public(path: str) -> bool:
        # SECURITY: exact-match `/docs` or `/docs/<sub>` only — NOT `startswith("/docs")`,
        # which let prefix-colliding paths (e.g. `/docs_evil`) bypass the PSK auth gate.
        return path in _public_paths or path == "/docs" or path.startswith("/docs/")

    @application.middleware("http")
    async def _psk_auth_middleware(request: Any, call_next: Any) -> Any:
        path = request.url.path
        if not _worker_is_public(path):
            if _posture.no_auth and _posture.require_auth:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "auth_required",
                        "reason": (
                            "No GLUDD_AUTH_PSK configured. Set GLUDD_AUTH_PSK to enable auth "
                            "or GLUDD_PSK_DISABLE=1 to explicitly disable it."
                        ),
                    },
                )
            if _psk:
                auth = request.headers.get("Authorization", "")
                if not check_bearer_token(auth, _psk):
                    return JSONResponse(status_code=401, content={"error": "unauthorized"})
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
        if job.work_type == "self_improve":
            from general_ludd.self_improve import (
                ApprovedSelfImprovePlan,
                ManagedSelfImproveResultArtifact,
            )

            def reject(reason: str, description: str) -> HTTPException:
                return HTTPException(
                    status_code=400,
                    detail={"reason": reason, "description": description},
                )

            if not job.plan_artifact:
                raise reject(
                    "self_improve_plan_required",
                    "managed self-improvement requires an approved plan artifact",
                )
            if not job.project_id:
                raise reject(
                    "self_improve_project_required",
                    "managed self-improvement requires a project identity",
                )
            try:
                plan = ApprovedSelfImprovePlan.from_json(job.plan_artifact)
            except (TypeError, ValueError):
                raise reject(
                    "invalid_self_improve_plan",
                    "managed self-improvement plan validation failed",
                ) from None
            if plan.project_id != job.project_id or plan.todo_id != job.todo_id:
                raise reject(
                    "self_improve_identity_mismatch",
                    "approved plan identity does not match the dispatched job",
                )
            try:
                resolved_root = application.state.self_improve_repo_resolver(
                    job.project_id
                )
                if not isinstance(resolved_root, Path):
                    raise TypeError("repository resolver must return pathlib.Path")
                canonical_root = resolved_root.resolve(strict=True)
                if not canonical_root.is_dir():
                    raise ValueError("resolved repository is not a directory")
            except (OSError, TypeError, ValueError, LookupError):
                raise reject(
                    "self_improve_repository_unavailable",
                    "managed self-improvement repository mapping is unavailable",
                ) from None
            if plan.repo_root != canonical_root:
                raise reject(
                    "self_improve_identity_mismatch",
                    "approved plan repository does not match the configured project",
                )

            try:
                managed_runner = application.state.self_improve_runner_factory(
                    canonical_root
                )
                async with application.state.self_improve_model_lock:
                    managed_result = await asyncio.to_thread(managed_runner.run, plan)
                result_artifact = ManagedSelfImproveResultArtifact.from_run_result(
                    managed_result
                )
                result_summary = result_artifact.to_json()
            except Exception as exc:
                logger.error(
                    "Managed self-improvement failed for job_id=%s error_type=%s",
                    job.job_id,
                    type(exc).__name__,
                )
                return {
                    "status": "created",
                    "return_id": f"RET-{job.job_id}",
                    "todo_id": job.todo_id,
                    "job_id": job.job_id,
                    "playbook": job.playbook,
                    "model_response": None,
                    "tool_calls_detected": [],
                    "tool_dispatch_results": [],
                    "exit_code": 1,
                    "result_summary": json.dumps(
                        {
                            "accepted": False,
                            "kind": "managed_self_improve",
                            "reason": "managed_execution_failed",
                        },
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    "artifacts": [],
                    "events": [
                        {
                            "event": "self_improve_failed",
                            "reason": "managed_execution_failed",
                        }
                    ],
                }

            accepted = result_artifact.accepted
            attempts = result_artifact.attempts
            return {
                "status": "created",
                "return_id": f"RET-{job.job_id}",
                "todo_id": job.todo_id,
                "job_id": job.job_id,
                "playbook": job.playbook,
                "model_response": None,
                "tool_calls_detected": [],
                "tool_dispatch_results": [],
                "exit_code": 0 if accepted else 1,
                "result_summary": result_summary,
                "artifacts": [],
                "events": [
                    {
                        "event": "self_improve_completed",
                        "accepted": accepted,
                        "attempts": attempts,
                        "plan_identity_digest": result_artifact.plan_identity_digest,
                        "attempt_identity_digest": (
                            result_artifact.attempt_identity_digest
                        ),
                        "attempted_model_ids": list(
                            result_artifact.attempted_model_ids
                        ),
                        "outcome_record_ids": list(
                            result_artifact.outcome_record_ids
                        ),
                    }
                ],
            }

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
        model_tool_calls: list[dict[str, Any]] | None = None
        _model_call_success: bool = False
        _model_call_error: str | None = None
        _model_call_duration_ms: float = 0.0
        _model_call_start: float = time.monotonic()
        gw = application.state.gateway
        if gw is not None and is_generation_work_type(job.work_type):
            try:
                # Offload the blocking gateway.call_model round-trip so it does
                # NOT stall the worker event loop for the full model latency
                # (every other blocking op in this handler already uses
                # to_thread; this model call was the lone miss).
                model_response, model_tool_calls = await asyncio.to_thread(_invoke_gateway_for_job, gw, job)
                _model_call_success = model_response is not None
            except Exception as _exc:
                model_response = None
                model_tool_calls = None
                _model_call_error = str(_exc)
                logger.warning(
                    "Worker model call failed for job %s: %s",
                    job.job_id,
                    _exc,
                )
        _model_call_duration_ms = (time.monotonic() - _model_call_start) * 1000
        # Feed the model-call duration (converted to seconds) into the shared
        # clock-time tracker so an anomalously-slow model call vs its learned
        # per-profile baseline is detectable.
        default_tracker().check_then_record(f"model:{job.model_profile or 'default'}", _model_call_duration_ms / 1000.0)
        # Record the model call in the performance repository if wired.
        _model_perf_repo = getattr(application.state, "model_perf_repo", None)
        if _model_perf_repo is not None and is_generation_work_type(job.work_type):
            try:
                _input_tokens = len(job.prompt_text or "") // 4
                _output_tokens = len(model_response or "") // 4
                _profile_id = job.model_profile or "default"
                _gw = gw
                _profile_obj: Any = None
                if _gw is not None:
                    _profile_obj = _gw.get_profile(_profile_id)
                _cost_usd = 0.0
                if _profile_obj is not None and hasattr(_profile_obj, "cost_per_input_token"):
                    _cost_usd = (
                        _input_tokens * _profile_obj.cost_per_input_token
                        + _output_tokens * _profile_obj.cost_per_output_token
                    )
                _provider = getattr(_profile_obj, "provider", "") if _profile_obj else ""
                _model_name = getattr(_profile_obj, "model_name", "") if _profile_obj else ""
                _model_perf_repo.record_call_sync(
                    service=_provider or "unknown",
                    model_name=_model_name or _profile_id,
                    model_profile_id=_profile_id,
                    task_type="generation",
                    work_type=job.work_type,
                    success=_model_call_success,
                    input_tokens=_input_tokens,
                    output_tokens=_output_tokens,
                    cost_usd=_cost_usd,
                    duration_ms=_model_call_duration_ms,
                    todo_id=job.todo_id,
                    job_id=job.job_id,
                    error_message=_model_call_error,
                )
            except Exception as _rec_exc:
                logger.debug(
                    "Worker model perf recording failed for %s: %s",
                    job.job_id,
                    _rec_exc,
                )

        # Dispatch the model's STRUCTURED tool_calls. When a DynamicDispatcher is
        # wired (mirrors the daemon's EventLoop pattern), EXECUTE the calls via
        # ``dispatch_all`` and surface the results. When no dispatcher is
        # available, fall back to the conservative detect-only path so the gap
        # is still observable in logs and the response dict. The legacy path
        # re-parsed the model TEXT (parse_tool_calls), which cannot recover the
        # structured calls, so model-driven tool actions were silently discarded.
        tool_calls_detected: list[dict[str, object]] = []
        tool_dispatch_results: list[dict[str, object]] = []
        if model_response is not None:
            from general_ludd.dispatch.dynamic_dispatcher import (
                structured_tool_calls_to_calls,
            )
            from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST

            calls = structured_tool_calls_to_calls(model_tool_calls)
            if len(calls) > MAX_CALLS_PER_REQUEST:
                logger.warning(
                    "Worker /jobs/execute: model returned %d tool call(s) which exceeds cap %d — all dropped (job %s)",
                    len(calls),
                    MAX_CALLS_PER_REQUEST,
                    job.job_id,
                )
            elif calls:
                _dispatcher = application.state.dispatcher
                if _dispatcher is None:
                    logger.warning(
                        "Worker /jobs/execute: model returned %d tool call(s) but no dispatcher "
                        "is wired in the worker — detect-only (job %s)",
                        len(calls),
                        job.job_id,
                    )
                    tool_calls_detected = [{"kind": c.kind, "name": c.name, "args": c.args} for c in calls]
                else:
                    dispatch_results = await _dispatcher.dispatch_all(calls)
                    ok_count = sum(1 for r in dispatch_results if r.ok)
                    err_count = len(dispatch_results) - ok_count
                    logger.info(
                        "Worker /jobs/execute: dispatched %d tool call(s): %d ok, %d error (job %s)",
                        len(dispatch_results),
                        ok_count,
                        err_count,
                        job.job_id,
                    )
                    tool_dispatch_results = [r.to_dict() for r in dispatch_results]

        runner = get_runner()
        try:
            # prepare_job_dirs does blocking os.makedirs; offload so it doesn't
            # stall the worker's asyncio event loop under concurrent jobs (AB).
            dirs = await asyncio.to_thread(runner.prepare_job_dirs, job.job_id)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="Job already in progress") from exc
        _max_timeout = float(os.environ.get("GLUDD_JOB_TIMEOUT_MAX", "600"))
        # Always a concrete float (the min, or the ceiling) — never None — so the
        # inner run_playbook gets a finite bound it can actually enforce, and the
        # outer backstop below can add a grace margin without Optional arithmetic.
        _timeout: float = (
            min(job.timeout, _max_timeout) if job.timeout is not None and job.timeout > 0 else _max_timeout
        )
        try:
            # write_vars does blocking os.makedirs + yaml file write + os.chmod;
            # offload so it doesn't stall the worker's asyncio loop (AB).
            await asyncio.to_thread(
                runner.write_vars,
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
            runner_result = await asyncio.wait_for(
                asyncio.to_thread(
                    runner.run_playbook,
                    playbook_name=job.playbook,
                    private_data_dir=dirs["root"],
                    extravars={"model_response": model_response} if model_response is not None else None,
                    timeout=_timeout,
                ),
                timeout=_timeout + 30.0,
            )
            # Build the response dict from runner_result *before* removing the
            # workspace.  finally block below guarantees cleanup on every path.
            response_payload = {
                "status": "created",
                "return_id": f"RET-{job.job_id}",
                "todo_id": job.todo_id,
                "job_id": job.job_id,
                "playbook": job.playbook,
                "model_response": model_response,
                "tool_calls_detected": tool_calls_detected,
                "tool_dispatch_results": tool_dispatch_results,
                "exit_code": runner_result.get("rc", runner_result.get("exit_code", 0)),
                "result_summary": runner_result.get("output", runner_result.get("result_summary", "")),
                "artifacts": runner_result.get("artifacts", []),
                "events": runner_result.get("events", []),
            }
            return response_payload
        finally:
            # Guaranteed cleanup on ALL paths — success, failure, cancellation.
            # ignore_errors=True handles missing directory, permission errors,
            # and thread-pool shutdown.  Offloaded to keep asyncio loop free.
            await asyncio.to_thread(shutil.rmtree, dirs["root"], ignore_errors=True)

    @application.post("/jobs/return-review")
    async def return_review_job(job: JobSpec) -> dict[str, Any]:
        # S2 fix: was a silent ack {"status":"ack"} that stranded
        # claims forever. Worker review dispatch is not built — return
        # 501 so the caller (EventLoop) can detect the failure and
        # release the claim rather than silently stranding it.
        raise HTTPException(
            status_code=501,
            detail={
                "reason": "not_implemented",
                "description": (
                    "/jobs/return-review has no backing review playbook yet. "
                    "Return reviews are handled in-process by the EventLoop "
                    "(ReturnReviewer / LangGraphReflexiveReviewer / ConsensusReviewer)."
                ),
                "job_id": job.job_id,
            },
        )

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
