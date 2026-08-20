"""Tests for #35 SLICE 2 — wiring PauseController into ModelGateway and EventLoop.

TDD: these tests document the expected pause-wiring behavior.  They will FAIL
until the gateway and event-loop are wired (the implementation follow-up).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.controllers.pause_controller import PauseController
from general_ludd.controllers.pause_store import PauseStore
from general_ludd.event_loop.loop import EventLoop
from general_ludd.models.gateway import ModelGateway, ModelPausedError, ModelProfile

# ---------------------------------------------------------------------------
# Test 1: ModelGateway raises ModelPausedError when the profile is paused
# ---------------------------------------------------------------------------

def test_call_model_raises_model_paused_error_when_profile_paused(tmp_path):
    """call_model must raise ModelPausedError before the budget/provider path."""
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    profile = ModelProfile(
        model_profile_id="paused-model",
        provider="openai",
        model_name="gpt-4",
        api_metered=False,
        enabled=True,
    )
    pc.pause("model", "paused-model", reason="testing")

    gw = ModelGateway(profiles=[profile], pause_controller=pc)

    with pytest.raises(ModelPausedError) as exc_info:
        gw.call_model("paused-model", [{"role": "user", "content": "hi"}])

    assert "paused" in str(exc_info.value)
    assert "paused-model" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 2: call_model proceeds normally after resume (no ModelPausedError)
# ---------------------------------------------------------------------------

def test_call_model_proceeds_after_resume(tmp_path):
    """After resume, call_model must NOT raise ModelPausedError.

    Without a real provider it will raise ValueError (no provider registry),
    but that is fine — what matters is that the pause gate does NOT fire.
    """
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    profile = ModelProfile(
        model_profile_id="resumed-model",
        provider="openai",
        model_name="gpt-4",
        api_metered=False,
        enabled=True,
    )
    pc.pause("model", "resumed-model", reason="testing")
    record = pc.resume("model", "resumed-model")
    assert record is not None

    gw = ModelGateway(profiles=[profile], pause_controller=pc)

    with pytest.raises(ValueError, match="provider registry"):
        gw.call_model("resumed-model", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# Test 3: _try_call_model propagates ModelPausedError (not swallowed)
# ---------------------------------------------------------------------------

def test_try_call_model_propagates_model_paused_error(tmp_path):
    """_try_call_model catches only (ValueError, ImportError).

    ModelPausedError inherits Exception — NOT ValueError — so the narrow
    except clause must let it propagate.  A swallowed pause would silently
    fail over to the next fallback profile, bypassing the pause.
    """
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    profile = ModelProfile(
        model_profile_id="try-call-paused",
        provider="openai",
        model_name="gpt-4",
        api_metered=False,
        enabled=True,
    )
    pc.pause("model", "try-call-paused", reason="testing")

    gw = ModelGateway(profiles=[profile], pause_controller=pc)

    with pytest.raises(ModelPausedError):
        gw._try_call_model(
            "try-call-paused", [{"role": "user", "content": "hi"}]
        )


# ---------------------------------------------------------------------------
# Test 4: EventLoop._phase_claim_runnable_todos returns [] when project paused
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_loop_skips_claim_when_project_paused(tmp_path):
    """When _tick_project_id is paused, _phase_claim_runnable_todos must
    short-circuit and set claimed_todos to an empty list without calling
    the repo."""
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)
    pc.pause("project", "test-project", reason="testing")

    loop = EventLoop()
    loop._tick_project_id = "test-project"
    loop._pause_controller = pc

    mock_todo_repo = MagicMock()
    mock_todo_repo.claim_runnable = MagicMock()
    loop._todo_repo = mock_todo_repo

    await loop._phase_claim_runnable_todos()

    claimed = loop._tick_state.get("claimed_todos", [])
    assert claimed == []
    mock_todo_repo.claim_runnable.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: EventLoop claims normally when project is NOT paused
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_loop_claims_normally_when_project_not_paused(tmp_path):
    """When _tick_project_id is not paused the event loop must delegate
    to the repo as usual and store the result in _tick_state."""
    store = PauseStore(base_dir=str(tmp_path / "pause_store"))
    pc = PauseController(store=store)

    loop = EventLoop()
    loop._tick_project_id = "test-project"
    loop._pause_controller = pc

    fake_todos = [MagicMock(), MagicMock()]
    mock_todo_repo = MagicMock()
    mock_todo_repo.claim_runnable = AsyncMock(return_value=fake_todos)
    loop._todo_repo = mock_todo_repo

    await loop._phase_claim_runnable_todos()

    claimed = loop._tick_state.get("claimed_todos", [])
    assert claimed == fake_todos
    assert len(claimed) == 2
    mock_todo_repo.claim_runnable.assert_called_once_with(limit=10, project_id="test-project")
