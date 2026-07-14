"""Cleanup routines for sandbox resources."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CleanupRecord:
    resource_type: str
    resource_id: str
    cleaned_at: float
    reason: str
    success: bool = True

class CleanupManager:
    def __init__(self) -> None:
        self._history: list[CleanupRecord] = []
        self._pending: set[tuple[str, str]] = set()

    def track(self, resource_type: str, resource_id: str) -> None:
        self._pending.add((resource_type, resource_id))

    def cleanup_resource(self, resource_type: str, resource_id: str) -> bool:
        cleaner = self._cleaners.get(resource_type)
        if cleaner is None:
            return False
        try:
            cleaner(resource_id)
            record = CleanupRecord(
                resource_type=resource_type,
                resource_id=resource_id,
                cleaned_at=time.time(),
                reason="explicit",
            )
        except Exception:
            record = CleanupRecord(
                resource_type=resource_type,
                resource_id=resource_id,
                cleaned_at=time.time(),
                reason="explicit",
                success=False,
            )
        self._history.append(record)
        self._pending.discard((resource_type, resource_id))
        return record.success

    def cleanup_all(self) -> int:
        pending = list(self._pending)
        success_count = 0
        for resource_type, resource_id in pending:
            if self.cleanup_resource(resource_type, resource_id):
                success_count += 1
        return success_count

    def cleanup_docker_containers(self, label: str = "gludd-sandbox", max_age_hours: int = 2) -> int:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label={label}", "--filter", "status=exited", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=30,
        )
        container_ids = [cid for cid in result.stdout.strip().split("\n") if cid]
        if not container_ids:
            return 0
        rm_result = subprocess.run(["docker", "rm", "-f", *container_ids], capture_output=True, text=True, timeout=30)
        for cid in container_ids:
            self._history.append(
                CleanupRecord(
                    resource_type="docker_container",
                    resource_id=cid,
                    cleaned_at=time.time(),
                    reason=f"age > {max_age_hours}h",
                )
            )
        return len(container_ids) if rm_result.returncode == 0 else 0

    def cleanup_kubernetes_resources(
        self, namespace: str = "default", label_selector: str = "app=gludd-sandbox",
    ) -> int:
        result = subprocess.run(
            ["kubectl", "delete", "pods", "-n", namespace, "-l", label_selector, "--grace-period=1", "--force"],
            capture_output=True, text=True, timeout=30,
        )
        return 0 if result.returncode != 0 else result.stdout.count("deleted")

    def history_count(self) -> int:
        return len(self._history)

    def pending_count(self) -> int:
        return len(self._pending)

    def last_cleanup(self) -> CleanupRecord | None:
        return self._history[-1] if self._history else None

    @property
    def _cleaners(self) -> dict[str, Any]:
        return {
            "docker_container": lambda cid: subprocess.run(
                ["docker", "rm", "-f", cid], capture_output=True, text=True, timeout=30, check=False,
            ),
            "kubernetes_pod": lambda name: subprocess.run(
                ["kubectl", "delete", "pod", name, "--grace-period=1", "--force"],
                capture_output=True, text=True, timeout=30, check=False,
            ),
        }
