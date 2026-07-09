"""Credential stripping proxy for Ansible playbook execution (OpenShell P3).

CredentialProxy intercepts ``ansible.builtin.uri`` and ``ansible.builtin.get_url``
tasks targeting managed LLM endpoints, strips caller credentials from headers and
body, and injects backend credentials as ephemeral env vars. The agent never sees
the real API key.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from general_ludd.ansible.audit import PlaybookAuditLogger

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


@dataclass
class ManagedEndpoint:
    """A known LLM API endpoint whose credentials are managed by the proxy.

    Attributes:
        host: fnmatch pattern matching the request hostname.
        backend_credential: env var name holding the real backend API key.
        strip_headers: header names to strip (case-insensitive match).
        strip_body_keys: JSON body keys to strip when they carry credentials.
    """

    host: str
    backend_credential: str
    strip_headers: list[str]
    strip_body_keys: list[str]


@dataclass
class ScanResult:
    """Result of scanning a single task for credential headers/body keys."""

    stripped: bool
    task_args: dict[str, object]
    matched_endpoint: ManagedEndpoint | None = None
    stripped_headers: list[str] = field(default_factory=list)
    stripped_body_keys: list[str] = field(default_factory=list)
    backend_credential_resolved: bool = False
    violations: list[str] = field(default_factory=list)


@dataclass
class CredentialInjection:
    """A backend credential that should be injected as an env var."""

    env_var: str
    host: str


@dataclass
class CredentialViolation:
    """A security violation found during credential scanning."""

    host: str
    header: str | None = None
    body_key: str | None = None
    message: str = ""


def _extract_host(url: str) -> str:
    try:
        parts = urlsplit(url)
        return (parts.hostname or "").lower()
    except (ValueError, AttributeError):
        return ""


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


class CredentialProxy:
    """Strips caller credentials from uri/get_url tasks and resolves backend keys.

    Usage::

        proxy = CredentialProxy(endpoints=[...], resolver=os.environ.get)
        result = proxy.scan_and_strip(task_args, audit=audit_logger, module="uri")
        if result.violations:
            raise SecurityError(...)

    The ``resolver`` callable receives an env var name and returns its value
    (or ``None`` if not set). The default resolver is ``os.environ.get``.
    """

    def __init__(
        self,
        endpoints: list[ManagedEndpoint] | None = None,
        resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._endpoints = endpoints or list(DEFAULT_MANAGED_ENDPOINTS)
        self._resolver = resolver if resolver is not None else os.environ.get

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_backend_credential(self, host: str) -> str | None:
        """Return the backend API key for *host*, or ``None`` if not found."""
        host_l = host.lower()
        for ep in self._endpoints:
            if fnmatch.fnmatch(host_l, ep.host.lower()):
                return self._resolver(ep.backend_credential)
        return None

    def scan_and_strip(
        self,
        task_args: dict[str, object],
        audit: PlaybookAuditLogger | None = None,
        module: str = "uri",
    ) -> ScanResult:
        """Scan *task_args* for credentials and strip them.

        Args:
            task_args: The Ansible task arguments dict (headers, body, url, etc.).
            audit: Optional audit logger for credential_access events.
            module: The Ansible module name (``uri`` or ``get_url``).

        Returns:
            A :class:`ScanResult` with stripped task args and any violations.
        """
        url_raw = task_args.get("url", "")
        url = str(url_raw) if url_raw else ""

        host = _extract_host(url)
        if not host:
            return ScanResult(stripped=False, task_args=dict(task_args))

        matched = self._match_endpoint(host)
        if matched is None:
            return ScanResult(stripped=False, task_args=dict(task_args))

        stripped_headers: list[str] = []
        stripped_body_keys: list[str] = []
        violations: list[str] = []
        result_args = dict(task_args)

        strip_header_set = {h.lower() for h in matched.strip_headers}

        # --- headers: dict (standard Ansible) ---
        headers = result_args.get("headers")
        if isinstance(headers, dict):
            new_headers: dict[str, str] = {}
            for key, value in headers.items():
                if str(key).lower() in strip_header_set:
                    stripped_headers.append(str(key))
                    if audit is not None:
                        audit.credential_access(module=module, secret_name=str(key))
                else:
                    new_headers[str(key)] = value
            result_args["headers"] = new_headers

        # --- headers: JSON string (YAML sometimes serialises this) ---
        elif isinstance(headers, str):
            try:
                parsed = json.loads(headers)
                if isinstance(parsed, dict):
                    new_parsed: dict[str, object] = {}
                    for key, value in parsed.items():
                        if str(key).lower() in strip_header_set:
                            stripped_headers.append(str(key))
                            if audit is not None:
                                audit.credential_access(module=module, secret_name=str(key))
                        else:
                            new_parsed[str(key)] = value
                    result_args["headers"] = new_parsed
            except (json.JSONDecodeError, TypeError):
                pass

        # --- body: dict ---
        body = result_args.get("body")
        if isinstance(body, dict):
            strip_body_set = set(matched.strip_body_keys)
            new_body: dict[str, object] = {}
            for key, value in body.items():
                if key in strip_body_set:
                    stripped_body_keys.append(key)
                    if audit is not None:
                        audit.credential_access(module=module, secret_name=str(key))
                else:
                    new_body[str(key)] = value
            result_args["body"] = new_body

        # --- body: JSON string ---
        elif isinstance(body, str):
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    strip_body_set = set(matched.strip_body_keys)
                    new_parsed_body: dict[str, object] = {}
                    for key, value in parsed.items():
                        if key in strip_body_set:
                            stripped_body_keys.append(key)
                            if audit is not None:
                                audit.credential_access(module=module, secret_name=str(key))
                        else:
                            new_parsed_body[str(key)] = value
                    result_args["body"] = new_parsed_body
            except (json.JSONDecodeError, TypeError):
                pass

        stripped = bool(stripped_headers or stripped_body_keys)

        # Violation: caller credentials found but no backend key configured
        if stripped:
            backend = self.resolve_backend_credential(host)
            if backend is None:
                violations.append(
                    f"caller credentials detected for {host} "
                    f"but backend credential {matched.backend_credential!r} is not set"
                )
            else:
                if audit is not None:
                    audit.credential_access(
                        module=module, secret_name=matched.backend_credential
                    )

        return ScanResult(
            stripped=stripped,
            task_args=result_args,
            matched_endpoint=matched,
            stripped_headers=stripped_headers,
            stripped_body_keys=stripped_body_keys,
            backend_credential_resolved=self.resolve_backend_credential(host) is not None,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _match_endpoint(self, host: str) -> ManagedEndpoint | None:
        host_l = host.lower()
        for ep in self._endpoints:
            if fnmatch.fnmatch(host_l, ep.host.lower()):
                return ep
        return None


# ---------------------------------------------------------------------------
# Default managed endpoints — covers every major LLM API provider
# ---------------------------------------------------------------------------

def _openai() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.openai.com",
        backend_credential="GLUDD_OPENAI_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _anthropic() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.anthropic.com",
        backend_credential="GLUDD_ANTHROPIC_API_KEY",
        strip_headers=["x-api-key", "api-key", "Authorization"],
        strip_body_keys=["api_key"],
    )


def _google_ai_studio() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="generativelanguage.googleapis.com",
        backend_credential="GLUDD_GOOGLE_API_KEY",
        strip_headers=["x-goog-api-key", "Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key", "key"],
    )


def _google_vertex() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="*.googleapis.com",
        backend_credential="GLUDD_GOOGLE_API_KEY",
        strip_headers=["Authorization", "x-goog-api-key", "x-api-key", "api-key"],
        strip_body_keys=["api_key", "key"],
    )


def _mistral() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.mistral.ai",
        backend_credential="GLUDD_MISTRAL_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _deepinfra() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.deepinfra.com",
        backend_credential="GLUDD_DEEPINFRA_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _together() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.together.xyz",
        backend_credential="GLUDD_TOGETHER_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _fireworks() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.fireworks.ai",
        backend_credential="GLUDD_FIREWORKS_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _groq() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.groq.com",
        backend_credential="GLUDD_GROQ_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _deepseek() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.deepseek.com",
        backend_credential="GLUDD_DEEPSEEK_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _deepseek_wild() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="*.deepseek.com",
        backend_credential="GLUDD_DEEPSEEK_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _openrouter() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="openrouter.ai",
        backend_credential="GLUDD_OPENROUTER_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _openrouter_api() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.openrouter.ai",
        backend_credential="GLUDD_OPENROUTER_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _xai() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.x.ai",
        backend_credential="GLUDD_XAI_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


DEFAULT_MANAGED_ENDPOINTS: tuple[ManagedEndpoint, ...] = (
    _openai(),
    _anthropic(),
    _google_ai_studio(),
    _google_vertex(),
    _mistral(),
    _deepinfra(),
    _together(),
    _fireworks(),
    _groq(),
    _deepseek(),
    _deepseek_wild(),
    _openrouter(),
    _openrouter_api(),
    _xai(),
)


# ---------------------------------------------------------------------------
# Playbook-level scanner
# ---------------------------------------------------------------------------

def scan_playbook_for_credentials(
    playbook_path: str,
    proxy: CredentialProxy,
    audit: PlaybookAuditLogger,
) -> tuple[list[CredentialInjection], list[CredentialViolation]]:
    """Scan every uri/get_url task in *playbook_path* through *proxy*.

    Returns:
        (injections, violations) — env vars to inject and any violations found.
    """
    import yaml

    injections: list[CredentialInjection] = []
    violations: list[CredentialViolation] = []

    try:
        with open(playbook_path) as f:
            plays = yaml.safe_load(f) or []
    except Exception:
        return [], []

    for play in plays:
        if not isinstance(play, dict):
            continue
        play_tasks = play.get("tasks", [])
        if not isinstance(play_tasks, list):
            continue

        play_data = list(play_tasks)

        for task_args in _extract_module_tasks(play_data, _URI_MODULES):
            result = proxy.scan_and_strip(task_args, audit=audit, module="uri")
            _collect(result, injections, violations)

        for task_args in _extract_module_tasks(play_data, _GET_URL_MODULES):
            result = proxy.scan_and_strip(task_args, audit=audit, module="get_url")
            _collect(result, injections, violations)

    return injections, violations


def _collect(
    result: ScanResult,
    injections: list[CredentialInjection],
    violations: list[CredentialViolation],
) -> None:
    if result.matched_endpoint is not None and result.backend_credential_resolved:
        injections.append(
            CredentialInjection(
                env_var=result.matched_endpoint.backend_credential,
                host=result.matched_endpoint.host,
            )
        )
    for v in result.violations:
        violations.append(
            CredentialViolation(
                host=result.matched_endpoint.host if result.matched_endpoint else "?",
                message=v,
            )
        )
