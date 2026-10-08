"""Tests for the native-first SearXServer lifecycle facade."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from general_ludd.searx.native import SearxLifecycleError
from general_ludd.searx.server import SearXServer


class _Backend:
    def __init__(self, uri: str = "searx+python://tests") -> None:
        self.instance_uri = uri
        self.process_pid = None
        self.running = False
        self.starts = 0
        self.stops = 0
        self.search_calls: list[tuple[str, dict[str, object]]] = []
        self.start_error: Exception | None = None

    def start(self) -> bool:
        if self.start_error is not None:
            raise self.start_error
        changed = not self.running
        self.running = True
        self.starts += int(changed)
        return changed

    def stop(self) -> bool:
        changed = self.running
        self.running = False
        self.stops += int(changed)
        return changed

    def is_running(self) -> bool:
        return self.running

    def search(self, query: str, **kwargs: object) -> dict[str, object]:
        self.search_calls.append((query, kwargs))
        return {"query": query, "results": []}


def test_defaults_select_native_without_a_child_process() -> None:
    backend = _Backend()
    server = SearXServer(native_runtime=backend)
    assert server.port == 8888
    assert server.external_url is None
    assert server.transport == "native"
    assert server.process_pid is None
    assert server._process is None


def test_custom_port_and_settings_are_compatibility_configuration() -> None:
    backend = _Backend()
    server = SearXServer(
        port=9999,
        settings_path="/tmp/custom/settings.yml",
        native_runtime=backend,
    )
    assert server.port == 9999
    assert server.settings_path == Path("/tmp/custom/settings.yml")


def test_port_from_environment() -> None:
    with mock.patch.dict("os.environ", {"GLUDD_SEARX_PORT": "6666"}, clear=True):
        server = SearXServer(native_runtime=_Backend())
    assert server.port == 6666


def test_native_instance_identity_is_not_a_raw_url() -> None:
    server = SearXServer(native_runtime=_Backend("searx+python://project-a"))
    assert server.get_instance_url() == "searx+python://project-a"


def test_external_url_requires_explicit_remote_backend() -> None:
    backend = _Backend("https://searx.example.com")
    server = SearXServer(
        external_url="https://searx.example.com/",
        remote_adapter=backend,
    )
    assert server.transport == "remote"
    assert server.external_url == "https://searx.example.com"
    assert server.get_instance_url() == "https://searx.example.com"


def test_start_is_idempotent_and_reports_health() -> None:
    backend = _Backend()
    server = SearXServer(native_runtime=backend)
    assert server.start() is True
    assert server.start() is True
    assert backend.starts == 1


def test_start_failure_is_recorded_without_leaking_exception() -> None:
    backend = _Backend()
    backend.start_error = SearxLifecycleError("unavailable")
    server = SearXServer(native_runtime=backend)
    assert server.start() is False
    assert isinstance(server.last_error, SearxLifecycleError)


def test_ensure_started_avoids_duplicate_start() -> None:
    backend = _Backend()
    server = SearXServer(native_runtime=backend)
    assert server.ensure_started() is True
    assert server.ensure_started() is True
    assert backend.starts == 1


def test_search_delegates_directly_to_backend() -> None:
    backend = _Backend()
    backend.start()
    server = SearXServer(native_runtime=backend)
    result = server.search("privacy", categories=["general"])
    assert result == {"query": "privacy", "results": []}
    assert backend.search_calls == [("privacy", {"categories": ["general"]})]


def test_stop_releases_native_backend() -> None:
    backend = _Backend()
    backend.start()
    server = SearXServer(native_runtime=backend)
    server.stop()
    assert backend.stops == 1
    assert server.is_running() is False


def test_stop_when_no_legacy_process_is_safe() -> None:
    server = SearXServer(native_runtime=_Backend())
    server.stop()


def test_stop_reaps_legacy_process_during_rolling_upgrade() -> None:
    process = mock.MagicMock(spec=subprocess.Popen)
    server = SearXServer(native_runtime=_Backend())
    server._process = process
    server.stop()
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(5.0)
    assert server._process is None


def test_stop_force_kills_and_waits_for_legacy_process() -> None:
    process = mock.MagicMock(spec=subprocess.Popen)
    process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="legacy-searxng", timeout=5.0),
        None,
    ]
    server = SearXServer(native_runtime=_Backend())
    server._process = process
    server.stop()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
