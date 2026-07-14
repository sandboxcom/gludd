"""Structural tests for worker/gunicorn_conf.py — gunicorn worker configuration."""

from __future__ import annotations

import general_ludd.worker.gunicorn_conf as gunicorn_conf


class TestGunicornConfig:
    def test_worker_class_set(self):
        assert gunicorn_conf.worker_class == "uvicorn_worker.UvicornWorker"

    def test_workers_set(self):
        assert isinstance(gunicorn_conf.workers, int)
        assert gunicorn_conf.workers >= 1

    def test_timeout_set(self):
        assert gunicorn_conf.timeout == 0

    def test_max_requests_set(self):
        assert isinstance(gunicorn_conf.max_requests, int)
        assert gunicorn_conf.max_requests > 0

    def test_max_requests_jitter_set(self):
        assert isinstance(gunicorn_conf.max_requests_jitter, int)

    def test_on_reload_exists(self):
        assert callable(gunicorn_conf.on_reload)

    def test_post_fork_exists(self):
        assert callable(gunicorn_conf.post_fork)

    def test_pre_exec_exists(self):
        assert callable(gunicorn_conf.pre_exec)
