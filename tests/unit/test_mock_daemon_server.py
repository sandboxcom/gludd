"""Behavioral tests for molecule/mock_daemon/server.py.

Starts the mock daemon server in a background thread and sends real HTTP
requests to verify every endpoint returns the correct response shape,
status code, and error handling behaviour.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
MOCK_DAEMON_SCRIPT = ROOT / "molecule" / "mock_daemon" / "server.py"


def _python() -> str:
    """Return the immutable interpreter owned by the current test process."""
    return sys.executable


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ChildProcess(Protocol):
    """Minimum child identity needed for readiness diagnostics."""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...


def _wait_for_server(
    url: str,
    timeout: float = 10.0,
    process: _ChildProcess | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process is not None:
            returncode = process.poll()
            if returncode is not None:
                raise RuntimeError(
                    "Mock daemon child terminated before readiness: "
                    f"pid={process.pid} returncode={returncode}"
                )
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=1):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    child = f"pid={process.pid} state=running" if process is not None else "child=unobserved"
    error = type(last_error).__name__ if last_error is not None else "none"
    raise TimeoutError(
        f"Mock daemon did not start within {timeout}s; {child}; last_error={error}"
    )


def _load_mock_daemon() -> ModuleType:
    """Load the server implementation for deterministic startup-order tests."""
    spec = importlib.util.spec_from_file_location("mock_daemon_startup_under_test", MOCK_DAEMON_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get(url: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(f"{url}{path}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode())
        return resp.status, body


def _post(url: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(f"{url}{path}", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode())
        return resp.status, body


class TestCollectionControlPlaneEndpoints:
    """Daemon seams used by the migrated collection modules."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_git_route_owns_worktree_lifecycle(self, url: str, tmp_path: Path) -> None:
        worktree = tmp_path / "candidate"
        status, created = _post(
            url,
            "/admin/git/operation",
            {
                "op": "worktree_create",
                "path": str(tmp_path / "repo"),
                "branch": "feature/molecule",
                "worktree_path": str(worktree),
            },
        )
        assert status == 200
        assert created["result"]["success"] is True
        assert worktree.is_dir()

        status, removed = _post(
            url,
            "/admin/git/operation",
            {
                "op": "worktree_remove",
                "path": str(tmp_path / "repo"),
                "worktree_path": str(worktree),
            },
        )
        assert status == 200
        assert removed["result"]["removed"] is True
        assert not worktree.exists()

    def test_git_route_returns_typed_commit_and_branch_results(self, url: str) -> None:
        _, committed = _post(
            url,
            "/admin/git/operation",
            {"op": "commit", "path": "/tmp/repo", "message": "molecule commit"},
        )
        _, branched = _post(
            url,
            "/admin/git/operation",
            {"op": "branch", "path": "/tmp/repo", "branch": "molecule/test-branch"},
        )
        assert committed["changed"] is True
        assert committed["result"]["sha"] == "0123456789abcdef"
        assert committed["result"]["message"] == "molecule commit"
        assert branched["result"]["branch"] == "molecule/test-branch"

    @pytest.mark.parametrize(
        ("target", "success", "exit_code", "needle"),
        [
            ("hello", True, 0, "hello from molecule test_gludd_make"),
            ("versions", True, 0, "make version:"),
            ("does-not-exist", False, 2, "No rule to make target"),
        ],
    )
    def test_make_route_preserves_structured_result(
        self, url: str, target: str, success: bool, exit_code: int, needle: str
    ) -> None:
        status, result = _post(url, "/admin/make", {"target": target})
        assert status == 200
        assert result["success"] is success
        assert result["exit_code"] == exit_code
        assert needle in (result["stdout_tail"] + result["stderr_tail"])

    def test_skill_route_renders_required_variables(self, url: str) -> None:
        status, result = _post(
            url,
            "/admin/skills/render",
            {
                "name": "mock-review",
                "variables": {"language": "python", "project_name": "gludd"},
            },
        )
        assert status == 200
        assert result["skill_name"] == "mock-review"
        assert "python" in result["rendered_body"]
        assert "gludd" in result["rendered_body"]

    @pytest.mark.parametrize(
        ("operation", "required"),
        [
            ("bom_detect", {"bom_detected", "encoding"}),
            ("encoding_detect", {"detected_encoding", "confidence_level"}),
            ("homoglyph_scan", {"input_length", "total_findings"}),
            ("language_detect", {"language", "confidence"}),
            ("locale_format", {"locale", "formatted_value", "is_rtl"}),
            ("phonetic_transcribe", {"method", "words"}),
            ("translate", {"translated_text", "target_language"}),
            ("transliterate", {"transliterated_text", "target_script"}),
            ("unicode_analyze", {"input_length", "codepoints", "normalization"}),
        ],
    )
    def test_language_route_returns_operation_schema(
        self, url: str, operation: str, required: set[str]
    ) -> None:
        status, response = _post(
            url,
            "/api/language/execute",
            {"operation": operation, "payload": {"input_text": "Hello"}},
        )
        assert status == 200
        assert required <= response["result"].keys()

    def test_language_route_fails_closed_for_unknown_operation(self, url: str) -> None:
        status, response = _post_error(
            url,
            "/api/language/execute",
            {"operation": "not_registered", "payload": {}},
        )
        assert status == 422
        assert response == {"detail": "unsupported language operation: not_registered"}

    def test_language_action_crosses_the_installed_collection_boundary(self, url: str) -> None:
        from ansible_collections.general_ludd.language.plugins.action.language_operation import (
            execute_action,
        )

        result = execute_action(
            {
                "operation": "bom_detect",
                "payload": {"input_bytes": "efbbbf48656c6c6f"},
                "daemon_url": url,
                "psk": "molecule-language-psk",
            }
        )
        assert result["failed"] is False
        assert result["result"]["bom_detected"] is True

    def test_observe_facade_preserves_fanout_and_isolated_errors(self, url: str) -> None:
        _post(url, "/__requests/reset")
        operations = (
            ("query_sources", ["logs", "metrics", "traces"]),
            ("timeline", ["logs", "metrics", "traces", "events"]),
            ("correlate_incident", ["logs", "metrics", "traces"]),
            ("topology", ["logs", "metrics", "traces"]),
        )
        results: dict[str, dict] = {}
        for operation, kinds in operations:
            request_payload = {
                "operation": operation,
                "role": "molecule_observe_probe",
                "kinds": kinds,
                "seed": {
                    "ts": 25.0,
                    "source": "incident-seed",
                    "kind": "events",
                    "labels": {"trace_id": "incident-42"},
                },
                "start": 5.0,
                "end": 35.0,
            }
            if operation == "correlate_incident":
                request_payload["start"] = None
                request_payload["end"] = None
                request_payload["window_s"] = 20.0
            status, response = _post(
                url,
                "/api/observe/facade",
                request_payload,
            )
            assert status == 200
            results[operation] = response["result"]

        assert [record["ts"] for record in results["query_sources"]["records"]] == [
            10.0,
            20.0,
            30.0,
        ]
        assert results["timeline"]["errors"] == [
            {"source": "broken-events", "message": "query failed"}
        ]
        assert len(results["correlate_incident"]["groups"]["incident-42"]) == 4
        assert results["topology"]["topology"]["services"]["checkout"] == ["web-01"]
        _, requests = _get(url, "/__requests")
        assert requests["requests"].count("GET /api/observe/sources") == 4
        assert requests["requests"].count("POST /api/observe/query") == 13

    @pytest.mark.parametrize("source_root", ["", "src"])
    def test_reload_route_promotes_or_rolls_back_from_health_gate(
        self, url: str, tmp_path: Path, source_root: str
    ) -> None:
        live = tmp_path / source_root / "demo" / "leaf.py"
        live.parent.mkdir(parents=True)
        live.write_text('VERSION = "v1"\n')
        candidate = tmp_path / "candidate.py"
        candidate.write_text('VERSION = "v2"\n')
        payload = {
            "module_name": "demo.leaf",
            "candidate_source_path": str(candidate),
            "health_url": f"{url}/readyz",
        }
        _, promoted = _post(url, "/admin/reload/code", payload)
        assert promoted["success"] is True
        assert promoted["rolled_back"] is False
        assert '"v2"' in live.read_text()

        candidate.write_text('VERSION = "broken"\n')
        payload["health_url"] = f"{url}/readyz-degraded"
        _, rolled_back = _post(url, "/admin/reload/code", payload)
        assert rolled_back["success"] is False
        assert rolled_back["rolled_back"] is True
        assert '"v2"' in live.read_text()


def _patch(url: str, path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(f"{url}{path}", data=data, headers=headers, method="PATCH")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode())
        return resp.status, body


def _request_raw(url: str, path: str, method: str = "GET", headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(f"{url}{path}", headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


def _post_error(url: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(f"{url}{path}", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as error:
        with error:
            body = json.loads(error.read().decode())
            return error.code, body


def _get_error(url: str, path: str) -> tuple[int, dict]:
    try:
        req = urllib.request.Request(f"{url}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as error:
        with error:
            body = json.loads(error.read().decode())
            return error.code, body


class TestMockDaemonStartup:
    """Tests for mock daemon process startup and shutdown."""

    def test_wait_reports_terminal_child_before_timeout(self) -> None:
        class ExitedChild:
            pid = 4242

            def poll(self) -> int:
                return 17

        with pytest.raises(
            RuntimeError,
            match=r"pid=4242 returncode=17",
        ):
            _wait_for_server(
                "http://127.0.0.1:1",
                timeout=10.0,
                process=ExitedChild(),
            )

    def test_readiness_waits_for_delayed_pidfile_publish(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_mock_daemon()
        port = _find_free_port()
        url = f"http://127.0.0.1:{port}"
        pidfile = tmp_path / "mock-daemon.pid"
        pid_write_started = threading.Event()
        allow_pid_write = threading.Event()
        original_write = module._atomic_write

        def delayed_write(path: str, content: str) -> None:
            if Path(path) == pidfile:
                pid_write_started.set()
                if not allow_pid_write.wait(timeout=2):
                    raise TimeoutError("test did not release delayed pidfile write")
            original_write(path, content)

        monkeypatch.setattr(module, "_atomic_write", delayed_write)
        monkeypatch.setattr(module.signal, "signal", lambda *_args: None)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                str(MOCK_DAEMON_SCRIPT),
                "--port",
                str(port),
                "--pidfile",
                str(pidfile),
                "--lease-seconds",
                "0.75",
            ],
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(module.main)
            assert pid_write_started.wait(timeout=2)
            try:
                with (
                    pytest.raises(OSError),
                    urllib.request.urlopen(f"{url}/healthz", timeout=0.2),
                ):
                    pass
            finally:
                allow_pid_write.set()

            _wait_for_server(url, timeout=2)
            assert pidfile.read_text(encoding="utf-8") == str(os.getpid())
            assert future.result(timeout=3) == 0

    def test_daemon_starts_and_writes_pidfile(self, tmp_path: Path):
        port = _find_free_port()
        pidfile = tmp_path / "mock-daemon.pid"
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port), "--pidfile", str(pidfile)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_server(f"http://127.0.0.1:{port}", process=proc)
            assert pidfile.exists()
            pid = int(pidfile.read_text().strip())
            assert pid == proc.pid
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_daemon_binds_only_localhost(self, tmp_path: Path):
        port = _find_free_port()
        pidfile = tmp_path / "mock-daemon.pid"
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port), "--pidfile", str(pidfile)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_server(f"http://127.0.0.1:{port}", process=proc)
            _, body = _get(f"http://127.0.0.1:{port}", "/healthz")
            assert body == {"status": "ok"}
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    def test_daemon_clean_shutdown_on_sigterm(self, tmp_path: Path):
        port = _find_free_port()
        pidfile = tmp_path / "mock-daemon.pid"
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port), "--pidfile", str(pidfile)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_server(f"http://127.0.0.1:{port}", process=proc)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
            assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}"


class TestHealthEndpoints:
    """GET /healthz, /readyz, /readyz-degraded, /ci-status."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_healthz_returns_ok(self, url: str):
        status, body = _get(url, "/healthz")
        assert status == 200
        assert body == {"status": "ok"}

    def test_readyz_returns_not_degraded(self, url: str):
        status, body = _get(url, "/readyz")
        assert status == 200
        assert body == {"status": "ok", "degraded": False}

    def test_readyz_degraded_returns_degraded_true(self, url: str):
        status, body = _get(url, "/readyz-degraded")
        assert status == 200
        assert body == {"status": "degraded", "degraded": True}

    def test_ci_status_returns_success(self, url: str):
        status, body = _get(url, "/ci-status")
        assert status == 200
        assert body["status"] == "completed"
        assert body["conclusion"] == "success"
        assert body["passed"] is True
        assert "run_id" in body
        assert "commit_sha" in body


class TestFactsMetricsTraces:
    """GET /api/facts, /api/metrics, /api/traces."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_facts_returns_complete_snapshot(self, url: str):
        status, body = _get(url, "/api/facts")
        assert status == 200
        for key in ("work", "todos", "models", "history", "messages", "metrics", "traces", "codebase"):
            assert key in body, f"Facts missing key {key}"
        assert body["codebase"]["coverage"]["overall_line_rate"] == 0.74

    def test_metrics_returns_agent_and_cost_data(self, url: str):
        status, body = _get(url, "/api/metrics")
        assert status == 200
        assert body["total_agents"] == 1
        assert body["running_agents"] == 1
        assert len(body["agents"]) == 1
        assert body["benchmark_rankings"][0]["task_type"] == "code"

    def test_traces_returns_spans_and_phases(self, url: str):
        status, body = _get(url, "/api/traces")
        assert status == 200
        assert body["count"] == 1
        assert len(body["recent"]) == 1
        assert len(body["recent"][0]["spans"]) == 2
        assert "plan" in body["by_phase"]
        assert "generate" in body["by_phase"]


class TestObserveEndpoints:
    """GET /api/observe/sources, POST /api/observe/query."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_sources_lists_all_registered_sources(self, url: str):
        status, body = _get(url, "/api/observe/sources")
        assert status == 200
        assert len(body["sources"]) == 4
        names = [s["name"] for s in body["sources"]]
        assert "prod-logs" in names
        assert "broken-events" in names

    def test_query_valid_source_returns_records(self, url: str):
        status, body = _post(url, "/api/observe/query", {"source": "prod-logs"})
        assert status == 200
        assert len(body["records"]) == 2
        assert body["records"][0]["source"] == "prod-logs"

    def test_query_broken_source_returns_503(self, url: str):
        status, body = _post_error(url, "/api/observe/query", {"source": "broken-events"})
        assert status == 503
        assert "detail" in body

    def test_query_unknown_source_returns_404(self, url: str):
        status, _body = _post_error(url, "/api/observe/query", {"source": "unknown-source"})
        assert status == 404


class TestMessagesEndpoints:
    """GET /api/messages, POST /api/messages, POST /api/messages/<id>/ack."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_get_messages_returns_inbox(self, url: str):
        status, body = _get(url, "/api/messages")
        assert status == 200
        assert "messages" in body
        assert len(body["messages"]) == 1
        assert body["messages"][0]["sender"] == "planner"

    def test_post_message_creates_message(self, url: str):
        status, body = _post(
            url,
            "/api/messages",
            {
                "sender": "agent-1",
                "recipient": "agent-2",
                "topic": "standup",
            },
        )
        assert status == 201
        assert body["id"] == "MSG-MOCK-0001"
        assert body["sender"] == "agent-1"
        assert body["recipient"] == "agent-2"

    def test_ack_message_returns_acked_true(self, url: str):
        status, body = _post(url, "/api/messages/any-id/ack")
        assert status == 200
        assert body == {"acked": True}


class TestTodosEndpoints:
    """GET /api/todos/<id>, PATCH /api/todos/<id>."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_get_todo_returns_canned_record(self, url: str):
        status, body = _get(url, "/api/todos/TODO-001")
        assert status == 200
        assert body["id"] == "TODO-001"
        assert body["status"] == "backlog"
        assert body["work_type"] == "code"

    def test_patch_todo_updates_status(self, url: str):
        status, body = _patch(url, "/api/todos/TODO-002", {"status": "done"})
        assert status == 200
        assert body["id"] == "TODO-002"
        assert body["status"] == "done"


class TestFeaturesSpendAccounting:
    """Feature, spend, and accounting read endpoints."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_features_list_returns_all(self, url: str):
        status, body = _get(url, "/api/features")
        assert status == 200
        assert body["total"] == 2
        assert len(body["features"]) == 2
        assert body["features"][0]["id"] == "FEAT-0001"

    def test_features_verify_returns_summary(self, url: str):
        status, body = _post(url, "/api/features/verify")
        assert status == 200
        assert "summary" in body
        assert body["summary"]["total"] == 2
        assert len(body["results"]) == 2

    def test_spend_returns_snapshot(self, url: str):
        status, body = _get(url, "/api/spend")
        assert status == 200
        assert body["limit_usd"] == 10.0
        assert body["window_label"] == "24h"

    def test_spend_configure_returns_updated_config(self, url: str):
        status, body = _post(
            url,
            "/api/spend/configure",
            {
                "limit_usd": 25.0,
                "window_seconds": 7200,
            },
        )
        assert status == 200
        assert body["limit_usd"] == 25.0
        assert body["window_seconds"] == 7200
        assert body["updated"] is True

    def test_accounting_list_returns_all(self, url: str):
        status, body = _get(url, "/api/accounting")
        assert status == 200
        assert body["total"] == 2
        assert len(body["accounting"]) == 2
        assert body["accounting"][0]["project_id"] == "mock-project-alpha"

    def test_accounting_project_found_returns_snapshot(self, url: str):
        status, body = _get(url, "/api/accounting/mock-project-alpha")
        assert status == 200
        assert body["project_id"] == "mock-project-alpha"
        assert body["usd_spent"] == 0.0048

    def test_accounting_project_not_found_returns_404(self, url: str):
        status, body = _get_error(url, "/api/accounting/nonexistent-project")
        assert status == 404
        assert "detail" in body


class TestScheduleDispatch:
    """POST /api/schedule, POST /api/dispatch, GET /api/dispatch/available, GET /api/dispatch/recent."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_schedule_empty_items_returns_empty_batches(self, url: str):
        status, body = _post(url, "/api/schedule", {"items": []})
        assert status == 200
        assert body == {"batches": []}

    def test_schedule_with_items_batches_by_dependency(self, url: str):
        items = [
            {"id": "A", "resources": ["X"], "is_greenfield": False},
            {"id": "B", "resources": ["Y"], "is_greenfield": False},
            {"id": "C", "depends_on": ["A"], "resources": ["Z"], "is_greenfield": False},
        ]
        status, body = _post(url, "/api/schedule", {"items": items})
        assert status == 200
        assert "batches" in body
        assert len(body["batches"]) >= 1
        all_ids = [iid for batch in body["batches"] for iid in batch]
        assert "A" in all_ids
        assert "B" in all_ids
        assert "C" in all_ids

    def test_schedule_greenfield_items_batched_together(self, url: str):
        items = [
            {"id": "G1", "is_greenfield": True},
            {"id": "G2", "is_greenfield": True},
        ]
        status, body = _post(url, "/api/schedule", {"items": items})
        assert status == 200
        all_ids = [iid for batch in body["batches"] for iid in batch]
        assert "G1" in all_ids
        assert "G2" in all_ids

    def test_dispatch_returns_result_with_id(self, url: str):
        status, body = _post(url, "/api/dispatch", {"kind": "tool", "name": "shell", "args": {"command": "ls"}})
        assert status == 200
        assert body["result"]["id"] == "dispatch-mock-new"
        assert body["result"]["status"] == "success"
        assert "output" in body["result"]

    def test_dispatch_available_lists_handlers(self, url: str):
        status, body = _get(url, "/api/dispatch/available")
        assert status == 200
        assert len(body["handlers"]) == 3
        kinds = [h["kind"] for h in body["handlers"]]
        assert "tool" in kinds

    def test_dispatch_recent_returns_records(self, url: str):
        status, body = _get(url, "/api/dispatch/recent")
        assert status == 200
        assert len(body["records"]) == 1
        assert body["records"][0]["status"] == "success"


class TestEnvironmentEndpoints:
    """GET /api/environment, GET /api/environment/advise."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_environment_snapshot_has_budget_field(self, url: str):
        status, body = _get(url, "/api/environment")
        assert status == 200
        assert body["budget"]["run_remaining_usd"] == 5.0
        assert body["system"]["cpu_count"] == 4

    def test_environment_advise_feature_uses_workflow(self, url: str):
        status, body = _get(url, "/api/environment/advise?work_type=feature")
        assert status == 200
        assert body["use_workflow"] is True
        assert body["task_type"] == "feature"

    def test_environment_advise_docs_is_single_shot(self, url: str):
        status, body = _get(url, "/api/environment/advise?work_type=docs")
        assert status == 200
        assert body["use_workflow"] is False
        assert body["task_type"] == "docs"

    def test_environment_advise_no_work_type_returns_default(self, url: str):
        status, body = _get(url, "/api/environment/advise")
        assert status == 200
        assert "task_type" in body
        assert "recommendation" in body


class TestModelEndpoints:
    """Model endpoints: call, workflow, performance, ranking."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_model_call_plain_returns_human_readable_text(self, url: str):
        status, body = _post(url, "/admin/models/call", {"model_profile": "mock-profile"})
        assert status == 200
        assert body["text"] == "[mock-daemon] applied the requested change."
        assert body["model_profile_id"] == "mock-profile"
        assert body["usage"]["total_tokens"] == 15

    def test_model_call_json_format_returns_json_string_text(self, url: str):
        status, body = _post(url, "/admin/models/call", {"response_format": "json", "options": ["a", "b"]})
        assert status == 200
        parsed = json.loads(body["text"])
        assert parsed["decision"] == "proceed"
        assert parsed["rationale"] == "looks correct"

    def test_model_workflow_returns_content_and_quality(self, url: str):
        status, body = _post(url, "/admin/models/workflow", {})
        assert status == 200
        assert body["content"] == "def solution(): return 42"
        assert body["quality_score"] == 0.82
        assert body["retries"] == 1

    def test_model_performance_returns_profiles(self, url: str):
        status, body = _get(url, "/admin/models/performance")
        assert status == 200
        assert len(body["profiles"]) == 3
        assert body["profiles"][0]["task_type"] == "plan"

    def test_model_ranking_filters_by_task_type(self, url: str):
        status, body = _get(url, "/admin/models/ranking?task_type=code")
        assert status == 200
        assert body["task_type"] == "code"
        assert len(body["rankings"]) == 1
        assert body["rankings"][0]["task_type"] == "code"

    def test_model_ranking_no_task_type_returns_all(self, url: str):
        status, body = _get(url, "/admin/models/ranking")
        assert status == 200
        assert len(body["rankings"]) == 3


class TestSTSTokenLifecycle:
    """STS token mint, validate, get, list, revoke."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_mint_creates_token_with_metadata(self, url: str):
        status, body = _post(
            url,
            "/admin/sts/mint",
            {
                "agent_id": "agent-42",
                "parent_agent_id": "root",
            },
        )
        assert status == 201
        assert body["agent_id"] == "agent-42"
        assert body["token_id"] == "tok-agent-42"
        assert body["parent_agent_id"] == "root"
        assert "created_at" in body
        assert "expires_at" in body

    def test_validate_valid_token_returns_true(self, url: str):
        _post(url, "/admin/sts/mint", {"agent_id": "agent-valid"})
        status, body = _get(url, "/admin/sts/validate/agent-valid")
        assert status == 200
        assert body["valid"] is True
        assert body["agent_id"] == "agent-valid"

    def test_validate_nonexistent_token_returns_false(self, url: str):
        status, body = _get(url, "/admin/sts/validate/agent-ghost")
        assert status == 200
        assert body["valid"] is False

    def test_get_token_returns_record(self, url: str):
        _post(url, "/admin/sts/mint", {"agent_id": "agent-get"})
        status, body = _get(url, "/admin/sts/tokens/agent-get")
        assert status == 200
        assert body["agent_id"] == "agent-get"

    def test_list_tokens_returns_all(self, url: str):
        status, body = _get(url, "/admin/sts/tokens")
        assert status == 200
        assert "tokens" in body
        assert isinstance(body["tokens"], list)

    def test_revoke_token_sets_revoked_status(self, url: str):
        _post(url, "/admin/sts/mint", {"agent_id": "agent-revoke"})
        status, body = _post(url, "/admin/sts/revoke/agent-revoke")
        assert status == 200
        assert body["status"] == "revoked"
        assert body["agent_id"] == "agent-revoke"

    def test_validate_revoked_token_returns_invalid(self, url: str):
        _post(url, "/admin/sts/mint", {"agent_id": "agent-revoked"})
        _post(url, "/admin/sts/revoke/agent-revoked")
        _status, body = _get(url, "/admin/sts/validate/agent-revoked")
        assert body["valid"] is False
        assert body["revoked"] is True


class TestProcessManagement:
    """GET /admin/processes, GET /admin/processes/<pid>/stats, POST /admin/processes/<pid>/signal."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_process_list_returns_registry(self, url: str):
        status, body = _get(url, "/admin/processes")
        assert status == 200
        assert body["count"] >= 1
        assert len(body["processes"]) >= 1

    def test_process_stats_returns_cpu_and_memory(self, url: str):
        status, body = _get(url, "/admin/processes/424242/stats")
        assert status == 200
        assert body["pid"] == 424242
        assert "cpu_percent" in body
        assert "memory" in body
        assert body["status"] == "sleeping"

    def test_process_signal_returns_ack(self, url: str):
        status, body = _post(
            url,
            "/admin/processes/424242/signal",
            {
                "signal": "SIGKILL",
                "group": False,
            },
        )
        assert status == 200
        assert body["ok"] is True
        assert body["pid"] == 424242
        assert body["signal"] == "SIGKILL"

    def test_process_bad_path_returns_404(self, url: str):
        status, _ = _get_error(url, "/admin/processes/not-a-number/stats")
        assert status == 404


class TestOrnithAndHumanTodos:
    """GET /admin/ornith/pairs, POST /api/human-todos."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_ornith_pairs_filters_by_status_csv(self, url: str):
        status, body = _get(url, "/admin/ornith/pairs?status=rejected_by_gate,rejected_by_review,reverted&limit=5")
        assert status == 200
        assert body["count"] == 3
        assert len(body["pairs"]) == 3

    def test_ornith_pairs_single_status_filters(self, url: str):
        status, body = _get(url, "/admin/ornith/pairs?status=rejected_by_gate&limit=5")
        assert status == 200
        assert body["count"] == 1

    def test_ornith_pairs_unknown_status_returns_empty(self, url: str):
        status, body = _get(url, "/admin/ornith/pairs?status=nonexistent&limit=5")
        assert status == 200
        assert body["pairs"] == []

    def test_human_todo_created_returns_record(self, url: str):
        status, body = _post(
            url,
            "/api/human-todos",
            {
                "agent_id": "agent-1",
                "title": "Need approval",
                "category": "decision",
                "priority": "high",
            },
        )
        assert status == 201
        assert body["id"] == "HTODO-MOCK-0001"
        assert body["title"] == "Need approval"
        assert body["category"] == "decision"
        assert body["status"] == "open"


class TestStreamDispatch:
    """POST /admin/stream/dispatch."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_dispatch_returns_task_id_and_clone_path(self, url: str):
        status, body = _post(
            url,
            "/admin/stream/dispatch",
            {
                "dispatch_role_clone": {"role": "agent_orchestrate", "inject_as": "stream_chunk"},
            },
        )
        assert status == 200
        assert body["accepted"] is True
        assert body["task_id"].startswith("stream-task-mock-")
        assert body["clone_path"].startswith("/tmp/gludd-stream-clone-")

    def test_multiple_dispatches_return_distinct_task_ids(self, url: str):
        _, a = _post(
            url,
            "/admin/stream/dispatch",
            {
                "dispatch_role_clone": {"role": "test", "inject_as": "chunk1"},
            },
        )
        _, b = _post(
            url,
            "/admin/stream/dispatch",
            {
                "dispatch_role_clone": {"role": "test", "inject_as": "chunk2"},
            },
        )
        assert a["task_id"] != b["task_id"]


class TestProcessAuditAndResourcePreferences:
    """GET /process-audit, GET /api/resource-preferences."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_process_audit_returns_guardrail_health(self, url: str):
        status, body = _get(url, "/process-audit")
        assert status == 200
        assert "guardrail_health" in body
        assert body["guardrail_health"]["health_score"] == 0.125
        assert body["guardrail_health"]["overfitted"] is True

    def test_resource_preferences_returns_value(self, url: str):
        status, body = _get(url, "/api/resource-preferences")
        assert status == 200
        assert body["preference"] == "mock-profile"


class TestGitHubApiMocks:
    """Mock GitHub API routes."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_actions_runs_returns_workflow_runs(self, url: str):
        status, body = _get(url, "/repos/mock-org/mock-repo/actions/runs")
        assert status == 200
        assert body["total_count"] == 5
        assert len(body["workflow_runs"]) == 5
        run_names = [r["name"] for r in body["workflow_runs"]]
        assert "gate" in run_names
        assert "pytest" in run_names

    def test_actions_workflows_returns_workflow_list(self, url: str):
        status, body = _get(url, "/repos/mock-org/mock-repo/actions/workflows")
        assert status == 200
        assert body["total_count"] == 3
        assert body["workflows"][0]["name"] == "gate"

    def test_billing_returns_minutes_used(self, url: str):
        status, body = _get(url, "/orgs/mock-org/settings/billing/actions")
        assert status == 200
        assert body["total_minutes_used"] == 320
        assert body["included_minutes"] == 2000


class TestOpenBaoBreakGlass:
    """OpenBao snapshot and restore mock endpoints."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_snapshot_requires_vault_token(self, url: str):
        status, _body = _request_raw(url, "/v1/sys/storage/raft/snapshot")
        assert status == 403

    def test_snapshot_with_token_returns_octet_stream(self, url: str):
        headers = {"X-Vault-Token": "mock-token"}
        req = urllib.request.Request(
            f"{url}/v1/sys/storage/raft/snapshot",
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "application/octet-stream"
            data = resp.read()
            assert b"OPENBAO-RAFT-SNAPSHOT-MOCK" in data

    def test_restore_requires_vault_token(self, url: str):
        status, _ = _request_raw(url, "/v1/sys/storage/raft/restore", method="POST")
        assert status == 403

    def test_restore_with_token_returns_204(self, url: str):
        headers = {"X-Vault-Token": "mock-token", "Content-Type": "application/octet-stream"}
        data = b"fake-raft-payload" * 4
        req = urllib.request.Request(
            f"{url}/v1/sys/storage/raft/restore",
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 204


class TestRequestLogIntrospection:
    """GET /__requests, POST /__requests/reset."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_requests_log_records_hits(self, url: str):
        _get(url, "/healthz")
        _get(url, "/api/facts")
        status, body = _get(url, "/__requests")
        assert status == 200
        assert "GET /healthz" in body["requests"]
        assert "GET /api/facts" in body["requests"]

    def test_requests_log_excludes_introspection_paths(self, url: str):
        _get(url, "/__requests")
        status, body = _get(url, "/__requests")
        assert status == 200
        requests_list = body["requests"]
        introspection_hits = [r for r in requests_list if r.startswith("GET /__requests")]
        assert len(introspection_hits) == 0

    def test_requests_reset_clears_log(self, url: str):
        _get(url, "/healthz")
        _, body_before = _get(url, "/__requests")
        assert len(body_before["requests"]) > 0
        _post(url, "/__requests/reset")
        _, body_after = _get(url, "/__requests")
        assert body_after["requests"] == []


class TestErrorHandling:
    """Error responses for unknown routes and bad paths."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_unknown_get_route_returns_404(self, url: str):
        status, body = _get_error(url, "/api/nonexistent-endpoint")
        assert status == 404
        assert "detail" in body

    def test_unknown_post_route_returns_404(self, url: str):
        status, _body = _get_error(url, "/api/nonexistent-endpoint")
        assert status == 404

    def test_unknown_patch_route_returns_404(self, url: str):
        status, _body = _get_error(url, "/api/nonexistent-endpoint")
        assert status == 404

    def test_sts_validate_invalid_id_returns_404(self, url: str):
        status, _body = _get_error(url, "/admin/sts/validate/")
        assert status == 404

    def test_accounting_nonexistent_project_returns_404(self, url: str):
        status, body = _get_error(url, "/api/accounting/zzz-nonexistent-zzz")
        assert status == 404
        assert "Project not found" in body["detail"]

    def test_healthz_always_returns_200(self, url: str):
        for _ in range(5):
            status, body = _get(url, "/healthz")
            assert status == 200
            assert body == {"status": "ok"}


class TestConcurrentRequests:
    """Concurrent request handling with multiple threads."""

    @pytest.fixture(scope="class")
    def url(self) -> Generator[str]:
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        _wait_for_server(base, process=proc)
        yield base
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    def test_concurrent_get_healthz_all_return_200(self, url: str):
        def hit_healthz(_: int) -> int:
            status, _ = _get(url, "/healthz")
            return status

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(hit_healthz, range(10)))
        assert all(s == 200 for s in results)
        assert len(results) == 10

    def test_concurrent_mixed_endpoints_all_succeed(self, url: str):
        def hit_endpoint(i: int) -> int:
            endpoints = ["/healthz", "/api/facts", "/api/metrics", "/api/traces", "/api/environment"]
            path = endpoints[i % len(endpoints)]
            status, _ = _get(url, path)
            return status

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
            results = list(ex.map(hit_endpoint, range(15)))
        assert all(s == 200 for s in results)

    def test_concurrent_sts_mint_produces_distinct_records(self, url: str):
        def mint_agent(i: int) -> dict:
            _, body = _post(
                url,
                "/admin/sts/mint",
                {
                    "agent_id": f"concurrent-agent-{i}",
                },
            )
            return body

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            records = list(ex.map(mint_agent, range(5)))
        agent_ids = [r["agent_id"] for r in records]
        assert len(set(agent_ids)) == 5

    def test_concurrent_request_log_is_accurate(self, url: str):
        _post(url, "/__requests/reset")

        def hit_healthz_once(_: int) -> None:
            _get(url, "/healthz")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(hit_healthz_once, range(8)))
        _, body = _get(url, "/__requests")
        healthz_hits = [r for r in body["requests"] if r == "GET /healthz"]
        assert len(healthz_hits) == 8


class TestManagedPidOverride:
    """--managed-pid flag replaces the first process record's pid/pgid."""

    def test_managed_pid_overrides_first_record(self, tmp_path: Path):
        port = _find_free_port()
        proc = subprocess.Popen(
            [_python(), str(MOCK_DAEMON_SCRIPT), "--port", str(port), "--managed-pid", "99999"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_server(f"http://127.0.0.1:{port}", process=proc)
            _, body = _get(f"http://127.0.0.1:{port}", "/admin/processes")
            assert body["processes"][0]["pid"] == 99999
            assert body["processes"][0]["pgid"] == 99999
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
