"""Unit tests for sandbox Docker executor."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from general_ludd.sandbox.docker_executor import DockerContainerConfig, DockerExecutor, DockerResult


class TestDockerContainerConfig:
    def test_default_values(self) -> None:
        config = DockerContainerConfig(image="alpine", command="echo hi")
        assert config.workdir == "/workspace"
        assert config.environment == {}
        assert config.volumes == {}
        assert config.memory_bytes is None
        assert config.cpu_shares is None
        assert config.network_mode == "none"

    def test_custom_values(self) -> None:
        config = DockerContainerConfig(
            image="python:3.11", command="python -c 'print(1)'",
            workdir="/app", environment={"KEY": "val"},
            memory_bytes=512_000_000, cpu_shares=512,
        )
        assert config.image == "python:3.11"
        assert config.workdir == "/app"
        assert config.environment["KEY"] == "val"
        assert config.memory_bytes == 512_000_000
        assert config.cpu_shares == 512


class TestDockerResult:
    def test_creation(self) -> None:
        result = DockerResult(returncode=0, stdout="ok", stderr="", container_id="abc123")
        assert result.returncode == 0
        assert result.stdout == "ok"
        assert result.stderr == ""
        assert result.container_id == "abc123"

    def test_default_container_id(self) -> None:
        result = DockerResult(returncode=1, stdout="", stderr="error")
        assert result.container_id == ""


class TestDockerExecutor:
    def test_build_command_minimal(self) -> None:
        executor = DockerExecutor(timeout=60)
        config = DockerContainerConfig(image="alpine", command="echo hello")
        cmd = executor._build_command(config)
        assert "docker" in cmd
        assert "run" in cmd
        assert "--rm" in cmd
        assert "alpine" in cmd
        assert "echo hello" in cmd

    def test_build_command_with_volumes(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="ubuntu", command="ls", volumes={"/host/path": "/container/path"})
        cmd = executor._build_command(config)
        assert "-v" in cmd
        assert "/host/path:/container/path" in cmd

    def test_build_command_with_limits(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="true", memory_bytes=1_000_000, cpu_shares=256)
        cmd = executor._build_command(config)
        assert "--memory" in cmd
        assert "1000000" in cmd
        assert "--cpu-shares" in cmd
        assert "256" in cmd

    def test_build_command_with_env(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="env", environment={"FOO": "bar", "BAZ": "qux"})
        cmd = executor._build_command(config)
        assert "-e" in cmd
        assert "FOO=bar" in cmd
        assert "BAZ=qux" in cmd

    def test_build_command_with_network(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="id", network_mode="bridge")
        cmd = executor._build_command(config)
        assert "--network" in cmd
        assert "bridge" in cmd

    def test_parse_container_id(self) -> None:
        output = "abc\ndef123\n"
        assert DockerExecutor.parse_container_id(output) == "def123"

    def test_pull_image(self) -> None:
        executor = DockerExecutor(timeout=30)
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "pull", "alpine"], returncode=0, stdout="ok", stderr="",
            )
            result = executor.pull_image("alpine")
            mock_run.assert_called_once_with(["docker", "pull", "alpine"], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0

    def test_execute_passes_config(self) -> None:
        executor = DockerExecutor(timeout=10)
        config = DockerContainerConfig(image="alpine", command="echo test")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "run"], returncode=0, stdout="container123\n", stderr="")
            result = executor.execute(config)
            assert result.returncode == 0
            assert result.container_id == "container123"

    def test_stop_container(self) -> None:
        executor = DockerExecutor()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "stop", "abc"], returncode=0, stdout="", stderr="")
            result = executor.stop_container("abc")
            mock_run.assert_called_once_with(["docker", "stop", "abc"], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0

    def test_remove_container(self) -> None:
        executor = DockerExecutor()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["docker", "rm", "-f", "abc"], returncode=0, stdout="", stderr="")
            result = executor.remove_container("abc")
            assert result.returncode == 0

    def test_auto_remove_default(self) -> None:
        executor = DockerExecutor()
        assert executor.auto_remove is True

    def test_auto_remove_disabled(self) -> None:
        executor = DockerExecutor(auto_remove=False)
        config = DockerContainerConfig(image="alpine", command="true")
        cmd = executor._build_command(config)
        assert "--rm" not in cmd

    def test_build_command_omits_none_limits(self) -> None:
        executor = DockerExecutor()
        config = DockerContainerConfig(image="alpine", command="true")
        cmd = executor._build_command(config)
        assert "--memory" not in cmd
        assert "--cpu-shares" not in cmd
