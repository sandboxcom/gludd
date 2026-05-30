"""Worker CLI entrypoint."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    cmd = [
        "gunicorn",
        "agentic_harness.worker.app:create_app",
        "--factory",
        "--worker-class",
        "uvicorn_worker.UvicornWorker",
        "--workers",
        "2",
        "--bind",
        "0.0.0.0:8000",
    ]
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
