"""Ansible runner adapter module."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


class AnsibleRunnerAdapter:
    def __init__(self, private_data_dir: str | None = None) -> None:
        self.private_data_dir = private_data_dir or tempfile.mkdtemp(prefix="harness-runner-")

    def prepare_job_dirs(self, job_id: str) -> dict[str, str]:
        job_dir = os.path.join(self.private_data_dir, job_id)
        dirs = {
            "root": job_dir,
            "env": os.path.join(job_dir, "env"),
            "project": os.path.join(job_dir, "project"),
            "inventory": os.path.join(job_dir, "inventory"),
            "vars": os.path.join(job_dir, "vars"),
            "artifacts": os.path.join(job_dir, "artifacts"),
        }
        for d in dirs.values():
            os.makedirs(d, exist_ok=True)
        return dirs

    def write_vars(
        self, job_id: str, vars_data: dict[str, Any], filename: str = "extravars"
    ) -> str:
        vars_dir = os.path.join(self.private_data_dir, job_id, "env")
        os.makedirs(vars_dir, exist_ok=True)
        path = os.path.join(vars_dir, filename)
        with open(path, "w") as f:
            json.dump(vars_data, f, indent=2)
        os.chmod(path, 0o600)
        return path

    def run_playbook(
        self,
        playbook: str,
        private_data_dir: str | None = None,
        extravars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_dir = private_data_dir or self.private_data_dir
        try:
            import ansible_runner
            result = ansible_runner.run(
                playbook=playbook,
                private_data_dir=target_dir,
                extravars=extravars or {},
            )
            return {
                "status": result.status,
                "rc": result.rc,
                "events": [],
            }
        except Exception as exc:
            logger.error("Ansible runner failed: %s", exc)
            return {"status": "failed", "rc": 1, "error": str(exc)}
