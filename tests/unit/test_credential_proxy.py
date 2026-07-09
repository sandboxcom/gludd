"""TDD tests for credential stripping proxy (OpenShell P3 transfer).

CredentialProxy intercepts ``uri`` / ``get_url`` tasks targeting managed LLM
endpoints, strips caller credentials from headers and body, and resolves backend
credentials from env vars. The agent never sees the real API key.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable

from general_ludd.ansible.audit import PlaybookAuditLogger
from general_ludd.ansible.credential_proxy import (
    DEFAULT_MANAGED_ENDPOINTS,
    CredentialProxy,
    ManagedEndpoint,
    scan_playbook_for_credentials,
)


def _openai_endpoint() -> ManagedEndpoint:
    return ManagedEndpoint(
        host="api.openai.com",
        backend_credential="GLUDD_OPENAI_API_KEY",
        strip_headers=["Authorization", "x-api-key", "api-key"],
        strip_body_keys=["api_key"],
    )


def _make_proxy(
    endpoints: list[ManagedEndpoint] | None = None,
    resolver: Callable[[str], str | None] | None = None,
) -> CredentialProxy:
    if endpoints is None:
        endpoints = [_openai_endpoint()]
    if resolver is None:
        resolver = lambda _: None  # noqa: E731
    return CredentialProxy(endpoints=endpoints, resolver=resolver)


def _dict_resolver(mapping: dict[str, str]) -> Callable[[str], str | None]:
    def resolve(name: str) -> str | None:
        return mapping.get(name)
    return resolve


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------

class TestHeaderStripping:
    """Credential headers are removed from task args for managed endpoints."""

    def test_strips_authorization_header(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "method": "POST",
            "headers": {"Authorization": "Bearer sk-abc123"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is True
        assert "Authorization" in result.stripped_headers
        headers = result.task_args.get("headers", {})
        assert isinstance(headers, dict)
        assert "Authorization" not in headers

    def test_strips_x_api_key_header(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"x-api-key": "sk-abc123"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is True
        assert "x-api-key" in result.stripped_headers

    def test_strips_api_key_header(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"api-key": "sk-abc123"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is True
        assert "api-key" in result.stripped_headers

    def test_preserves_non_credential_headers(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {
                "Authorization": "Bearer sk-abc123",
                "Content-Type": "application/json",
                "X-Request-Id": "12345",
            },
        }
        result = proxy.scan_and_strip(task)
        headers = result.task_args.get("headers", {})
        assert isinstance(headers, dict)
        assert "Content-Type" in headers
        assert "X-Request-Id" in headers
        assert "Authorization" not in headers

    def test_header_stripping_is_case_insensitive(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {
                "authorization": "Bearer sk-abc123",
                "AUTHORIZATION": "Bearer sk-def456",
                "X-Api-Key": "sk-ghi789",
            },
        }
        result = proxy.scan_and_strip(task)
        assert len(result.stripped_headers) == 3
        headers = result.task_args.get("headers", {})
        assert isinstance(headers, dict)
        assert len(headers) == 0

    def test_strips_multiple_credential_headers(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {
                "Authorization": "Bearer sk-1",
                "x-api-key": "sk-2",
                "api-key": "sk-3",
                "Content-Type": "application/json",
            },
        }
        result = proxy.scan_and_strip(task)
        assert len(result.stripped_headers) == 3
        assert "Authorization" in result.stripped_headers
        assert "x-api-key" in result.stripped_headers
        assert "api-key" in result.stripped_headers
        headers = result.task_args.get("headers", {})
        assert isinstance(headers, dict)
        assert headers == {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Body stripping
# ---------------------------------------------------------------------------

class TestBodyStripping:
    """Body keys containing credentials are stripped for managed endpoints."""

    def test_strips_body_api_key(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "method": "POST",
            "body": {"api_key": "sk-abc123", "model": "gpt-4", "prompt": "hello"},  # pragma: allowlist secret
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is True
        assert "api_key" in result.stripped_body_keys
        body = result.task_args.get("body", {})
        assert isinstance(body, dict)
        assert "api_key" not in body
        assert body["model"] == "gpt-4"
        assert body["prompt"] == "hello"


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

class TestNoop:
    """Tasks targeting unmanaged hosts or without credentials are unmodified."""

    def test_noop_on_unmanaged_host(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://example.com/api",
            "headers": {"Authorization": "Bearer sk-abc123"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is False
        assert result.matched_endpoint is None
        assert result.stripped_headers == []
        headers = result.task_args.get("headers", {})
        assert isinstance(headers, dict)
        assert "Authorization" in headers

    def test_noop_when_no_credentials_present(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"Content-Type": "application/json"},
            "body": {"model": "gpt-4"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is False
        assert result.stripped_headers == []
        assert result.stripped_body_keys == []

    def test_noop_when_no_headers_and_no_body(self) -> None:
        proxy = _make_proxy()
        task = {"url": "https://api.openai.com/v1/chat/completions", "method": "GET"}
        result = proxy.scan_and_strip(task)
        assert result.stripped is False


# ---------------------------------------------------------------------------
# Violation: caller creds but no backend key
# ---------------------------------------------------------------------------

class TestViolation:
    """Violations are reported when caller credentials exist but backend key
    cannot be resolved."""

    def test_returns_violation_when_caller_creds_and_no_backend_key(self) -> None:
        proxy = _make_proxy(resolver=lambda _: None)
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"Authorization": "Bearer sk-abc123"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is True
        assert len(result.violations) == 1
        assert "GLUDD_OPENAI_API_KEY" in result.violations[0]


# ---------------------------------------------------------------------------
# Backend credential resolution
# ---------------------------------------------------------------------------

class TestBackendCredentialResolution:
    """The proxy resolves backend credentials from env vars."""

    def test_resolves_backend_credential_from_env(self) -> None:
        resolver = _dict_resolver({"GLUDD_OPENAI_API_KEY": "sk-backend-real"})
        proxy = _make_proxy(resolver=resolver)
        result = proxy.resolve_backend_credential("api.openai.com")
        assert result == "sk-backend-real"

    def test_backend_credential_resolved_flag(self) -> None:
        resolver = _dict_resolver({"GLUDD_OPENAI_API_KEY": "sk-backend-real"})
        proxy = _make_proxy(resolver=resolver)
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"Authorization": "Bearer sk-caller123"},
        }
        result = proxy.scan_and_strip(task)
        assert result.backend_credential_resolved is True
        assert result.violations == []

    def test_resolve_returns_none_for_unmanaged_host(self) -> None:
        resolver = _dict_resolver({"GLUDD_OPENAI_API_KEY": "sk-backend-real"})
        proxy = _make_proxy(resolver=resolver)
        result = proxy.resolve_backend_credential("example.com")
        assert result is None


# ---------------------------------------------------------------------------
# Wildcard matching
# ---------------------------------------------------------------------------

class TestWildcardMatching:
    """Host patterns use fnmatch for wildcard support."""

    def test_endpoint_wildcard_matching(self) -> None:
        wildcard_endpoint = ManagedEndpoint(
            host="*.openai.com",
            backend_credential="GLUDD_OPENAI_API_KEY",
            strip_headers=["Authorization"],
            strip_body_keys=["api_key"],
        )
        proxy = _make_proxy(endpoints=[wildcard_endpoint])

        prod = proxy.scan_and_strip(
            {"url": "https://api.openai.com/v1/chat", "headers": {"Authorization": "Bearer x"}}
        )
        assert prod.stripped is True
        assert prod.matched_endpoint is not None
        assert prod.matched_endpoint.host == "*.openai.com"

        beta = proxy.scan_and_strip(
            {"url": "https://beta.openai.com/v1/chat", "headers": {"Authorization": "Bearer x"}}
        )
        assert beta.stripped is True

        other = proxy.scan_and_strip(
            {"url": "https://openai.com/v1/chat", "headers": {"Authorization": "Bearer x"}}
        )
        assert other.stripped is False


# ---------------------------------------------------------------------------
# Playbook scanning
# ---------------------------------------------------------------------------

class TestPlaybookScanning:
    """scan_playbook_for_credentials finds uri and get_url tasks in YAML
    playbooks."""

    def test_scan_playbook_tasks_finds_uri_and_get_url(self) -> None:
        playbook_yaml = """\
- hosts: localhost
  tasks:
    - ansible.builtin.uri:
        url: https://api.openai.com/v1/chat/completions
        method: POST
        headers:
          Authorization: Bearer sk-abc123
    - ansible.builtin.get_url:
        url: https://api.openai.com/v1/models
        headers:
          x-api-key: sk-def456
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(playbook_yaml)
            f.flush()
            path = f.name

        try:
            resolver = _dict_resolver({"GLUDD_OPENAI_API_KEY": "sk-backend-real"})
            proxy = _make_proxy(resolver=resolver)
            audit = PlaybookAuditLogger(playbook=path)
            injections, violations = scan_playbook_for_credentials(
                path, proxy, audit
            )
            assert len(injections) == 2
            assert len(violations) == 0
            for inj in injections:
                assert inj.env_var == "GLUDD_OPENAI_API_KEY"
                assert inj.host == "api.openai.com"
        finally:
            os.unlink(path)

    def test_scan_returns_violations(self) -> None:
        playbook_yaml = """\
- hosts: localhost
  tasks:
    - uri:
        url: https://api.openai.com/v1/chat/completions
        method: POST
        headers:
          Authorization: Bearer sk-abc123
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(playbook_yaml)
            f.flush()
            path = f.name

        try:
            proxy = _make_proxy(resolver=lambda _: None)
            audit = PlaybookAuditLogger(playbook=path)
            injections, violations = scan_playbook_for_credentials(
                path, proxy, audit
            )
            assert len(injections) == 0
            assert len(violations) == 1
            assert "GLUDD_OPENAI_API_KEY" in violations[0].message
        finally:
            os.unlink(path)

    def test_strips_from_get_url_too(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/models",
            "headers": {"Authorization": "Bearer sk-abc123"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is True
        assert "Authorization" in result.stripped_headers

    def test_scan_uses_uri_module_aliases(self) -> None:
        """Legacy module names (uri, get_url) are also scanned."""
        playbook_yaml = """\
- hosts: localhost
  tasks:
    - uri:
        url: https://api.openai.com/v1/chat
        headers:
          Authorization: Bearer x
    - get_url:
        url: https://api.openai.com/v1/models
        headers:
          api-key: y
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(playbook_yaml)
            f.flush()
            path = f.name

        try:
            proxy = _make_proxy(resolver=lambda _: None)
            audit = PlaybookAuditLogger(playbook=path)
            injections, violations = scan_playbook_for_credentials(
                path, proxy, audit
            )
            assert len(violations) == 2
            assert len(injections) == 0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------

class TestAuditEvents:
    """Audit events are emitted on credential strip and backend injection."""

    def test_audit_emits_credential_access_on_strip(self) -> None:
        proxy = _make_proxy()
        audit = PlaybookAuditLogger(playbook="deploy.yml")
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"Authorization": "Bearer sk-abc123"},
        }
        proxy.scan_and_strip(task, audit=audit, module="uri")
        events = audit.flush()
        assert len(events) == 1
        assert events[0].event_type == "credential_access"
        assert events[0].detail["secret_name"] == "Authorization"
        assert events[0].module == "uri"

    def test_audit_emits_credential_access_on_inject(self) -> None:
        resolver = _dict_resolver({"GLUDD_OPENAI_API_KEY": "sk-backend-real"})
        proxy = _make_proxy(resolver=resolver)
        audit = PlaybookAuditLogger(playbook="deploy.yml")
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"Authorization": "Bearer sk-caller"},
        }
        proxy.scan_and_strip(task, audit=audit, module="uri")
        events = audit.flush()
        event_types = {e.detail["secret_name"] for e in events}
        assert "Authorization" in event_types
        assert "GLUDD_OPENAI_API_KEY" in event_types

    def test_audit_emits_per_stripped_header(self) -> None:
        proxy = _make_proxy()
        audit = PlaybookAuditLogger(playbook="deploy.yml")
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {
                "Authorization": "Bearer sk-1",
                "x-api-key": "sk-2",
                "api-key": "sk-3",
            },
        }
        proxy.scan_and_strip(task, audit=audit, module="uri")
        events = audit.flush()
        stripped_names = {e.detail["secret_name"] for e in events}
        assert stripped_names >= {"Authorization", "x-api-key", "api-key"}


# ---------------------------------------------------------------------------
# Default endpoints
# ---------------------------------------------------------------------------

class TestDefaultEndpoints:
    """DEFAULT_MANAGED_ENDPOINTS covers the major LLM providers."""

    def test_default_endpoints_cover_major_providers(self) -> None:
        hosts = {ep.host for ep in DEFAULT_MANAGED_ENDPOINTS}
        required = {
            "api.openai.com",
            "api.anthropic.com",
            "*.googleapis.com",
            "generativelanguage.googleapis.com",
            "api.mistral.ai",
            "api.deepinfra.com",
            "api.together.xyz",
            "api.fireworks.ai",
            "api.groq.com",
            "api.deepseek.com",
            "*.deepseek.com",
            "openrouter.ai",
            "api.openrouter.ai",
            "api.x.ai",
        }
        assert hosts.issuperset(required)

    def test_default_endpoints_are_valid_managed_endpoints(self) -> None:
        for ep in DEFAULT_MANAGED_ENDPOINTS:
            assert isinstance(ep, ManagedEndpoint)
            assert ep.host
            assert ep.backend_credential
            assert isinstance(ep.strip_headers, list)
            assert isinstance(ep.strip_body_keys, list)


# ---------------------------------------------------------------------------
# Ansible validity
# ---------------------------------------------------------------------------

class TestAnsibleValidity:
    """Stripped task args remain valid as Ansible task definitions."""

    def test_stripped_task_still_valid_ansible(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat/completions",
            "method": "POST",
            "headers": {
                "Authorization": "Bearer sk-abc123",
                "Content-Type": "application/json",
            },
            "body": {"model": "gpt-4", "api_key": "sk-abc123", "messages": []},
            "status_code": [200],
        }
        result = proxy.scan_and_strip(task)
        stripped = result.task_args
        assert stripped["url"] == "https://api.openai.com/v1/chat/completions"
        assert stripped["method"] == "POST"
        assert stripped["status_code"] == [200]
        assert isinstance(stripped["body"], dict)
        assert "model" in stripped["body"]
        assert "messages" in stripped["body"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Corner cases: missing fields, empty values, malformed tasks."""

    def test_task_without_url_is_not_matched(self) -> None:
        proxy = _make_proxy()
        task: dict[str, object] = {
            "headers": {"Authorization": "Bearer sk-abc"},
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is False

    def test_task_with_empty_url_is_not_matched(self) -> None:
        proxy = _make_proxy()
        task = {"url": "", "headers": {"Authorization": "Bearer sk-abc"}}
        result = proxy.scan_and_strip(task)
        assert result.stripped is False

    def test_unparsable_url_is_not_matched(self) -> None:
        proxy = _make_proxy()
        task: dict[str, object] = {"url": "not-a-valid-url://", "headers": {"Authorization": "Bearer x"}}
        result = proxy.scan_and_strip(task)
        assert result.stripped is False

    def test_headers_is_not_a_dict(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat",
            "headers": "Authorization: Bearer x",
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is False

    def test_body_is_not_a_dict(self) -> None:
        proxy = _make_proxy()
        task = {
            "url": "https://api.openai.com/v1/chat",
            "body": "api_key=sk-abc",
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is False

    def test_headers_is_json_string(self) -> None:
        """YAML sometimes loads headers as a JSON string that needs parsing."""
        proxy = _make_proxy()
        task: dict[str, object] = {
            "url": "https://api.openai.com/v1/chat",
            "headers": '{"Authorization": "Bearer sk-abc123"}',
        }
        result = proxy.scan_and_strip(task)
        assert result.stripped is True
        assert "Authorization" in result.stripped_headers
