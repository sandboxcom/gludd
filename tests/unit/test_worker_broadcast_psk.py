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
    b.register(WorkerInfo(worker_id="w1", address="https://worker-1.internal:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret123"


def test_broadcast_reload_no_header_without_psk(monkeypatch) -> None:
    monkeypatch.delenv("GLUDD_PSK", raising=False)
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w1", address="https://worker-1.internal:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
        assert "Authorization" not in mock_post.call_args[1]["headers"]


def test_broadcast_model_update_attaches_psk_when_set(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_PSK", "modelpsk")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w1", address="https://worker-1.internal:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer modelpsk"


# ---------------------------------------------------------------------------
# SSRF / PSK-leak guard: never send the daemon PSK to an unsafe worker address.
# ---------------------------------------------------------------------------

_METADATA_ADDR = "http://169.254.169.254"  # AWS/GCP cloud-metadata endpoint
_EVIL_HTTP_ADDR = "http://evil.example"  # plain-http attacker target
_SAFE_HTTPS_ADDR = "https://worker-1.internal"  # legitimate https worker


def test_register_refuses_metadata_address(monkeypatch) -> None:
    """A worker whose address is the cloud-metadata IP must never be registered
    (so the PSK can never be POSTed to it)."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="meta", address=_METADATA_ADDR))
    assert b.list_workers() == []


def test_register_refuses_plain_http_address(monkeypatch) -> None:
    """A plain-http (non-https) address must be refused — the PSK would travel
    in cleartext."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="evil", address=_EVIL_HTTP_ADDR))
    assert b.list_workers() == []


def test_broadcast_reload_never_posts_psk_to_metadata_address(monkeypatch) -> None:
    """Registering a metadata address then broadcasting must issue NO POST at all
    — the Bearer PSK is never sent to 169.254.169.254."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="meta", address=_METADATA_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
    mock_post.assert_not_called()


def test_broadcast_reload_never_posts_psk_to_plain_http(monkeypatch) -> None:
    """A plain-http attacker address must never receive the PSK Bearer header."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="evil", address=_EVIL_HTTP_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
    mock_post.assert_not_called()


def test_broadcast_reload_defense_in_depth_skips_injected_unsafe_worker(monkeypatch) -> None:
    """Even if an unsafe worker slips directly into the registry (bypassing
    register), broadcast_reload must skip it and NOT send the Bearer PSK."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    # Bypass register() to simulate a registry that already holds a bad entry.
    b._workers["meta"] = WorkerInfo(worker_id="meta", address=_METADATA_ADDR)
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_reload("ALL")
    mock_post.assert_not_called()
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == "unsafe address"


def test_broadcast_reload_still_posts_to_https_worker_with_psk(monkeypatch) -> None:
    """Regression: a legitimate https worker STILL receives the broadcast WITH the
    Bearer PSK header — the fix must not weaken the happy path."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_reload("ALL")
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret123"
    assert mock_post.call_args[0][0] == f"{_SAFE_HTTPS_ADDR}/admin/reload"
    assert results[0].success is True


def test_broadcast_model_update_never_posts_psk_to_metadata_address(monkeypatch) -> None:
    """broadcast_model_update gets the same SSRF treatment: no POST to metadata."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="meta", address=_METADATA_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
    mock_post.assert_not_called()


def test_broadcast_model_update_defense_in_depth_skips_injected_unsafe_worker(monkeypatch) -> None:
    """broadcast_model_update skips an injected unsafe worker without sending PSK."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b._workers["evil"] = WorkerInfo(worker_id="evil", address=_EVIL_HTTP_ADDR)
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
    mock_post.assert_not_called()
    assert results[0].success is False
    assert results[0].error == "unsafe address"


def test_broadcast_model_update_still_posts_to_https_worker_with_psk(monkeypatch) -> None:
    """Regression: legitimate https worker still gets the model-update broadcast
    WITH the Bearer PSK."""
    monkeypatch.setenv("GLUDD_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret123"
    assert mock_post.call_args[0][0] == f"{_SAFE_HTTPS_ADDR}/admin/models/sync"
