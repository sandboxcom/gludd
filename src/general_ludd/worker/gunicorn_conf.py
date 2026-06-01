"""Gunicorn configuration for the agentic harness worker."""

worker_class = "uvicorn_worker.UvicornWorker"
workers = 2
timeout = 0
