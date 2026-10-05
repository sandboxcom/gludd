"""Phase handler and self-improve methods for the EventLoop.

Extracted from loop.py to reduce module size below 5000 lines.
All methods in this mixin reference ``self`` attributes defined in
the main ``EventLoop`` class and are resolved at runtime.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from general_ludd.db import task_decisions_retention
from general_ludd.self_improve.harness import SelfImprovementHarness

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _session_scope(factory: Any) -> AsyncIterator[Any]:
    """Yield a DB session from a session factory.

    Production factories are sync-callable (``async_sessionmaker``) and
    return an async context manager (``AsyncSession``); test doubles are
    frequently async-callable (``AsyncMock``) and return a plain session
    when awaited. Both shapes are supported.
    """
    ctx = factory()
    if inspect.isawaitable(ctx):
        session = await ctx
        try:
            yield session
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                maybe = close()
                if inspect.isawaitable(maybe):
                    await maybe
    else:
        async with ctx as session:
            yield session


class EventLoopHandlers:
    """Mixin providing phase handlers, self-improve, and memory methods."""

    _self_improve_interval: int
    _total_ticks: int
    _tick_project_id: str | None
    _daemon_state: dict[str, Any] | None
    _tick_metrics: dict[str, Any]
    _todo_repo: Any
    _active_session: Any
    _model_gateway: Any
    _session_factory: Any
    _memory_repo: Any
    _config_snapshot: dict[str, Any]
    _service_discovery: Any
    _service_discovery_last_run: float
    _issue_ingestor: Any
    _issue_poll_tick_counter: int
    _issue_poll_interval_ticks: int
    _ephemeral_account_manager: Any
    _consolidation_tick_counter: int
    _consolidation_interval_ticks: int
    _procedural_memory: Any
    _semantic_memory: Any
    _model_perf_repo: Any
    _model_performance_interval: int
    _adaptive_router: Any
    _bounded_to_thread: Any
    _resolve_repo_root: Any
    _persist_self_improve_todos: Any
    config: dict[str, Any]

    async def _phase_self_improve(self) -> None:
        interval = self._self_improve_interval
        if interval <= 0:
            return
        if self._total_ticks % interval != 0:
            return
        try:
            harness = SelfImprovementHarness(
                repo_root=self._resolve_repo_root(self._tick_project_id),
                model_gateway=self._model_gateway,
            )
            recurring = await self._collect_recurring_failures()
            findings = await self._bounded_to_thread(harness.run_gap_analysis, recurring)
            todos: list[dict[str, Any]] = []
            if findings:
                todos = harness.generate_fix_todos(findings)
            grinding_todos = self._detect_grinding_patterns()
            if grinding_todos:
                todos.extend(grinding_todos)
            if todos:
                enqueued = await self._persist_self_improve_todos(todos, project_id=self._tick_project_id)
                if self._daemon_state is not None:
                    self._daemon_state["self_improve_last_analysis"] = {
                        "findings": findings,
                        "findings_count": len(findings),
                        "grinding_todos": len(grinding_todos),
                        "todos_enqueued": enqueued,
                    }
                self._tick_metrics["self_improve_gaps"] = len(findings)
                self._tick_metrics["self_improve_todos_persisted"] = enqueued
                logger.info(
                    "Self-improve cycle: %d gaps, %d grinding todos → %d persisted",
                    len(findings),
                    len(grinding_todos),
                    enqueued,
                )
            else:
                self._tick_metrics["self_improve_gaps"] = 0
            try:
                training_recorded = await self._collect_training_data_from_returns()
            except Exception:
                logger.warning(
                    "Training data collection failed",
                    exc_info=True,
                )
                training_recorded = 0
            self._tick_metrics["self_improve_training_recorded"] = training_recorded
            try:
                await self._auto_consolidate_memory()
            except Exception:
                logger.warning("AutoMemory consolidation failed", exc_info=True)
            try:
                await self._auto_cross_task_learn()
            except Exception:
                logger.warning("AutoMemory cross-task learning failed", exc_info=True)
            try:
                await self._apply_self_improvements()
            except Exception:
                logger.warning(
                    "Self-improve analysis failed",
                    exc_info=True,
                )
        except Exception as exc:
            logger.warning("Self-improve phase failed: %s", exc, exc_info=True)
            self._tick_metrics["self_improve_gaps"] = 0

    def _detect_grinding_patterns(self) -> list[dict[str, Any]]:
        from general_ludd.self_update.grinding_detector import detect_and_create_todos

        return detect_and_create_todos()

    async def _collect_recurring_failures(self) -> list[Any]:
        if self._todo_repo is None or self._active_session is None:
            return []
        si_cfg = self.config.get("self_improve", {}) if isinstance(self.config, dict) else {}
        if not isinstance(si_cfg, dict):
            si_cfg = {}
        if not si_cfg.get("ingest_recurring_failures", True):
            return []
        try:
            from general_ludd.remediation.blocker_detector import (
                BlockerDetector,
                RemediationConfig,
            )

            rc_kwargs: dict[str, Any] = {}
            if "chronic_lookback_days" in si_cfg:
                rc_kwargs["chronic_lookback_days"] = int(si_cfg["chronic_lookback_days"])
            if "min_chronic_incidents" in si_cfg:
                rc_kwargs["min_chronic_incidents"] = int(si_cfg["min_chronic_incidents"])
            config = RemediationConfig(**rc_kwargs) if rc_kwargs else RemediationConfig()
            detector = BlockerDetector(
                todo_repo=self._todo_repo,
                session=self._active_session,
                config=config,
            )
            records = await detector.chronic_blockers()
            if records:
                logger.info(
                    "Self-improve: ingested %d recurring-failure signal(s) from real work",
                    len(records),
                )
            return list(records)
        except Exception as exc:
            logger.warning(
                "Self-improve: recurring-failure ingest failed (%s); continuing with static/model gap scan",
                exc,
                exc_info=True,
            )
            return []

    async def _collect_training_data_from_returns(self) -> int:
        factory = self._session_factory
        if factory is None:
            return 0
        try:
            from sqlalchemy import select

            from general_ludd.db.models import (
                TaskDecisionModel,
                TaskReturnModel,
                TodoModel,
            )
            from general_ludd.ornith.training_data import TrainingDataCollector

            async with _session_scope(factory) as session:
                collector = TrainingDataCollector(session)

                stmt = (
                    select(TaskReturnModel)
                    .where(TaskReturnModel.status == "reviewed")
                    .order_by(TaskReturnModel.updated_at.desc().nulls_last())
                    .limit(50)
                )
                result = await session.execute(stmt)
                returns = list(result.scalars().all())

                return_ids = [tr.return_id for tr in returns]
                todo_ids = [tr.todo_id for tr in returns if tr.todo_id]

                dec_map: dict[str, Any] = {}
                if return_ids:
                    dec_stmt = select(TaskDecisionModel).where(TaskDecisionModel.return_id.in_(return_ids))
                    dec_result = await session.execute(dec_stmt)
                    dec_map = {d.return_id: d for d in dec_result.scalars().all()}

                todo_map: dict[str, Any] = {}
                if todo_ids:
                    todo_stmt = select(TodoModel).where(TodoModel.todo_id.in_(todo_ids))
                    todo_result = await session.execute(todo_stmt)
                    todo_map = {t.todo_id: t for t in todo_result.scalars().all()}

                recorded = 0
                for tr in returns:
                    decision_row = dec_map.get(tr.return_id)
                    instruction = ""
                    if tr.todo_id:
                        todo_row = todo_map.get(tr.todo_id)
                        if todo_row:
                            instruction = todo_row.title or ""
                            if todo_row.description:
                                instruction = f"{instruction}: {todo_row.description}"

                    decision = decision_row.decision if decision_row else "unknown"
                    outcome_status = "succeeded" if decision == "complete" else "rejected_by_review"

                    try:
                        pair = await collector.capture(
                            instruction=instruction or tr.work_type,
                            response=tr.result_summary or "",
                            scaffold_kind="patch",
                            agent_id=tr.producer_worker_id or "",
                            project_id=tr.project_id,
                        )
                        await collector.resolve_outcome(pair.id, outcome_status)
                        recorded += 1
                    except Exception as pair_exc:
                        logger.debug(
                            "Failed to record training pair for return %s: %s",
                            tr.return_id,
                            pair_exc,
                        )

                await session.commit()
                if recorded:
                    logger.info(
                        "Self-improve: recorded %d training (instruction, response, outcome) triples",
                        recorded,
                    )
                return recorded
        except Exception as exc:
            logger.warning(
                "Training data collection from returns failed: %s",
                exc,
                exc_info=True,
            )
            return 0

    async def _apply_self_improvements(self) -> None:
        factory = self._session_factory
        if factory is None:
            return
        try:
            from general_ludd.ornith.training_data import TrainingDataCollector
            from general_ludd.self_improve.outcomes import OutcomeAnalyzer

            async with _session_scope(factory) as session:
                collector = TrainingDataCollector(session)
                report = await collector.quality_report()

                logger.info(
                    "Self-improve quality report: total=%d resolved=%d positive=%d negative=%d",
                    report["total_pairs"],
                    report["resolved"],
                    report["positive_examples"],
                    report["negative_examples"],
                )

                rejected = await collector.list_by_statuses(
                    statuses=[
                        "rejected_by_review",
                        "rejected_by_gate",
                        "reverted",
                    ],
                    limit=100,
                    lookback_days=7,
                )

                error_patterns: dict[str, int] = {}
                stop_keywords = {"stop", "premature", "halt", "abort"}
                grind_keywords = {"grind", "token", "main_thread", "inline"}
                for ex in rejected:
                    instr = ex.instruction.lower()
                    if any(kw in instr for kw in stop_keywords):
                        error_patterns["premature_stop"] = error_patterns.get("premature_stop", 0) + 1
                    if any(kw in instr for kw in grind_keywords):
                        error_patterns["grind_failure"] = error_patterns.get("grind_failure", 0) + 1
                    if not any(kw in instr for kw in stop_keywords | grind_keywords):
                        error_patterns["generic_failure"] = error_patterns.get("generic_failure", 0) + 1

                if error_patterns:
                    logger.warning(
                        "Self-improve: detected error patterns across %d rejected examples: %s",
                        len(rejected),
                        error_patterns,
                    )
                    if self._daemon_state is not None:
                        self._daemon_state["self_improve_error_patterns"] = {
                            "patterns": error_patterns,
                            "rejected_count": len(rejected),
                            "report": report,
                        }
                else:
                    logger.info(
                        "Self-improve: no error patterns detected in %d rejected examples",
                        len(rejected),
                    )

                outcome_analyzer = OutcomeAnalyzer()
                outcome_records: list[dict[str, Any]] = []
                for ex in rejected:
                    outcome_records.append(
                        {
                            "task_type": getattr(ex, "work_type", "unknown"),
                            "model": getattr(ex, "model", "unknown"),
                            "passed": getattr(ex, "exit_code", 1) == 0,
                            "tokens_used": getattr(ex, "tokens_used", 0),
                            "duration_ms": getattr(ex, "duration_ms", 0),
                        }
                    )
                suggestions = outcome_analyzer.analyze(
                    outcomes=outcome_records,
                )
                if suggestions["suggestions"]:
                    logger.info(
                        "OutcomeAnalyzer: %d improvement suggestions",
                        len(suggestions["suggestions"]),
                    )
        except Exception as exc:
            logger.warning(
                "Self-improve analysis failed: %s",
                exc,
                exc_info=True,
            )

    async def _phase_refresh_model_performance(self) -> None:
        interval = getattr(self, "_model_performance_interval", 10)
        if interval <= 0 or self._total_ticks % interval != 0:
            return
        repo: Any = getattr(self, "_model_perf_repo", None)
        if repo is None or self._session_factory is None:
            return
        try:
            async with _session_scope(self._session_factory) as perf_session:
                refreshed = await repo.refresh_recent_stats(session=perf_session)
                await perf_session.commit()
                logger.debug(
                    "Model performance: refreshed %d profile(s)",
                    refreshed,
                )
        except Exception as exc:
            logger.warning(
                "Model performance refresh failed: %s",
                exc,
                exc_info=True,
            )
        router: Any = getattr(self, "_adaptive_router", None)
        if router is None:
            logger.warning("_adaptive_router not initialized; skipping routing decision capture")
        elif hasattr(router, "current_routing_decisions"):
            try:
                decisions = await router.current_routing_decisions()
                self._tick_metrics["model_routing_decisions"] = len(decisions)
            except Exception as exc:
                logger.debug(
                    "Model routing decision capture failed: %s",
                    exc,
                )

    async def _phase_poll_issue_sources(self) -> None:
        if self._issue_ingestor is None:
            return
        self._issue_poll_tick_counter += 1
        if self._issue_poll_tick_counter < self._issue_poll_interval_ticks:
            return
        self._issue_poll_tick_counter = 0
        try:
            new_todos = await self._issue_ingestor.poll_issues()
            if not new_todos:
                return
            persisted = 0
            for todo in new_todos:
                if self._todo_repo is None:
                    break
                try:
                    await self._todo_repo.create(todo)
                    persisted += 1
                except Exception as exc:
                    logger.warning("Failed to persist polled issue todo: %s", exc)
            if persisted:
                self._tick_metrics["issues_polled"] = persisted
                logger.info("Polled %d new issue(s) into intake queue", persisted)
        except Exception as exc:
            logger.warning("Issue source polling failed: %s", exc)

    async def _phase_service_discovery(self) -> None:
        if self._service_discovery is None:
            return
        cfg = self.config if isinstance(self.config, dict) else {}
        enabled = cfg.get("service_discovery_enabled", True)
        if not enabled:
            return
        interval = cfg.get("service_discovery_interval_seconds", 86400)
        now = time.monotonic()
        if now - self._service_discovery_last_run < interval:
            return
        self._service_discovery_last_run = now
        try:
            report = await self._bounded_to_thread(
                self._service_discovery.run_discovery_pipeline,
            )
            logger.info(
                "Service discovery: %d new, %d changed, %d retired, %d total, %d errors",
                len(getattr(report, "new_services", []) or []),
                len(getattr(report, "changed_services", []) or []),
                len(getattr(report, "retired_services", []) or []),
                getattr(report, "total_discovered", 0),
                len(getattr(report, "errors", []) or []),
            )
        except Exception as exc:
            logger.warning("Service discovery tick failed: %s", exc, exc_info=True)

    async def _phase_reap_expired_sts_tokens(self) -> None:
        if self._daemon_state is None:
            return
        reaper = self._daemon_state.get("_sts_reaper")
        if reaper is None:
            return
        interval = int(self.config.get("sts_reap_interval_ticks", 60))
        if interval <= 0 or self._total_ticks % interval != 0:
            return
        try:
            reaped = await reaper.reap_expired()
            self._tick_metrics["sts_tokens_reaped"] = reaped
        except Exception as exc:
            logger.warning("STS reap phase failed: %s: %s", type(exc).__name__, exc)

    async def _phase_purge_old_task_decisions(self) -> None:
        if self._active_session is None:
            return
        interval = int(self.config.get("task_decisions_retention_interval_ticks", 3600))
        if interval <= 0 or self._total_ticks % interval != 0:
            return
        try:
            deleted = await task_decisions_retention.cleanup_old_task_decisions(
                self._active_session,
                retention_days=int(self.config.get("task_decisions_retention_days", 365)),
            )
            self._tick_metrics["task_decisions_purged"] = deleted
        except Exception as exc:
            logger.warning(
                "task_decisions retention purge failed: %s: %s",
                type(exc).__name__,
                exc,
            )

    async def _phase_emit_tick_metrics(self) -> None:
        logger.info("Tick metrics: %s", self._tick_metrics)

    async def _maybe_cleanup_ephemeral(self, todo: Any) -> None:
        if self._ephemeral_account_manager is None:
            return
        try:
            from general_ludd.account.ephemeral import maybe_delete_ephemeral_after_task

            tags = getattr(todo, "tags", None) or {}
            metadata = tags if isinstance(tags, dict) else {}
            result = maybe_delete_ephemeral_after_task(
                manager=self._ephemeral_account_manager,
                metadata=metadata,
            )
            if result is not None and result.get("deleted"):
                logger.info(
                    "Ephemeral cleanup: deleted account %s for completed todo %s",
                    result.get("account_id"),
                    getattr(todo, "todo_id", "?"),
                )
        except Exception:
            logger.warning(
                "Ephemeral cleanup failed for todo %s",
                getattr(todo, "todo_id", "?"),
                exc_info=True,
            )

    async def _auto_record_episode(
        self,
        todo: Any,
        new_status: Any,
        decision: Any,
    ) -> None:
        if self._memory_repo is None:
            return
        try:
            from general_ludd.memory.episodic import EpisodicMemoryRecorder

            recorder = EpisodicMemoryRecorder(self._memory_repo)
            agent_id = getattr(todo, "assigned_agent", None) or getattr(todo, "work_type", None) or "agent"
            work_type = getattr(todo, "work_type", "code") or "code"
            outcome = (
                "success"
                if str(new_status.value).upper() == "COMPLETE"
                else "failure"
                if str(new_status.value).upper() == "FAILED"
                else "partial"
            )
            takeaway = getattr(decision, "summary", None) or getattr(todo, "title", None) or ""
            error_msg = getattr(decision, "failure_reason", None) or getattr(todo, "last_error", None) or ""
            await recorder.record_completion(
                agent_id=str(agent_id),
                task_type=getattr(todo, "task_type", None) or work_type,
                work_type=work_type,
                priority=getattr(todo, "priority", "medium") or "medium",
                outcome=outcome,
                context={
                    "todo_id": getattr(todo, "todo_id", ""),
                    "decision": getattr(decision, "decision", ""),
                    "project_id": getattr(todo, "project_id", None),
                },
                takeaway=str(takeaway)[:500] if takeaway else "",
                error_message=str(error_msg)[:500] if error_msg else "",
                project_id=getattr(todo, "project_id", None),
            )
            self._tick_metrics.setdefault("episodes_recorded", 0)
            self._tick_metrics["episodes_recorded"] += 1
        except Exception as exc:
            logger.warning("Episodic memory recording failed: %s", exc)

    async def _auto_consolidate_memory(self) -> None:
        if self._memory_repo is None:
            return
        try:
            from general_ludd.memory.consolidation import MemoryConsolidator

            consolidator = MemoryConsolidator(
                self._memory_repo,
                model_gateway=self._model_gateway,
                min_episodes_to_consolidate=10,
            )
            agent_id = str(self._tick_project_id or "gludd")
            result = await consolidator.consolidate(agent_id, project_id=self._tick_project_id)
            if result["consolidated"] > 0:
                self._tick_metrics["memory_consolidated"] = result["consolidated"]
                self._tick_metrics["memory_episodes_consolidated"] = result.get("episodes_consolidated", 0)
                logger.info(
                    "Memory consolidation: %d summaries from %d episodes",
                    result["consolidated"],
                    result.get("episodes_consolidated", 0),
                )
        except Exception as exc:
            logger.warning("Memory consolidation failed: %s", exc)

    async def _auto_cross_task_learn(self) -> None:
        if self._memory_repo is None:
            return
        try:
            from general_ludd.memory.cross_task import CrossTaskLearner

            learner = CrossTaskLearner(
                self._memory_repo,
                model_gateway=self._model_gateway,
            )
            agent_id = str(self._tick_project_id or "gludd")
            report = await learner.generate_improvement_report(agent_id)
            improvements = report.get("improvements_needed", [])
            if improvements:
                self._tick_metrics["cross_task_improvements"] = len(improvements)
                logger.info(
                    "Cross-task learning: %d improvements identified across %d episodes",
                    len(improvements),
                    report.get("total_episodes", 0),
                )
                cross_todos: list[dict[str, Any]] = []
                for imp in improvements:
                    cross_todos.append(
                        {
                            "title": f"AutoMemory: {imp.get('suggested_action', 'Improve task performance')}",
                            "description": json.dumps(imp, default=str),
                            "work_type": "self_improve",
                            "priority": "medium",
                            "source": "cross_task_learner",
                            "gap_type": "cross_task_insight",
                        }
                    )
                if cross_todos:
                    try:
                        enqueued = await self._persist_self_improve_todos(
                            cross_todos,
                            project_id=self._tick_project_id,
                        )
                        self._tick_metrics["cross_task_todos_persisted"] = enqueued
                    except Exception as exc:
                        logger.warning(
                            "Cross-task todo persistence failed: %s",
                            exc,
                        )
        except Exception as exc:
            logger.warning("Cross-task learning failed: %s", exc)

    async def _phase_consolidate_memory(self) -> None:
        self._consolidation_tick_counter += 1
        interval = self._config_snapshot.get("consolidation_interval_ticks", self._consolidation_interval_ticks)
        if self._consolidation_tick_counter < interval:
            return
        self._consolidation_tick_counter = 0

        if self._memory_repo is None:
            return

        agent_id = str(self._tick_project_id or "system")
        project_id = self._tick_project_id
        consolidated = {"procedures": 0, "facts": 0}

        from general_ludd.memory.consolidation import MemoryConsolidator
        from general_ludd.memory.episodic import EpisodicMemoryRecorder
        from general_ludd.memory.procedural import ProceduralMemoryStore
        from general_ludd.memory.semantic import SemanticMemoryStore

        procedural_store: Any = self._procedural_memory or ProceduralMemoryStore(memory_repo=self._memory_repo)
        semantic_store: Any = self._semantic_memory or SemanticMemoryStore(memory_repo=self._memory_repo)

        try:
            recorder = EpisodicMemoryRecorder(self._memory_repo)
            created_procs = await procedural_store.consolidate_from_episodes(
                recorder,
                agent_id,
                project_id=project_id,
            )
            consolidated["procedures"] = created_procs
        except Exception as exc:
            logger.warning(
                "Procedural memory consolidation failed: %s",
                exc,
            )

        try:
            consolidator = MemoryConsolidator(
                self._memory_repo,
                model_gateway=self._model_gateway,
            )
            created_facts = await semantic_store.consolidate_from_consolidated(
                consolidator,
                agent_id,
                project_id=project_id,
            )
            consolidated["facts"] = created_facts
        except Exception as exc:
            logger.warning(
                "Semantic memory consolidation failed: %s",
                exc,
            )

        if consolidated["procedures"] or consolidated["facts"]:
            logger.info(
                "Memory consolidation cascade: %d procedures, %d facts (tick %d)",
                consolidated["procedures"],
                consolidated["facts"],
                self._total_ticks,
            )
            self._tick_metrics["memory_consolidated_procedures"] = consolidated["procedures"]
            self._tick_metrics["memory_consolidated_facts"] = consolidated["facts"]
