"""Unit tests for sandbox network policy."""

from __future__ import annotations

from general_ludd.sandbox.network_policy import NetworkPolicy


class TestNetworkPolicy:
    def test_default_fully_isolated(self) -> None:
        policy = NetworkPolicy()
        assert policy.allow_outbound is False
        assert policy.allow_inbound is False
        assert policy.is_isolated()

    def test_fully_isolated_classmethod(self) -> None:
        policy = NetworkPolicy.fully_isolated()
        assert policy.is_isolated()

    def test_allow_localhost_classmethod(self) -> None:
        policy = NetworkPolicy.allow_localhost()
        assert "127.0.0.1" in policy.allowed_hosts
        assert "::1" in policy.allowed_hosts
        assert policy.allow_outbound is True

    def test_allows_host_with_allowed_list(self) -> None:
        policy = NetworkPolicy(allowed_hosts=["example.com", "api.example.com"])
        assert policy.allows_host("example.com")
        assert policy.allows_host("api.example.com")

    def test_allows_host_not_in_list(self) -> None:
        policy = NetworkPolicy(allowed_hosts=["example.com"])
        assert policy.allows_host("other.com") is False

    def test_allows_host_blocklist(self) -> None:
        policy = NetworkPolicy(blocked_hosts=["evil.com"], allow_outbound=True)
        assert policy.allows_host("evil.com") is False
        assert policy.allows_host("safe.com") is True

    def test_allows_host_empty_allowlist_with_outbound(self) -> None:
        policy = NetworkPolicy(allow_outbound=True)
        assert policy.allows_host("any-host.com") is True

    def test_allows_host_empty_allowlist_no_outbound(self) -> None:
        policy = NetworkPolicy(allow_outbound=False)
        assert policy.allows_host("any-host.com") is False

    def test_allows_port(self) -> None:
        policy = NetworkPolicy(allowed_ports=[80, 443])
        assert policy.allows_port(80)
        assert policy.allows_port(443)
        assert policy.allows_port(8080) is False

    def test_allows_port_blocked(self) -> None:
        policy = NetworkPolicy(allowed_ports=[80, 443], blocked_ports=[443])
        assert policy.allows_port(443) is False
        assert policy.allows_port(80) is True

    def test_to_docker_args_isolated(self) -> None:
        policy = NetworkPolicy()
        args = policy.to_docker_args()
        assert "--network" in args
        assert "none" in args

    def test_to_docker_args_with_dns(self) -> None:
        policy = NetworkPolicy(dns_servers=["8.8.8.8", "1.1.1.1"], allow_outbound=True)
        args = policy.to_docker_args()
        assert "--dns" in args
        assert "8.8.8.8" in args
        assert "1.1.1.1" in args

    def test_to_docker_args_outbound_without_network_none(self) -> None:
        policy = NetworkPolicy(allow_outbound=True)
        args = policy.to_docker_args()
        assert "--network" not in args

    def test_to_kubernetes_policy(self) -> None:
        policy = NetworkPolicy(allowed_hosts=["10.0.0.0/8"], allow_outbound=True)
        k8s_policy = policy.to_kubernetes_policy("default", {"app": "test"})
        assert k8s_policy["kind"] == "NetworkPolicy"
        assert k8s_policy["spec"]["podSelector"]["matchLabels"] == {"app": "test"}
        assert k8s_policy["metadata"]["namespace"] == "default"

    def test_to_kubernetes_policy_ingress(self) -> None:
        policy = NetworkPolicy(allow_inbound=True)
        k8s_policy = policy.to_kubernetes_policy("ns", {"app": "x"})
        assert k8s_policy["spec"]["ingress"] is not None

    def test_is_isolated_mixed(self) -> None:
        policy = NetworkPolicy(allow_outbound=True, allow_inbound=False)
        assert policy.is_isolated() is False

    def test_proxy_field(self) -> None:
        policy = NetworkPolicy(proxy="http://proxy:8080")
        assert policy.proxy == "http://proxy:8080"

    def test_policy_types_outbound_only(self) -> None:
        policy = NetworkPolicy(allow_outbound=True)
        types = policy._policy_types()
        assert "Egress" in types
        assert "Ingress" not in types

    def test_policy_types_both(self) -> None:
        policy = NetworkPolicy(allow_outbound=True, allow_inbound=True)
        types = policy._policy_types()
        assert "Egress" in types
        assert "Ingress" in types

    def test_policy_types_isolated(self) -> None:
        policy = NetworkPolicy(allow_outbound=False, allow_inbound=False)
        types = policy._policy_types()
        assert "Egress" in types
