"""Deep failure injection and chaos tests — retry, fallback, recovery.

Covers failover routing, degraded mode operation, partial failure recovery,
timeout escalation, and circuit breaker reset across the codebase's resilience
surfaces: ReflexionLoop, RemediationDispatcher, BlockerDetector, SafeStopResult,
and the chat-session retry logic.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.ag14_reflexion.loop import (
    EpisodeRecord,
    ReflexionLoop,
    ReflexionMemory,
    create_reflexion_loop,
)
from general_ludd.ai_ml.adaptation import (
    AdapterManifest,
    AdapterMethod,
    SafeStopResult,
    TrainingStopReason,
    validate_adapter,
)
from general_ludd.chat.session import ChatSession
from general_ludd.db.models import Base
from general_ludd.db.repository import (
    RemediationActionRepository,
    TodoRepository,
)
from general_ludd.remediation.blocker_detector import (
    BLOCKER_KINDS,
    REMEDIATION_KINDS,
    BlockedTask,
    BlockerDetector,
    ChronicBlocker,
    RemediationConfig,
    _classify_blocker,
)
from general_ludd.remediation.dispatcher import (
    RemediationActionKind,
    RemediationDispatcher,
)

_HEX64 = "a" * 64


def _make_adapter_manifest(**overrides: Any) -> AdapterManifest:
    defaults: dict[str, Any] = {
        "base_model_digest": _HEX64,
        "method": AdapterMethod.LORA,
        "target_modules": ("q_proj", "v_proj"),
        "rank": 8,
        "alpha": 16,
        "dropout": 0.0,
        "optimizer": "adamw",
        "seed": 42,
        "dataset_manifest_sha256": "b" * 64,
        "tokenizer": "bert-base",
        "precision": "bf16",
        "dependency_lock_sha256": "c" * 64,
        "base_model_record_id": "base-1",
        "quantization": None,
    }
    defaults.update(overrides)
    return AdapterManifest(**defaults)


def _make_blocked(
    *,
    todo_id: str = "TODO-X",
    blocker_kind: str = "resource_contention",
    remediation: str = "dispatch_agent",
    task_type: str = "code",
    blocked_duration_seconds: int = 36000,
) -> BlockedTask:
    return BlockedTask(
        todo_id=todo_id,
        project_id=None,
        blocked_at=datetime.now(UTC) - timedelta(seconds=blocked_duration_seconds),
        blocked_duration_seconds=blocked_duration_seconds,
        blocker_kind=blocker_kind,
        blocker_summary="test summary",
        suggested_remediation=remediation,
        task_type=task_type,
    )


@dataclass
class _FakeDetector:
    config: RemediationConfig


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


# ── failover routing ─────────────────────────────────────────────────────────


class TestFailoverRouting:
    def test_post_with_retry_exponential_backoff_on_connection_error(self):
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("refused"),
                httpx.TimeoutException("timed out"),
                MagicMock(status_code=200, raise_for_status=lambda: None),
            ]
        )
        chat = ChatSession()
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = asyncio.run(
                chat._post_with_retry(
                    mock_client,
                    "http://test/v1/chat",
                    {"Authorization": "Bearer k"},
                    {"model": "x", "messages": []},
                )
            )
        assert response is not None
        assert mock_client.post.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    def test_post_with_retry_does_not_retry_on_http_status_error(self):
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock(status_code=500))
        )
        chat = ChatSession()
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(
                chat._post_with_retry(
                    mock_client,
                    "http://test/v1/chat",
                    {"Authorization": "Bearer k"},
                    {"model": "x", "messages": []},
                )
            )
        assert mock_client.post.call_count == 1

    def test_post_with_retry_raises_last_exc_after_max_retries(self):
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("refused"),
                httpx.ConnectError("refused again"),
                httpx.ConnectError("refused final"),
            ]
        )
        chat = ChatSession()
        with patch("asyncio.sleep", new_callable=AsyncMock), pytest.raises(httpx.ConnectError, match="refused final"):
            asyncio.run(
                chat._post_with_retry(
                    mock_client,
                    "http://test/v1/chat",
                    {"Authorization": "Bearer k"},
                    {"model": "x", "messages": []},
                )
            )
        assert mock_client.post.call_count == 3

    def test_classify_blocker_permission_escalation_maps_to_schedule_retry(self):
        mock_ht = MagicMock()
        mock_ht.category = "permission_escalation"
        kind, rem = _classify_blocker(mock_ht, is_chronic_requeue=False)
        assert kind == "permission_escalation"
        assert rem == "schedule_retry"

    def test_classify_blocker_input_request_maps_to_file_human_todo(self):
        mock_ht = MagicMock()
        mock_ht.category = "input_request"
        kind, rem = _classify_blocker(mock_ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"

    def test_classify_blocker_unknown_category_falls_to_file_human_todo(self):
        mock_ht = MagicMock()
        mock_ht.category = "mystery"
        kind, rem = _classify_blocker(mock_ht, is_chronic_requeue=False)
        assert kind == "human_input"
        assert rem == "file_human_todo"


# ── degraded mode operation ──────────────────────────────────────────────────


class TestDegradedModeOperation:
    def test_remediation_scan_continues_after_blocked_human_query_fails(self):
        detector = BlockerDetector(
            todo_repo=MagicMock(),
            human_todo_repo=MagicMock(),
            event_log_repo=MagicMock(),
            session=MagicMock(),
        )

        async def _failing_list(*args, **kw):
            raise RuntimeError("db gone")

        detector._todo_repo.list_by_status = _failing_list  # type: ignore[assignment]

        findings = asyncio.run(detector.scan())
        assert isinstance(findings, list)

    def test_dispatcher_action_failure_returns_no_action_with_reason(self):
        detector = _FakeDetector(config=RemediationConfig())
        todo_repo = MagicMock()
        todo_repo.get_by_id = AsyncMock(side_effect=RuntimeError("boom"))
        dispatcher = RemediationDispatcher(
            detector=detector,
            todo_repo=todo_repo,
        )
        blocked = _make_blocked(remediation="dispatch_agent")
        action = asyncio.run(dispatcher.remediate(blocked))
        assert action.kind == RemediationActionKind.NO_ACTION
        assert not action.ok
        assert "boom" in action.summary

    def test_dispatcher_idempotency_returns_cached_action(self):

        fake_existing = MagicMock()
        fake_existing.id = "REP-1"
        mock_remediation_repo = MagicMock()
        mock_remediation_repo.find_by_idempotency_key = AsyncMock(return_value=[fake_existing])
        todo_repo = MagicMock()
        detector = _FakeDetector(config=RemediationConfig())
        dispatcher = RemediationDispatcher(
            detector=detector,
            todo_repo=todo_repo,
            remediation_repo=mock_remediation_repo,
        )
        blocked = _make_blocked(remediation="dispatch_agent")
        action = asyncio.run(dispatcher.remediate(blocked, idempotency_key="KEY-1"))
        assert action.kind == RemediationActionKind.NO_ACTION
        assert action.ok
        assert action.detail.get("idempotent_replay") is True
        todo_repo.get_by_id.assert_not_called()

    def test_chronic_blockers_returns_empty_when_session_is_none(self):
        detector = BlockerDetector(session=None)
        result = asyncio.run(detector.chronic_blockers())
        assert result == []

    def test_scan_blocked_on_human_returns_empty_when_todo_repo_is_none(self):
        detector = BlockerDetector(todo_repo=None)
        result = asyncio.run(detector._scan_blocked_on_human(datetime.now(UTC), None))
        assert result == []


# ── partial failure recovery ─────────────────────────────────────────────────


class TestPartialFailureRecovery:
    def test_reflexion_loop_partial_success_on_third_attempt(self):
        def flaky_actor(task, feedback):
            if len(feedback) < 2:
                return "bad"
            return "good"

        def flaky_evaluator(task, output):
            return 0.95 if output == "good" else 0.2

        loop = ReflexionLoop(
            actor=flaky_actor,
            evaluator=flaky_evaluator,
            max_retries=5,
            score_threshold=0.8,
        )
        result = loop.run("test task")
        assert result.success
        assert result.total_retries == 2
        assert len(result.episodes) == 3
        scores = [ep.evaluation_score for ep in result.episodes]
        assert scores == [0.2, 0.2, 0.95]

    def test_reflexion_loop_exhausts_max_retries(self):
        loop = ReflexionLoop(
            actor=lambda task, fb: f"output for {task}",
            evaluator=lambda task, out: 0.2,
            max_retries=2,
            score_threshold=0.8,
        )
        result = loop.run("hard task")
        assert not result.success
        assert result.total_retries == 2
        assert len(result.episodes) == 3

    def test_reflexion_memory_accumulates_across_episodes(self):
        memory = ReflexionMemory(max_window=5)
        for i in range(3):
            ep = EpisodeRecord(
                episode_id=f"ep-{i}",
                task_description="t",
                actor_output="out",
                evaluation_score=0.5,
                reflexion_text=f"reflection {i}",
            )
            memory.add(ep)
        assert memory.episode_count == 3
        feedback = memory.recent_feedback(3)
        assert feedback == ["reflection 0", "reflection 1", "reflection 2"]
        assert memory.last_score() == 0.5

    def test_safe_stop_result_retryable_oom(self):
        result = SafeStopResult(
            reason=TrainingStopReason.OOM,
            terminal_step=1500,
            preserved_checkpoint="s3://ckpts/step-1000",
            diagnostics=("OOM at step 1500; last stable checkpoint step 1000",),
            retryable=True,
        )
        assert result.retryable
        assert result.preserved_checkpoint == "s3://ckpts/step-1000"
        assert result.terminal_step == 1500

    def test_safe_stop_result_non_retryable_divergent_loss(self):
        result = SafeStopResult(
            reason=TrainingStopReason.DIVERGENT_LOSS,
            terminal_step=300,
            preserved_checkpoint=None,
            diagnostics=("loss exploded to 1e6 at step 300",),
            retryable=False,
        )
        assert not result.retryable
        assert result.preserved_checkpoint is None

    def test_safe_stop_result_rejects_negative_terminal_step(self):
        with pytest.raises(ValueError, match="terminal_step"):
            SafeStopResult(
                reason=TrainingStopReason.OOM,
                terminal_step=-1,
                preserved_checkpoint=None,
                diagnostics=(),
                retryable=False,
            )

    def test_reflexion_memory_prunes_beyond_max_window_doubled(self):
        memory = ReflexionMemory(max_window=3)
        for i in range(8):
            ep = EpisodeRecord(
                episode_id=f"ep-{i}",
                task_description="t",
                actor_output="out",
                evaluation_score=0.5,
            )
            memory.add(ep)
        assert memory.episode_count == 4


# ── timeout escalation ───────────────────────────────────────────────────────


class TestTimeoutEscalation:
    def test_remediation_config_defaults_conservative(self):
        cfg = RemediationConfig()
        assert cfg.human_input_block_hours == 24
        assert cfg.permission_escalation_block_hours == 4
        assert cfg.retry_delay_hours == 4
        assert cfg.max_requeues_before_chronic == 3
        assert cfg.min_chronic_incidents == 5

    def test_blocked_task_duration_escalation_signals_urgency(self):
        recent = _make_blocked(todo_id="TODO-R", blocked_duration_seconds=14400)
        old = _make_blocked(todo_id="TODO-O", blocked_duration_seconds=172800)
        assert recent.blocked_duration_seconds == 14400
        assert old.blocked_duration_seconds == 172800
        assert old.blocked_duration_seconds > recent.blocked_duration_seconds * 10

    @pytest.mark.asyncio
    async def test_schedule_retry_uses_config_delay(self):
        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[arg-type]
        async with session_factory() as session:
            todo_repo = TodoRepository(session)
            detector = _FakeDetector(config=RemediationConfig(retry_delay_hours=2))
            dispatcher = RemediationDispatcher(
                detector=detector,
                todo_repo=todo_repo,
                remediation_repo=RemediationActionRepository(session),
            )
            blocked = _make_blocked(remediation="schedule_retry")
            action = await dispatcher.remediate(blocked)
            assert action.ok
            new_id = action.detail.get("scheduled_todo_id")
            scheduled = await todo_repo.get_by_id(new_id)
            assert scheduled is not None
            assert scheduled.status == "scheduled"
            sat = scheduled.scheduled_at
            assert sat is not None
            delta = sat.replace(tzinfo=UTC) - datetime.now(UTC)
            assert timedelta(hours=1, minutes=55) <= delta <= timedelta(hours=2, minutes=5)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    def test_remediation_config_immutability(self):
        cfg = RemediationConfig(retry_delay_hours=6)
        with pytest.raises(FrozenInstanceError):
            cfg.retry_delay_hours = 2  # type: ignore[misc]


# ── circuit breaker reset ────────────────────────────────────────────────────


class TestCircuitBreakerReset:
    def test_reflexion_loop_reset_clears_memory_and_counter(self):
        loop = ReflexionLoop(
            actor=lambda task, fb: f"out {task}",
            evaluator=lambda task, out: 0.2,
            max_retries=1,
            score_threshold=0.8,
        )
        loop.run("first")
        assert loop.memory.episode_count == 2
        loop.reset()
        assert loop.memory.episode_count == 0
        result = loop.run("second")
        assert result.total_retries == 1
        assert len(result.episodes) == 2
        assert result.episodes[0].retry_count == 0

    def test_reflexion_memory_clear_empties_store(self):
        memory = ReflexionMemory()
        memory.add(
            EpisodeRecord(
                episode_id="ep-1",
                task_description="t",
                actor_output="out",
                evaluation_score=0.9,
            )
        )
        assert memory.episode_count == 1
        assert memory.last_score() == 0.9
        memory.clear()
        assert memory.episode_count == 0
        assert memory.last_score() is None

    def test_reflexion_memory_recent_feedback_skips_empty_reflexion(self):
        memory = ReflexionMemory()
        for i in range(3):
            memory.add(
                EpisodeRecord(
                    episode_id=f"ep-{i}",
                    task_description="t",
                    actor_output="out",
                    evaluation_score=round(0.3 + i * 0.2, 2),
                    reflexion_text=f"reflection {i}" if i % 2 == 0 else "",
                )
            )
        feedback = memory.recent_feedback(5)
        assert feedback == ["reflection 0", "reflection 2"]

    def test_episode_record_is_success_threshold_bounds(self):
        ep = EpisodeRecord(
            episode_id="ep-0",
            task_description="t",
            actor_output="out",
            evaluation_score=0.79,
        )
        assert not ep.is_success(0.8)
        assert ep.is_success(0.7)
        assert ep.is_success(0.0)
        assert not ep.is_success(1.0)

    def test_reflexion_loop_rejects_invalid_max_retries(self):
        with pytest.raises(ValueError, match="max_retries"):
            ReflexionLoop(
                actor=lambda task, fb: "out",
                evaluator=lambda task, out: 0.9,
                max_retries=-1,
            )

    def test_reflexion_loop_rejects_invalid_score_threshold(self):
        with pytest.raises(ValueError, match="score_threshold"):
            ReflexionLoop(
                actor=lambda task, fb: "out",
                evaluator=lambda task, out: 0.9,
                score_threshold=1.5,
            )

    def test_reflexion_loop_zero_max_retries_runs_once(self):
        loop = ReflexionLoop(
            actor=lambda task, fb: "out",
            evaluator=lambda task, out: 0.2,
            max_retries=0,
            score_threshold=0.8,
        )
        result = loop.run("task")
        assert result.total_retries == 0
        assert len(result.episodes) == 1
        assert not result.success

    def test_create_reflexion_loop_factory_defaults(self):
        loop = create_reflexion_loop(
            actor=lambda task, fb: "out",
            evaluator=lambda task, out: 0.9,
        )
        assert loop.max_retries == 3
        assert loop.score_threshold == 0.8

    def test_blocked_task_frozen(self):
        bt = _make_blocked(blocker_kind="human_input")
        with pytest.raises(FrozenInstanceError):
            bt.blocker_kind = "other"  # type: ignore[misc]

    def test_chronic_blocker_holds_incident_count(self):
        now = datetime.now(UTC)
        cb = ChronicBlocker(
            task_type="infra",
            blocker_kind="resource_contention",
            incident_count=7,
            first_seen=now - timedelta(days=5),
            last_seen=now,
            recent_todo_ids=["TODO-1", "TODO-2"],
        )
        assert cb.incident_count == 7
        assert cb.task_type == "infra"
        assert len(cb.recent_todo_ids) == 2


# ── smoketest ─────────────────────────────────────────────────────────────────


def test_all_enums_and_constants_resolve():
    assert "permission_escalation" in BLOCKER_KINDS
    assert "dispatch_agent" in REMEDIATION_KINDS
    assert "schedule_retry" in REMEDIATION_KINDS
    assert TrainingStopReason.OOM.value == "oom"
    assert TrainingStopReason.DIVERGENT_LOSS.value == "divergent_loss"


def test_adaptation_validate_adapter_mismatch_raises():
    m = _make_adapter_manifest()
    with pytest.raises(ValueError, match="base_model_digest"):
        validate_adapter(m, serving_base_digest="b" * 64)
