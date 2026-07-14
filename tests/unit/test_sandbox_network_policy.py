"""Tests for sandbox network policy."""

from __future__ import annotations

from general_ludd.sandbox.network_policy import NetworkPolicy


def test_fully_isolated() -> None:
    policy = NetworkPolicy.fully_isolated()
    assert policy.is_isolated()
    assert not policy.allows_host("example.com")
    assert not policy.allows_port(80)


def test_allow_outbound_allows_host() -> None:
    policy = NetworkPolicy(allow_outbound=True)
    assert policy.allows_host("example.com")
    assert policy.allows_port(443)


def test_allowed_hosts_restricts() -> None:
    policy = NetworkPolicy(allowed_hosts=["api.github.com"], allow_outbound=True)
    assert policy.allows_host("api.github.com")
    assert not policy.allows_host("evil.com")


def test_blocked_hosts_override() -> None:
    policy = NetworkPolicy(
        allowed_hosts=["api.github.com", "evil.com"],
        blocked_hosts=["evil.com"],
        allow_outbound=True,
    )
    assert policy.allows_host("api.github.com")
    assert not policy.allows_host("evil.com")


def test_allowed_ports_restricts() -> None:
    policy = NetworkPolicy(allowed_ports=[80, 443], allow_outbound=True)
    assert policy.allows_port(80)
    assert not policy.allows_port(22)


def test_blocked_ports_override() -> None:
    policy = NetworkPolicy(
        allowed_ports=[80, 443, 22],
        blocked_ports=[22],
        allow_outbound=True,
    )
    assert policy.allows_port(80)
    assert not policy.allows_port(22)


def test_docker_network_none_when_isolated() -> None:
    policy = NetworkPolicy.fully_isolated()
    args = policy.to_docker_args()
    assert "--network" in args
    assert "none" in args


def test_docker_dns_servers() -> None:
    policy = NetworkPolicy(dns_servers=["8.8.8.8", "1.1.1.1"])
    args = policy.to_docker_args()
    assert "--dns" in args
    assert "8.8.8.8" in args
    assert "1.1.1.1" in args


def test_allow_localhost() -> None:
    policy = NetworkPolicy.allow_localhost()
    assert policy.allows_host("127.0.0.1")
    assert policy.allows_host("::1")
    assert policy.allow_outbound


def test_kubernetes_policy_structure() -> None:
    policy = NetworkPolicy.allow_localhost()
    k8s = policy.to_kubernetes_policy("test-ns", {"app": "sandbox"})
    assert k8s["apiVersion"] == "networking.k8s.io/v1"
    assert k8s["kind"] == "NetworkPolicy"
    assert k8s["metadata"]["namespace"] == "test-ns"
    assert k8s["spec"]["podSelector"]["matchLabels"] == {"app": "sandbox"}
