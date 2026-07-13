"""Unit tests for sandbox Kubernetes executor."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from general_ludd.sandbox.kubernetes_executor import KubernetesExecutor, KubernetesPodConfig, KubernetesResult


class TestKubernetesPodConfig:
    def test_default_values(self) -> None:
        config = KubernetesPodConfig(image="alpine", command="echo hi")
        assert config.namespace == "default"
        assert config.pod_name == ""
        assert config.service_account == ""
        assert config.memory_limit == "512Mi"
        assert config.cpu_limit == "500m"
        assert config.environment == {}
        assert config.labels == {}

    def test_custom_values(self) -> None:
        config = KubernetesPodConfig(
            image="python:3.11", command="python -c 'print(1)'",
            namespace="sandbox", pod_name="test-pod",
            environment={"KEY": "val"}, memory_limit="1Gi",
            cpu_limit="1000m", labels={"team": "ai"})
        assert config.image == "python:3.11"
        assert config.namespace == "sandbox"
        assert config.pod_name == "test-pod"
        assert config.memory_limit == "1Gi"
        assert config.cpu_limit == "1000m"


class TestKubernetesResult:
    def test_creation(self) -> None:
        result = KubernetesResult(returncode=0, stdout="ok", stderr="", pod_name="pod-abc", namespace="default")
        assert result.returncode == 0
        assert result.stdout == "ok"
        assert result.pod_name == "pod-abc"
        assert result.namespace == "default"


class TestKubernetesExecutor:
    def test_generate_pod_name(self) -> None:
        executor = KubernetesExecutor()
        name = executor._generate_pod_name()
        assert name.startswith("gludd-sandbox-")
        assert len(name) > len("gludd-sandbox-")

    def test_generate_pod_name_unique(self) -> None:
        executor = KubernetesExecutor()
        names = {executor._generate_pod_name() for _ in range(100)}
        assert len(names) == 100

    def test_execute_uses_provided_pod_name(self) -> None:
        executor = KubernetesExecutor(timeout=5)
        config = KubernetesPodConfig(image="alpine", command="true", pod_name="my-pod")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
            result = executor.execute(config)
            assert result.returncode == 0

    def test_execute_creates_and_execs(self) -> None:
        executor = KubernetesExecutor(timeout=5)
        config = KubernetesPodConfig(image="alpine", command="echo hello")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="hello\n", stderr="")
            result = executor.execute(config)
            assert result.returncode == 0
            assert result.pod_name is not None

    def test_cleanup_deletes_pods(self) -> None:
        executor = KubernetesExecutor()
        executor._created_pods = [("pod-a", "default"), ("pod-b", "sandbox")]
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            executor.cleanup()
            assert len(executor._created_pods) == 0

    def test_delete_pod_single(self) -> None:
        executor = KubernetesExecutor()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            executor.delete_pod("pod-x", "default")

    def test_pod_spec_json(self) -> None:
        executor = KubernetesExecutor()
        config = KubernetesPodConfig(image="alpine", command="true", pod_name="json-test", environment={"KEY": "val"})
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            executor._create_pod(config, "json-test")
            apply_call = mock_run.call_args_list[0]
            spec = json.loads(apply_call[1]["input"])
            assert spec["metadata"]["name"] == "json-test"
            assert spec["spec"]["containers"][0]["image"] == "alpine"

    def test_pod_spec_with_service_account(self) -> None:
        executor = KubernetesExecutor()
        config = KubernetesPodConfig(image="alpine", command="true", pod_name="sa-test", service_account="my-sa")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            executor._create_pod(config, "sa-test")
            spec = json.loads(mock_run.call_args_list[0][1]["input"])
            assert spec["spec"]["serviceAccountName"] == "my-sa"
