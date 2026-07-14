from __future__ import annotations

import subprocess
from unittest import mock

import httpx
import pytest

from general_ludd.searx.server import SearXServer


@pytest.fixture
def mock_process() -> mock.MagicMock:
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = None
    proc.returncode = None
    return proc


@pytest.fixture
def mock_health_ok() -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status_code = 200
    return resp


class TestSearXServerConstruction:
    def test_defaults(self) -> None:
        server = SearXServer()
        assert server.port == 8888
        assert server.external_url is None
        assert server._process is None

    def test_custom_port(self) -> None:
        server = SearXServer(port=9999)
        assert server.port == 9999

    def test_port_from_env(self) -> None:
        with mock.patch.dict("os.environ", {"GLUDD_SEARX_PORT": "6666"}, clear=True):
            server = SearXServer()
            assert server.port == 6666

    def test_external_url_mode(self) -> None:
        server = SearXServer(external_url="https://searx.example.com")
        assert server.external_url == "https://searx.example.com"
        assert server.port == 8888

    def test_custom_settings_path(self) -> None:
        server = SearXServer(settings_path="/tmp/custom/settings.yml")
        assert str(server.settings_path) == "/tmp/custom/settings.yml"


class TestGetInstanceUrl:
    def test_local_url(self) -> None:
        server = SearXServer(port=9999)
        assert server.get_instance_url() == "http://127.0.0.1:9999"

    def test_external_url(self) -> None:
        server = SearXServer(external_url="https://searx.example.com")
        assert server.get_instance_url() == "https://searx.example.com"

    def test_default_port_local_url(self) -> None:
        server = SearXServer()
        assert server.get_instance_url() == "http://127.0.0.1:8888"


class TestStart:
    def test_start_with_external_url_is_noop(self) -> None:
        server = SearXServer(external_url="https://searx.example.com")
        assert server.start() is True

    def test_start_tries_commands(self, mock_process: mock.MagicMock) -> None:
        server = SearXServer(port=9999)
        with mock.patch("subprocess.Popen", return_value=mock_process), mock.patch.object(
            server, "_health_check", return_value=True
        ), mock.patch.object(server, "_detect_bound_port", return_value=9999):
            result = server.start()
        assert result is True
        assert server.port == 9999

    def test_start_first_command_fails_second_succeeds(
        self, mock_process: mock.MagicMock
    ) -> None:
        server = SearXServer(port=9999)
        popen_mock = mock.MagicMock(side_effect=[
            FileNotFoundError("searxng-run not found"),
            mock_process,
        ])
        with mock.patch("subprocess.Popen", popen_mock), mock.patch.object(
            server, "_health_check", return_value=True
        ), mock.patch.object(server, "_detect_bound_port", return_value=9999):
            result = server.start()
        assert result is True

    def test_start_no_command_available(self) -> None:
        server = SearXServer(port=9999)
        with mock.patch("subprocess.Popen", side_effect=FileNotFoundError):
            result = server.start()
        assert result is False

    def test_start_process_exits_too_early(self, mock_process: mock.MagicMock) -> None:
        mock_process.poll.return_value = 1
        mock_process.returncode = 1
        server = SearXServer(port=9999)
        with mock.patch("subprocess.Popen", return_value=mock_process), mock.patch(
            "time.sleep", return_value=None
        ):
            result = server.start()
        assert result is False
        assert server._process is None

    def test_start_timeout_stops_process(self, mock_process: mock.MagicMock) -> None:
        server = SearXServer(port=9999)
        with mock.patch("subprocess.Popen", return_value=mock_process), mock.patch.object(
            server, "_health_check", return_value=False
        ), mock.patch("time.monotonic", side_effect=[0, 0, 11, 11]), mock.patch(
            "time.sleep", return_value=None
        ), mock.patch.object(server, "stop") as mock_stop:
            result = server.start()
        assert result is False
        mock_stop.assert_called_once()


class TestStop:
    def test_stop_when_no_process(self) -> None:
        server = SearXServer()
        server.stop()

    def test_stop_terminates(self, mock_process: mock.MagicMock) -> None:
        server = SearXServer()
        server._process = mock_process
        server.stop()
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called_once_with(5.0)

    def test_stop_force_kills_on_timeout(self, mock_process: mock.MagicMock) -> None:
        mock_process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="searxng-run", timeout=5.0),
            None,
        ]
        server = SearXServer()
        server._process = mock_process
        server.stop()
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        assert mock_process.wait.call_count == 2


class TestIsRunning:
    def test_is_running_with_external_url(self) -> None:
        server = SearXServer(external_url="https://searx.example.com")
        with mock.patch.object(server, "_health_check", return_value=True):
            assert server.is_running() is True

    def test_is_running_external_url_not_healthy(self) -> None:
        server = SearXServer(external_url="https://searx.example.com")
        with mock.patch.object(server, "_health_check", return_value=False):
            assert server.is_running() is False

    def test_is_running_no_process(self) -> None:
        server = SearXServer()
        assert server.is_running() is False

    def test_is_running_process_alive_and_healthy(self, mock_process: mock.MagicMock) -> None:
        server = SearXServer()
        server._process = mock_process
        with mock.patch.object(server, "_health_check", return_value=True):
            assert server.is_running() is True

    def test_is_running_process_dead(self, mock_process: mock.MagicMock) -> None:
        mock_process.poll.return_value = 1
        server = SearXServer()
        server._process = mock_process
        assert server.is_running() is False
        assert server._process is None


class TestEnsureStarted:
    def test_already_running(self, mock_process: mock.MagicMock) -> None:
        server = SearXServer()
        server._process = mock_process
        with mock.patch.object(server, "_health_check", return_value=True):
            assert server.ensure_started() is True

    def test_not_running_starts(self) -> None:
        server = SearXServer(port=9999)
        mock_proc = mock.MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None
        with mock.patch.object(server, "is_running", return_value=False), mock.patch(
            "subprocess.Popen", return_value=mock_proc
        ), mock.patch.object(server, "_health_check", return_value=True), mock.patch.object(
            server, "_detect_bound_port", return_value=9999
        ):
            assert server.ensure_started() is True


class TestHealthCheck:
    def test_health_success(self) -> None:
        server = SearXServer(port=9999)
        resp = mock.MagicMock()
        resp.status_code = 200
        with mock.patch("httpx.get", return_value=resp):
            assert server._health_check() is True

    def test_health_redirect_is_not_healthy(self) -> None:
        server = SearXServer(port=9999)
        resp = mock.MagicMock()
        resp.status_code = 302
        with mock.patch("httpx.get", return_value=resp):
            assert server._health_check() is False

    def test_health_error_returns_false(self) -> None:
        server = SearXServer(port=9999)
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            assert server._health_check() is False

    def test_health_server_error_returns_false(self) -> None:
        server = SearXServer(port=9999)
        resp = mock.MagicMock()
        resp.status_code = 500
        with mock.patch("httpx.get", return_value=resp):
            assert server._health_check() is False
