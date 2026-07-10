"""Tests: WorkerBroadcaster 401 handling surfaces as ERROR log + success=False."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from general_ludd.reload.worker_broadcast import BroadcastResult, WorkerBroadcaster, WorkerInfo


def _make_broadcaster(*statuses: int) -> tuple[WorkerBroadcaster, list[WorkerInfo]]:
    """Return a broadcaster pre-loaded with one worker per status code."""
    broadcaster = WorkerBroadcaster()
    workers: list[WorkerInfo] = []
    for i, _status in enumerate(statuses):
        w = WorkerInfo(worker_id=f"w{i}", address=f"https://worker{i}.internal:8000")
        broadcaster.register(w)
        workers.append(w)
    return broadcaster, workers


def _fake_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# broadcast_reload
# ---------------------------------------------------------------------------


class TestBroadcastReload:
    def test_200_yields_success_true(self):
        broadcaster, _ = _make_broadcaster(200)
        with patch("general_ludd.reload.worker_broadcast.httpx.post", return_value=_fake_response(200)):
            results = broadcaster.broadcast_reload("config")
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].error is None

    def test_401_yields_success_false_unauthorized(self, caplog):
        broadcaster, workers = _make_broadcaster(401)
        caplog.propagate = True
        # xdist-order pollution: alembic/env.py's fileConfig(alembic.ini) (run by
        # ANY test that exercises the real daemon lifespan against SQLite, e.g.
        # `with TestClient(app) as client:`) defaults to disable_existing_loggers
        # =True. alembic.ini's [loggers] only lists root/sqlalchemy/alembic, so
        # this logger — already created at collection time — gets `.disabled =
        # True` for the rest of the xdist worker process. Neither conftest's
        # `_isolate_root_logger` (snapshots level/propagate/handlers only, not
        # `.disabled`) nor pytest's own caplog machinery (`_force_enable_logging`
        # only clears the global `logging.disable()` level, never a logger's own
        # `.disabled`) ever restores it, so a bare `propagate = True` is powerless
        # — `Logger.isEnabledFor()` short-circuits on `.disabled` before it ever
        # looks at level/propagate. Force it back on here so this test passes
        # regardless of what ran before it on this worker.
        logging.getLogger("general_ludd.reload.worker_broadcast").disabled = False
        logging.getLogger("general_ludd.reload.worker_broadcast").propagate = True
        with caplog.at_level(logging.ERROR, logger="general_ludd.reload.worker_broadcast"), patch(
            "general_ludd.reload.worker_broadcast.httpx.post", return_value=_fake_response(401)
        ):
            results = broadcaster.broadcast_reload("config")

        assert len(results) == 1
        r: BroadcastResult = results[0]
        assert r.success is False
        assert r.error == "Unauthorized"
        assert r.worker_id == workers[0].worker_id

        # Must have produced an ERROR-level log mentioning 401 and the worker
        error_records = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
        assert error_records, "Expected at least one ERROR log for 401"
        assert "401" in error_records[0].getMessage() or "401" in str(error_records[0].args)

    def test_non_200_non_401_yields_http_status_error(self):
        broadcaster, _ = _make_broadcaster(503)
        with patch("general_ludd.reload.worker_broadcast.httpx.post", return_value=_fake_response(503)):
            results = broadcaster.broadcast_reload("config")
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "HTTP 503"


# ---------------------------------------------------------------------------
# broadcast_model_update
# ---------------------------------------------------------------------------


class TestBroadcastModelUpdate:
    def test_200_yields_success_true(self):
        broadcaster, _ = _make_broadcaster(200)
        with patch("general_ludd.reload.worker_broadcast.httpx.post", return_value=_fake_response(200)):
            results = broadcaster.broadcast_model_update("add", "gpt-x", {})
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].error is None

    def test_401_yields_success_false_unauthorized(self, caplog):
        broadcaster, workers = _make_broadcaster(401)
        caplog.propagate = True
        # xdist-order pollution: any test that runs the real daemon lifespan
        # against SQLite (e.g. `with TestClient(app) as client:`) triggers
        # alembic/env.py's fileConfig(alembic.ini), which defaults to
        # disable_existing_loggers=True. alembic.ini's [loggers] only lists
        # root/sqlalchemy/alembic, so this already-created logger gets
        # `.disabled = True` for the rest of the xdist worker — and
        # `Logger.isEnabledFor()` short-circuits on `.disabled` before ever
        # checking level/propagate, so `propagate = True` alone can't fix it.
        logging.getLogger("general_ludd.reload.worker_broadcast").disabled = False
        logging.getLogger("general_ludd.reload.worker_broadcast").propagate = True
        with caplog.at_level(logging.ERROR, logger="general_ludd.reload.worker_broadcast"), patch(
            "general_ludd.reload.worker_broadcast.httpx.post", return_value=_fake_response(401)
        ):
            results = broadcaster.broadcast_model_update("add", "gpt-x", {})

        assert len(results) == 1
        r: BroadcastResult = results[0]
        assert r.success is False
        assert r.error == "Unauthorized"
        assert r.worker_id == workers[0].worker_id

        error_records = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
        assert error_records, "Expected at least one ERROR log for 401"
        assert "401" in error_records[0].getMessage() or "401" in str(error_records[0].args)

    def test_non_200_non_401_yields_http_status_error(self):
        broadcaster, _ = _make_broadcaster(404)
        with patch("general_ludd.reload.worker_broadcast.httpx.post", return_value=_fake_response(404)):
            results = broadcaster.broadcast_model_update("remove", "gpt-x", {})
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "HTTP 404"
