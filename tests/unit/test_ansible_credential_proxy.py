"""Unit tests for ansible/credential_proxy.py — CredentialProxy, scan_playbook_for_credentials."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.ansible.audit import PlaybookAuditLogger
from general_ludd.ansible.credential_proxy import (
    DEFAULT_MANAGED_ENDPOINTS,
    CredentialInjection,
    CredentialProxy,
    CredentialViolation,
    ManagedEndpoint,
    ScanResult,
    _collect,
    _extract_host,
    _extract_module_tasks,
    scan_playbook_for_credentials,
)

pytestmark = pytest.mark.xdist_group("ansible_credential_proxy")


class TestExtractHost:
    def test_plain_https_url(self):
        assert _extract_host("https://api.openai.com/v1/chat") == "api.openai.com"

    def test_url_with_port(self):
        assert _extract_host("https://api.example.com:8443/endpoint") == "api.example.com"

    def test_url_with_query(self):
        assert _extract_host("https://example.com/path?key=value") == "example.com"

    def test_no_scheme(self):
        assert _extract_host("api.openai.com/v1") == ""

    def test_empty_string(self):
        assert _extract_host("") == ""

    def test_malformed_url(self):
        assert _extract_host("not a url at all") == ""

    def test_none_passed_empty_return(self):
        result = _extract_host(None)
        assert result == ""

    def test_ipv4_url(self):
        assert _extract_host("https://192.168.1.1/api") == "192.168.1.1"

    def test_subdomain_url(self):
        assert _extract_host("https://sub.domain.example.com/path") == "sub.domain.example.com"


class TestExtractModuleTasks:
    def test_flat_uri_task(self):
        tasks = _extract_module_tasks(
            {"ansible.builtin.uri": {"url": "https://api.example.com"}},
            frozenset({"ansible.builtin.uri"}),
        )
        assert tasks == [{"url": "https://api.example.com"}]

    def test_flat_get_url_task(self):
        tasks = _extract_module_tasks(
            {"ansible.builtin.get_url": {"url": "https://example.com/file"}},
            frozenset({"ansible.builtin.get_url"}),
        )
        assert tasks == [{"url": "https://example.com/file"}]

    def test_task_inside_dict_with_meta_keys(self):
        tasks = _extract_module_tasks(
            {
                "name": "do something",
                "ansible.builtin.uri": {"url": "https://api.example.com"},
            },
            frozenset({"ansible.builtin.uri"}),
        )
        assert tasks == [{"url": "https://api.example.com"}]

    def test_nested_in_list(self):
        tasks = _extract_module_tasks(
            [
                {"ansible.builtin.uri": {"url": "https://one.com"}},
                {"ansible.builtin.uri": {"url": "https://two.com"}},
            ],
            frozenset({"ansible.builtin.uri"}),
        )
        assert len(tasks) == 2
        assert tasks[0] == {"url": "https://one.com"}
        assert tasks[1] == {"url": "https://two.com"}

    def test_deeply_nested_block(self):
        task_data = {
            "block": [
                {
                    "name": "inner task",
                    "ansible.builtin.uri": {"url": "https://deep.example.com"},
                }
            ]
        }
        tasks = _extract_module_tasks(task_data, frozenset({"ansible.builtin.uri"}))
        assert len(tasks) == 1
        assert tasks[0] == {"url": "https://deep.example.com"}

    def test_skips_non_module_keys_at_top_level(self):
        tasks = _extract_module_tasks(
            {"name": "only metadata", "when": "true", "tags": ["always"]},
            frozenset({"ansible.builtin.uri"}),
        )
        assert tasks == []

    def test_ignores_none_module_value_type(self):
        tasks = _extract_module_tasks(
            {"ansible.legacy.uri": "not-a-dict"},
            frozenset({"ansible.legacy.uri", "uri"}),
        )
        assert tasks == []

    def test_empty_input(self):
        assert _extract_module_tasks({}, frozenset({"ansible.builtin.uri"})) == []
        assert _extract_module_tasks([], frozenset({"ansible.builtin.uri"})) == []

    def test_matches_legacy_and_short_names(self):
        tasks = _extract_module_tasks(
            {"uri": {"url": "https://example.com"}},
            frozenset({"ansible.builtin.uri", "uri", "ansible.legacy.uri"}),
        )
        assert tasks == [{"url": "https://example.com"}]


class TestManagedEndpoint:
    def test_construction(self):
        ep = ManagedEndpoint(
            host="api.example.com",
            backend_credential="GLUDD_EXAMPLE_KEY",
            strip_headers=["Authorization"],
            strip_body_keys=["api_key"],
        )
        assert ep.host == "api.example.com"
        assert ep.backend_credential == "GLUDD_EXAMPLE_KEY"
        assert ep.strip_headers == ["Authorization"]
        assert ep.strip_body_keys == ["api_key"]

    def test_defaults_are_lists(self):
        ep = ManagedEndpoint(
            host="api.example.com",
            backend_credential="GLUDD_KEY",
            strip_headers=[],
            strip_body_keys=[],
        )
        assert ep.strip_headers == []
        assert ep.strip_body_keys == []


class TestScanResult:
    def test_not_stripped_default(self):
        sr = ScanResult(stripped=False, task_args={"url": "https://example.com"})
        assert sr.stripped is False
        assert sr.matched_endpoint is None
        assert sr.stripped_headers == []
        assert sr.stripped_body_keys == []
        assert sr.backend_credential_resolved is False
        assert sr.violations == []

    def test_stripped_with_matches(self):
        ep = ManagedEndpoint(
            host="api.example.com",
            backend_credential="GLUDD_KEY",
            strip_headers=["Authorization"],
            strip_body_keys=["api_key"],
        )
        sr = ScanResult(
            stripped=True,
            task_args={"url": "https://api.example.com"},
            matched_endpoint=ep,
            stripped_headers=["Authorization"],
            stripped_body_keys=["api_key"],
            backend_credential_resolved=True,
            violations=["some violation"],
        )
        assert sr.stripped is True
        assert sr.matched_endpoint is ep
        assert sr.stripped_headers == ["Authorization"]
        assert sr.stripped_body_keys == ["api_key"]
        assert sr.backend_credential_resolved is True
        assert sr.violations == ["some violation"]


class TestCredentialInjection:
    def test_construction(self):
        ci = CredentialInjection(env_var="GLUDD_KEY", host="api.example.com")
        assert ci.env_var == "GLUDD_KEY"
        assert ci.host == "api.example.com"


class TestCredentialViolation:
    def test_construction_with_all_fields(self):
        cv = CredentialViolation(
            host="api.example.com",
            header="Authorization",
            body_key="api_key",
            message="caller credentials detected",
        )
        assert cv.host == "api.example.com"
        assert cv.header == "Authorization"
        assert cv.body_key == "api_key"
        assert cv.message == "caller credentials detected"

    def test_construction_minimal(self):
        cv = CredentialViolation(host="api.example.com", message="no backend key")
        assert cv.host == "api.example.com"
        assert cv.header is None
        assert cv.body_key is None
        assert cv.message == "no backend key"


class TestDefaultManagedEndpoints:
    def test_all_14_providers_present(self):
        hosts = {ep.host for ep in DEFAULT_MANAGED_ENDPOINTS}
        assert "api.openai.com" in hosts
        assert "api.anthropic.com" in hosts
        assert "generativelanguage.googleapis.com" in hosts
        assert "*.googleapis.com" in hosts
        assert "api.mistral.ai" in hosts
        assert "api.deepinfra.com" in hosts
        assert "api.together.xyz" in hosts
        assert "api.fireworks.ai" in hosts
        assert "api.groq.com" in hosts
        assert "api.deepseek.com" in hosts
        assert "*.deepseek.com" in hosts
        assert "openrouter.ai" in hosts
        assert "api.openrouter.ai" in hosts
        assert "api.x.ai" in hosts
        assert len(DEFAULT_MANAGED_ENDPOINTS) == 14

    def test_each_endpoint_has_credential(self):
        for ep in DEFAULT_MANAGED_ENDPOINTS:
            assert ep.backend_credential.startswith("GLUDD_"), (
                f"{ep.host} missing GLUDD_ prefix on {ep.backend_credential}"
            )

    def test_each_endpoint_has_strip_headers(self):
        for ep in DEFAULT_MANAGED_ENDPOINTS:
            assert len(ep.strip_headers) > 0, f"{ep.host} has no strip_headers"

    def test_each_endpoint_has_strip_body_keys(self):
        for ep in DEFAULT_MANAGED_ENDPOINTS:
            assert len(ep.strip_body_keys) > 0, f"{ep.host} has no strip_body_keys"


class TestCredentialProxyResolveBackendCredential:
    def test_resolve_exact_match(self):
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-test-key"}.get)
        result = proxy.resolve_backend_credential("api.openai.com")
        assert result == "sk-test-key"

    def test_resolve_wildcard_match(self):
        proxy = CredentialProxy(resolver={"GLUDD_DEEPSEEK_API_KEY": "sk-deep-key"}.get)
        result = proxy.resolve_backend_credential("us.deepseek.com")
        assert result == "sk-deep-key"

    def test_resolve_no_match(self):
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-test-key"}.get)
        result = proxy.resolve_backend_credential("unknown.host.com")
        assert result is None

    def test_resolve_credential_not_set(self):
        proxy = CredentialProxy(resolver={}.get)
        result = proxy.resolve_backend_credential("api.openai.com")
        assert result is None

    def test_resolve_case_insensitive_host(self):
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-test-key"}.get)
        result = proxy.resolve_backend_credential("API.OPENAI.COM")
        assert result == "sk-test-key"

    def test_custom_resolver_function(self):
        proxy = CredentialProxy(resolver=lambda name: "resolved-value" if "OPENAI" in name else None)
        assert proxy.resolve_backend_credential("api.openai.com") == "resolved-value"

    def test_default_resolver_uses_os_environ(self):
        with patch.dict(os.environ, {"GLUDD_OPENAI_API_KEY": "env-key"}, clear=True):
            proxy = CredentialProxy()
            assert proxy.resolve_backend_credential("api.openai.com") == "env-key"


class TestCredentialProxyScanAndStrip:
    def _openai_endpoint(self):
        return ManagedEndpoint(
            host="api.openai.com",
            backend_credential="GLUDD_OPENAI_API_KEY",
            strip_headers=["Authorization", "x-api-key", "api-key"],
            strip_body_keys=["api_key"],
        )

    def _mock_audit(self):
        return MagicMock(spec=PlaybookAuditLogger)

    def test_strips_authorization_header(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        result = proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake", "Content-Type": "application/json"},
                "body": {"model": "gpt-4"},
            }
        )
        assert result.stripped is True
        assert result.stripped_headers == ["Authorization"]
        assert result.task_args["headers"] == {"Content-Type": "application/json"}

    def test_strips_multiple_headers(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        result = proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {
                    "Authorization": "Bearer sk-fake",
                    "X-Api-Key": "sk-fake-key",
                    "Content-Type": "application/json",
                },
            }
        )
        assert sorted(result.stripped_headers) == sorted(["Authorization", "X-Api-Key"])
        assert result.task_args["headers"] == {"Content-Type": "application/json"}

    def test_header_case_insensitive_strip(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        result = proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"authorization": "Bearer sk-lower", "CONTENT-TYPE": "json"},
            }
        )
        assert result.stripped_headers == ["authorization"]
        assert result.task_args["headers"] == {"CONTENT-TYPE": "json"}

    def test_strips_json_headers_string(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        headers_json = json.dumps(
            {
                "Authorization": "Bearer sk-fake",
                "Content-Type": "application/json",
            }
        )
        result = proxy.scan_and_strip({"url": "https://api.openai.com/v1/chat", "headers": headers_json})
        assert result.stripped_headers == ["Authorization"]
        stripped = result.task_args["headers"]
        assert "Authorization" not in stripped
        assert stripped["Content-Type"] == "application/json"

    def test_ignores_invalid_json_header_string(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        result = proxy.scan_and_strip({"url": "https://api.openai.com/v1/chat", "headers": "not-json"})
        assert result.stripped is False

    def test_strips_body_keys(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        result = proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "body": {"api_key": "sk-fake-body", "model": "gpt-4"},
            }
        )
        assert result.stripped_body_keys == ["api_key"]
        assert result.task_args["body"] == {"model": "gpt-4"}

    def test_strips_json_body_string(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        body_json = json.dumps({"api_key": "sk-fake", "model": "gpt-4"})
        result = proxy.scan_and_strip({"url": "https://api.openai.com/v1/chat", "body": body_json})
        assert result.stripped_body_keys == ["api_key"]
        body_stripped = result.task_args["body"]
        assert "api_key" not in body_stripped
        assert body_stripped["model"] == "gpt-4"

    def test_ignores_invalid_json_body_string(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        result = proxy.scan_and_strip({"url": "https://api.openai.com/v1/chat", "body": "not-json"})
        assert result.stripped is False

    def test_no_url_returns_not_stripped(self):
        proxy = CredentialProxy(endpoints=[self._openai_endpoint()])
        result = proxy.scan_and_strip({"body": {"api_key": "fake"}})
        assert result.stripped is False

    def test_non_matching_host_not_stripped(self):
        proxy = CredentialProxy(endpoints=[self._openai_endpoint()])
        result = proxy.scan_and_strip(
            {
                "url": "https://other.example.com/api",
                "headers": {"Authorization": "Bearer token"},
            }
        )
        assert result.stripped is False

    def test_violation_when_stripped_but_no_backend_key(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={}.get,
        )
        result = proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake"},
            }
        )
        assert result.stripped is True
        assert len(result.violations) == 1
        assert "backend credential 'GLUDD_OPENAI_API_KEY' is not set" in result.violations[0]

    def test_no_violation_when_backend_key_resolved(self):
        proxy = CredentialProxy(
            endpoints=[self._openai_endpoint()],
            resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get,
        )
        result = proxy.scan_and_strip(
            {"url": "https://api.openai.com/v1/chat", "headers": {"Authorization": "Bearer sk-fake"}}
        )
        assert result.stripped is True
        assert result.violations == []

    def test_audit_called_on_header_strip(self):
        audit = self._mock_audit()
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake"},
            },
            audit=audit,
            module="uri",
        )
        audit.credential_access.assert_any_call(module="uri", secret_name="Authorization")
        audit.credential_access.assert_any_call(module="uri", secret_name="GLUDD_OPENAI_API_KEY")

    def test_audit_called_on_body_strip(self):
        audit = self._mock_audit()
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "body": {"api_key": "sk-body"},
            },
            audit=audit,
        )
        audit.credential_access.assert_any_call(module="uri", secret_name="api_key")

    def test_audit_called_on_json_header_strip(self):
        audit = self._mock_audit()
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        headers_json = json.dumps({"Authorization": "Bearer sk-fake"})
        proxy.scan_and_strip(
            {"url": "https://api.openai.com/v1/chat", "headers": headers_json},
            audit=audit,
        )
        audit.credential_access.assert_any_call(module="uri", secret_name="Authorization")

    def test_audit_called_on_json_body_strip(self):
        audit = self._mock_audit()
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        body_json = json.dumps({"api_key": "sk-body-fake"})
        proxy.scan_and_strip(
            {"url": "https://api.openai.com/v1/chat", "body": body_json},
            audit=audit,
        )
        audit.credential_access.assert_any_call(module="uri", secret_name="api_key")

    def test_backend_credential_resolved_flag_false_when_not_set(self):
        proxy = CredentialProxy(resolver={}.get)
        result = proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake"},
            }
        )
        assert result.backend_credential_resolved is False

    def test_backend_credential_resolved_flag_true(self):
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        result = proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake"},
            }
        )
        assert result.backend_credential_resolved is True

    def test_does_not_modify_original_task_args(self):
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        original = {
            "url": "https://api.openai.com/v1/chat",
            "headers": {"Authorization": "Bearer sk-fake", "Content-Type": "json"},
        }
        result = proxy.scan_and_strip(original)
        assert original["headers"]["Authorization"] == "Bearer sk-fake"
        assert "Authorization" not in result.task_args["headers"]

    def test_wildcard_endpoint_match(self):
        proxy = CredentialProxy(resolver={"GLUDD_DEEPSEEK_API_KEY": "sk-deep"}.get)
        result = proxy.scan_and_strip(
            {
                "url": "https://us.deepseek.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake"},
            }
        )
        assert result.stripped is True

    def test_uri_module_label(self):
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        audit = self._mock_audit()
        proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake"},
            },
            audit=audit,
            module="uri",
        )
        audit.credential_access.assert_any_call(module="uri", secret_name="Authorization")

    def test_get_url_module_label(self):
        proxy = CredentialProxy(resolver={"GLUDD_OPENAI_API_KEY": "sk-real"}.get)
        audit = self._mock_audit()
        proxy.scan_and_strip(
            {
                "url": "https://api.openai.com/v1/chat",
                "headers": {"Authorization": "Bearer sk-fake"},
            },
            audit=audit,
            module="get_url",
        )
        audit.credential_access.assert_any_call(module="get_url", secret_name="Authorization")


class TestCredentialProxyMatchEndpoint:
    def test_exact_match(self):
        proxy = CredentialProxy(
            endpoints=[
                ManagedEndpoint(
                    host="api.openai.com",
                    backend_credential="GLUDD_KEY",
                    strip_headers=["Authorization"],
                    strip_body_keys=["api_key"],
                )
            ]
        )
        ep = proxy._match_endpoint("api.openai.com")
        assert ep is not None
        assert ep.host == "api.openai.com"

    def test_no_match(self):
        proxy = CredentialProxy(
            endpoints=[
                ManagedEndpoint(
                    host="api.openai.com",
                    backend_credential="GLUDD_KEY",
                    strip_headers=["Authorization"],
                    strip_body_keys=["api_key"],
                )
            ]
        )
        assert proxy._match_endpoint("unknown.com") is None

    def test_wildcard_match(self):
        proxy = CredentialProxy()
        ep = proxy._match_endpoint("us.deepseek.com")
        assert ep is not None
        assert ep.host == "*.deepseek.com"

    def test_wildcard_subdomain_match(self):
        proxy = CredentialProxy()
        ep = proxy._match_endpoint("some-service.googleapis.com")
        assert ep is not None
        assert ep.host == "*.googleapis.com"


class TestCollect:
    def test_injection_from_resolved_endpoint(self):
        ep = ManagedEndpoint(
            host="api.openai.com",
            backend_credential="GLUDD_OPENAI_API_KEY",
            strip_headers=["Authorization"],
            strip_body_keys=["api_key"],
        )
        result = ScanResult(
            stripped=True,
            task_args={},
            matched_endpoint=ep,
            backend_credential_resolved=True,
            violations=[],
        )
        injections: list[CredentialInjection] = []
        violations: list[CredentialViolation] = []
        _collect(result, injections, violations)
        assert len(injections) == 1
        assert injections[0].env_var == "GLUDD_OPENAI_API_KEY"
        assert injections[0].host == "api.openai.com"
        assert violations == []

    def test_no_injection_when_backend_not_resolved(self):
        ep = ManagedEndpoint(
            host="api.openai.com",
            backend_credential="GLUDD_OPENAI_API_KEY",
            strip_headers=["Authorization"],
            strip_body_keys=["api_key"],
        )
        result = ScanResult(
            stripped=True,
            task_args={},
            matched_endpoint=ep,
            backend_credential_resolved=False,
            violations=["backend credential not set"],
        )
        injections: list[CredentialInjection] = []
        violations: list[CredentialViolation] = []
        _collect(result, injections, violations)
        assert injections == []
        assert len(violations) == 1
        assert violations[0].host == "api.openai.com"
        assert violations[0].message == "backend credential not set"

    def test_no_matched_endpoint_no_injection(self):
        result = ScanResult(
            stripped=False,
            task_args={},
            matched_endpoint=None,
            backend_credential_resolved=False,
            violations=[],
        )
        injections: list[CredentialInjection] = []
        violations: list[CredentialViolation] = []
        _collect(result, injections, violations)
        assert injections == []
        assert violations == []

    def test_violation_host_fallback(self):
        result = ScanResult(
            stripped=True,
            task_args={},
            matched_endpoint=None,
            backend_credential_resolved=False,
            violations=["caller credentials detected but backend key not set"],
        )
        injections: list[CredentialInjection] = []
        violations: list[CredentialViolation] = []
        _collect(result, injections, violations)
        assert len(violations) == 1
        assert violations[0].host == "?"


class TestScanPlaybookForCredentials:
    def _make_proxy(self):
        resolver = {"GLUDD_OPENAI_API_KEY": "sk-real", "GLUDD_ANTHROPIC_API_KEY": "sk-ant-real"}.get
        return CredentialProxy(resolver=resolver)

    def test_uri_task_yields_injection(self):
        playbook = [
            {
                "hosts": "localhost",
                "tasks": [
                    {
                        "name": "call openai",
                        "ansible.builtin.uri": {
                            "url": "https://api.openai.com/v1/chat",
                            "headers": {"Authorization": "Bearer sk-fake"},
                        },
                    }
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, _violations = scan_playbook_for_credentials(f.name, self._make_proxy(), audit)
                assert len(injections) == 1
                assert injections[0].env_var == "GLUDD_OPENAI_API_KEY"
                assert _violations == []
            finally:
                os.unlink(f.name)

    def test_get_url_task_yields_injection(self):
        playbook = [
            {
                "hosts": "localhost",
                "tasks": [
                    {
                        "name": "download model",
                        "ansible.builtin.get_url": {
                            "url": "https://api.openai.com/v1/files",
                            "headers": {"Authorization": "Bearer sk-fake"},
                        },
                    }
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, _violations = scan_playbook_for_credentials(f.name, self._make_proxy(), audit)
                assert len(injections) == 1
                assert injections[0].env_var == "GLUDD_OPENAI_API_KEY"
            finally:
                os.unlink(f.name)

    def test_multiple_endpoint_injections(self):
        playbook = [
            {
                "hosts": "localhost",
                "tasks": [
                    {
                        "name": "openai",
                        "ansible.builtin.uri": {
                            "url": "https://api.openai.com/v1/chat",
                            "headers": {"Authorization": "Bearer sk-fake"},
                        },
                    },
                    {
                        "name": "anthropic",
                        "ansible.builtin.uri": {
                            "url": "https://api.anthropic.com/v1/messages",
                            "headers": {"x-api-key": "sk-ant-fake"},
                        },
                    },
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, _violations = scan_playbook_for_credentials(f.name, self._make_proxy(), audit)
                assert len(injections) == 2
                env_vars = {i.env_var for i in injections}
                assert "GLUDD_OPENAI_API_KEY" in env_vars
                assert "GLUDD_ANTHROPIC_API_KEY" in env_vars
            finally:
                os.unlink(f.name)

    def test_missing_file_returns_empty(self):
        audit = MagicMock(spec=PlaybookAuditLogger)
        injections, violations = scan_playbook_for_credentials("/nonexistent/path.yml", self._make_proxy(), audit)
        assert injections == []
        assert violations == []

    def test_empty_playbook_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("[]")
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, violations = scan_playbook_for_credentials(f.name, self._make_proxy(), audit)
                assert injections == []
                assert violations == []
            finally:
                os.unlink(f.name)

    def test_play_with_non_dict_entry_skipped(self):
        playbook = [
            "not-a-dict",
            {
                "hosts": "localhost",
                "tasks": [
                    {
                        "name": "call",
                        "ansible.builtin.uri": {
                            "url": "https://api.openai.com/v1/chat",
                            "headers": {"Authorization": "Bearer sk-fake"},
                        },
                    }
                ],
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, _violations = scan_playbook_for_credentials(f.name, self._make_proxy(), audit)
                assert len(injections) == 1
            finally:
                os.unlink(f.name)

    def test_play_without_tasks_skipped(self):
        playbook = [{"hosts": "localhost"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, violations = scan_playbook_for_credentials(f.name, self._make_proxy(), audit)
                assert injections == []
                assert violations == []
            finally:
                os.unlink(f.name)

    def test_non_matching_host_no_injection(self):
        playbook = [
            {
                "hosts": "localhost",
                "tasks": [
                    {
                        "name": "call internal api",
                        "ansible.builtin.uri": {
                            "url": "https://internal.corp.example.com/api",
                            "headers": {"Authorization": "Bearer token"},
                        },
                    }
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, violations = scan_playbook_for_credentials(f.name, self._make_proxy(), audit)
                assert injections == []
                assert violations == []
            finally:
                os.unlink(f.name)

    def test_violation_when_no_backend_key(self):
        proxy = CredentialProxy(resolver={}.get)
        playbook = [
            {
                "hosts": "localhost",
                "tasks": [
                    {
                        "name": "call openai",
                        "ansible.builtin.uri": {
                            "url": "https://api.openai.com/v1/chat",
                            "headers": {"Authorization": "Bearer sk-fake"},
                        },
                    }
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            try:
                audit = MagicMock(spec=PlaybookAuditLogger)
                injections, violations = scan_playbook_for_credentials(f.name, proxy, audit)
                assert injections == []
                assert len(violations) == 1
            finally:
                os.unlink(f.name)
