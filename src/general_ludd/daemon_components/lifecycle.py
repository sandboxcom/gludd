"""Daemon resource acquisition and reverse-order teardown lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI

from general_ludd.daemon_components.ports import LifecyclePorts


@asynccontextmanager
async def lifespan(app: FastAPI, ports: LifecyclePorts) -> AsyncIterator[None]:
    """Run the daemon startup and shutdown sequence through facade-owned ports.

    Args:
        app: FastAPI application whose state owns lifecycle resources.
        ports: Call-time dependencies resolved by ``general_ludd.daemon``.

    Yields:
        Control to FastAPI while all daemon resources are active.
    """
    AdversarialCodeDetector = ports.AdversarialCodeDetector
    AnsibleRunnerAdapter = ports.AnsibleRunnerAdapter
    AutoBenchmarkRecorder = ports.AutoBenchmarkRecorder
    BenchmarkRepository = ports.BenchmarkRepository
    BinaryBootstrapper = ports.BinaryBootstrapper
    DashboardDataProvider = ports.DashboardDataProvider
    DeploymentHealthChecker = ports.DeploymentHealthChecker
    EstimationTracker = ports.EstimationTracker
    EvalHarness = ports.EvalHarness
    EventLoop = ports.EventLoop
    ExecutionEngine = ports.ExecutionEngine
    IsolationLevel = ports.IsolationLevel
    LangGraphModelCallError = ports.LangGraphModelCallError
    LangSmithTracer = ports.LangSmithTracer
    LocalAgentMemory = ports.LocalAgentMemory
    ManagedSelfImproveProcessExecutor = ports.ManagedSelfImproveProcessExecutor
    MemoryRepository = ports.MemoryRepository
    ModelEvaluator = ports.ModelEvaluator
    ModelGateway = ports.ModelGateway
    ModelHealthTracker = ports.ModelHealthTracker
    ModelPerformanceRepository = ports.ModelPerformanceRepository
    ModelProfile = ports.ModelProfile
    OTelBridge = ports.OTelBridge
    OutputTemplateRegistry = ports.OutputTemplateRegistry
    Path = ports.Path
    PromptEnhancer = ports.PromptEnhancer
    PromptRegistry = ports.PromptRegistry
    ProviderRegistry = ports.ProviderRegistry
    RunBudgetGuard = ports.RunBudgetGuard
    RunRecorder = ports.RunRecorder
    SandboxCapabilityRouter = ports.SandboxCapabilityRouter
    SandboxConfig = ports.SandboxConfig
    SandboxExecutor = ports.SandboxExecutor
    SelfHealingRouter = ports.SelfHealingRouter
    SemanticSearcher = ports.SemanticSearcher
    SlowOperationEvent = ports.SlowOperationEvent
    SlurmJobRepository = ports.SlurmJobRepository
    StallDetectedEvent = ports.StallDetectedEvent
    StallWatchdog = ports.StallWatchdog
    TaskEmbeddingStore = ports.TaskEmbeddingStore
    VmSandboxConfig = ports.VmSandboxConfig
    WriteQueue = ports.WriteQueue
    WriterProcess = ports.WriterProcess
    _DEFAULT_WORKER_ID = ports._DEFAULT_WORKER_ID
    _FS = ports._FS
    _build_pipeline_controller = ports._build_pipeline_controller
    _build_self_improve_runner_factory = ports._build_self_improve_runner_factory
    _build_self_update_audit_sink = ports._build_self_update_audit_sink
    _build_sts_audit_logger = ports._build_sts_audit_logger
    _build_sts_reaper = ports._build_sts_reaper
    _compaction_config_dict = ports._compaction_config_dict
    _configure_network_state = ports._configure_network_state
    _drain_self_update_audit_tasks = ports._drain_self_update_audit_tasks
    _get_or_create_extended_subsystems = ports._get_or_create_extended_subsystems
    _get_or_create_subsystems = ports._get_or_create_subsystems
    _init_project_workspaces = ports._init_project_workspaces
    _log_owned_self_improve_event = ports._log_owned_self_improve_event
    _on_event_loop_done = ports._on_event_loop_done
    _parse_budget_config = ports._parse_budget_config
    _remediation_config_from_uc = ports._remediation_config_from_uc
    _remediation_tick_settings = ports._remediation_tick_settings
    _restore_persisted_projects = ports._restore_persisted_projects
    _restore_persisted_spend = ports._restore_persisted_spend
    _warm_start_local_models = ports._warm_start_local_models
    asyncio = ports.asyncio
    build_event_loop_mcp_dispatcher = ports.build_event_loop_mcp_dispatcher
    build_secrets_resolver = ports.build_secrets_resolver
    contextlib = ports.contextlib
    create_async_session_factory = ports.create_async_session_factory
    create_read_only_session_factory = ports.create_read_only_session_factory
    default_tracker = ports.default_tracker
    ensure_tables = ports.ensure_tables
    get_checkpointer = ports.get_checkpointer
    init_engine_from_config = ports.init_engine_from_config
    init_read_only_engine_from_config = ports.init_read_only_engine_from_config
    is_sqlite_url = ports.is_sqlite_url
    logger = ports.logger
    managed_execution_timeout_seconds = ports.managed_execution_timeout_seconds
    migrate_profile_secrets = ports.migrate_profile_secrets
    os = ports.os
    quiesce_task_before_drain = ports.quiesce_task_before_drain
    run_preflight = ports.run_preflight
    seed_initial_queues = ports.seed_initial_queues
    sys = ports.sys

    tick_interval = app.state.tick_interval
    # Per-app state dict (created in create_daemon_app). Read from app.state so
    # concurrently-running apps never share/overwrite one another's state. If a
    # caller invokes the lifespan on a bare app (unit tests), materialise a fresh
    # per-app dict rather than falling back to the shared module global.
    daemon_state: dict[str, Any] = getattr(app.state, "daemon_state", None) or {
        "todos": [],
        "tick_metrics": {},
        "quality_gate": {},
    }
    event_loop = None
    task = None
    execution_engine = None

    try:
        startup_config = getattr(app.state, "_startup_config", {}) or {}
        db_config: dict[str, Any] = {}
        uc = startup_config.get("user_config")
        bc = _parse_budget_config(uc)
        if uc and hasattr(uc, "network"):
            _configure_network_state(app, uc.network)
        if uc and hasattr(uc, "database"):
            db_config = uc.database or {}
        _db_override: str | None = getattr(app.state, "_db_path_override", None)
        if _db_override:
            db_config["url"] = f"sqlite+aiosqlite:///{_db_override}"

        # B3.1.3 Slice 4 — GLUDD_WRITER_MODE selects between the inline
        # single-process daemon path (default; zero behavioural change) and
        # the subprocess mode where HTTP workers get a read-only engine and
        # all DB writes are routed through a WriteQueue to a dedicated writer
        # subprocess. The env-var read MUST happen before engine construction
        # so the branch below can pick the right engine/factory pair.
        writer_mode = os.environ.get("GLUDD_WRITER_MODE", "inline").strip().lower()
        if writer_mode not in {"inline", "subprocess"}:
            logger.warning(
                "GLUDD_WRITER_MODE=%r is not 'inline' or 'subprocess'; falling back to inline",
                writer_mode,
            )
            writer_mode = "inline"

        # Schema + seed need write access; the read-only factory published
        # to HTTP workers in subprocess mode is built AFTER seeding completes.
        engine = init_engine_from_config(db_config)
        await ensure_tables(engine)

        if writer_mode == "subprocess":
            # Seed on a writable factory, then swap to a read-only factory for
            # the HTTP workers' runtime sessions. The writer subprocess owns
            # all subsequent DB writes; HTTP workers enqueue via WriteQueue.
            _seed_factory = create_async_session_factory(engine)
            async with _seed_factory() as session:
                await seed_initial_queues(session)
                await session.commit()
            await engine.dispose()

            engine = init_read_only_engine_from_config(db_config)
            session_factory = create_read_only_session_factory(engine)
            write_queue: Any = WriteQueue()
            _wp = WriterProcess(config=dict(db_config))
            _wp.start()
            writer_process: Any = _wp
            logger.info(
                "GLUDD_WRITER_MODE=subprocess: read-only engine + WriteQueue + WriterProcess(pid=%s) started",
                _wp.pid,
            )
        else:
            session_factory = create_async_session_factory(engine)
            async with session_factory() as session:
                await seed_initial_queues(session)
                await session.commit()
            write_queue = None
            writer_process = None

        # Publish on app.state so router code can branch via enqueue_or_commit.
        if write_queue is not None:
            app.state._write_queue = write_queue
        if writer_process is not None:
            app.state._writer_process = writer_process

        app.state._sts_audit_logger = _build_sts_audit_logger(session_factory)

        # Orphan detection: flag Slurm jobs from a prior daemon instance.
        import os as _os

        _current_pid = _os.getpid()
        try:
            async with session_factory() as session:
                slurm_repo = SlurmJobRepository(session)
                orphans = await slurm_repo.list_orphans(_current_pid)
            if orphans:
                logger.warning(
                    "Found %d orphan Slurm job(s) still marked 'running' from a prior "
                    "daemon instance (pid != %d): %s. Not auto-cancelling — they may "
                    "belong to another daemon.",
                    len(orphans),
                    _current_pid,
                    ", ".join(j.job_id for j in orphans),
                )
        except Exception:
            logger.warning("Orphan Slurm job detection failed")

        # Bill-3: preemption handler for Slurm jobs
        from general_ludd.infra.slurm_preemption import SlurmPreemptionHandler

        app.state._slurm_preemption_handler = SlurmPreemptionHandler()
        logger.info("Slurm preemption handler initialised")

        # Phase 2 Step 3 (self-improve wiring): build the audit_sink closure over
        # session_factory + AuditEventRepository and publish it on app.state so
        # the /admin/self-update/plan router can pass it through to apply_plan.
        # Built once here (after session_factory exists) so every request reuses
        # the same sink; the sink opens its own short-lived session per record.
        app.state._self_update_audit_tasks = set()
        app.state._self_update_audit_sink = _build_self_update_audit_sink(
            session_factory,
            app.state._self_update_audit_tasks,
        )

        if is_sqlite_url(str(engine.url)):
            try:
                from general_ludd.db.migrations import get_alembic_config, stamp_head

                alembic_cfg = get_alembic_config(str(engine.url))
                # Run the synchronous alembic stamp off the event loop so it
                # doesn't stall every other coroutine during daemon startup.
                await asyncio.to_thread(stamp_head, alembic_cfg)
                logger.info("Alembic stamped head on SQLite database")
            except Exception as exc:
                logger.warning("Alembic stamp failed: %s", exc)

        # Phase 2: resolve Ansible collections/roles search paths via the 3-tier
        # resolver (project .gludd/collections → user → bundled) so project-local
        # roles/collections shadow the bundled ones. The adapter self-resolves
        # _collections_env from project_root; we ALSO publish the resolved
        # paths/env on app.state for observability + the EventLoop project-switch
        # rebuild path.
        from general_ludd.ansible.paths import (
            resolve_collections_paths,
            to_ansible_env,
        )

        _proj_gludd = startup_config.get("project_gludd_dir")
        _initial_project_root = str(Path(_proj_gludd).parent) if _proj_gludd is not None else None
        _collections_paths = resolve_collections_paths(_initial_project_root)
        _ansible_env: dict[str, str] = to_ansible_env(_collections_paths)
        app.state._collections_paths = _collections_paths
        app.state._ansible_env = dict(_ansible_env)
        logger.info(
            "Resolved Ansible collections paths (%d tier(s)): %s",
            len(_collections_paths),
            ", ".join(f"{e.source}={e.path}" for e in _collections_paths),
        )

        from general_ludd.dispatch.capabilities import discover_capabilities

        capability_registry = await asyncio.to_thread(discover_capabilities)
        app.state._capability_registry = capability_registry
        logger.info(
            "CapabilityRegistry: %d collections, %d tags indexed",
            len(capability_registry.collections),
            len(capability_registry.tag_index),
        )

        # Bill-4: Terraform watchdog for stack cost monitoring
        stacks_dir = os.environ.get(
            "GLUDD_TERRAFORM_STACKS_DIR",
            str(Path.cwd() / "infra" / "terraform" / "stacks"),
        )
        from general_ludd.infra.terraform_watchdog import TerraformWatchdog

        app.state._terraform_watchdog = TerraformWatchdog(stacks_dir=stacks_dir)
        logger.info("Terraform watchdog initialised for stacks: %s", stacks_dir)

        from general_ludd.infra.spot_validator import SpotConfigValidator

        app.state._spot_config_validator = SpotConfigValidator(default_spot=True)

        # G3: Construct a shared CodebaseIndexer for semantic codebase retrieval.
        # Uses diskcache in .gludd/retrieval_cache by default.
        from general_ludd.retrieval.indexer import CodebaseIndexer

        _codebase_indexer = CodebaseIndexer()
        app.state._codebase_indexer = _codebase_indexer
        logger.info("CodebaseIndexer initialised (cache: %s)", _codebase_indexer.cache_dir)

        from general_ludd.retrieval.searx_client import SearxNGClient

        app.state._searx_client = SearxNGClient()

        if uc is not None and uc.searx_autostart:
            from general_ludd.searx.install import (
                ensure_searx_initialized,
                ensure_searx_installed,
            )
            from general_ludd.searx.server import SearXServer

            if ensure_searx_installed() and ensure_searx_initialized():
                searx_server = SearXServer()
                if searx_server.ensure_started():
                    app.state._searx_server = searx_server
                    logger.info(
                        "SearXNG server started on %s",
                        searx_server.get_instance_url(),
                    )
                else:
                    logger.error("SearXNG autostart was requested but startup failed")
            else:
                logger.error("SearXNG autostart was requested but setup failed")
        else:
            app.state._searx_server = None
            logger.info("SearXNG autostart disabled; expecting an external service")

        from general_ludd.retrieval.research_index import ResearchIndex

        app.state._research_index = ResearchIndex()

        def _update_ansible_env(paths: list[Any], env: dict[str, str]) -> None:
            """Callback the EventLoop invokes on project switch.

            Republishes the resolved paths/env on app.state. The adapter's own
            ``set_project_root`` (invoked separately by the EventLoop) handles
            rebinding its ``_collections_env``.
            """
            app.state._collections_paths = paths
            app.state._ansible_env = dict(env)

        app.state._ansible_env_updater = _update_ansible_env

        runner = AnsibleRunnerAdapter(
            default_env=dict(_ansible_env) if _ansible_env else None,
            project_root=_initial_project_root,
        )
        subsys = _get_or_create_subsystems(app)

        # CA-T7/CA-T8 fix: create and assign the health tracker BEFORE calling
        # _get_or_create_extended_subsystems so the AdaptiveRouter constructor
        # receives a live ModelHealthTracker (not None).  The tracker was
        # previously assigned ~50 lines later, after the router was already built,
        # making the health-filtering + quantization-penalty logic permanently
        # inert in the running daemon.  (ModelHealthTracker is imported at module
        # level so tests can patch general_ludd.daemon.ModelHealthTracker.)
        _pre_health_tracker = ModelHealthTracker()
        app.state._health_tracker = _pre_health_tracker

        # CA-T9 fix: create and assign the quantization tracker BEFORE calling
        # _get_or_create_extended_subsystems so the AdaptiveRouter constructor
        # receives a populated quantization_map rather than an empty {}.
        # Without this, getattr(app.state, "_quantization_tracker", None) inside
        # _get_or_create_extended_subsystems always returns None → quantization_map
        # stays {} → _apply_quantization_penalty never fires in the running daemon.
        # The tracker starts empty (no detections yet) but is the live instance
        # that /admin/quantization/detect will populate at runtime.
        from general_ludd.models.quantization import QuantizationTracker as _QuantizationTracker

        app.state._quantization_tracker = _QuantizationTracker()

        # Tier 2 RAG routing: construct + seed TaskEmbeddingStore before the
        # AdaptiveRouter is built so the router can borrow strength from
        # neighboring task types via cosine similarity. The store holds a
        # long-lived session (the router calls similarity_to() on every route),
        # so the session is kept open for the app lifetime and closed in the
        # lifespan teardown. ensure_embeddings() is idempotent — only empty rows
        # are embedded, so a warm restart never recomputes paid-for vectors.
        # Best-effort: on failure the store is left None and the router falls
        # back to exact-match history.
        app.state._embedding_store = None
        app.state._embedding_session = None
        try:
            _embedding_session = session_factory()
            _embedding_store = TaskEmbeddingStore(session=_embedding_session)
            await _embedding_store.ensure_embeddings()
            await _embedding_session.commit()
            app.state._embedding_store = _embedding_store
            app.state._embedding_session = _embedding_session
        except Exception:
            logger.error("TaskEmbeddingStore seeding failed")
            with contextlib.suppress(Exception):
                if "_embedding_session" in locals():
                    await _embedding_session.close()

        ext = _get_or_create_extended_subsystems(app, session_factory=session_factory)
        daemon_state["receiver_buffer"] = app.state._receiver_buffer

        # W3.11 (H13): merge DB-persisted projects into the manager so projects
        # added at runtime survive a restart, and materialize each repo_url into
        # its workspace so dispatched jobs have real code to edit.
        await _restore_persisted_projects(ext.get("projects"), session_factory)

        # H1 fix: build_secrets_resolver() calls hvac client.is_authenticated()
        # synchronously (a blocking HTTP call).  Offload to a thread so the
        # event loop is never stalled if OpenBao is slow or unreachable.
        secrets_resolver = await asyncio.to_thread(
            build_secrets_resolver,
            openbao_config=startup_config.get("openbao_config"),
            projects_active=bool(ext.get("projects")),
        )
        app.state._secrets_resolver = secrets_resolver

        # P5: construct the STS reaper pipeline (TokenStore + TokenRevoker +
        # TokenReaper + StsAuditPipeline) and publish on daemon_state so
        # EventLoop._phase_reap_expired_sts_tokens can sweep expired tokens
        # every sts_reap_interval_ticks. The cascade hook is wired so that
        # revoking a parent token tears down its delegation subtree.
        try:
            _sts_reaper = _build_sts_reaper(
                session_factory=session_factory,
                secrets_resolver=secrets_resolver,
            )
            daemon_state["_sts_reaper"] = _sts_reaper
            app.state._sts_reaper = _sts_reaper
            logger.info("STS TokenReaper wired into daemon tick")
        except Exception:
            logger.warning("STS TokenReaper construction failed; reaping disabled")

        model_profiles = startup_config.get("model_profiles", [])

        # Auto-config: for every provider whose credential env var is set
        # (e.g. MISTRAL_API_KEY, FIREWORKS_API_KEY) but which lacks an explicit
        # ModelProfile in the operator config, synthesize one using the
        # provider's flagship model. Explicit config-supplied profiles always
        # win (deduped by model_profile_id) so a user-written profile is never
        # silently clobbered. See AutoConfigurator.auto_configure_from_env.
        try:
            from general_ludd.models.auto_configurator import AutoConfigurator

            _auto_profiles = AutoConfigurator().auto_configure_profiles()
            if _auto_profiles:
                _existing_ids = {
                    getattr(_p, "model_profile_id", None) if not isinstance(_p, dict) else _p.get("model_profile_id")
                    for _p in model_profiles
                }
                _added = [_p for _p in _auto_profiles if _p.model_profile_id not in _existing_ids]
                if _added:
                    model_profiles = list(model_profiles) + _added
                    logger.info(
                        "Auto-config: appended %d env-derived profile(s): %s",
                        len(_added),
                        [_p.model_profile_id for _p in _added],
                    )
        except Exception:
            logger.warning(
                "Auto-config: env-var profile discovery failed; continuing with explicit config only",
            )

        if model_profiles and hasattr(secrets_resolver, "write_secret"):
            try:
                profile_dicts = [p.model_dump() if hasattr(p, "model_dump") else p for p in model_profiles]
                result = migrate_profile_secrets(secrets_resolver, profile_dicts)
                logger.info(
                    "Secret migration: %d migrated, %d skipped",
                    result["migrated"],
                    len(cast("list[str]", result["skipped"])),
                )
            except Exception:
                logger.error("Secret migration failed (non-critical — daemon continues)")

        templates_dir = getattr(app.state, "_templates_dir", None)
        # Phase 2: prepend project .gludd/templates/ so project-local templates
        # shadow same-named global ones.  No-op when the dir does not exist.
        _proj_for_prompts = startup_config.get("project_gludd_dir")
        _extra_tmpl_dirs: list[str] = []
        if _proj_for_prompts is not None:
            _proj_tmpl_dir = Path(_proj_for_prompts) / "templates"
            if _proj_tmpl_dir.is_dir():
                _extra_tmpl_dirs = [str(_proj_tmpl_dir)]
        hub_registry = None
        _use_hub = bool(getattr(uc, "use_hub", False)) if uc else False
        if _use_hub:
            from general_ludd.prompts.hub_registry import LangChainHubRegistry

            hub_registry = LangChainHubRegistry(use_hub=True)
            logger.info("LangChainHubRegistry enabled for prompt resolution")
        prompt_registry = PromptRegistry(
            template_dir=templates_dir,
            event_bus=subsys["bus"],
            extra_template_dirs=_extra_tmpl_dirs or None,
            hub_registry=hub_registry,
        )
        # P2 (perf): refresh() globs the template dir and read_text()s each *.j2
        # file — blocking filesystem IO. Offload it so the daemon-boot coroutine
        # does not stall the event loop while templates load. Return value is
        # unused; error handling is unchanged (an unreadable dir still raises and
        # is caught by the outer startup try/except → degraded mode).
        await asyncio.to_thread(prompt_registry.refresh)
        app.state._prompt_registry = prompt_registry
        output_template_dirs: list[str] = []
        if _proj_for_prompts is not None:
            _proj_output_tmpl_dir = Path(_proj_for_prompts) / "templates" / "log_output"
            if _proj_output_tmpl_dir.is_dir():
                output_template_dirs.append(str(_proj_output_tmpl_dir))
        output_template_registry = OutputTemplateRegistry.default(extra_template_dirs=output_template_dirs)
        output_template_summary = await asyncio.to_thread(output_template_registry.compile)
        app.state._output_template_registry = output_template_registry
        app.state._output_template_summary = output_template_summary
        logger.info("Output templates compiled: %d", output_template_summary.get("count", 0))
        app.state._prompt_enhancer = PromptEnhancer()

        # Build budget guard from config
        budget_guard = None
        if uc is not None:
            raw_budget = getattr(uc, "budget", None) or {}
            if raw_budget and any(raw_budget.values()):
                budget_guard = RunBudgetGuard(
                    run_budget_usd=bc.daily_limit,
                    run_timeout_seconds=bc.timeout_seconds,
                    per_call_budget_usd=bc.per_task_limit,
                )
        app.state._budget_guard = budget_guard

        # Build the model gateway once (H4/H12): both the in-process reviewer and
        # the agent dispatcher reuse the SAME gateway instance.
        # CA-T7/CA-T8: reuse the health_tracker pre-created before extended-subsystem
        # construction so the AdaptiveRouter holds the live instance (not None).
        health_tracker = app.state._health_tracker

        # LangSmith tracer: additive observability side-channel.
        # Enabled when LANGSMITH_API_KEY + LANGSMITH_PROJECT env vars are set.
        # Gracefully degrades — no-op when unconfigured or unavailable.
        app.state.langsmith_tracer = LangSmithTracer()

        from general_ludd.controllers.pause_controller import PauseController

        app.state._pause_controller = PauseController()

        from general_ludd.agents.hibernation import (
            HibernationController,
            HibernationStore,
            _load_hibernate_mac_key,
        )

        pause_base = app.state._pause_controller._store.base_dir
        hibernate_mac_key = _load_hibernate_mac_key(str(pause_base))
        app.state._hibernation_controller = HibernationController(
            store=HibernationStore(base_dir=str(pause_base), mac_key=hibernate_mac_key),
        )

        from general_ludd.controllers.floor import FloorController

        floor_controller = FloorController()
        app.state._floor_controller = floor_controller

        from general_ludd.controllers.compaction_aggressiveness import (
            CompactionAggressivenessController,
        )

        app.state._compaction_aggressiveness_controller = CompactionAggressivenessController()

        from general_ludd.approval.gate import ApprovalGate

        app.state._approval_gate = ApprovalGate()

        model_gateway = None
        deployment_health_router = None
        semantic_searcher = None
        if model_profiles:
            _resolved_profiles = [
                p if isinstance(p, ModelProfile) else ModelProfile(**p)
                for p in model_profiles
                if isinstance(p, (ModelProfile, dict))
            ]
            model_gateway = ModelGateway(
                profiles=_resolved_profiles,
                # CI-1 fix: register each profile's provider so live calls have a
                # usable provider class. With provider_registry=None the gateway's
                # _registry was None and every live call raised "No provider registry
                # configured" — the daemon could not make a single live model call.
                provider_registry=ProviderRegistry.from_profiles(_resolved_profiles),
                secrets_manager=secrets_resolver,
                metrics_collector=ext.get("metrics_collector"),
                health_tracker=health_tracker,
                # Wire the operator-configured budget guard so a configured spend
                # ceiling is actually enforced — it was built above but never passed,
                # leaving budgets silently inert in the daemon.
                budget_guard=budget_guard,
                pause_controller=app.state._pause_controller,
                langsmith_tracer=app.state.langsmith_tracer,
            )
            app.state._model_gateway = model_gateway

            await _warm_start_local_models(model_gateway)

            semantic_searcher = SemanticSearcher()
            app.state._semantic_searcher = semantic_searcher

            eval_harness = EvalHarness(
                model="sonnet",
                evaluator=ModelEvaluator(model_gateway, profile_id="sonnet"),
            )
            app.state.eval_harness = eval_harness

            # Deployment health: track per-model-deployment failures and
            # self-heal by routing away from unhealthy deployments.
            deployment_health_checker = DeploymentHealthChecker()
            deployment_health_router = SelfHealingRouter(
                health_checker=deployment_health_checker,
            )
            # Feed each profile's fallback chain into the self-healing router
            # so it knows the healthy alternatives when a deployment degrades.
            for _p in model_gateway._profiles.values():
                if _p.fallback_profiles:
                    deployment_health_router.set_fallbacks(
                        _p.model_profile_id,
                        list(_p.fallback_profiles),
                    )
            app.state._deployment_health_router = deployment_health_router
            app.state._model_gateway._deployment_health_checker = deployment_health_checker

            # Warn when a reasoning-model profile has a low max_output_tokens budget.
            _REASONING_MODEL_PREFIXES = ("glm-4.5", "glm-5")
            for _p in model_gateway._profiles.values():
                _mn = (_p.model_name or "").lower()
                if (
                    any(_mn.startswith(_pfx) for _pfx in _REASONING_MODEL_PREFIXES)
                    and (_p.max_output_tokens or 0) < 8192
                ):
                    logger.warning(
                        "Profile %s: max_output_tokens=%d may be too low for "
                        "reasoning model %s (reasoning_content fills first; "
                        "content may be empty). Recommend >= 8192.",
                        _p.model_profile_id,
                        _p.max_output_tokens,
                        _p.model_name,
                    )

        if model_profiles:
            # model_gateway already set above
            pass
        else:
            # S1 fix: model gateway is unconfigured — set a flag so /readyz
            # can report NOT ready and the dispatcher fails-loud instead of
            # silently completing every task with the noop executor.
            app.state._model_unconfigured = True
            if uc is not None and uc.allow_unconfigured_model:
                logger.info("Model gateway intentionally disabled for this process")
            else:
                logger.warning(
                    "No model_profiles loaded — model gateway is unconfigured. "
                    "All agent dispatch will fail until model profiles are "
                    "provided via GLUDD_CONFIG_DIR / config/model_profiles/*.yml."
                )

        if getattr(app.state, "eval_harness", None) is None:
            app.state.eval_harness = EvalHarness(model="sonnet")

        # LangChain/LangGraph integration: feature-flag-gated construction of
        # LangChainModelRouter and LangChainRetryGateway. Both default OFF.
        _use_langchain_routing = bool(getattr(uc, "use_langchain_routing", False)) if uc else False
        _use_langchain_retry = bool(getattr(uc, "use_langchain_retry", False)) if uc else False
        app.state._langchain_router = None
        app.state._langchain_retry_gateway = None
        if _use_langchain_routing:
            from general_ludd.models.langchain_router import LangChainModelRouter

            app.state._langchain_router = LangChainModelRouter()
            logger.info("LangChainModelRouter enabled for model routing")
        if _use_langchain_retry and model_gateway is not None:
            from general_ludd.models.langchain_retry import LangChainRetryGateway

            app.state._langchain_retry_gateway = LangChainRetryGateway(model_gateway)
            logger.info("LangChainRetryGateway enabled for retry/fallback orchestration")

        # H4 (W3.2): wire a real ReturnReviewer into the review phase when a
        # gateway exists. Review failure escalates the todo; it is never a
        # silent pass.
        return_reviewer = None
        adversarial_detector = AdversarialCodeDetector()
        estimation_tracker = EstimationTracker()
        app.state._adversarial_detector = adversarial_detector
        app.state._estimation_tracker = estimation_tracker
        daemon_state["_adversarial_detector"] = adversarial_detector
        daemon_state["_estimation_tracker"] = estimation_tracker
        logger.info(
            "Wired adversarial detector (%d patterns) and estimation tracker",
            len(adversarial_detector.get_all_categories()),
        )
        if model_gateway is not None and uc is not None and uc.service_discovery_enabled:
            from general_ludd.review.reviewer import ReturnReviewer

            return_reviewer = ReturnReviewer(
                gateway=model_gateway,
                prompt_registry=prompt_registry,
                router=ext.get("adaptive_router"),
                budget_guard=budget_guard,
                adversarial_detector=adversarial_detector,
                estimation_tracker=estimation_tracker,
            )

        langgraph_reviewer = None
        review_cfg: dict[str, Any] = {}
        if uc is not None:
            hitl = getattr(uc, "human_in_the_loop", None)
            review_cfg["human_in_the_loop"] = bool(getattr(hitl, "enabled", False))
            review_cfg["confidence_threshold"] = float(getattr(hitl, "confidence_threshold", 0.7))
        if model_gateway is not None:
            review_use_langgraph = False
            if uc is not None:
                with contextlib.suppress(Exception):
                    review_use_langgraph = bool(
                        getattr(uc, "use_langgraph_review", False)
                        or startup_config.get("review", {}).get("use_langgraph", False)
                    )
            if review_use_langgraph:
                from general_ludd.review.langgraph_reviewer import LangGraphReflexiveReviewer

                review_cfg = {"use_langgraph": True}

                def _langgraph_call_model(prompt: str) -> str:
                    try:
                        response = model_gateway.call_model(
                            "default",
                            messages=[{"role": "user", "content": prompt}],
                            work_type="review",
                        )
                        return cast(str, response.content)
                    except Exception as exc:
                        logger.debug("langgraph model call failed")
                        raise LangGraphModelCallError(exc) from exc

                langgraph_reviewer = LangGraphReflexiveReviewer(
                    call_model=_langgraph_call_model,
                    max_iterations=startup_config.get("review", {}).get("max_iterations", 3),
                    confidence_threshold=startup_config.get("review", {}).get("confidence_threshold", 0.8),
                )
                logger.info(
                    "LangGraphReflexiveReviewer enabled: max_iterations=%d confidence_threshold=%.2f",
                    langgraph_reviewer._max_iterations,
                    langgraph_reviewer._confidence_threshold,
                )

        # G11: Consensus-based multi-agent review. Wired when a model gateway
        # exists so the consensus review path (3-agent debate) is available.
        # Config-gated via ``consensus_review.enabled`` (default OFF).
        consensus_reviewer = None
        consensus_cfg: dict[str, Any] = {}
        if uc is not None:
            with contextlib.suppress(Exception):
                cc = getattr(uc, "consensus_review", None)
                if cc is not None:
                    consensus_cfg["enabled"] = bool(getattr(cc, "enabled", False))
                    consensus_cfg["num_agents"] = int(getattr(cc, "num_agents", 3))
                    consensus_cfg["max_rounds"] = int(getattr(cc, "max_rounds", 3))
        if model_gateway is not None:
            from general_ludd.review.consensus_reviewer import ConsensusReviewer

            consensus_reviewer = ConsensusReviewer(
                gateway=model_gateway,
                num_agents=consensus_cfg.get("num_agents", 3),
                max_rounds=consensus_cfg.get("max_rounds", 3),
                use_langgraph=False,
            )
            logger.info(
                "ConsensusReviewer wired: num_agents=%d max_rounds=%d (enabled=%s)",
                consensus_reviewer._num_agents,
                consensus_reviewer._max_rounds,
                consensus_cfg.get("enabled", False),
            )

        # H2 (W3.7): self-improvement interval comes from config; 0 disables it.
        # interval=0 → disabled; default is 10 minutes so the feature is on out-of-the-box.
        self_improve_interval = 0
        if uc is not None:
            si_cfg = getattr(uc, "self_improve", None) or {}
            with contextlib.suppress(Exception):
                self_improve_interval = int(si_cfg.get("interval", 10))
        if not self_improve_interval:
            with contextlib.suppress(Exception):
                self_improve_interval = int(startup_config.get("self_improve_interval", 10))

        # Compaction eval wiring: build a self-improving compactor with the
        # default candidate pool. The arena can be re-run at runtime via the
        # /admin/compaction/eval-status endpoint to re-evaluate the champion.
        from general_ludd.compaction.arena import build_self_improving_compactor
        from general_ludd.compaction.evaluate import CompactionMetrics as EvalMetrics

        _summary_fn = None
        if model_gateway is not None and hasattr(model_gateway, "_profiles"):
            from general_ludd.compaction.slm import make_slm_summarize_fn

            try:
                _summary_fn = make_slm_summarize_fn(model_gateway, profile_id="compactor")
            except Exception:
                logger.info(
                    "SLM summarizer unavailable for compaction eval — running "
                    "with offline fallback (candidates use extractive truncation)"
                )

        _compaction_compactor = build_self_improving_compactor(
            summarize_fn=_summary_fn,
        )
        app.state._compaction_compactor = _compaction_compactor
        app.state._compaction_metrics = EvalMetrics(compactor="noop")
        logger.info(
            "Compaction eval wired: champion=%s",
            _compaction_compactor.champion.name,
        )

        # W3.9 MCP wiring: build MCPToolRegistry and conditionally start MCPClient
        from general_ludd.mcp.client import MCPClient
        from general_ludd.mcp.config import MCPServerConfig
        from general_ludd.mcp.registry import MCPToolRegistry

        mcp_tool_registry = MCPToolRegistry()
        mcp_client = None
        mcp_configs = startup_config.get("mcp_servers", {}) or {}
        if mcp_configs:
            # Ensure values are MCPServerConfig instances
            typed_configs: dict[str, MCPServerConfig] = {}
            for srv_id, srv_cfg in mcp_configs.items():
                if isinstance(srv_cfg, MCPServerConfig):
                    typed_configs[srv_id] = srv_cfg
                elif isinstance(srv_cfg, dict):
                    typed_configs[srv_id] = MCPServerConfig(**srv_cfg)
            if typed_configs:
                try:
                    mcp_client = MCPClient(
                        configs=typed_configs,
                        registry=mcp_tool_registry,
                        secrets_mgr=secrets_resolver,
                    )
                    await mcp_client.start_all()
                    # Expose gludd's own in-process builtin tools (e.g.
                    # run_project_check) alongside the external MCP servers so
                    # the agent can run a target project's declared checks. This
                    # only registers a synthetic "gludd-builtin" server on the
                    # already-built client; it does not touch external flows.
                    from general_ludd.mcp.builtins import register_builtins

                    # Construct a shared WebRetriever so the MCP builtin tool
                    # reuses one cache across calls instead of creating a fresh
                    # diskcache per invocation.
                    from general_ludd.retrieval.web import WebRetriever

                    _web_retriever = WebRetriever()
                    app.state._web_retriever = _web_retriever

                    # Isolate builtin registration: a failure here (e.g. an
                    # external server already advertising the same tool name,
                    # which the registry rejects as a collision) must NOT
                    # discard the working external MCP client or leak its
                    # already-started subprocesses.
                    try:
                        register_builtins(mcp_client, web_retriever=_web_retriever)
                    except Exception:
                        logger.warning(
                            "builtin MCP tool registration failed; continuing with external MCP servers only",
                        )
                    logger.info("MCPClient started with %d server(s)", len(typed_configs))
                except Exception as _mcp_exc:
                    logger.error(
                        "MCP startup failed (continuing without MCP)",
                    )
                    if mcp_client is not None:
                        try:
                            await mcp_client.stop_all()
                        except Exception:
                            logger.warning("MCP cleanup during startup failure also failed")
                    mcp_client = None
        app.state._mcp_client = mcp_client

        # Completion-integrity HIGH fix (audit a30dc5ac): wire a DynamicDispatcher
        # so the EventLoop can EXECUTE a model's MCP tool-call instead of dropping
        # it ("no dispatcher is wired"). Acts under the mcp-capable "event_loop"
        # role; None when there's nothing to dispatch (loop keeps prior behaviour).
        event_loop_dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=mcp_client,
            mcp_tool_registry=mcp_tool_registry,
            skill_registry=ext["skill_registry"],
        )

        # H3 fix: SpendLimiter must be constructed and rehydrated BEFORE
        # asyncio.create_task(event_loop.run_forever(...)) so the event loop's
        # first tick cannot bypass the operator spend cap.  PricingCatalog and
        # SpendLimiter are built here (including the persisted-spend rehydration
        # await) and then passed into the EventLoop constructor so _spend_limiter
        # is never None once the loop task is scheduled.
        # W: event-loop-wiring (#27) — SpendLimiter pre-call budget gate.
        # Build a rolling-window limiter from budget config when configured.
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.pricing_intel import PricingCatalog

        # PricingCatalog is the PRIMARY price source for cost projection;
        # SpendLimiter.token_cost_usd() falls back to the static
        # infra/pricing.py table when the catalog has no live price.  Shared
        # across the limiter and any other subsystem that needs live rates.
        pricing_catalog = PricingCatalog()
        # Publish the catalog on app.state so /api/pricing (routers/observe.py)
        # can serve the SAME instance the SpendLimiter consumes — not a second
        # copy. Routers read it via ``_get_pricing_catalog(app)``.
        app.state._pricing_catalog = pricing_catalog

        spend_limiter: SpendLimiter | None = None
        if uc is not None:
            spend_window_usd = bc.spend_window_usd
            spend_window_seconds = bc.spend_window_seconds
            if spend_window_usd > 0.0:
                import time as _time

                # Wall-clock (time.time), NOT monotonic: persisted spend
                # timestamps must survive a process restart so the rolling
                # window can be rehydrated from the DB (#49 #2).
                spend_limiter = SpendLimiter(
                    limit_usd=spend_window_usd,
                    window_seconds=spend_window_seconds,
                    clock=_time.time,
                    catalog=pricing_catalog,
                )
                logger.info(
                    "SpendLimiter configured: limit=%.4f USD / %.0f s window",
                    spend_window_usd,
                    spend_window_seconds,
                )
                # Rehydrate accumulated spend so a restart can't reset the cap.
                await _restore_persisted_spend(
                    spend_limiter,
                    session_factory,
                    window_seconds=spend_window_seconds,
                )
        app.state._spend_limiter = spend_limiter

        # Construct only after the rolling limiter has been restored. This
        # guarantees the engine shares the live daemon limiter before its first
        # dispatch and leaves EventLoop as the single spend-record DB writer.
        if model_gateway is not None and semantic_searcher is not None:
            execution_engine = ExecutionEngine(
                model_gateway=model_gateway,
                benchmark_recorder=None,
                metrics_collector=ext.get("metrics_collector"),
                budget_guard=budget_guard,
                searcher=semantic_searcher,
                spend_limiter=spend_limiter,
            )
            app.state._execution_engine = execution_engine

        # Prepaid service credit tracker — queries DeepSeek / OpenAI / Z.AI /
        # OpenRouter balance APIs on the EventLoop's periodic
        # check_service_credits phase and exposes results via GET /api/credits.
        # API keys are read lazily from the conventional env vars per provider
        # (DEEPSEEK_API_KEY, OPENAI_API_KEY, ZAI_API_KEY, OPENROUTER_API_KEY).
        from general_ludd.budget.credit_tracker import CreditTracker

        credit_tracker = CreditTracker(
            thresholds=getattr(uc, "credit_thresholds", None) if uc else None,
            historical_spend_rates=getattr(uc, "credit_spend_rates", None) if uc else None,
        )
        app.state._credit_tracker = credit_tracker

        memory_repo = MemoryRepository(session_factory=session_factory)
        app.state._memory_repo = memory_repo

        local_memory = LocalAgentMemory()
        app.state._local_memory = local_memory
        logger.info("LocalAgentMemory initialised (cache: %s)", local_memory.cache_dir)

        from general_ludd.memory.procedural import ProceduralMemoryStore

        procedural_memory = ProceduralMemoryStore(memory_repo=memory_repo)
        app.state._procedural_memory = procedural_memory
        logger.info("ProceduralMemoryStore wired into daemon")

        from general_ludd.memory.semantic import SemanticMemoryStore

        semantic_memory = SemanticMemoryStore(memory_repo=memory_repo)
        app.state._semantic_memory = semantic_memory
        logger.info("SemanticMemoryStore wired into daemon")

        from general_ludd.memory.embedding_store import MemoryEmbeddingStore

        embedding_memory = MemoryEmbeddingStore(memory_repo=memory_repo)
        app.state._embedding_memory = embedding_memory
        logger.info("MemoryEmbeddingStore wired into daemon (in-memory index)")

        # P3: VM sandbox config — load from UserConfig, override SandboxConfig,
        # and optionally pre-build the default image at startup.
        vm_sandbox_cfg = VmSandboxConfig()
        if uc is not None:
            _vm_raw = getattr(uc, "vm_sandbox", None)
            if _vm_raw is not None:
                vm_sandbox_cfg = _vm_raw

        sandbox_executor = SandboxExecutor(timeout=30)
        sandbox_config = SandboxConfig(
            backend=vm_sandbox_cfg.image_type if vm_sandbox_cfg.enabled else "auto",
            isolation=IsolationLevel.NONE,
            image_path=vm_sandbox_cfg.default_image,
            vsock_port=vm_sandbox_cfg.vsock_port,
            memory_mb=vm_sandbox_cfg.mem_mib,
        )
        from general_ludd.security.policy.profiles import resolve_sandbox_profile
        from general_ludd.security.sandboxes.attestation import (
            DurableSandboxAttestationStore,
        )

        sandbox_profile = resolve_sandbox_profile(vm_sandbox_cfg.profile)
        sandbox_attestation_store = DurableSandboxAttestationStore(session_factory)
        app.state._sandbox_config = sandbox_config
        app.state._sandbox_router = SandboxCapabilityRouter(sandbox_config)
        app.state._vm_sandbox_config = vm_sandbox_cfg
        app.state._sandbox_profile = sandbox_profile

        if vm_sandbox_cfg.enabled and vm_sandbox_cfg.auto_build:
            try:
                from general_ludd.security.sandboxes.vm.image_builder import (
                    ImageManifest,
                    build_rootfs,
                )

                _img_path = vm_sandbox_cfg.default_image or str(
                    Path.home() / ".cache" / "gludd" / "sandbox" / "default.ext4"
                )
                _manifest = ImageManifest(
                    name="gludd-sandbox-default",
                    packages=("python3", "ansible", "git"),
                    architecture="x86_64",
                )
                _built = await asyncio.to_thread(
                    build_rootfs,
                    _img_path,
                    vm_sandbox_cfg.image_type,
                    _manifest,
                )
                logger.info(
                    "VM sandbox default image built: %s (%d bytes, type=%s, hash=%s)",
                    _built.path,
                    _built.size_bytes,
                    _built.image_type,
                    _built.manifest_hash[:12],
                )
            except Exception:
                logger.warning(
                    "VM sandbox auto_build failed — continuing without pre-built image",
                )

        _cfg_dir = getattr(app.state, "_config_dir", None)
        replay_dir = os.path.join(_cfg_dir, "replay") if _cfg_dir else ".gludd/replay"
        run_recorder = RunRecorder(_FS(root_path=replay_dir))
        app.state._run_recorder = run_recorder

        issue_ingestor = None
        if uc is not None:
            issues_cfg = getattr(uc, "issues", None)
            if issues_cfg is not None and getattr(issues_cfg, "polling_enabled", False):
                from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor

                issue_ingestor = GitHubIssueIngestor(
                    owner=getattr(issues_cfg, "github_owner", ""),
                    repo=getattr(issues_cfg, "github_repo", ""),
                    label=getattr(issues_cfg, "github_label", "gludd"),
                    poll_interval_seconds=getattr(issues_cfg, "poll_interval_ticks", 300),
                    seen_ids=daemon_state.setdefault("issue_ingestor_seen_ids", {}).setdefault(
                        (
                            f"{getattr(issues_cfg, 'github_owner', '')}/"
                            f"{getattr(issues_cfg, 'github_repo', '')}#"
                            f"{getattr(issues_cfg, 'github_label', 'gludd')}"
                        ),
                        set(),
                    ),
                )
                app.state._issue_ingestor = issue_ingestor
                logger.info("Issue ingestor wired: polling enabled")

        _checkpointing_cfg = getattr(uc, "checkpointing", {}) if uc else {}
        _checkpointing_enabled = (
            bool(_checkpointing_cfg.get("enabled", False)) if isinstance(_checkpointing_cfg, dict) else False
        )
        if _checkpointing_enabled:
            app.state.checkpointer = get_checkpointer(
                db_url=str(engine.url) if engine and str(engine.url).startswith("sqlite") else None
            )
        else:
            from general_ludd.execution.graph_checkpointer import TickCheckpointer

            app.state.checkpointer = TickCheckpointer(saver=None)

        # Cost-tracking deps constructed BEFORE the EventLoop so the bill-7
        # idle-GPU teardown phase (loop.py:3595 record_gpu_seconds / loop.py:3610
        # deployment_manager.destroy) receives live instances, not None. Prior to
        # this, InfraTracker was built ~170 lines later and DeploymentManager was
        # never built in the daemon (only lazily by routers/compute.py), so both
        # were permanently None on the EventLoop → GPU-seconds never recorded and
        # idle GPUs unregistered from bookkeeping but never actually destroyed.
        # InfraTracker shares the SAME pricing_catalog as the SpendLimiter (H3).
        # Publishing deployment_manager on app.state lets routers/compute.py's
        # identity-check cache reuse the SAME instance so /admin/compute/destroy
        # and the idle-teardown tick agree on deployment state.
        from general_ludd.infra.deployment import DeploymentManager
        from general_ludd.infra.pricing import InfraTracker

        infra_tracker = InfraTracker(catalog=pricing_catalog)
        app.state._infra_tracker = infra_tracker

        deployment_manager = getattr(app.state, "_deployment_manager", None)
        if deployment_manager is None:
            _cfg_dir = getattr(app.state, "_config_dir", None)
            _deploy_working_dir = os.path.join(_cfg_dir, "deployments") if _cfg_dir else None
            deployment_manager = DeploymentManager(
                secrets_resolver=secrets_resolver,
                working_dir=_deploy_working_dir,
                event_bus=subsys["bus"],
                session_factory=session_factory,
                worker_id=f"{os.environ.get('GLUDD_WORKER_ID', _DEFAULT_WORKER_ID)}-{os.getpid()}",
            )
            app.state._deployment_manager = deployment_manager

        service_discovery = None
        if uc is not None and uc.service_discovery_enabled:
            from general_ludd.infra.service_catalog import DEFAULT_CATALOG_PATH

            searx_url = getattr(uc, "service_discovery_searx_url", "http://localhost:8888")
            catalog_path = getattr(uc, "service_discovery_catalog_path", DEFAULT_CATALOG_PATH)
            from general_ludd.service_discovery.pipeline import ServiceDiscoveryPipeline

            service_discovery = ServiceDiscoveryPipeline(
                searx_url=searx_url,
                catalog_path=catalog_path,
            )
            logger.info("ServiceDiscoveryPipeline wired: searx=%s catalog=%s", searx_url, catalog_path)

        searx_model_discoverer = None
        if model_gateway is not None:
            from general_ludd.infra.model_search import SEARX_DEFAULT_URL
            from general_ludd.models.searx_discoverer import SearxModelDiscoverer

            _srv = getattr(app.state, "_searx_server", None)
            _discover_url = _srv.get_instance_url() if _srv else None
            searx_model_discoverer = SearxModelDiscoverer(
                gateway=model_gateway,
                searx_url=_discover_url or SEARX_DEFAULT_URL,
            )
            try:
                searx_model_discoverer.sync_models()
            except Exception:
                logger.info("SearX model discoverer sync skipped at startup")
            app.state._searx_model_discoverer = searx_model_discoverer
            logger.info(
                "SearxModelDiscoverer wired: searx=%s index=%d",
                _discover_url or "default",
                searx_model_discoverer.index_size,
            )

        self_improve_config = dict(getattr(uc, "self_improve", {}) if uc else {})
        self_improve_runner_factory = _build_self_improve_runner_factory(
            self_improve_config
        )
        self_improve_executor = ManagedSelfImproveProcessExecutor(
            runner_factory=self_improve_runner_factory,
            timeout_seconds=managed_execution_timeout_seconds(self_improve_config),
            event_sink=_log_owned_self_improve_event,
        )

        event_loop = EventLoop(
            worker_base_url="http://localhost:8000",
            runner=runner,
            session=session_factory,
            http_client=None,
            todo_repo=None,
            task_return_repo=None,
            budget_guard=budget_guard,
            model_gateway=model_gateway,
            mcp_client=mcp_client,
            mcp_tool_registry=mcp_tool_registry,
            dispatcher=event_loop_dispatcher,
            event_bus=subsys["bus"],
            project_manager=ext["projects"],
            skill_registry=ext["skill_registry"],
            prompt_registry=prompt_registry,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": startup_config.get("model_profiles", []),
                "rules": startup_config.get("rules", []),
                "queues": getattr(uc, "queues", []) if uc else [],
                "budget": getattr(uc, "budget", {}) if uc else {},
                "self_improve": self_improve_config,
                # #56: reachable SLM context-compaction on the generation path.
                # Serialized to a plain dict so the EventLoop config stays a
                # dict[str, Any]. Default OFF (compaction.enabled = False).
                "compaction": _compaction_config_dict(uc),
                # Daemon-level default repo_root: the process cwd at startup time
                # is a reasonable single-project fallback so verify_completion can
                # check commit:/artifact: refs without a resolved per-project
                # workspace. EventLoop._resolve_repo_root() overrides this with the
                # per-project workspace.repo_dir when available.
                "repo_root": os.getcwd(),
                "review": review_cfg,
                "use_langgraph_tool_loop": bool(getattr(uc, "use_langgraph_tool_loop", False)) if uc else False,
                "compute_idle_check_interval_ticks": getattr(uc, "compute_idle_check_interval_ticks", 60) if uc else 60,
                "compute_idle_teardown_threshold_ticks": getattr(uc, "compute_idle_teardown_threshold_ticks", 3)
                if uc
                else 3,
                "compute_idle_gpu_sm_pct": getattr(uc, "compute_idle_gpu_sm_pct", 5.0) if uc else 5.0,
                "compute_idle_preemption_notice_ticks": getattr(uc, "compute_idle_preemption_notice_ticks", 1)
                if uc
                else 1,
                # #52: auto-remediation tick-phase cadence + per-tick action cap.
                # The RemediationConfig thresholds themselves are NOT read from
                # here — they live on daemon_state["remediation_config"] (set
                # below via _remediation_config_from_uc) so the tick phase and
                # the /admin/remediation/* HTTP endpoints share one instance.
                "remediation_check_interval_ticks": _remediation_tick_settings(uc)[0],
                "remediation_max_actions_per_tick": _remediation_tick_settings(uc)[1],
                # SPD-1: how often the EventLoop persists in-memory spend
                # records to the spend_records table (in ticks). 60 ticks ≈
                # 60 seconds at the default 1 s tick interval.  <=0 disables.
                "spend_persist_interval_ticks": getattr(uc, "spend_persist_interval_ticks", 60) if uc else 60,
                # STS token reaper: sweep TTL-expired tokens every N ticks.
                # Default 60 (~60s at the 1s tick interval). <=0 disables.
                "sts_reap_interval_ticks": getattr(uc, "sts_reap_interval_ticks", 60) if uc else 60,
            },
            adaptive_router=ext["adaptive_router"],
            daemon_state=daemon_state,
            project_workspace=_init_project_workspaces(ext["projects"]),
            project_secrets_manager=secrets_resolver,
            reviewer=return_reviewer,
            consensus_reviewer=consensus_reviewer,
            langgraph_reviewer=langgraph_reviewer,
            self_improve_interval=self_improve_interval,
            self_improve_runner_factory=self_improve_runner_factory,
            self_improve_executor=self_improve_executor,
            # H3: spend_limiter passed via constructor so _spend_limiter is set
            # before the run_forever task is scheduled — the first tick can never
            # bypass the operator spend cap.
            spend_limiter=spend_limiter,
            # #31 (multi-agent safety): share the coordination router's
            # FileClaimRegistry (created in routers/coordination.register and
            # surfaced via /api/coordination + /api/facts) with the event loop's
            # git-delivery path so concurrent todos cannot clobber the same file.
            file_claim_registry=getattr(app.state, "_file_claims", None),
            ansible_env_updater=getattr(app.state, "_ansible_env_updater", None),
            deployment_health_router=deployment_health_router,
            pause_controller=getattr(app.state, "_pause_controller", None),
            memory_repo=memory_repo,
            sandbox_executor=sandbox_executor,
            sandbox_config=sandbox_config,
            sandbox_attestation_store=sandbox_attestation_store,
            sandbox_profile=sandbox_profile,
            run_recorder=run_recorder,
            checkpointer=app.state.checkpointer,
            utilization_tracker=getattr(app.state, "_utilization_tracker", None),
            deployment_manager=deployment_manager,
            floor_controller=floor_controller,
            issue_ingestor=issue_ingestor,
            infra_tracker=infra_tracker,
            compaction_controller=getattr(app.state, "_compaction_aggressiveness_controller", None),
            credit_tracker=getattr(app.state, "_credit_tracker", None),
            service_discovery=service_discovery,
            decision_codification=getattr(app.state, "decision_codification", None),
        )
        app.state.event_loop = event_loop
        app.state.event_loop._runner = runner
        from general_ludd.infra.deployment_events import (
            PostgresWakeupListener,
            TerraformEventBridge,
        )

        terraform_worker_id = f"{os.environ.get('GLUDD_WORKER_ID', _DEFAULT_WORKER_ID)}-{os.getpid()}"
        terraform_wakeup_listener = None
        if engine.dialect.name == "postgresql":
            terraform_wakeup_listener = PostgresWakeupListener(
                database_url=engine.url.render_as_string(hide_password=False),
                session_factory=session_factory,
                wake=event_loop.wake,
                worker_id=terraform_worker_id,
                reconnect_min_seconds=float(os.environ.get("GLUDD_PG_WAKE_RECONNECT_SECONDS", "0.1")),
                reconnect_max_seconds=float(os.environ.get("GLUDD_PG_WAKE_RECONNECT_MAX_SECONDS", "5.0")),
            )

        terraform_event_bridge = TerraformEventBridge(
            event_bus=subsys["bus"],
            session_factory=session_factory,
            wake=event_loop.wake,
            worker_id=terraform_worker_id,
            listener=terraform_wakeup_listener,
        )
        terraform_event_bridge.start()
        app.state._terraform_event_bridge = terraform_event_bridge
        daemon_state["human_gate"] = event_loop._human_gate
        # #52: single config source for the auto-remediation tick phase AND
        # the /admin/remediation/* HTTP endpoints (see
        # _remediation_config_from_uc — daemon_state previously never
        # carried a RemediationConfig, so the router always fell back to
        # hardcoded defaults regardless of operator config).
        daemon_state["remediation_config"] = _remediation_config_from_uc(uc)
        app.state._runner = runner
        app.state._db_engine = engine
        app.state._session_factory = session_factory
        app.state._training_data_session_factory = session_factory
        # Preserve an explicitly injected task used by health/readiness probes
        # in tests and embedding applications. The real runtime task is still
        # started and tracked locally for shutdown; an auto-created task is
        # intentionally not considered ready until a caller replaces the
        # probe handle (or a future readiness signal is added).
        probe_task = getattr(app.state, "_event_loop_task", None)
        task = asyncio.create_task(event_loop.run_forever(interval=tick_interval))
        task.add_done_callback(_on_event_loop_done)
        app.state._event_loop_runtime_task = task
        app.state._event_loop_task = probe_task if probe_task is not None else task
        app.state._event_loop_task_auto = probe_task is None

        from general_ludd.controllers.budget_manager import BudgetManager
        from general_ludd.observability.metrics_exporter import get_metrics_exporter
        from general_ludd.observability.run_history import RunHistoryRecorder

        app.state._budget_manager = BudgetManager(
            daily_limit_usd=bc.daily_limit,
            per_todo_limit_usd=bc.per_task_limit,
        )
        app.state._run_history = RunHistoryRecorder()
        app.state._dashboard_data = DashboardDataProvider(
            metrics_exporter=get_metrics_exporter(),
            session_factory=session_factory,
        )

        benchmark_recorder = AutoBenchmarkRecorder(
            benchmark_repo=BenchmarkRepository(session_factory=session_factory),
            trace_buffer=getattr(app.state, "_recent_traces", None),
        )
        event_loop._benchmark_recorder = benchmark_recorder

        model_perf_repo = ModelPerformanceRepository(
            session_factory=session_factory,
        )
        event_loop._model_perf_repo = model_perf_repo
        app.state.model_perf_repo = model_perf_repo

        from general_ludd.models.performance_router import (
            ModelPerformanceRepository as _PerfRepoProtocol,
        )
        from general_ludd.models.performance_router import (
            ModelPerformanceRouter,
        )

        app.state._model_performance_router = ModelPerformanceRouter(
            perf_repo=cast(_PerfRepoProtocol, model_perf_repo),
        )

        from general_ludd.worktree.core import WorktreeMonitor, WorktreeMonitorConfig

        config_dir = getattr(app.state, "_config_dir", None)
        wt_monitor = WorktreeMonitor(
            config=WorktreeMonitorConfig(
                watch_paths=[config_dir] if config_dir else [],
            ),
        )
        app.state._worktree_monitor = wt_monitor

        from general_ludd.quantization.monitor import MonitorConfig as QuantMonitorConfig
        from general_ludd.quantization.monitor import QuantizationMonitor

        quant_monitor = QuantizationMonitor(QuantMonitorConfig())
        app.state._quantization_monitor = quant_monitor
        await quant_monitor.start()

        from general_ludd.agents.dispatcher import AgentDispatcher
        from general_ludd.agents.registry import default_registry
        from general_ludd.agents.types import AgentTask

        # Use default_registry() so the 4 built-in agents (build/plan/explore/
        # general) are registered. A bare AgentRegistry() leaves the registry
        # empty, which makes the dispatcher's can_invoke permission gate
        # (dispatcher.py) reject every dispatch ("not found in registry") —
        # silently disabling the agent-permission matrix in the daemon.
        registry = default_registry()
        dispatcher_executor = None

        # SpendLimiter, PricingCatalog, and spend_limiter are built and
        # rehydrated above, BEFORE the EventLoop constructor (H3 fix).
        # make_spend_guarded_executor is imported here for the gateway executor
        # block below.
        from general_ludd.daemon_wiring import make_spend_guarded_executor

        # InfraTracker / DeploymentManager are constructed earlier, before the
        # EventLoop constructor (see the "Cost-tracking deps" block above), so
        # the loop's bill-7 teardown phase receives live instances instead of
        # None. InfraTracker shares the SAME pricing_catalog as the SpendLimiter.
        if model_gateway is not None:
            logger.info(
                "Gateway-backed executor enabled with %d model profile(s)",
                len(model_profiles),
            )

            # W: event-loop-wiring (#27) — compute the per-call cost projection
            # BEFORE defining _gateway_executor so the closure captures the real
            # value for the BudgetManager pre-checks.  Both layers use this same
            # projection: BudgetManager gates daily/per-todo ceilings against
            # (cumulative actuals + this projection); SpendLimiter enforces the
            # rolling-window soft cap atomically.  Passing 0.0 to BudgetManager
            # made its pre-checks purely reactive (could only block AFTER a prior
            # call's actual cost already crossed the limit, never the call that
            # would itself exceed it).
            #
            # Prefer the SpendLimiter's projection (PricingCatalog primary,
            # static table fallback) when a limiter is wired; otherwise use the
            # standalone static token_cost_usd() so projection still works when
            # budgeting is disabled.
            from general_ludd.infra.pricing import token_cost_usd

            _projected_cost_usd = 0.0
            _default_profile = model_gateway.get_profile("default")
            if _default_profile is not None:
                _project_model = _default_profile.model_name or "__default__"
                _project_in = min(_default_profile.max_input_tokens, 1000)
                _project_out = _default_profile.max_output_tokens
                if spend_limiter is not None:
                    _projected_cost_usd = spend_limiter.token_cost_usd(_project_model, _project_in, _project_out)
                else:
                    _projected_cost_usd = token_cost_usd(_project_model, _project_in, _project_out)

            async def _gateway_executor(task: AgentTask) -> str:
                # S10: route to the best-cost profile via ModelPerformanceRouter
                # when data exists; fall back to "default" for cold start.
                _perf_router = getattr(app.state, "_model_performance_router", None)
                if _perf_router is not None:
                    try:
                        profile_id = _perf_router.select_cost_effective_profile(
                            task_type=task.agent_name or "generation",
                        )
                    except Exception:
                        profile_id = "default"
                else:
                    profile_id = "default"
                budget_manager = getattr(app.state, "_budget_manager", None)

                _saved_env: dict[str, str] = {}
                _sts_env = getattr(task, "env", None)
                if _sts_env:
                    _sts_role = _sts_env.get("GLUDD_STS_ROLE_ID")
                    _sts_secret = _sts_env.get("GLUDD_STS_SECRET_ID")
                    if _sts_role:
                        _saved_env["GLUDD_STS_ROLE_ID"] = os.environ.pop("GLUDD_STS_ROLE_ID", "")
                        os.environ["GLUDD_STS_ROLE_ID"] = _sts_role
                    if _sts_secret:
                        _saved_env["GLUDD_STS_SECRET_ID"] = os.environ.pop("GLUDD_STS_SECRET_ID", "")
                        os.environ["GLUDD_STS_SECRET_ID"] = _sts_secret

                try:
                    if budget_manager is not None:
                        daily = budget_manager.check_daily_budget_reserved(task.task_id, _projected_cost_usd)
                        if not daily.get("allowed", True):
                            logger.warning(
                                "Gateway executor deferred for %s: daily budget exhausted",
                                task.task_id,
                            )
                            return "deferred:budget_exhausted"
                        per_todo = budget_manager.check_todo_budget(task.task_id, _projected_cost_usd)
                        if not per_todo.get("allowed", True):
                            logger.warning(
                                "Gateway executor deferred for %s: per-todo budget exhausted",
                                task.task_id,
                            )
                            # Release the daily reservation made above so a deferred
                            # call does not leak held budget.
                            budget_manager.release_reservation(task.task_id)
                            return "deferred:budget_exhausted"
                    if task.agent_name == "research":
                        from general_ludd.agents.researcher import ResearcherAgent

                        searx = getattr(app.state, "_searx_client", None)
                        agent = ResearcherAgent(searx_client=searx)
                        report = await agent.research(query=task.prompt)
                        return report.model_dump_json()
                    try:
                        call_kwargs: dict[str, Any] = {}
                        if getattr(task, "tools", None):
                            call_kwargs["tools"] = task.tools
                        result = await model_gateway.call_model_with_retry(
                            profile_id,
                            [{"role": "user", "content": task.prompt}],
                            **call_kwargs,
                        )
                        if budget_manager is not None:
                            budget_manager.record_spend(
                                task.task_id,
                                float(getattr(result, "cost_estimate", 0.0) or 0.0),
                            )
                        content = result.content
                        return content if isinstance(content, str) else str(content)
                    except Exception as exc:
                        logger.warning("Gateway executor failed for %s: %s", task.task_id, exc)
                        # The call never produced a cost, so release both reservations
                        # instead of leaking the held projected budget.
                        if budget_manager is not None:
                            budget_manager.release_reservation(task.task_id)
                        return f"Error: {exc}"
                finally:
                    for k, v in _saved_env.items():
                        if v:
                            os.environ[k] = v
                        else:
                            os.environ.pop(k, None)

            dispatcher_executor = make_spend_guarded_executor(
                executor=_gateway_executor,
                spend_limiter=spend_limiter,
                projected_cost_usd=_projected_cost_usd,
            )

        app.state._agent_dispatcher = AgentDispatcher(
            registry=registry,
            executor=dispatcher_executor,
            pause_controller=app.state._pause_controller,
            hibernation=app.state._hibernation_controller,
            run_recorder=run_recorder,
        )

        # --- 3-lane multitask+merge pipeline (#77), behind config flag ----- #
        # Default OFF: only starts when pipeline.enabled is true. Owns its own
        # dispatch/integrate/gate asyncio tasks + heartbeat. The daemon owns the
        # repo it merges into (the process cwd's git root).
        app.state._pipeline_controller = None
        pipeline_cfg = getattr(uc, "pipeline", None) if uc else None
        if pipeline_cfg is not None and getattr(pipeline_cfg, "enabled", False):
            try:
                pipeline_controller = _build_pipeline_controller(
                    pipeline_cfg,
                    app.state._agent_dispatcher,
                )
                await pipeline_controller.start()
                app.state._pipeline_controller = pipeline_controller
                logger.info("Pipeline (#77) started: 3 lanes + heartbeat")
            except Exception as exc:
                logger.error("Pipeline startup failed (continuing degraded): %s", exc)

        logger.info("Daemon started: db=%s event_loop=running", engine.url)

        bootloader = BinaryBootstrapper(store=_FS())
        # P2 (perf): sync_bundled_to_filestore() read_bytes() each bundled binary
        # (multi-MB) and write_bytes() it into the filestore — blocking IO that
        # would stall the loop on boot. Offload to a thread; the returned list of
        # synced names and the method's own internal try/except are unchanged.
        synced = await asyncio.to_thread(bootloader.sync_bundled_to_filestore)
        if synced:
            logger.info("Synced bundled binaries to filestore: %s", ", ".join(synced))

        async def _init_preflight() -> None:
            loop = asyncio.get_running_loop()
            result: dict[str, Any] = await loop.run_in_executor(None, run_preflight)
            daemon_state["quality_gate"] = result
            logger.info(
                "Preflight quality gate: %s (%d/%d)",
                result["overall"],
                result["passed_count"],
                result["total_count"],
            )

        app.state._preflight_task = asyncio.create_task(_init_preflight())

        app.state._stall_watchdog = StallWatchdog(
            default_tracker(),
            on_stall=lambda r: (
                subsys["bus"].publish(
                    StallDetectedEvent(
                        operation=r.key,
                        elapsed_s=r.elapsed_s,
                        deadline_s=r.deadline_s,
                        thread_stacks=r.thread_stacks,
                    )
                ),
                subsys["bus"].publish(
                    SlowOperationEvent(
                        operation=r.key,
                        duration_s=r.elapsed_s,
                        baseline_s=r.deadline_s,
                        factor=(r.elapsed_s / r.deadline_s) if r.deadline_s > 0 else 0.0,
                    )
                ),
            )[0],
        )
        app.state._stall_watchdog.start_sweeper()

        # Wire the shared watchdog into the agent dispatcher so in-flight agent
        # tasks are registered with the stall sweeper (and hung tasks are flagged
        # + published as StallDetectedEvent). The dispatcher is constructed above
        # BEFORE the watchdog exists, so the watchdog is injected here now that
        # both are live. The dispatcher already records per-task durations into
        # default_tracker() — the SAME tracker this watchdog uses for deadlines —
        # so learned baselines drive the stall deadlines.
        _agent_dispatcher = getattr(app.state, "_agent_dispatcher", None)
        if _agent_dispatcher is not None:
            _agent_dispatcher._watchdog = app.state._stall_watchdog

        otel_bridge: Any = None
        if uc is not None and hasattr(uc, "observability"):
            obs_cfg = uc.observability
            if obs_cfg.otel_endpoint:
                otel_bridge = OTelBridge(
                    endpoint=obs_cfg.otel_endpoint,
                    service_name=obs_cfg.service_name,
                )
                app.state._otel_bridge = otel_bridge
                if otel_bridge.is_available():
                    logger.info("OTel bridge active: %s", obs_cfg.otel_endpoint)
    except Exception as exc:
        logger.error("Daemon startup failed: %s", exc)
        app.state._degraded = str(exc)
        # Also record on the shared daemon_state dict. The EventLoop's code-reload
        # health probe (_make_daemon_health_probe) reads daemon_state["_degraded"]
        # to decide whether to roll a hot-reload back; previously the flag lived
        # ONLY as an app.state attribute the probe never read, so the reload
        # health gate silently always passed and a bad reload could stick.
        daemon_state["_degraded"] = str(exc)

    # Phase 1 minimal hook: optionally launch the Ornith MCP server subprocess.
    app.state._ornith_mcp_proc = None
    _ornith_env_enabled = os.environ.get("ORNITH_ENABLED", "").lower() in {"1", "true", "yes"}
    _ornith_cfg_enabled = bool(getattr(uc, "ornith_enabled", False)) if uc is not None else False
    if _ornith_env_enabled or _ornith_cfg_enabled:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "general_ludd.ornith.mcp_server",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            app.state._ornith_mcp_proc = proc
            logger.info("Ornith MCP server subprocess launched (pid=%s)", proc.pid)
        except Exception as ornith_exc:
            logger.warning("Failed to launch Ornith MCP subprocess: %s", ornith_exc)

    # AG.12: Off-peak scheduler — defer expensive model-API tasks to cheaper
    # off-peak hours. Runs as a background asyncio task polling every 60s.
    app.state._off_peak_scheduler = None
    app.state._off_peak_stop = None
    app.state._off_peak_task = None
    try:
        from general_ludd.budget.off_peak_scheduler import OffPeakScheduler

        off_peak_cfg = getattr(uc, "off_peak", None) if uc else None
        _op_start = getattr(off_peak_cfg, "start_hour", 0)
        _op_end = getattr(off_peak_cfg, "end_hour", 6)
        _op_enabled = getattr(off_peak_cfg, "enabled", False)
        _op_cost_tracker = getattr(app.state, "_combined_cost_tracker", None)

        if _op_enabled:
            _op_sched = OffPeakScheduler(
                cost_tracker=_op_cost_tracker,
                off_peak_start=_op_start,
                off_peak_end=_op_end,
            )
            app.state._off_peak_scheduler = _op_sched
            app.state._off_peak_stop = asyncio.Event()
            app.state._off_peak_task = asyncio.create_task(
                _op_sched._background_loop(stop_event=app.state._off_peak_stop)
            )
            logger.info(
                "Off-peak scheduler started: %02d:00-%02d:00",
                _op_start,
                _op_end,
            )
    except Exception as _op_exc:
        logger.warning("Off-peak scheduler startup failed (continuing degraded): %s", _op_exc)

    # S.1: Seal the process registry so no code path can modify it
    # (register/deregister/reap) after daemon initialization.
    from general_ludd.process.registry import default_registry as _proc_default_registry

    _proc_default_registry().seal()

    _lifespan_failure: BaseException | None = None
    _shutdown_failures: list[Exception] = []
    try:
        yield
    except BaseException as exc:
        # Defer propagation until every application-owned resource below has
        # received its shutdown callback, including cancellation paths.
        _lifespan_failure = exc

    # ── Off-peak scheduler shutdown ──────────────────────────────────────
    await _drain_self_update_audit_tasks(
        getattr(app.state, "_self_update_audit_tasks", set())
    )
    _op_stop = getattr(app.state, "_off_peak_stop", None)
    _op_task = getattr(app.state, "_off_peak_task", None)
    if _op_stop is not None:
        _op_stop.set()
    if _op_task is not None and not _op_task.done():
        _op_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _op_task
    _op_scheduler = getattr(app.state, "_off_peak_scheduler", None)
    if _op_scheduler is not None:
        logger.info(
            "Off-peak scheduler shut down: %s deferred, $%.4f saved",
            _op_scheduler.savings.total_deferred,
            _op_scheduler.savings.total_savings,
        )

    # ── Slurm shutdown: scancel all active jobs owned by this daemon ──────
    _session_factory = getattr(app.state, "_session_factory", None)
    if _session_factory is not None:
        try:
            import os as _os

            _current_pid = _os.getpid()
            async with _session_factory() as session:
                slurm_repo = SlurmJobRepository(session)
                active_jobs = await slurm_repo.list_active(daemon_pid=_current_pid)
            if active_jobs:
                logger.info(
                    "Shutdown: cancelling %d active Slurm job(s) owned by pid=%d",
                    len(active_jobs),
                    _current_pid,
                )
                for job in active_jobs:
                    try:
                        from general_ludd.infra.slurm import SlurmAdapter

                        adapter = SlurmAdapter()
                        adapter.cancel(job.job_id)
                        logger.info("Slurm shutdown: cancelled job %s", job.job_id)
                        async with _session_factory() as session:
                            await SlurmJobRepository(session).update_status(job.job_id, "cancelled")
                    except Exception as cancel_exc:
                        logger.warning(
                            "Slurm shutdown: failed to cancel job %s: %s",
                            job.job_id,
                            cancel_exc,
                        )
        except Exception:
            logger.warning("Slurm shutdown hook failed")

    # Bill-2: stop all Slurm cost-cap monitors on shutdown
    monitors: dict[str, Any] = getattr(app.state, "_slurm_monitors", None) or {}
    for job_id, monitor in list(monitors.items()):
        try:
            monitor.stop()
            logger.info("Slurm shutdown: stopped cost-cap monitor for %s", job_id)
        except Exception as exc:
            logger.warning("Slurm shutdown: failed to stop monitor %s: %s", job_id, exc)

    if getattr(app.state, "_degraded", None):
        logger.warning("Daemon is running in degraded mode: %s", app.state._degraded)
    pipeline_controller = getattr(app.state, "_pipeline_controller", None)
    if pipeline_controller is not None:
        try:
            await pipeline_controller.stop()
        except Exception as exc:
            logger.warning("pipeline_controller.stop() failed during shutdown")
            _shutdown_failures.append(exc)
    mcp_client_ref = getattr(app.state, "_mcp_client", None)
    if mcp_client_ref is not None:
        try:
            await mcp_client_ref.stop_all()
        except Exception as exc:
            logger.warning("mcp_client.stop_all() failed during shutdown")
            _shutdown_failures.append(exc)
    _el = event_loop if event_loop is not None else getattr(app.state, "event_loop", None)
    _terraform_bridge = getattr(app.state, "_terraform_event_bridge", None)
    if _terraform_bridge is not None:
        try:
            await _terraform_bridge.aclose()
        except Exception as exc:
            logger.warning("terraform event bridge cleanup failed")
            _shutdown_failures.append(exc)
    _event_bus = getattr(app.state, "_event_bus", None)
    if _event_bus is not None:
        try:
            await _event_bus.drain()
        except Exception as exc:
            logger.warning("event bus drain failed during shutdown")
            _shutdown_failures.append(exc)
    if _el is not None:
        try:
            await quiesce_task_before_drain(
                task,
                request_stop=_el.stop,
                drain=_el.shutdown,
                timeout_seconds=5.0,
            )
        except Exception:
            logger.warning("event_loop shutdown failed")
    preflight_task_ref = getattr(app.state, "_preflight_task", None)
    if preflight_task_ref is not None:
        preflight_task_ref.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await preflight_task_ref
    if execution_engine is not None:
        try:
            await execution_engine.shutdown()
        except Exception:
            logger.warning("execution_engine.shutdown() failed")
    _deployment_router_ref = getattr(app.state, "_deployment_health_router", None)
    if _deployment_router_ref is not None:
        try:
            await _deployment_router_ref.aclose()
        except Exception:
            logger.warning("deployment health persistence cleanup failed")
    _credit_tracker_ref = getattr(app.state, "_credit_tracker", None)
    if _credit_tracker_ref is not None:
        try:
            _credit_tracker_ref.close()
        except Exception:
            logger.warning("CreditTracker.close() failed during shutdown")
    _secrets_resolver_ref = getattr(app.state, "_secrets_resolver", None)
    if _secrets_resolver_ref is not None:
        try:
            close = getattr(_secrets_resolver_ref, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.warning("secrets resolver close failed during shutdown")
    _searx_client_ref = getattr(app.state, "_searx_client", None)
    if _searx_client_ref is not None:
        try:
            await _searx_client_ref.close()
        except Exception:
            logger.warning("SearxNGClient.close() failed during shutdown")
    _model_gateway_ref = getattr(app.state, "_model_gateway", None)
    if _model_gateway_ref is not None:
        try:
            _model_gateway_ref.close()
        except Exception:
            logger.warning("ModelGateway.close() failed during shutdown")
    for _cache_attr in (
        "_codebase_indexer",
        "_research_index",
        "_local_memory",
        "_semantic_searcher",
    ):
        _cache_owner = getattr(app.state, _cache_attr, None)
        if _cache_owner is not None:
            try:
                _cache_owner.close()
            except Exception:
                logger.warning("%s.close() failed during shutdown", _cache_attr)
    _web_retriever_ref = getattr(app.state, "_web_retriever", None)
    _web_cache_ref = getattr(_web_retriever_ref, "_cache", None)
    if _web_cache_ref is not None:
        try:
            _web_cache_ref.close()
        except Exception:
            logger.warning("WebRetriever cache close failed during shutdown")
    _sw = getattr(app.state, "_stall_watchdog", None)
    if _sw is not None:
        with contextlib.suppress(Exception):
            _sw.stop_sweeper()
    # B3.1.3 Slice 4 — drain the WriteQueue and stop the writer subprocess
    # BEFORE disposing the engine: a lingering writer holding a DB handle
    # during engine.dispose() can deadlock. The queue is cleared (best-effort
    # lossy drain) because the writer subprocess owns durable writes; anything
    # still buffered at shutdown is abandoned by design.
    _write_queue_ref = getattr(app.state, "_write_queue", None)
    if _write_queue_ref is not None:
        with contextlib.suppress(Exception):
            _write_queue_ref.clear()
    _writer_process_ref = getattr(app.state, "_writer_process", None)
    if _writer_process_ref is not None:
        with contextlib.suppress(Exception):
            # WriterProcess.stop() polls with blocking time.sleep for up to
            # ~15s waiting on the subprocess to exit; run it off the event
            # loop so shutdown doesn't freeze the loop for that long.
            await asyncio.to_thread(_writer_process_ref.stop)
    _embedding_session_ref = getattr(app.state, "_embedding_session", None)
    if _embedding_session_ref is not None:
        with contextlib.suppress(Exception):
            await _embedding_session_ref.close()
    if engine is not None:
        try:
            await engine.dispose()
        except Exception:
            logger.warning("engine.dispose() failed")
    otel_bridge_ref = getattr(app.state, "_otel_bridge", None)
    if otel_bridge_ref is not None and hasattr(otel_bridge_ref, "shutdown"):
        try:
            otel_bridge_ref.shutdown()
        except Exception as exc:
            logger.warning("OTel bridge shutdown failed")
            _shutdown_failures.append(exc)
    _ornith_proc = getattr(app.state, "_ornith_mcp_proc", None)
    if _ornith_proc is not None:
        with contextlib.suppress(Exception):
            _ornith_proc.terminate()
            try:
                await asyncio.wait_for(_ornith_proc.wait(), timeout=5.0)
            except TimeoutError:
                _ornith_proc.kill()
                with contextlib.suppress(Exception):
                    await _ornith_proc.wait()
    _searx_srv = getattr(app.state, "_searx_server", None)
    if _searx_srv is not None:
        with contextlib.suppress(Exception):
            _searx_srv.stop()
    _quant_monitor = getattr(app.state, "_quantization_monitor", None)
    if _quant_monitor is not None:
        try:
            await _quant_monitor.stop()
        except Exception:
            logger.warning("QuantizationMonitor.stop() failed during shutdown")
    if _lifespan_failure is not None and _shutdown_failures:
        raise BaseExceptionGroup(
            "daemon body and shutdown failures",
            [_lifespan_failure, *_shutdown_failures],
        )
    if _lifespan_failure is not None:
        raise _lifespan_failure.with_traceback(_lifespan_failure.__traceback__)
    if _shutdown_failures:
        raise ExceptionGroup("daemon shutdown failures", _shutdown_failures)
