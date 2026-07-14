"""Structural tests for worker/gunicorn_conf.py — gunicorn config module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestGunicornConfModuleAttrs:
    def test_worker_class_is_uvicorn(self):
        import general_ludd.worker.gunicorn_conf as gc
        assert gc.worker_class == "uvicorn_worker.UvicornWorker"

    def test_workers_positive(self):
        import general_ludd.worker.gunicorn_conf as gc
        assert gc.workers > 0

    def test_timeout_zero_no_hard_timeout(self):
        import general_ludd.worker.gunicorn_conf as gc
        assert gc.timeout == 0

    def test_max_requests_configured(self):
        import general_ludd.worker.gunicorn_conf as gc
        assert gc.max_requests == 1000

    def test_max_requests_jitter_configured(self):
        import general_ludd.worker.gunicorn_conf as gc
        assert gc.max_requests_jitter == 50


class TestGunicornConfHooks:
    def test_on_reload_is_callable(self):
        import general_ludd.worker.gunicorn_conf as gc
        arbiter = MagicMock()
        gc.on_reload(arbiter)

    def test_post_fork_is_callable(self):
        import general_ludd.worker.gunicorn_conf as gc
        server = MagicMock()
        worker = MagicMock()
        worker.pid = 12345
        worker.spawned = True
        gc.post_fork(server, worker)

    def test_pre_exec_is_callable(self):
        import general_ludd.worker.gunicorn_conf as gc
        worker = MagicMock()
        worker.pid = 6789
        gc.pre_exec(worker)
