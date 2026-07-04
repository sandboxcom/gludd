"""G6: Prompt A/B test wiring in the dispatch path — variant selection + recording."""

from __future__ import annotations

import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.filestore.store import FileStore
from general_ludd.prompts.variant_selector import PromptVariantSelector
from general_ludd.replay.recorder import RunRecorder


def _temp_recorder():
    tmp = tempfile.mkdtemp(prefix="gludd_ab_test_")
    store = FileStore(root_path=tmp)
    return RunRecorder(store=store), tmp


# ---------------------------------------------------------------------------
# PromptVariantSelector unit tests
# ---------------------------------------------------------------------------


class TestPromptVariantSelector:
    def test_disabled_returns_none(self):
        sel = PromptVariantSelector(enabled=False)
        assert sel.select("default") is None
        assert sel.select("default") is None

    def test_enabled_alternates_ab(self):
        sel = PromptVariantSelector(enabled=True)
        r0 = sel.select("default")
        r1 = sel.select("default")
        r2 = sel.select("default")
        assert r0["variant"] == "A"
        assert r0["run_index"] == 0
        assert r1["variant"] == "B"
        assert r1["run_index"] == 1
        assert r2["variant"] == "A"
        assert r2["run_index"] == 2

    def test_template_hash_included_when_set(self):
        sel = PromptVariantSelector(template_hash="abc123", enabled=True)
        r = sel.select()
        assert r["template_hash"] == "abc123"

    def test_template_hash_omitted_when_none(self):
        sel = PromptVariantSelector(template_hash=None, enabled=True)
        r = sel.select()
        assert "template_hash" not in r

    def test_template_name_included_when_passed(self):
        sel = PromptVariantSelector(enabled=True)
        r = sel.select("implementation.md.j2")
        assert r["template_name"] == "implementation.md.j2"

    def test_template_name_omitted_when_none(self):
        sel = PromptVariantSelector(enabled=True)
        r = sel.select()
        assert "template_name" not in r

    def test_run_index_increments(self):
        sel = PromptVariantSelector(enabled=True)
        sel.select()
        sel.select()
        sel.select()
        assert sel.current_run_index() == 3

    def test_run_index_does_not_increment_when_disabled(self):
        sel = PromptVariantSelector(enabled=False)
        sel.select()
        sel.select()
        assert sel.current_run_index() == 0

    def test_deterministic_after_reset(self):
        sel = PromptVariantSelector(enabled=True)
        sel.select()
        sel.select()
        sel.select()
        sel2 = PromptVariantSelector(enabled=True)
        assert sel2.select()["variant"] == "A"
        assert sel2.current_run_index() == 1

    def test_enabled_setter(self):
        sel = PromptVariantSelector(enabled=True)
        assert sel.enabled is True
        sel.enabled = False
        assert sel.enabled is False
        assert sel.select() is None

    def test_template_hash_setter(self):
        sel = PromptVariantSelector(enabled=True)
        sel.template_hash = "newhash"
        r = sel.select()
        assert r["template_hash"] == "newhash"


# ---------------------------------------------------------------------------
# EventLoop dispatch-path wiring tests
# ---------------------------------------------------------------------------

@pytest.fixture
def ab_recorder():
    rec, tmp = _temp_recorder()
    yield rec
    shutil.rmtree(tmp)


def _make_todo(todo_id="test-todo-001", work_type="code", prompt_profile="default"):
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.queue = "core"
    todo.work_type = work_type
    todo.prompt_profile = prompt_profile
    todo.model_profile = None
    todo.title = "Test task"
    todo.description = "A test task"
    todo.priority = "medium"
    todo.tags = []
    todo.project_id = None
    return todo


class TestEventLoopABDispatch:
    @pytest.mark.asyncio
    async def test_variant_recorded_in_dispatch_started(self, ab_recorder):
        sel = PromptVariantSelector(enabled=True)
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200
        http_client.post.return_value.json = AsyncMock(return_value={
            "return_id": "RET-001", "exit_code": 0, "result_summary": "ok",
        })

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt for dispatch"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0, "prompt_ab_testing": {"enabled": True}},
            session=session,
            http_client=http_client,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            prompt_registry=prompt_registry,
            run_recorder=ab_recorder,
            prompt_variant_selector=sel,
        )

        todo = _make_todo()
        await loop._dispatch_execute_job(todo)

        events = ab_recorder.replay("EXEC-test-todo-001")
        started = next(e for e in events if e["type"] == "dispatch_started")
        assert "ab_variant" in started
        assert started["ab_variant"]["variant"] == "A"
        assert started["ab_variant"]["run_index"] == 0

    @pytest.mark.asyncio
    async def test_variant_alternates_across_dispatches(self, ab_recorder):
        sel = PromptVariantSelector(enabled=True)
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200
        http_client.post.return_value.json = AsyncMock(return_value={
            "return_id": "RET-001", "exit_code": 0, "result_summary": "ok",
        })

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt for dispatch"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0, "prompt_ab_testing": {"enabled": True}},
            session=session,
            http_client=http_client,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            prompt_registry=prompt_registry,
            run_recorder=ab_recorder,
            prompt_variant_selector=sel,
        )

        await loop._dispatch_execute_job(_make_todo(todo_id="todo-A"))
        await loop._dispatch_execute_job(_make_todo(todo_id="todo-B"))
        await loop._dispatch_execute_job(_make_todo(todo_id="todo-C"))

        variants = []
        for tid in ["todo-A", "todo-B", "todo-C"]:
            events = ab_recorder.replay(f"EXEC-{tid}")
            started = next(e for e in events if e["type"] == "dispatch_started")
            variants.append(started["ab_variant"]["variant"])

        assert variants == ["A", "B", "A"]

    @pytest.mark.asyncio
    async def test_variant_not_recorded_when_disabled(self, ab_recorder):
        sel = PromptVariantSelector(enabled=True)
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200
        http_client.post.return_value.json = AsyncMock(return_value={
            "return_id": "RET-001", "exit_code": 0, "result_summary": "ok",
        })

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0},
            session=session,
            http_client=http_client,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            prompt_registry=prompt_registry,
            run_recorder=ab_recorder,
            prompt_variant_selector=sel,
        )

        await loop._dispatch_execute_job(_make_todo(todo_id="todo-no-ab"))

        events = ab_recorder.replay("EXEC-todo-no-ab")
        started = next(e for e in events if e["type"] == "dispatch_started")
        assert "ab_variant" not in started

    @pytest.mark.asyncio
    async def test_variant_not_recorded_when_no_selector(self, ab_recorder):
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200
        http_client.post.return_value.json = AsyncMock(return_value={
            "return_id": "RET-001", "exit_code": 0, "result_summary": "ok",
        })

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={
                "tick_interval": 1.0,
                "prompt_ab_testing": {"enabled": True},
            },
            session=session,
            http_client=http_client,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            prompt_registry=prompt_registry,
            run_recorder=ab_recorder,
        )

        await loop._dispatch_execute_job(_make_todo(todo_id="todo-no-sel"))

        events = ab_recorder.replay("EXEC-todo-no-sel")
        started = next(e for e in events if e["type"] == "dispatch_started")
        assert "ab_variant" not in started

    @pytest.mark.asyncio
    async def test_template_hash_propagated_to_variant(self, ab_recorder):
        sel = PromptVariantSelector(template_hash="sha256-deadbeef", enabled=True)
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200
        http_client.post.return_value.json = AsyncMock(return_value={
            "return_id": "RET-001", "exit_code": 0, "result_summary": "ok",
        })

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt with hash"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0, "prompt_ab_testing": {"enabled": True}},
            session=session,
            http_client=http_client,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            prompt_registry=prompt_registry,
            run_recorder=ab_recorder,
            prompt_variant_selector=sel,
        )

        await loop._dispatch_execute_job(_make_todo(todo_id="todo-hash"))

        events = ab_recorder.replay("EXEC-todo-hash")
        started = next(e for e in events if e["type"] == "dispatch_started")
        assert started["ab_variant"]["template_hash"] == "sha256-deadbeef"

    @pytest.mark.asyncio
    async def test_variant_profile_resolution(self, ab_recorder):
        sel = PromptVariantSelector(enabled=True)
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt for variant profile"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0, "prompt_ab_testing": {"enabled": True}},
            session=session,
            http_client=http_client,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            prompt_registry=prompt_registry,
            run_recorder=ab_recorder,
            prompt_variant_selector=sel,
        )

        await loop._dispatch_execute_job(
            _make_todo(todo_id="todo-vp-A", prompt_profile="default")
        )
        await loop._dispatch_execute_job(
            _make_todo(todo_id="todo-vp-B", prompt_profile="default")
        )

        # Variant A should resolve to "default.variant_a" as prompt_profile
        events_a = ab_recorder.replay("EXEC-todo-vp-A")
        started_a = next(e for e in events_a if e["type"] == "dispatch_started")
        assert started_a["ab_variant"]["variant"] == "A"
        assert started_a["prompt_profile"] == "default.variant_a"

        # Variant B should resolve to "default.variant_b"
        events_b = ab_recorder.replay("EXEC-todo-vp-B")
        started_b = next(e for e in events_b if e["type"] == "dispatch_started")
        assert started_b["ab_variant"]["variant"] == "B"
        assert started_b["prompt_profile"] == "default.variant_b"

    @pytest.mark.asyncio
    async def test_disable_mid_session_stops_recording(self, ab_recorder):
        sel = PromptVariantSelector(enabled=True)
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0, "prompt_ab_testing": {"enabled": True}},
            session=session,
            http_client=http_client,
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            prompt_registry=prompt_registry,
            run_recorder=ab_recorder,
            prompt_variant_selector=sel,
        )

        await loop._dispatch_execute_job(_make_todo(todo_id="todo-before"))
        events_before = ab_recorder.replay("EXEC-todo-before")
        assert "ab_variant" in next(e for e in events_before if e["type"] == "dispatch_started")

        sel.enabled = False

        await loop._dispatch_execute_job(_make_todo(todo_id="todo-after"))
        events_after = ab_recorder.replay("EXEC-todo-after")
        assert "ab_variant" not in next(e for e in events_after if e["type"] == "dispatch_started")
