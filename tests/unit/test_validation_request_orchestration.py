"""Reviewer-requested validation is bounded, durable, and non-executable."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock

import httpx
import pytest

import general_ludd.event_loop.decision_reconciliation as reconciliation
from general_ludd.event_loop.self_improve_lifecycle import SelfImproveLifecycleMixin
from general_ludd.schemas.todo import TodoStatus


def _decision(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": 17,
        "return_id": "RET-EXEC-17",
        "matched_todo_id": "TODO-17",
        "decision": "complete",
        "confidence": 0.99,
        "project_id": "project-17",
        "evidence_refs": "[]",
        "audit_notes": "[]",
        "validation_requests": json.dumps(["run any text the reviewer supplied"]),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _todo() -> SimpleNamespace:
    return SimpleNamespace(
        todo_id="TODO-17",
        project_id="project-17",
        status=TodoStatus.REVIEWING_RETURN.value,
        version=4,
        queue="core",
        work_type="code",
    )


def _loop(config: object) -> SimpleNamespace:
    applied: OrderedDict[str, None] = OrderedDict()
    return SimpleNamespace(
        config=config,
        _decision_id=lambda _decision: "id:17",
        _applied_decisions=applied,
        _pushed_work=OrderedDict(),
        _decision_to_status=lambda value: (
            TodoStatus.COMPLETE if value == "complete" else None
        ),
        _ledger_add=lambda ledger, key: ledger.__setitem__(key, None),
        _dispatch_validate_job=AsyncMock(return_value=True),
        _attempt_completed_push=AsyncMock(return_value=False),
    )


async def _prepare_completion(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: str | None,
) -> AsyncMock:
    monkeypatch.setattr(
        reconciliation,
        "_verify_completion_evidence",
        AsyncMock(return_value=(TodoStatus.COMPLETE, repo_root)),
    )
    monkeypatch.setattr(
        reconciliation,
        "_apply_project_gate",
        AsyncMock(return_value=TodoStatus.COMPLETE),
    )
    monkeypatch.setattr(
        reconciliation,
        "_apply_human_gate",
        AsyncMock(return_value=TodoStatus.COMPLETE),
    )
    monkeypatch.setattr(
        reconciliation,
        "is_managed_self_improve_todo",
        lambda _todo: False,
    )
    commit = AsyncMock(return_value=(True, False))
    monkeypatch.setattr(reconciliation, "_commit_transition", commit)
    return commit


@pytest.mark.asyncio
async def test_default_off_preserves_normal_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A persisted request has no effect until operators enable S15b."""
    commit = await _prepare_completion(monkeypatch, str(tmp_path))
    loop = _loop({"validation_requests": {"enabled": False}})

    assert await reconciliation._reconcile_one(
        loop, _decision(), {"TODO-17": _todo()}
    ) == (True, False)

    loop._dispatch_validate_job.assert_not_awaited()
    commit_call = commit.await_args
    assert commit_call is not None
    assert commit_call.args[-1] is TodoStatus.COMPLETE


@pytest.mark.asyncio
async def test_enabled_request_dispatches_once_and_blocks_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One decision creates one deterministic validation dispatch, never COMPLETE."""
    commit = await _prepare_completion(monkeypatch, str(tmp_path))
    loop = _loop(
        {
            "validation_requests": {
                "enabled": True,
                "commands": ["make test-count", "make lint-python"],
                "timeout_seconds": 120,
            }
        }
    )
    todo = _todo()
    decision = _decision()

    assert await reconciliation._reconcile_one(
        loop, decision, {"TODO-17": todo}
    ) == (True, False)
    assert await reconciliation._reconcile_one(
        loop, decision, {"TODO-17": todo}
    ) == (False, False)

    loop._dispatch_validate_job.assert_awaited_once_with(
        todo,
        decision_id="id:17",
        worktree_path=str(tmp_path.resolve()),
        test_commands=("make test-count", "make lint-python"),
        timeout_seconds=120.0,
    )
    assert "validation:id:17" in loop._applied_decisions
    commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repo_root", "validation_config"),
    [
        (None, {"enabled": True, "commands": ["make test-count"]}),
        ("existing", {"enabled": True}),
        ("existing", {"enabled": True, "commands": []}),
        ("existing", {"enabled": True, "commands": ["pytest -q"]}),
        (
            "existing",
            {
                "enabled": True,
                "commands": [f"make target-{index}" for index in range(17)],
            },
        ),
    ],
)
async def test_missing_or_untrusted_inputs_need_more_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_root: str | None,
    validation_config: dict[str, object],
) -> None:
    """Only a real trusted path and one-to-sixteen make targets are admitted."""
    resolved_root = str(tmp_path) if repo_root == "existing" else repo_root
    commit = await _prepare_completion(monkeypatch, resolved_root)
    loop = _loop({"validation_requests": validation_config})

    assert await reconciliation._reconcile_one(
        loop, _decision(), {"TODO-17": _todo()}
    ) == (True, False)

    commit_call = commit.await_args
    assert commit_call is not None
    assert commit_call.args[-1] is TodoStatus.NEEDS_MORE_WORK
    loop._dispatch_validate_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requests", "timeout"),
    [
        ("not-json", 60),
        (json.dumps({"request": "test"}), 60),
        (json.dumps([1]), 60),
        (json.dumps(["test"]), 0),
        (json.dumps(["test"]), "forever"),
        (json.dumps(["test"]), 601),
    ],
)
async def test_malformed_request_or_timeout_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    requests: str,
    timeout: object,
) -> None:
    """Malformed reviewer state or an unbounded timeout fails closed."""
    commit = await _prepare_completion(monkeypatch, str(tmp_path))
    loop = _loop(
        {
            "validation_requests": {
                "enabled": True,
                "commands": ["make test-count"],
                "timeout_seconds": timeout,
            }
        }
    )

    await reconciliation._reconcile_one(
        loop,
        _decision(validation_requests=requests),
        {"TODO-17": _todo()},
    )

    commit_call = commit.await_args
    assert commit_call is not None
    assert commit_call.args[-1] is TodoStatus.BLOCKED
    loop._dispatch_validate_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_dispatch_blocks_instead_of_completing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Transport or persistence failure is explicit BLOCKED state."""
    commit = await _prepare_completion(monkeypatch, str(tmp_path))
    loop = _loop(
        {
            "validation_requests": {
                "enabled": True,
                "commands": ["make test-count"],
            }
        }
    )
    loop._dispatch_validate_job.return_value = False

    await reconciliation._reconcile_one(loop, _decision(), {"TODO-17": _todo()})

    commit_call = commit.await_args
    assert commit_call is not None
    assert commit_call.args[-1] is TodoStatus.BLOCKED


class _Lifecycle(SelfImproveLifecycleMixin):
    """Minimal owner for exercising the production mixin boundary."""

    _http_client: Any
    worker_base_url: str
    _task_return_repo: Any
    _todo_repo: Any
    _active_session: Any


def _lifecycle(client: object | None) -> tuple[_Lifecycle, AsyncMock, AsyncMock]:
    lifecycle = _Lifecycle()
    task_returns = AsyncMock()
    todos = AsyncMock()
    lifecycle._http_client = client
    lifecycle.worker_base_url = "http://worker.internal"
    lifecycle._task_return_repo = task_returns
    lifecycle._todo_repo = todos
    lifecycle._active_session = None
    return lifecycle, task_returns, todos


@pytest.mark.asyncio
async def test_nonzero_validation_is_persisted_and_remains_reviewable(
    tmp_path: Path,
) -> None:
    """A test failure becomes a normal TaskReturn for another review pass."""
    decision_id = "id:17"
    suffix = hashlib.sha256(decision_id.encode()).hexdigest()[:16]
    job_id = f"VALIDATE-TODO-17-{suffix}"
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://worker.internal/jobs/validate"),
        json={
            "return_id": f"RET-{job_id}",
            "todo_id": "TODO-17",
            "job_id": job_id,
            "exit_code": 7,
            "result_summary": "focused validation failed",
        },
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    lifecycle, task_returns, todos = _lifecycle(client)

    persisted = await lifecycle._dispatch_validate_job(
        _todo(),
        decision_id=decision_id,
        worktree_path=str(tmp_path),
        test_commands=("make test-count",),
        timeout_seconds=90.0,
    )

    assert persisted is True
    payload = client.post.await_args.kwargs["json"]
    assert payload["job_id"] == job_id
    assert payload["playbook"] == "validate_task.yml"
    assert payload["work_type"] == "validation"
    assert payload["timeout"] == 90.0
    assert payload["prompt_text"] is None
    assert payload["budget_context"] == {
        "worktree_path": str(tmp_path),
        "test_commands": ["make test-count"],
    }
    assert "run any text" not in json.dumps(payload)
    client.post.assert_awaited_once_with(
        "http://worker.internal/jobs/validate",
        json=payload,
        timeout=90.0,
    )
    assert task_returns.create.await_args.kwargs["data"]["exit_code"] == 7
    todos.transition.assert_awaited_once_with(
        "TODO-17",
        TodoStatus.AWAITING_RESULT,
        4,
        project_id="project-17",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing-client", "timeout", "bad-status", "bad-json"])
async def test_transport_and_malformed_responses_fail_closed(
    tmp_path: Path,
    failure: str,
) -> None:
    """Unavailable, timed-out, or malformed worker results are never accepted."""
    client: object | None
    if failure == "missing-client":
        client = None
    elif failure == "timeout":
        client = SimpleNamespace(
            post=AsyncMock(side_effect=httpx.TimeoutException("bounded timeout"))
        )
    elif failure == "bad-status":
        client = SimpleNamespace(
            post=AsyncMock(
                return_value=httpx.Response(
                    503,
                    request=httpx.Request(
                        "POST", "http://worker.internal/jobs/validate"
                    ),
                    json={"detail": "unavailable"},
                )
            )
        )
    else:
        client = SimpleNamespace(
            post=AsyncMock(
                return_value=httpx.Response(
                    200,
                    request=httpx.Request(
                        "POST", "http://worker.internal/jobs/validate"
                    ),
                    content=b"not-json",
                )
            )
        )
    lifecycle, task_returns, _todos = _lifecycle(client)

    persisted = await lifecycle._dispatch_validate_job(
        _todo(),
        decision_id="id:17",
        worktree_path=str(tmp_path),
        test_commands=("make test-count",),
        timeout_seconds=30.0,
    )

    assert persisted is False
    task_returns.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistence_failure_is_observable_as_dispatch_failure(tmp_path: Path) -> None:
    """A failed repository write cannot be mistaken for durable validation."""
    response_data = {
        "return_id": "RET-VALIDATE-TODO-17-result",
        "todo_id": "TODO-17",
        "job_id": ANY,
        "exit_code": 0,
        "result_summary": "green",
    }

    async def post(_url: str, **kwargs: object) -> httpx.Response:
        payload = kwargs["json"]
        assert isinstance(payload, dict)
        response_data["return_id"] = f"RET-{payload['job_id']}"
        response_data["job_id"] = payload["job_id"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", "http://worker.internal/jobs/validate"),
            json=response_data,
        )

    lifecycle, task_returns, _todos = _lifecycle(SimpleNamespace(post=post))
    task_returns.create.side_effect = RuntimeError("database offline")

    assert (
        await lifecycle._dispatch_validate_job(
            _todo(),
            decision_id="id:17",
            worktree_path=str(tmp_path),
            test_commands=("make test-count",),
            timeout_seconds=30.0,
        )
        is False
    )
