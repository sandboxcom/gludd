"""Regression coverage for signal registration on worker model-call threads."""

from __future__ import annotations

import atexit
import signal
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from general_ludd.cloud import resource_lifecycle
from general_ludd.models.gateway import ModelResponse
from general_ludd.worker.app import create_app


def test_generation_job_calls_gateway_from_testclient_worker_thread(
    monkeypatch,
) -> None:
    """Lazy deployment imports must not abort before the model call."""
    monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
    monkeypatch.setenv("GLUDD_PSK_DISABLE", "1")
    gateway = MagicMock()
    gateway.call_model.return_value = ModelResponse(
        content="thread-safe generation",
        model_name="test-model",
    )
    client = TestClient(create_app(gateway=gateway, dispatcher=None))

    response = client.post(
        "/jobs/execute",
        json={
            "job_id": "JOB-THREAD-SIGNAL",
            "todo_id": "TODO-THREAD-SIGNAL",
            "playbook": "noop.yml",
            "queue": "core",
            "work_type": "code",
            "model_profile": "default",
            "prompt_text": "Generate safely from the worker thread",
        },
    )

    assert response.status_code == 200
    gateway.call_model.assert_called_once()
    assert response.json()["model_response"] == "thread-safe generation"


def test_lifecycle_first_created_in_worker_then_installs_signals_on_main(
    monkeypatch,
) -> None:
    """A worker-first singleton remains usable and main-thread setup can retry."""
    monkeypatch.setattr(resource_lifecycle, "_lifecycle_singleton", None)
    monkeypatch.setattr(
        resource_lifecycle,
        "_signal_handlers_installed",
        False,
        raising=False,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        manager = pool.submit(resource_lifecycle.get_lifecycle).result(timeout=5)

    installed: list[tuple[int, object]] = []
    monkeypatch.setattr(
        resource_lifecycle.signal,
        "signal",
        lambda signum, handler: installed.append((signum, handler)),
    )
    assert resource_lifecycle.get_lifecycle() is manager
    assert [signum for signum, _handler in installed] == [
        signal.SIGTERM,
        signal.SIGINT,
    ]
    atexit.unregister(manager._guaranteed_cleanup)
