#!/usr/bin/env python3
"""Run the live PostgreSQL multiworker E2E in a namespaced disposable container."""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = Path("tests/e2e/test_postgres_multiworker_live.py")


def project_namespace(root: Path) -> str:
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:10]
    return f"gludd-{digest}"


def parse_mapped_port(output: str) -> int:
    last_line = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    try:
        port = int(last_line.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"could not resolve mapped PostgreSQL port from {last_line!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"mapped PostgreSQL port is out of range: {port}")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--timeout-seconds", required=True, type=int)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _validate_test_file() -> Path:
    test_file = PROJECT_ROOT / TEST_FILE
    if not test_file.is_file():
        raise FileNotFoundError(f"PostgreSQL E2E test file is missing: {test_file}")
    return test_file


def _run_live(runtime: str, image: str, timeout_seconds: int, test_file: Path) -> int:
    runtime_path = shutil.which(runtime)
    if runtime_path is None:
        raise RuntimeError(f"container runtime is unavailable: {runtime}")
    if timeout_seconds < 1:
        raise ValueError("timeout-seconds must be positive")

    namespace = project_namespace(PROJECT_ROOT)
    container_name = f"{namespace}-pg-{os.getpid()}"
    password = secrets.token_hex(16)
    print(f"POSTGRES_E2E_START namespace={namespace} image={image}", flush=True)
    start = subprocess.run(
        [
            runtime_path,
            "run",
            "--detach",
            "--name",
            container_name,
            "--label",
            f"gludd.project={namespace}",
            "--env",
            "POSTGRES_USER=gludd",
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "--env",
            "POSTGRES_DB=gludd",
            "--publish",
            "127.0.0.1::5432",
            image,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if start.returncode != 0:
        raise RuntimeError(f"PostgreSQL container start failed: {start.stderr.strip()}")

    try:
        mapping = subprocess.run(
            [runtime_path, "port", container_name, "5432/tcp"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if mapping.returncode != 0:
            raise RuntimeError(f"PostgreSQL port lookup failed: {mapping.stderr.strip()}")
        port = parse_mapped_port(mapping.stdout)
        deadline = time.monotonic() + timeout_seconds
        attempt = 0
        while True:
            attempt += 1
            ready = subprocess.run(
                [runtime_path, "exec", container_name, "pg_isready", "-U", "gludd", "-d", "gludd"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            print(
                f"POSTGRES_E2E_POLL attempt={attempt} ready={ready.returncode == 0}",
                flush=True,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("PostgreSQL container did not become ready before timeout")
            time.sleep(1)

        env = os.environ.copy()
        env["POSTGRES_E2E_URL"] = (
            f"postgresql+psycopg://gludd:{password}@127.0.0.1:{port}/gludd"
        )
        env["POSTGRES_AVAILABLE"] = "1"
        print("POSTGRES_E2E_TESTS starting", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-vv", "-s", "-W", "error"],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
        )
        print(f"POSTGRES_E2E_TESTS exit_code={result.returncode}", flush=True)
        return result.returncode
    finally:
        print(f"POSTGRES_E2E_CLEANUP container={container_name}", flush=True)
        subprocess.run(
            [runtime_path, "rm", "-f", container_name],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    test_file = _validate_test_file()
    if args.validate_only:
        print(
            "POSTGRES_E2E_VALIDATE_ONLY "
            f"runtime={args.runtime} image={args.image} timeout_seconds={args.timeout_seconds}",
            flush=True,
        )
        return 0
    return _run_live(args.runtime, args.image, args.timeout_seconds, test_file)


if __name__ == "__main__":
    raise SystemExit(main())
