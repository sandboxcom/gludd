"""Execute commands inside Kubernetes pods."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KubernetesPodConfig:
    image: str
    command: str
    namespace: str = "default"
    pod_name: str = ""
    service_account: str = ""
    environment: dict[str, str] = field(default_factory=dict)
    memory_limit: str = "512Mi"
    cpu_limit: str = "500m"
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class KubernetesResult:
    returncode: int
    stdout: str
    stderr: str
    pod_name: str = ""
    namespace: str = ""


class KubernetesExecutor:
    def __init__(self, timeout: int = 300, max_output_bytes: int = 1_000_000) -> None:
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self._created_pods: list[tuple[str, str]] = []

    def execute(self, config: KubernetesPodConfig) -> KubernetesResult:
        pod_name = config.pod_name or self._generate_pod_name()
        self._create_pod(config, pod_name)
        self._created_pods.append((pod_name, config.namespace))
        self._wait_for_pod(pod_name, config.namespace)
        result = self._exec_in_pod(pod_name, config.namespace, config.command)
        return KubernetesResult(
            returncode=result.returncode, stdout=result.stdout,
            stderr=result.stderr, pod_name=pod_name, namespace=config.namespace,
        )

    def delete_pod(self, pod_name: str, namespace: str = "default") -> None:
        subprocess.run(
            ["kubectl", "delete", "pod", pod_name, "-n", namespace, "--grace-period=1", "--force"],
            capture_output=True, text=True, timeout=self.timeout,
        )

    def cleanup(self) -> None:
        for pod_name, namespace in self._created_pods:
            self.delete_pod(pod_name, namespace)
        self._created_pods.clear()

    def _generate_pod_name(self) -> str:
        import uuid
        return f"gludd-sandbox-{uuid.uuid4().hex[:12]}"

    def _create_pod(self, config: KubernetesPodConfig, pod_name: str) -> None:
        pod_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": config.namespace,
                "labels": {**config.labels, "app": "gludd-sandbox"},
            },
            "spec": {
                "restartPolicy": "Never",
                "containers": [{
                    "name": "sandbox",
                    "image": config.image,
                    "command": ["sleep", "infinity"],
                    "resources": {
                        "limits": {
                            "memory": config.memory_limit,
                            "cpu": config.cpu_limit,
                        },
                    },
                }],
            },
        }
        if config.service_account:
            pod_spec["spec"]["serviceAccountName"] = config.service_account
        if config.environment:
            pod_spec["spec"]["containers"][0]["env"] = [{"name": k, "value": v} for k, v in config.environment.items()]
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=json.dumps(pod_spec),
            capture_output=True, text=True, timeout=self.timeout,
        )

    def _wait_for_pod(self, pod_name: str, namespace: str) -> None:
        subprocess.run(
            ["kubectl", "wait", "--for=condition=Ready", f"pod/{pod_name}",
             "-n", namespace, "--timeout=120s"],
            capture_output=True, text=True, timeout=180,
        )

    def _exec_in_pod(
        self, pod_name: str, namespace: str, command: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["kubectl", "exec", pod_name, "-n", namespace, "--", *command.split()],
            capture_output=True, text=True, timeout=self.timeout,
        )
