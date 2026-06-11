"""Core Ansible runner using ansible-core as a native Python library.

Uses ansible-core's PlaybookExecutor, DataLoader, VariableManager, and
InventoryManager directly instead of the ansible-runner subprocess wrapper.

This provides direct access to:
- Ansible's variable manager and templating engine
- Inventory management
- Callback plugins
- Module execution
- Task-level control
"""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

try:
    from ansible.parsing.dataloader import DataLoader
    from ansible.plugins.callback import CallbackBase
    from ansible.template import Templar

    _HAS_ANSIBLE_CORE = True
except ImportError:
    _HAS_ANSIBLE_CORE = False
    CallbackBase = object


def _get_templar(loader: Any = None, variables: dict[str, Any] | None = None) -> Any:
    if not _HAS_ANSIBLE_CORE:
        raise ImportError("ansible-core is required for templating but is not installed")
    if loader is None:
        loader = DataLoader()
    return Templar(loader=loader, variables=variables or {})


class _EventCollectorCallback(CallbackBase):  # type: ignore[misc]
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "gludd_event_collector"

    def __init__(self) -> None:
        super().__init__()
        self._events: list[dict[str, Any]] = []
        self._host_stats: dict[str, Any] = {}

    def v2_runner_on_start(self, host: Any, task: Any) -> None:
        self._events.append({
            "event": "runner_on_start",
            "host": str(host),
            "task": str(task),
        })

    def v2_runner_on_ok(self, result: Any) -> None:
        self._events.append({
            "event": "runner_on_ok",
            "host": str(result._host),
            "task": str(result._task),
            "result": result._result,
        })

    def v2_runner_on_failed(self, result: Any, ignore_errors: bool = False) -> None:
        self._events.append({
            "event": "runner_on_failed",
            "host": str(result._host),
            "task": str(result._task),
            "result": result._result,
            "ignore_errors": ignore_errors,
        })

    def v2_runner_on_skipped(self, result: Any) -> None:
        self._events.append({
            "event": "runner_on_skipped",
            "host": str(result._host),
            "task": str(result._task),
        })

    def v2_runner_on_unreachable(self, result: Any) -> None:
        self._events.append({
            "event": "runner_on_unreachable",
            "host": str(result._host),
            "task": str(result._task),
        })

    def v2_playbook_on_start(self, playbook: Any) -> None:
        self._events.append({
            "event": "playbook_on_start",
            "playbook": str(playbook),
        })

    def v2_playbook_on_stats(self, stats: Any) -> None:
        self._host_stats = {}
        for host, host_stats in stats.processed.items():
            self._host_stats[str(host)] = host_stats
        self._events.append({
            "event": "playbook_on_stats",
            "stats": self._host_stats,
        })


class AnsibleOptions:
    def __init__(
        self,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        diff: bool = False,
        forks: int = 5,
        become: bool = False,
        become_method: str | None = None,
        become_user: str | None = None,
        connection: str = "local",
        module_path: list[str] | None = None,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        start_at_task: str | None = None,
    ) -> None:
        self.inventory = inventory or ["localhost,"]
        self.extravars = extravars
        self.verbosity = verbosity
        self.check = check
        self.diff = diff
        self.forks = forks
        self.become = become
        self.become_method = become_method
        self.become_user = become_user
        self.connection = connection
        self.module_path = module_path or []
        self.tags = tags or ["all"]
        self.skip_tags = skip_tags or []
        self.start_at_task = start_at_task


class AnsibleResult(BaseModel):
    status: str = "unknown"
    rc: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    host_results: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status", mode="before")
    @classmethod
    def _strip(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


class CoreAnsibleRunner:
    def __init__(
        self,
        module_paths: list[str] | None = None,
        callback_plugins: list[str] | None = None,
        process_isolation: Any | None = None,
    ) -> None:
        self._module_paths = module_paths or []
        self._callback_plugins = callback_plugins or []
        self._process_isolation = process_isolation
        self._collected_events: list[dict[str, Any]] = []

    def run_playbook(
        self,
        playbook_path: str,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        connection: str = "local",
        become: bool = False,
    ) -> AnsibleResult:
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for playbook execution but is not installed")
        return self._execute_with_core(
            playbook_path=playbook_path,
            inventory=inventory,
            extravars=extravars,
            verbosity=verbosity,
            check=check,
            tags=tags,
            skip_tags=skip_tags,
            connection=connection,
            become=become,
        )

    def _execute_with_core(
        self,
        playbook_path: str,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        connection: str = "local",
        become: bool = False,
    ) -> AnsibleResult:
        from ansible import context
        from ansible.executor.playbook_executor import PlaybookExecutor
        from ansible.inventory.manager import InventoryManager
        from ansible.module_utils.common.collections import ImmutableDict
        from ansible.vars.manager import VariableManager

        loader = DataLoader()

        options = AnsibleOptions(
            inventory=inventory or ["localhost,"],
            extravars=extravars,
            verbosity=verbosity,
            check=check,
            tags=tags,
            skip_tags=skip_tags,
            connection=connection,
            become=become,
        )

        context.CLIARGS = ImmutableDict(
            inventory=options.inventory,
            extravars=options.extravars or {},
            verbosity=options.verbosity,
            check=options.check,
            diff=options.diff,
            forks=options.forks,
            become=options.become,
            become_method=options.become_method or "sudo",
            become_user=options.become_user or "root",
            connection=options.connection,
            module_path=options.module_path,
            tags=options.tags,
            skip_tags=options.skip_tags,
            start_at_task=options.start_at_task or None,
            listhosts=False,
            listtasks=False,
            listtags=False,
            syntax=False,
            subset=None,
            private_key_file=None,
            ssh_common_args=None,
            ssh_extra_args=None,
            sftp_extra_args=None,
            scp_extra_args=None,
            ask_vault_pass=False,
            vault_password_files=None,
            vault_ids=None,
        )

        inventory_mgr = InventoryManager(loader=loader, sources=options.inventory)
        variable_mgr = VariableManager(loader=loader, inventory=inventory_mgr)
        if extravars:
            variable_mgr.extra_vars = extravars

        self._collected_events = []

        callback = _EventCollectorCallback()

        pb_exec = PlaybookExecutor(
            playbooks=[playbook_path],
            inventory=inventory_mgr,
            variable_manager=variable_mgr,
            loader=loader,
            passwords={},
        )
        pb_exec._tqm._callback_plugins.append(callback)

        pb_exec.run()

        self._collected_events = list(callback._events)

        stats: dict[str, Any] = {}
        if hasattr(pb_exec, "_tqm") and hasattr(pb_exec._tqm, "_stats"):
            tqm_stats = pb_exec._tqm._stats
            if hasattr(tqm_stats, "process_tally"):
                stats = dict(tqm_stats.process_tally) if tqm_stats.process_tally else {}
            elif hasattr(tqm_stats, "processed"):
                for _host, host_stats in tqm_stats.processed.items():
                    for key, val in host_stats.items():
                        stats[key] = stats.get(key, 0) + val
        if not stats:
            stats = dict(callback._host_stats)

        return AnsibleResult(
            status="successful",
            rc=0,
            stats=stats,
            events=list(self._collected_events),
        )

    def render_template(
        self,
        template_str: str,
        variables: dict[str, Any] | None = None,
    ) -> str:
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for templating but is not installed")
        templar = _get_templar(variables=variables)
        result = templar.template(template_str)
        return str(result)

    def resolve_variable(
        self,
        var_name: str,
        host: str = "localhost",
        inventory_path: str | None = None,
        extravars: dict[str, Any] | None = None,
    ) -> Any:
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for variable resolution but is not installed")
        return self._resolve_with_variable_manager(var_name, host, inventory_path, extravars)

    def _resolve_with_variable_manager(
        self,
        var_name: str,
        host: str = "localhost",
        inventory_path: str | None = None,
        extravars: dict[str, Any] | None = None,
    ) -> Any:
        from ansible.inventory.manager import InventoryManager
        from ansible.vars.manager import VariableManager

        loader = DataLoader()
        sources = [inventory_path] if inventory_path else ["localhost,"]
        inventory_mgr = InventoryManager(loader=loader, sources=sources)
        variable_mgr = VariableManager(loader=loader, inventory=inventory_mgr)
        if extravars:
            variable_mgr.extra_vars = extravars

        hosts = inventory_mgr.get_hosts(pattern=host)
        if not hosts:
            return None
        host_vars = variable_mgr.get_vars(host=hosts[0])
        return host_vars.get(var_name)

    def list_tasks(
        self,
        playbook_path: str,
        extravars: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        _NON_MODULE_KEYS = {
            "name", "when", "loop", "with_items", "with_dict", "register",
            "become", "become_user", "delegate_to", "ignore_errors",
            "notify", "tags", "vars", "block", "rescue", "always",
            "args", "changed_when", "failed_when", "retries", "delay", "until",
            "run_once", "local_action", "delegate_facts",
        }

        try:
            with open(playbook_path) as f:
                plays = yaml.safe_load(f) or []
        except Exception:
            return []

        tasks: list[dict[str, str]] = []
        for play in plays:
            if not isinstance(play, dict):
                continue
            play_hosts = play.get("hosts", "all")
            for task in play.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                task_name = task.get("name", "")
                module = ""
                for key in task:
                    if key not in _NON_MODULE_KEYS:
                        module = key
                        break
                tasks.append({
                    "name": task_name,
                    "module": module,
                    "hosts": str(play_hosts),
                })
        return tasks

    def validate_playbook_syntax(self, playbook_path: str) -> list[str]:
        errors: list[str] = []

        if not os.path.isfile(playbook_path):
            errors.append(f"Playbook file not found: {playbook_path}")
            return errors

        try:
            with open(playbook_path) as f:
                plays = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            errors.append(f"YAML syntax error: {exc}")
            return errors

        if not isinstance(plays, list):
            errors.append("Playbook must be a list of plays")
            return errors

        for i, play in enumerate(plays):
            if not isinstance(play, dict):
                errors.append(f"Play {i} is not a mapping")
                continue
            if "hosts" not in play:
                errors.append(f"Play {i} ({play.get('name', 'unnamed')}) is missing 'hosts' key")

        return errors
