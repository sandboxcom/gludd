#!/usr/bin/env python3
"""Run the real daemon smoke check with explicit endpoint failures."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from general_ludd.cli import _build_daemon_start_cmd

LOG_PATH = Path(os.environ.get("GLUDD_SMOKE_LOG", "/tmp/gludd-smoke.log"))
SMOKE_AUTH_TOKEN = "gludd-smoke-token"
BAD_LOG_MARKERS = ("typeerror", "traceback", "swallowed", "expecting value")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _snippet(raw: bytes, limit: int = 400) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return "<empty>"
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def _headers(psk: str | None = None, *, content_type: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if psk:
        headers["Authorization"] = f"Bearer {psk}"
    return headers


def _request_status(base_url: str, path: str, *, psk: str | None = None, timeout: float = 3.0) -> int:
    req = urllib.request.Request(_url(base_url, path), headers=_headers(psk), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GET {path} returned HTTP {exc.code}: {_snippet(exc.read())}") from exc
    except Exception as exc:
        raise RuntimeError(f"GET {path} failed: {type(exc).__name__}: {exc}") from exc


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    psk: str | None = None,
    timeout: float = 3.0,
) -> Any:
    data = None
    headers = _headers(psk)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_url(base_url, path), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {_snippet(exc.read())}") from exc
    except Exception as exc:
        raise RuntimeError(f"{method} {path} failed: {type(exc).__name__}: {exc}") from exc
    if not 200 <= status < 300:
        raise RuntimeError(f"{method} {path} returned HTTP {status}: {_snippet(raw)}")
    if not raw.strip():
        raise RuntimeError(f"{method} {path} returned empty body")
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {path} returned non-JSON body: {_snippet(raw)}") from exc


def _prepare_config() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    tmpdir = tempfile.TemporaryDirectory(prefix="gludd-smoke-")
    root = Path(tmpdir.name)
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    db_path = root / "smoke.db"
    config_text = f"database:\n  url: sqlite+aiosqlite:///{db_path}\n"
    (config_dir / "general-ludd.yml").write_text(config_text, encoding="utf-8")
    return tmpdir, config_dir


def _start_daemon(port: int, *, config_dir: Path) -> tuple[subprocess.Popen[bytes], Any]:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = LOG_PATH.open("wb")
    env = os.environ.copy()
    env["GLUDD_DAEMON_PORT"] = str(port)
    env["GLUDD_AUTH_PSK"] = SMOKE_AUTH_TOKEN
    env["GLUDD_CONFIG_DIR"] = str(config_dir)
    env["GLUDD_LOG_LEVEL"] = "info"
    cmd = _build_daemon_start_cmd(host="127.0.0.1", port=port, workers=1)
    cmd.extend(["--log-level", "info", "--access-logfile", "-", "--error-logfile", "-"])
    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    return proc, log_handle


def _stop_daemon(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


def _log_tail(limit: int = 4000) -> str:
    if not LOG_PATH.exists():
        return ""
    data = LOG_PATH.read_bytes()[-limit:]
    return data.decode("utf-8", errors="replace")


def _assert_log_clean() -> None:
    text = _log_tail()
    lowered = text.lower()
    for marker in BAD_LOG_MARKERS:
        if marker in lowered:
            raise RuntimeError(f"daemon log contains {marker}: {text}")

def _wait_for_health(base_url: str, proc: subprocess.Popen[bytes], *, attempts: int = 30) -> None:
    last_error = "not checked"
    for _index in range(attempts):
        if proc.poll() is not None:
            raise RuntimeError(f"daemon exited before health check, rc={proc.returncode}: {_log_tail()}")
        try:
            status = _request_status(base_url, "/healthz", timeout=1.0)
        except RuntimeError as exc:
            last_error = str(exc)
        else:
            if 200 <= status < 300:
                return
            last_error = f"GET /healthz returned HTTP {status}"
        time.sleep(0.5)
    raise RuntimeError(f"healthz did not become ready: {last_error}; log: {_log_tail()}")


def run() -> int:
    print("=== SMOKE TEST: real daemon boot ===", flush=True)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    tmpdir, config_dir = _prepare_config()
    print(f"Using port {port}", flush=True)
    print(f"Using isolated config {config_dir}", flush=True)
    proc, log_handle = _start_daemon(port, config_dir=config_dir)
    print(f"Daemon PID: {proc.pid}", flush=True)
    try:
        _wait_for_health(base_url, proc)
        print("Healthz OK", flush=True)
        status = _request_json(base_url, "/api/status", psk=SMOKE_AUTH_TOKEN)
        print(json.dumps(status, indent=4, sort_keys=True), flush=True)
        print("Status API OK", flush=True)
        todo = _request_json(
            base_url,
            "/api/todos",
            method="POST",
            psk=SMOKE_AUTH_TOKEN,
            body={
                "title": "smoke-test-todo",
                "description": "auto-created by make smoke",
                "queue": "intake",
                "work_type": "code",
            },
        )
        print(json.dumps(todo, indent=4, sort_keys=True), flush=True)
        _request_json(base_url, "/api/todos", psk=SMOKE_AUTH_TOKEN)
        print("Todo API OK", flush=True)
        _assert_log_clean()
        print("No startup errors in log", flush=True)
        print("=== SMOKE: PASSED ===", flush=True)
        return 0
    finally:
        _stop_daemon(proc)
        log_handle.close()
        tmpdir.cleanup()
        print("Daemon stopped", flush=True)


def main() -> int:
    try:
        return run()
    except Exception as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr, flush=True)
        tail = _log_tail()
        if tail:
            print("--- daemon log tail ---", file=sys.stderr, flush=True)
            print(tail, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
