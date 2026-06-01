from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

worker_class = "uvicorn_worker.UvicornWorker"
workers = 2
timeout = 0
max_requests = 1000
max_requests_jitter = 50


def on_reload(arbiter: Any) -> None:
    logger.info("SIGHUP received — reloading workers")


def post_fork(server: Any, worker: Any) -> None:
    worker_id = f"worker-{worker.pid}"
    logger.info("Worker %s forked (spawned=%s)", worker_id, worker.spawned)


def pre_exec(worker: Any) -> None:
    logger.info("Worker %s pre-exec for graceful restart", worker.pid)
