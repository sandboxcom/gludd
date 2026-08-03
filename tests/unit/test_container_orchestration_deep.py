"""Deep container orchestration tests: lifecycle, isolation, resource limits, health.

Covers: ContainerBuilder, ContainerBackend, DockerExecutor, DockerEngineSource,
PodmanSource, ContainerdSource, ResourceLimits. 20+ tests.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.connectors.containerd import ContainerdConfig, ContainerdSource
from general_ludd.connectors.docker_engine import (
    DockerEngineSource,
    _DockerResponse,
    _is_internal_literal_host,
    _iter_log_payload,
)
from general_ludd.connectors.podman import PodmanSource
from general_ludd.runtime.container import (
    ContainerBuilder,
)
from general_ludd.sandbox.backends.container_backend import ContainerBackend
from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxConfig,
)
from general_ludd.sandbox.docker_executor import DockerContainerConfig, DockerExecutor
from general_ludd.sandbox.resource_limits import ResourceLimits

Response = _DockerResponse


def _resp(status: int, body: object = b"") -> Response:
    if isinstance(body, (dict, list)):
        raw = json.dumps(body).encode()
    elif isinstance(body, bytes):
        raw = body
    else:
        raw = str(body).encode()
    return Response(status=status, headers={"Content-Type": "application/json"}, body=raw)


class FakeTransport:
    def __init__(self, routes: dict[tuple[str, str], Response]) -> None:
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        path: str,
        query: dict[str, object] | None,
        base_url: str,
        timeout: float,
    ) -> Response:
        self.calls.append({"method": method, "path": path, "query": query, "base_url": base_url, "timeout": timeout})
        return self.routes[(method, path)]


# ── ContainerBuilder image build lifecycle ────────────────────────────────


class TestContainerBuilderBuildImage:
    def test_build_success_extracts_digest(self) -> None:
        builder = ContainerBuilder()
        fake = MagicMock(returncode=0, stdout="step1\nsha256:abcdef1234\nstep2\n", stderr="")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.build_image("/ctx", "img:v1", runtime="docker")
        assert result.success is True
        assert "sha256:abcdef1234" in result.image_digest
        assert result.image_ref == "img:v1"

    def test_build_failure_captures_stderr(self) -> None:
        builder = ContainerBuilder()
        fake = MagicMock(returncode=1, stdout="", stderr="No Dockerfile found")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.build_image("/ctx", "img:v1")
        assert result.success is False
        assert "No Dockerfile found" in result.logs

    def test_build_runtime_not_found(self) -> None:
        builder = ContainerBuilder()
        with patch("general_ludd.runtime.container.subprocess.run", side_effect=FileNotFoundError):
            result = builder.build_image("/ctx", "img:v1", runtime="nonexistent")
        assert result.success is False
        assert "not found on PATH" in result.logs

    def test_build_timeout(self) -> None:
        builder = ContainerBuilder()
        with patch("general_ludd.runtime.container.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 600)):
            result = builder.build_image("/ctx", "img:v1")
        assert result.success is False
        assert "timed out" in result.logs

    def test_build_injection_rejected_before_subprocess(self) -> None:
        builder = ContainerBuilder()
        with patch("general_ludd.runtime.container.subprocess.run") as mrun:
            result = builder.build_image("/ctx", "img;rm -rf /")
            mrun.assert_not_called()
        assert result.success is False

    def test_build_image_ref_with_digest_shorthand(self) -> None:
        builder = ContainerBuilder()
        fake = MagicMock(returncode=0, stdout="sha256:ccc\n", stderr="")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.build_image(".", "registry.example.com/team/app:sha256-abc")
        assert result.success is True


class TestContainerBuilderValidateImage:
    def test_validate_valid_image(self) -> None:
        builder = ContainerBuilder()
        inspect = [{"Config": {"Entrypoint": ["gludd", "serve"]}, "Size": 50_000_000}]
        fake = MagicMock(returncode=0, stdout=json.dumps(inspect), stderr="")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.validate_image("img:v1")
        assert result.valid is True
        assert result.entrypoint_correct is True
        assert result.size_mb > 0

    def test_validate_baked_secrets_detected(self) -> None:
        builder = ContainerBuilder()
        inspect = [{"Config": {"Env": ["PASSWORD=secret123"], "Entrypoint": ["gludd"]}, "Size": 10_000_000}]
        fake = MagicMock(returncode=0, stdout=json.dumps(inspect), stderr="")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.validate_image("img:v1")
        assert result.has_baked_state is True

    def test_validate_token_in_env_detected(self) -> None:
        builder = ContainerBuilder()
        inspect = [{"Config": {"Env": ["API_KEY=sk-abc", "HOME=/root"], "Entrypoint": ["gludd"]}, "Size": 10_000_000}]
        fake = MagicMock(returncode=0, stdout=json.dumps(inspect), stderr="")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.validate_image("img:v1")
        assert result.has_baked_state is True

    def test_validate_no_secrets_clean(self) -> None:
        builder = ContainerBuilder()
        inspect = [{"Config": {"Env": ["HOME=/root", "PATH=/usr/bin"], "Entrypoint": ["gludd"]}, "Size": 10_000_000}]
        fake = MagicMock(returncode=0, stdout=json.dumps(inspect), stderr="")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.validate_image("img:v1")
        assert result.has_baked_state is False

    def test_validate_invalid_json_returns_not_valid(self) -> None:
        builder = ContainerBuilder()
        fake = MagicMock(returncode=0, stdout="not json", stderr="")
        with patch("general_ludd.runtime.container.subprocess.run", return_value=fake):
            result = builder.validate_image("img:v1")
        assert result.valid is False

    def test_validate_runtime_not_found(self) -> None:
        builder = ContainerBuilder()
        with patch("general_ludd.runtime.container.subprocess.run", side_effect=FileNotFoundError):
            result = builder.validate_image("img:v1")
        assert result.valid is False

    def test_validate_injection_rejected(self) -> None:
        builder = ContainerBuilder()
        with patch("general_ludd.runtime.container.subprocess.run") as mrun:
            result = builder.validate_image("img;cat /etc/passwd")
            mrun.assert_not_called()
        assert result.valid is False


# ── DockerExecutor lifecycle (build/pull/run/cleanup) ──────────────────────


class TestDockerExecutorLifecycle:
    def test_pull_then_run_sequence(self) -> None:
        executor = DockerExecutor(timeout=30)
        config = DockerContainerConfig(image="alpine:3.19", command="echo ok")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(["docker", "pull", "alpine:3.19"], 0, "ok\n", ""),
                subprocess.CompletedProcess(["docker", "run"], 0, "abc123\n", ""),
            ]
            pull = executor.pull_image("alpine:3.19")
            assert pull.returncode == 0
            result = executor.execute(config)
            assert result.returncode == 0
            assert result.container_id == "abc123"

    def test_exec_then_stop_then_remove_sequence(self) -> None:
        executor = DockerExecutor()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(["docker", "run"], 0, "cid1\n", ""),
                subprocess.CompletedProcess(["docker", "exec", "cid1", "ls", "-la"], 0, "ok\n", ""),
                subprocess.CompletedProcess(["docker", "stop", "cid1"], 0, "", ""),
                subprocess.CompletedProcess(["docker", "rm", "-f", "cid1"], 0, "", ""),
            ]
            config = DockerContainerConfig(image="ubuntu", command="sleep 1")
            executor.execute(config)
            exec_result = executor.execute_in_container("cid1", "ls -la")
            assert exec_result.returncode == 0
            stop_result = executor.stop_container("cid1")
            assert stop_result.returncode == 0
            rm_result = executor.remove_container("cid1")
            assert rm_result.returncode == 0


class TestDockerExecutorPortMapping:
    @pytest.mark.parametrize(
        "volumes",
        [
            {"/host/src": "/container/src"},
            {"/tmp/in": "/data", "/tmp/out": "/output"},
        ],
    )
    def test_volume_mounting(self, volumes: dict[str, str]) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="ls", volumes=volumes)
        cmd = executor._build_command(config)
        for host_path, cont_path in volumes.items():
            assert "-v" in cmd
            assert f"{host_path}:{cont_path}" in cmd

    def test_no_volumes_no_v_flag(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="ls")
        cmd = executor._build_command(config)
        assert "-v" not in cmd


class TestDockerExecutorEnvInjection:
    def test_single_env_var(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="env", environment={"DEBUG": "1"})
        cmd = executor._build_command(config)
        assert "-e" in cmd
        assert "DEBUG=1" in cmd

    def test_multiple_env_vars(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(
            image="alpine",
            command="env",
            environment={"DB_HOST": "localhost", "DB_PORT": "5432", "LOG_LEVEL": "info"},
        )
        cmd = executor._build_command(config)
        assert cmd.count("-e") == 3
        assert "DB_HOST=localhost" in cmd
        assert "DB_PORT=5432" in cmd
        assert "LOG_LEVEL=info" in cmd

    def test_env_var_with_equals_sign_value(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(
            image="alpine", command="env", environment={"CONN_STR": "host=localhost;port=5432"}
        )
        cmd = executor._build_command(config)
        assert "CONN_STR=host=localhost;port=5432" in cmd


class TestDockerExecutorNetworkConfig:
    def test_network_none_default(self) -> None:
        config = DockerContainerConfig(image="alpine", command="curl")
        assert config.network_mode == "none"

    def test_network_bridge(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="curl", network_mode="bridge")
        cmd = executor._build_command(config)
        assert "--network" in cmd
        assert "bridge" in cmd


# ── ContainerBackend (podman/docker sandbox) ──────────────────────────────


class TestContainerBackendLifecycle:
    def test_pull_image_podman(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER, image_path="alpine:latest")
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(["podman", "pull", "alpine:latest"], 0, "ok", "")
                result = backend.pull_image("alpine:latest")
                assert result.returncode == 0
                assert "podman" in mock_run.call_args.args[0]

    def test_pull_image_docker_fallback(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER, image_path="alpine:latest")
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value=None):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(["docker", "pull", "alpine:latest"], 0, "ok", "")
                result = backend.pull_image("alpine:latest")
                assert result.returncode == 0
                assert "docker" in mock_run.call_args.args[0]

    def test_cleanup_removes_containers(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER)
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            backend._container_ids = ["c1", "c2"]
            with patch.object(subprocess, "run") as mock_run:
                backend.cleanup()
            assert mock_run.call_count == 2

    def test_cleanup_clears_after_removal(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER)
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            backend._container_ids = ["c1"]
            with patch.object(subprocess, "run"):
                backend.cleanup()
            assert backend._container_ids == []

    def test_cleanup_survives_removal_error(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER)
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            backend._container_ids = ["c1"]
            with patch.object(subprocess, "run", side_effect=Exception("gone")):
                backend.cleanup()
            assert backend._container_ids == []


class TestContainerBackendExecute:
    def test_execute_network_disabled(self) -> None:
        config = SandboxConfig(
            backend="container", isolation=IsolationLevel.CONTAINER, image_path="img:v1", allow_network=False
        )
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(["podman", "run"], 0, "ok", "")
                backend.execute("echo hi")
            cmd_args = mock_run.call_args.args[0]
            assert "--network" in cmd_args
            assert "none" in cmd_args

    def test_execute_network_enabled_no_network_flag(self) -> None:
        config = SandboxConfig(
            backend="container", isolation=IsolationLevel.CONTAINER, image_path="img:v1", allow_network=True
        )
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(["podman", "run"], 0, "ok", "")
                backend.execute("echo hi")
            assert "--network" not in mock_run.call_args.args[0]

    def test_execute_env_injection(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER, image_path="img:v1")
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(["podman", "run"], 0, "ok", "")
                backend.execute("env", env={"KEY_A": "val_a", "KEY_B": "val_b"})
            cmd_args = mock_run.call_args.args[0]
            assert "-e" in cmd_args
            assert "KEY_A=val_a" in cmd_args
            assert "KEY_B=val_b" in cmd_args

    def test_execute_memory_limit(self) -> None:
        config = SandboxConfig(
            backend="container",
            isolation=IsolationLevel.CONTAINER,
            image_path="img:v1",
            memory_mb=256,
        )
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(["podman", "run"], 0, "ok", "")
                backend.execute("echo hi")
            cmd_args = mock_run.call_args.args[0]
            assert "--memory" in cmd_args
            assert "268435456" in cmd_args

    def test_execute_pids_limit(self) -> None:
        config = SandboxConfig(
            backend="container",
            isolation=IsolationLevel.CONTAINER,
            image_path="img:v1",
            max_processes=25,
        )
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(["podman", "run"], 0, "ok", "")
                backend.execute("echo hi")
            cmd_args = mock_run.call_args.args[0]
            assert "--pids-limit" in cmd_args
            assert "25" in cmd_args

    def test_execute_timeout_handled(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER, image_path="img:v1", timeout=10)
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
                result = backend.execute("sleep 999")
            assert result.returncode == -1
            assert result.was_killed is True

    def test_execute_runtime_not_found(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER, image_path="img:v1")
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value=None):
            backend = ContainerBackend(config)
            with patch.object(subprocess, "run", side_effect=FileNotFoundError):
                result = backend.execute("echo hi")
            assert result.returncode == 127


# ── Health check integration (DockerEngine + Podman) ─────────────────────


class TestDockerEngineHealth:
    def test_health_ping_ok(self) -> None:
        t = FakeTransport({("GET", "/_ping"): _resp(200)})
        src = DockerEngineSource({"transport": t})
        result = src.health()
        assert result["ok"] is True

    def test_health_ping_failure(self) -> None:
        t = FakeTransport({("GET", "/_ping"): _resp(500)})
        src = DockerEngineSource({"transport": t})
        result = src.health()
        assert result["ok"] is False

    def test_health_connection_refused(self) -> None:
        def _refused(*a: Any, **kw: Any) -> Response:
            raise ConnectionRefusedError("down")

        src = DockerEngineSource({"transport": _refused})
        result = src.health()
        assert result["ok"] is False
        assert "ConnectionRefusedError" in str(result["detail"])


class TestPodmanHealth:
    def test_health_podman_ping_ok(self) -> None:
        t = FakeTransport({("GET", "/_ping"): _resp(200)})
        src = PodmanSource({"transport": t})
        result = src.health()
        assert result["ok"] is True

    def test_health_podman_ping_fail(self) -> None:
        t = FakeTransport({("GET", "/_ping"): _resp(503)})
        src = PodmanSource({"transport": t})
        result = src.health()
        assert result["ok"] is False

    def test_podman_ssrf_blocked_health_returns_false(self) -> None:
        src = PodmanSource({"base_url": "http://127.0.0.1:8080"})
        result = src.health()
        assert result["ok"] is False


# ── Containerd health ────────────────────────────────────────────────────


class TestContainerdHealth:
    def test_health_with_runner_reachable(self) -> None:
        def fake_runner(argv: list[str], timeout: float = 10.0) -> str:
            return json.dumps({"version": "1.7.0"})

        cfg = ContainerdConfig()
        src = ContainerdSource(cfg, runner=fake_runner)
        result = src.health()
        assert result["ok"] is True
        assert "crictl reachable" in result["detail"]

    def test_health_with_runner_unreachable(self) -> None:
        def fake_runner(argv: list[str], timeout: float = 10.0) -> str:
            raise RuntimeError("connection refused")

        cfg = ContainerdConfig()
        src = ContainerdSource(cfg, runner=fake_runner)
        result = src.health()
        assert result["ok"] is False

    def test_health_socket_outside_allowed_dirs(self) -> None:
        cfg = ContainerdConfig(runtime_endpoint="/etc/hacked.sock")
        src = ContainerdSource(cfg)
        result = src.health()
        assert result["ok"] is False
        assert "socket-confinement" in result["detail"]


# ── ResourceLimits conversions ────────────────────────────────────────────


class TestResourceLimitsToDockerArgs:
    def test_full_limits(self) -> None:
        limits = ResourceLimits(
            memory_bytes=512_000_000,
            cpu_shares=2048,
            pids_limit=100,
        )
        args = limits.to_docker_args()
        assert "--memory" in args
        assert "512000000" in args
        assert "--cpu-shares" in args
        assert "2048" in args
        assert "--pids-limit" in args
        assert "100" in args

    def test_empty_limits_no_args(self) -> None:
        limits = ResourceLimits()
        assert limits.to_docker_args() == []

    def test_thresholds_memory_exceeded(self) -> None:
        limits = ResourceLimits(memory_bytes=1_000_000)
        assert limits.exceed_memory(2_000_000) is True
        assert limits.exceed_memory(500_000) is False

    def test_thresholds_timeout_exceeded(self) -> None:
        limits = ResourceLimits(timeout_seconds=60)
        assert limits.exceed_timeout(90.0) is True
        assert limits.exceed_timeout(30.0) is False


# ── Multiplexed log parsing (Docker / Podman shared) ─────────────────────


class TestMultiplexedLogParsing:
    def test_multiplexed_stdout_stderr_split(self) -> None:
        frame1 = bytes([1, 0, 0, 0, 0, 0, 0, 5]) + b"hello"
        frame2 = bytes([2, 0, 0, 0, 0, 0, 0, 5]) + b"error"
        result = _iter_log_payload(frame1 + frame2)
        streams = {s for s, _ in result}
        assert "stdout" in streams
        assert "stderr" in streams

    def test_multiplexed_stdin_is_stdin(self) -> None:
        frame = bytes([0, 0, 0, 0, 0, 0, 0, 4]) + b"hi\n"
        result = _iter_log_payload(frame)
        assert result[0][0] == "stdin"

    def test_plain_text_split_by_newlines(self) -> None:
        result = _iter_log_payload(b"line1\nline2\nline3")
        assert len(result) == 3


# ── Config validation / defaults ─────────────────────────────────────────


class TestContainerConfigDefaults:
    def test_docker_config_default_no_limits(self) -> None:
        config = DockerContainerConfig(image="alpine", command="true")
        assert config.memory_bytes is None
        assert config.cpu_shares is None

    def test_container_backend_default_runtime(self) -> None:
        config = SandboxConfig(backend="container")
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value=None):
            backend = ContainerBackend(config)
        assert backend._runtime == "docker"

    def test_container_backend_prefers_podman(self) -> None:
        config = SandboxConfig(backend="container")
        with patch("general_ludd.sandbox.backends.container_backend.shutil.which", return_value="/usr/bin/podman"):
            backend = ContainerBackend(config)
        assert backend._runtime == "podman"

    def test_resource_limits_default_light(self) -> None:
        rl = ResourceLimits.default_light()
        assert rl.cpu_shares == 1024
        assert rl.memory_bytes == 256 * 1024 * 1024

    def test_resource_limits_default_medium(self) -> None:
        rl = ResourceLimits.default_medium()
        assert rl.cpu_shares == 2048
        assert rl.memory_bytes == 512 * 1024 * 1024

    def test_resource_limits_default_heavy(self) -> None:
        rl = ResourceLimits.default_heavy()
        assert rl.cpu_shares == 4096
        assert rl.memory_bytes == 1024 * 1024 * 1024


# ── Podman query: ps, logs, events ───────────────────────────────────────


class TestPodmanQueryDeep:
    def test_ps_returns_containers(self) -> None:
        payload = [{"Id": "p1", "Names": ["/my-pod"], "State": "running", "Status": "Up 2h", "Image": "alpine:3.19"}]
        t = FakeTransport({("GET", "/containers/json"): _resp(200, payload)})
        src = PodmanSource({"transport": t})
        records = src.query({"mode": "ps"})
        assert len(records) == 1
        labels = records[0]["labels"]
        assert isinstance(labels, dict)
        assert labels.get("container_name") == "my-pod"

    def test_ps_connection_resilience(self) -> None:
        def _fail(*a: Any, **kw: Any) -> Response:
            raise ConnectionError("socket down")

        src = PodmanSource({"transport": _fail})
        records = src.query({"mode": "ps"})
        assert records == []

    def test_logs_mode_requires_container_id(self) -> None:
        src = PodmanSource({})
        with pytest.raises(ValueError, match="container_id"):
            src.query({"mode": "logs"})

    def test_events_parses_via_since(self) -> None:
        t = FakeTransport({("GET", "/events"): _resp(200, b"")})
        src = PodmanSource({"transport": t})
        result = src.query({"mode": "events", "since": "1700000000"})
        assert result == []


# ── Containerd query integration ─────────────────────────────────────────


class TestContainerdQuery:
    def test_ps_with_runner(self) -> None:
        def fake_runner(argv: list[str], timeout: float = 10.0) -> str:
            return json.dumps(
                {
                    "containers": [
                        {
                            "metadata": {"name": "app"},
                            "labels": {"io.kubernetes.pod.name": "app-pod"},
                            "state": "CONTAINER_RUNNING",
                        }
                    ],
                }
            )

        cfg = ContainerdConfig()
        src = ContainerdSource(cfg, runner=fake_runner)
        records = src.query({"what": "ps"})
        assert len(records) >= 1
        assert records[0]["labels"]["container"] == "app"

    def test_stats_with_runner(self) -> None:
        def fake_runner(argv: list[str], timeout: float = 10.0) -> str:
            return json.dumps(
                {
                    "stats": [
                        {
                            "attributes": {
                                "metadata": {"name": "sidecar"},
                                "labels": {},
                            },
                            "cpu": {
                                "usageCoreNanoSeconds": {"value": "1234567890"},
                                "timestamp": "1700000000",
                            },
                            "memory": {
                                "workingSetBytes": {"value": "52428800"},
                                "timestamp": "1700000000",
                            },
                        }
                    ],
                }
            )

        cfg = ContainerdConfig()
        src = ContainerdSource(cfg, runner=fake_runner)
        records = src.query({"what": "stats"})
        assert len(records) == 2
        kinds = {r["kind"] for r in records}
        assert "metrics" in kinds

    def test_pod_logs_path_confined(self) -> None:
        cfg = ContainerdConfig(pod_log_root="/var/log/pods")
        src = ContainerdSource(cfg)
        pod_log_content = "2024-01-01T00:00:00.000000001Z stdout F hello\n"
        with patch("pathlib.Path.read_text", return_value=pod_log_content):
            records = src.query({"what": "pod_logs", "pod_log_rel": "ns_pod_uid/app/0.log"})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"

    def test_pod_log_path_escape_rejected(self) -> None:
        cfg = ContainerdConfig(pod_log_root="/var/log/pods")
        src = ContainerdSource(cfg)
        with pytest.raises(ValueError, match="escapes"):
            src.query({"what": "pod_logs", "pod_log_rel": "../../../etc/shadow"})


# ── SSRF / TCP hardening ─────────────────────────────────────────────────


class TestTCPHardening:
    def test_localhost_rejected(self) -> None:
        assert _is_internal_literal_host("localhost") is True

    def test_metadata_ip_169_blocked(self) -> None:
        assert _is_internal_literal_host("169.254.169.254") is True

    def test_rfc1918_blocked(self) -> None:
        assert _is_internal_literal_host("192.168.1.1") is True

    def test_public_ip_allowed(self) -> None:
        assert _is_internal_literal_host("93.184.216.34") is False
