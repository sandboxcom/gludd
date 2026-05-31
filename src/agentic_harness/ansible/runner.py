"""Ansible runner adapter module."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from agentic_harness.ansible.isolation import ProcessIsolationConfig

logger = logging.getLogger(__name__)

_PLAYBOOKS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "playbooks"

DEFAULT_REGISTRY: dict[str, str] = {
    "noop.yml": str(_PLAYBOOKS_ROOT / "noop.yml"),
}


def _build_registry(extra: dict[str, str] | None = None) -> dict[str, str]:
    reg = dict(DEFAULT_REGISTRY)
    if extra:
        reg.update(extra)
    return reg


class AnsibleRunnerAdapter:
    def __init__(
        self,
        private_data_dir: str | None = None,
        registry: dict[str, str] | None = None,
        isolation_config: ProcessIsolationConfig | None = None,
    ) -> None:
        self.private_data_dir = private_data_dir or tempfile.mkdtemp(prefix="harness-runner-")
        self.registry = _build_registry(registry)
        self.isolation_config = isolation_config

    def resolve_playbook(self, playbook_name: str) -> str:
        if playbook_name not in self.registry:
            raise ValueError(f"Playbook '{playbook_name}' is not registered")
        return self.registry[playbook_name]

    def prepare_job_dirs(self, job_id: str) -> dict[str, str]:
        job_dir = os.path.join(self.private_data_dir, job_id)
        dirs = {
            "root": job_dir,
            "env": os.path.join(job_dir, "env"),
            "project": os.path.join(job_dir, "project"),
            "inventory": os.path.join(job_dir, "inventory"),
            "artifacts": os.path.join(job_dir, "artifacts"),
        }
        for d in dirs.values():
            os.makedirs(d, exist_ok=True)
        return dirs

    def write_vars(
        self,
        job_id: str,
        job_vars: dict[str, Any],
        shared_vars: dict[str, Any] | None = None,
        filename: str = "extravars",
    ) -> str:
        vars_dir = os.path.join(self.private_data_dir, job_id, "env")
        os.makedirs(vars_dir, exist_ok=True)
        payload: dict[str, Any] = {"job_vars": job_vars}
        if shared_vars is not None:
            payload["shared_vars"] = shared_vars
        path = os.path.join(vars_dir, filename)
        with open(path, "w") as f:
            yaml.dump(payload, f, default_flow_style=False)
        os.chmod(path, 0o600)
        return path

    def run_playbook(
        self,
        playbook_name: str,
        private_data_dir: str | None = None,
        extravars: dict[str, Any] | None = None,
        **runner_kwargs: Any,
    ) -> dict[str, Any]:
        playbook_path = self.resolve_playbook(playbook_name)
        target_dir = private_data_dir or self.private_data_dir
        merged_kwargs: dict[str, Any] = dict(runner_kwargs)
        if self.isolation_config is not None:
            merged_kwargs.update(self.isolation_config.to_runner_kwargs())
        try:
            import ansible_runner

            result = ansible_runner.run(
                playbook=playbook_path,
                private_data_dir=target_dir,
                extravars=extravars or {},
                **merged_kwargs,
            )
            return {
                "status": result.status,
                "rc": result.rc,
                "events": list(result.events),
            }
        except Exception as exc:
            logger.error("Ansible runner failed: %s", exc)
            return {"status": "failed", "rc": 1, "error": str(exc), "events": []}
