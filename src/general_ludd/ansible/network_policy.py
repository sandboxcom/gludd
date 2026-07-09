"""L7 HTTP network policy enforcement for Ansible playbook execution.

Every outbound HTTP request an Ansible playbook makes via ``ansible.builtin.uri``
or ``ansible.builtin.get_url`` is validated against a declarative
:class:`NetworkPolicy` BEFORE the playbook executes. This is the OpenShell P0 transfer:
a proxy that checks HTTP method + path + host against a declarative policy, adapted
to run inside the playbook-validator gate.
"""

from __future__ import annotations

import fnmatch
import logging
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_URI_MODULES = frozenset({"ansible.builtin.uri", "uri", "ansible.legacy.uri"})
_GET_URL_MODULES = frozenset({
    "ansible.builtin.get_url",
    "get_url",
    "ansible.legacy.get_url",
})

_NON_MODULE_KEYS: frozenset[str] = frozenset({
    "name", "when", "loop", "with_items", "with_dict", "register",
    "become", "become_user", "delegate_to", "ignore_errors",
    "notify", "tags", "vars", "block", "rescue", "always",
    "args", "changed_when", "failed_when", "retries", "delay", "until",
    "run_once", "local_action", "delegate_facts",
})


class PolicyRule(BaseModel):
    host: str = Field(description="fnmatch pattern matching the request hostname")
    methods: list[str] = Field(description="Allowed HTTP methods (case-insensitive)")
    path_prefix: str = Field(
        default="/",
        description="Required path prefix the request URL path must start with",
    )


class NetworkPolicy(BaseModel):
    rules: list[PolicyRule] = Field(
        default_factory=list,
        description="Allow rules checked in order; empty = default-deny",
    )

    def _extract_host_method_path(
        self, task_args: dict[str, object]
    ) -> tuple[str, str, str]:
        url_raw = task_args.get("url", "")
        url = str(url_raw) if url_raw else ""

        if url:
            try:
                parts = urlsplit(url)
                host = (parts.hostname or "").lower()
                path = parts.path or "/"
            except ValueError:
                host = ""
                path = "/"
        else:
            host = ""
            path = "/"

        method_raw = task_args.get("method", "GET")
        method = str(method_raw).upper() if method_raw else "GET"

        return host, method, path

    def check_uri_module(self, task_args: dict[str, object]) -> tuple[bool, str]:
        host, method, path = self._extract_host_method_path(task_args)

        for rule in self.rules:
            if not fnmatch.fnmatch(host, rule.host):
                continue
            if method.upper() not in {m.upper() for m in rule.methods}:
                continue
            if not path.startswith(rule.path_prefix):
                continue
            return True, "ok"

        if not self.rules:
            msg = "no rules in network policy (default-deny)"
        else:
            msg = (
                f"no matching rule for {method} {host}{path} "
                f"(checked {len(self.rules)} rule(s))"
            )

        self._log_deny(host, method, path, msg)
        return False, msg

    def _log_deny(self, host: str, method: str, path: str, message: str) -> None:
        logger.warning(
            "network_policy_deny: %s %s%s — %s",
            method,
            host,
            path,
            message,
            extra={
                "event_type": "network_policy_deny",
                "host": host,
                "method": method,
                "url_path": path,
                "reason": message,
            },
        )


def _extract_module_tasks(
    arg: object, module_names: frozenset[str]
) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    if isinstance(arg, dict):
        for key, value in arg.items():
            if key in module_names and isinstance(value, dict):
                tasks.append(dict(value))
            elif key in _NON_MODULE_KEYS or isinstance(value, (list, dict)):
                tasks.extend(_extract_module_tasks(value, module_names))
    elif isinstance(arg, list):
        for item in arg:
            tasks.extend(_extract_module_tasks(item, module_names))
    return tasks


def scan_playbook_tasks(playbook_path: str, policy: NetworkPolicy) -> list[str]:
    import yaml

    try:
        with open(playbook_path) as f:
            plays = yaml.safe_load(f) or []
    except Exception:
        return ["Unable to parse playbook YAML for network policy scan"]

    violations: list[str] = []

    for play in plays:
        if not isinstance(play, dict):
            continue
        play_tasks = play.get("tasks", [])
        if not isinstance(play_tasks, list):
            continue

        play_data = list(play_tasks)

        for task_args in _extract_module_tasks(play_data, _URI_MODULES):
            allowed, reason = policy.check_uri_module(task_args)
            if not allowed:
                url = task_args.get("url", "?")
                method = task_args.get("method", "GET")
                violations.append(f"uri: {method} {url} — {reason}")

        for task_args in _extract_module_tasks(play_data, _GET_URL_MODULES):
            allowed, reason = policy.check_uri_module(task_args)
            if not allowed:
                url = task_args.get("url", "?")
                violations.append(f"get_url: GET {url} — {reason}")

    return violations
