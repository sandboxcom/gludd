"""Ansible runner adapter module.

Delegates playbook execution to CoreAnsibleRunner which uses ansible-core
as a native Python library for playbook execution, variable resolution,
and Jinja2 templating.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from general_ludd.ansible.core_runner import CoreAnsibleRunner
from general_ludd.ansible.isolation import ProcessIsolationConfig

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
        playbooks_dir: str | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self.private_data_dir = private_data_dir or tempfile.mkdtemp(prefix="gl-runner-")
        self.registry = _build_registry(registry)
        self.isolation_config = isolation_config
        self._playbooks_dir = playbooks_dir
        self._event_bus = event_bus
        self._core_runner = CoreAnsibleRunner(
            process_isolation=isolation_config,
        )
        if playbooks_dir:
            self._scan_playbook_dir(playbooks_dir)

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
        _ = private_data_dir or self.private_data_dir
        try:
            result = self._core_runner.run_playbook(
                playbook_path=playbook_path,
                extravars=extravars or {},
            )
            return result.model_dump()
        except Exception as exc:
            logger.error("Ansible core runner failed: %s", exc)
            return {"status": "failed", "rc": 1, "error": str(exc), "events": []}

    def refresh_playbooks(self) -> dict[str, Any]:
        if self._playbooks_dir:
            self._scan_playbook_dir(self._playbooks_dir)
        return {"playbooks": list(self.registry.keys())}

    def register_playbook(self, name: str, path: str) -> None:
        self.registry[name] = path
        if self._event_bus:
            from general_ludd.events.types import PlaybookRegisteredEvent

            self._event_bus.publish(PlaybookRegisteredEvent(playbook=name))

    def unregister_playbook(self, name: str) -> None:
        self.registry.pop(name, None)

    def list_playbooks(self) -> list[str]:
        return list(self.registry.keys())

    def _scan_playbook_dir(self, playbooks_dir: str) -> None:
        pdir = Path(playbooks_dir)
        if pdir.is_dir():
            for f in sorted(pdir.glob("*.yml")):
                self.registry[f.name] = str(f)
                if self._event_bus:
                    from general_ludd.events.types import PlaybookRegisteredEvent

                    self._event_bus.publish(PlaybookRegisteredEvent(playbook=f.name))
