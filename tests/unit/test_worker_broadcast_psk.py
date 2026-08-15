"""Regression: WorkerBroadcaster must attach the daemon PSK to internal /admin POSTs.

Without the Authorization: Bearer <GLUDD_AUTH_PSK> header, broadcast_reload /
broadcast_model_update 401 silently against a secured worker and the fleet never
converges. When no PSK is configured (auth disabled) no header is sent.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from general_ludd.reload.worker_broadcast import WorkerBroadcaster, WorkerInfo


def test_broadcast_reload_attaches_psk_when_set(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w1", address="https://worker-1.internal:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret123"


def test_broadcast_reload_no_header_without_psk(monkeypatch) -> None:
    monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="w1", address="https://worker-1.internal:8001"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
        assert "Authorization" not in mock_post.call_args[1]["headers"]


def test_broadcast_model_update_attaches_psk_when_set(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_AUTH_PSK", "modelpsk")
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
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="meta", address=_METADATA_ADDR))
    assert b.list_workers() == []


def test_register_refuses_plain_http_address(monkeypatch) -> None:
    """A plain-http (non-https) address must be refused — the PSK would travel
    in cleartext."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="evil", address=_EVIL_HTTP_ADDR))
    assert b.list_workers() == []


def test_broadcast_reload_never_posts_psk_to_metadata_address(monkeypatch) -> None:
    """Registering a metadata address then broadcasting must issue NO POST at all
    — the Bearer PSK is never sent to 169.254.169.254."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="meta", address=_METADATA_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
    mock_post.assert_not_called()


def test_broadcast_reload_never_posts_psk_to_plain_http(monkeypatch) -> None:
    """A plain-http attacker address must never receive the PSK Bearer header."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="evil", address=_EVIL_HTTP_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
    mock_post.assert_not_called()


def test_broadcast_reload_defense_in_depth_skips_injected_unsafe_worker(monkeypatch) -> None:
    """Even if an unsafe worker slips directly into the registry (bypassing
    register), broadcast_reload must skip it and NOT send the Bearer PSK."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
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
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
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
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="meta", address=_METADATA_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
    mock_post.assert_not_called()


def test_broadcast_model_update_defense_in_depth_skips_injected_unsafe_worker(monkeypatch) -> None:
    """broadcast_model_update skips an injected unsafe worker without sending PSK."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
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
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret123"
    assert mock_post.call_args[0][0] == f"{_SAFE_HTTPS_ADDR}/admin/models/sync"


# ---------------------------------------------------------------------------
# Worker-identity ALLOWLIST (defense-in-depth, task #18): the daemon only
# broadcasts a reload / model-sync — and the PSK — to KNOWN/permitted workers,
# not any address that merely passes the format/SSRF check. Source: an explicit
# constructor `allowlist` set, else the GLUDD_WORKER_ALLOWLIST env var (comma-
# separated worker_ids and/or host:port addresses). No-allowlist default:
# preserve current (unrestricted) behavior but log a warning, matching the PSK's
# own fail-open-when-unset posture — least-surprising and non-breaking.
# ---------------------------------------------------------------------------

_LOGGER_NAME = "general_ludd.reload.worker_broadcast"


def test_allowlist_configured_permits_listed_worker(monkeypatch) -> None:
    """A worker IN the configured allowlist is broadcast to, WITH the PSK."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster(allowlist={"ok"})
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_reload("ALL")
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret123"
    assert results[0].success is True


def test_allowlist_configured_skips_unlisted_worker(monkeypatch) -> None:
    """A worker NOT in the configured allowlist is skipped: no POST, no PSK."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    # Allowlist a different worker; "ok" (a perfectly safe https target) is absent.
    b = WorkerBroadcaster(allowlist={"some-other-worker"})
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_reload("ALL")
    mock_post.assert_not_called()
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error == "not allowlisted"


def test_allowlist_matches_by_address_from_env(monkeypatch) -> None:
    """The allowlist may be sourced from GLUDD_WORKER_ALLOWLIST and match on the
    worker ADDRESS (not just its id)."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    monkeypatch.setenv("GLUDD_WORKER_ALLOWLIST", f"other-id, {_SAFE_HTTPS_ADDR}")
    b = WorkerBroadcaster()  # no constructor allowlist -> defers to env
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_reload("ALL")
    mock_post.assert_called_once()
    assert results[0].success is True


def test_no_allowlist_preserves_behavior_and_warns(monkeypatch, caplog) -> None:
    """No allowlist configured (constructor None + env unset): current behavior is
    preserved (broadcast proceeds WITH the PSK) but a warning is logged so the
    operator knows broadcasts are unrestricted."""
    caplog.propagate = True
    # xdist-order pollution: any test that runs the real daemon lifespan
    # against SQLite (e.g. `with TestClient(app) as client:`) triggers
    # alembic/env.py's fileConfig(alembic.ini), which defaults to
    # disable_existing_loggers=True. alembic.ini's [loggers] only lists
    # root/sqlalchemy/alembic, so this already-created logger gets
    # `.disabled = True` for the rest of the xdist worker — and
    # `Logger.isEnabledFor()` short-circuits on `.disabled` before ever
    # checking level/propagate, so `propagate = True` alone can't fix it.
    logging.getLogger(_LOGGER_NAME).disabled = False
    logging.getLogger(_LOGGER_NAME).propagate = True
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    monkeypatch.delenv("GLUDD_WORKER_ALLOWLIST", raising=False)
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            results = b.broadcast_reload("ALL")
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret123"
    assert results[0].success is True
    assert any("UNRESTRICTED" in rec.getMessage() for rec in caplog.records)


def test_allowlist_does_not_bypass_ssrf_guard(monkeypatch) -> None:
    """The allowlist is IN ADDITION to the SSRF guard, not a replacement: an
    allowlisted-but-unsafe address is STILL refused and the PSK never sent."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    # Allowlist the id, then inject a worker with an UNSAFE (metadata) address.
    b = WorkerBroadcaster(allowlist={"meta"})
    b._workers["meta"] = WorkerInfo(worker_id="meta", address=_METADATA_ADDR)
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_reload("ALL")
    mock_post.assert_not_called()
    assert results[0].success is False
    assert results[0].error == "unsafe address"


def test_allowlist_configured_skips_unlisted_worker_model_update(monkeypatch) -> None:
    """broadcast_model_update honors the same allowlist gate: an unlisted worker
    gets no POST and no PSK."""
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster(allowlist={"some-other-worker"})
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        results = b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
    mock_post.assert_not_called()
    assert results[0].success is False
    assert results[0].error == "not allowlisted"


# ---------------------------------------------------------------------------
# ping_all: SSRF/address re-check parity (defense-in-depth, task #37). ping_all
# sends NO PSK so it is lower risk, but the re-validate-on-send invariant must
# hold identically to the broadcast_* methods: an unsafe/disallowed worker
# address must NOT be contacted by the health-check transport either.
# ---------------------------------------------------------------------------


def test_ping_all_skips_injected_unsafe_worker(monkeypatch) -> None:
    """Even if an unsafe worker slips directly into the registry, ping_all must
    NOT issue an httpx.get to it (same re-validate-on-send invariant as the
    PSK-bearing broadcast methods) and must report it unreachable."""
    b = WorkerBroadcaster()
    # Bypass register() to simulate a registry that already holds a bad entry.
    b._workers["meta"] = WorkerInfo(worker_id="meta", address=_METADATA_ADDR)
    with patch("general_ludd.reload.worker_broadcast.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        results = b.ping_all()
    mock_get.assert_not_called()
    assert results == {"meta": False}


def test_ping_all_skips_injected_plain_http_worker(monkeypatch) -> None:
    """A plain-http (non-https) worker injected into the registry is not pinged."""
    b = WorkerBroadcaster()
    b._workers["evil"] = WorkerInfo(worker_id="evil", address=_EVIL_HTTP_ADDR)
    with patch("general_ludd.reload.worker_broadcast.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        results = b.ping_all()
    mock_get.assert_not_called()
    assert results == {"evil": False}


def test_ping_all_still_pings_safe_https_worker(monkeypatch) -> None:
    """Regression: a valid https worker is STILL pinged and reported reachable."""
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        results = b.ping_all()
    mock_get.assert_called_once()
    assert mock_get.call_args[0][0] == f"{_SAFE_HTTPS_ADDR}/healthz"
    assert results == {"ok": True}


# ---------------------------------------------------------------------------
# Explicit httpx follow_redirects=False + verify=True (defense-in-depth, task
# #37): the SSRF-via-redirect and TLS-verification guarantees must be
# independent of httpx library defaults. An SSRF-validated URL must not be
# redirect-able to an internal host AFTER the check, and TLS must be verified.
# ---------------------------------------------------------------------------


def test_broadcast_reload_sets_no_redirect_and_tls_verify(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_reload("ALL")
    assert mock_post.call_args[1]["follow_redirects"] is False
    assert mock_post.call_args[1]["verify"] is True


def test_broadcast_model_update_sets_no_redirect_and_tls_verify(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_AUTH_PSK", "secret123")
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        b.broadcast_model_update("add", "gpt-5", {"provider": "openai"})
    assert mock_post.call_args[1]["follow_redirects"] is False
    assert mock_post.call_args[1]["verify"] is True


def test_ping_all_sets_no_redirect_and_tls_verify(monkeypatch) -> None:
    b = WorkerBroadcaster()
    b.register(WorkerInfo(worker_id="ok", address=_SAFE_HTTPS_ADDR))
    with patch("general_ludd.reload.worker_broadcast.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        b.ping_all()
    assert mock_get.call_args[1]["follow_redirects"] is False
    assert mock_get.call_args[1]["verify"] is True
