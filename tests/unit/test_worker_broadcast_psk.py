"""Regression: WorkerBroadcaster must attach the daemon PSK to internal /admin POSTs.

Without the Authorization: Bearer <GLUDD_PSK> header, broadcast_reload /
broadcast_model_update 401 silently against a secured worker and the fleet never
converges. When no PSK is configured (auth disabled) no header is sent.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.reload.worker_broadcast import WorkerBroadcaster, WorkerInfo


def test_broadcast_reload_attaches_psk_when_set(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret123"


def test_broadcast_reload_no_header_without_psk(monkeypatch) -> None:
    monkeypatch.delenv("GLUDD_PSK", raising=False)
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
        assert "Authorization" not in mock_post.call_args[1]["headers"]


def test_broadcast_model_update_attaches_psk_when_set(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_PSK", "modelpsk")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w1", address="http://localhost:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer modelpsk"
