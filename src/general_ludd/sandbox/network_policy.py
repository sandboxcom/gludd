"""Network policy definitions for sandbox isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NetworkPolicy:
    allowed_hosts: list[str] = field(default_factory=list)
    blocked_hosts: list[str] = field(default_factory=list)
    allowed_ports: list[int] = field(default_factory=list)
    blocked_ports: list[int] = field(default_factory=list)
    allow_outbound: bool = False
    allow_inbound: bool = False
    dns_servers: list[str] = field(default_factory=list)
    proxy: str = ""

    def allows_host(self, host: str) -> bool:
        if host in self.blocked_hosts:
            return False
        if not self.allowed_hosts:
            return self.allow_outbound
        return host in self.allowed_hosts

    def allows_port(self, port: int) -> bool:
        if port in self.blocked_ports:
            return False
        if not self.allowed_ports:
            return self.allow_outbound
        return port in self.allowed_ports

    def to_docker_args(self) -> list[str]:
        args: list[str] = []
        if not self.allow_outbound and not self.allow_inbound:
            args.extend(["--network", "none"])
        if self.dns_servers:
            for dns in self.dns_servers:
                args.extend(["--dns", dns])
        return args

    def to_kubernetes_policy(self, namespace: str, pod_selector: dict[str, str]) -> dict[str, Any]:
        egress: list[dict[str, Any]] = []
        if self.allow_outbound and self.allowed_hosts:
            for host in self.allowed_hosts:
                egress.append({"to": [{"ipBlock": {"cidr": host}}]})
        ingress: list[dict[str, Any]] = []
        if self.allow_inbound:
            ingress.append({})
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "gludd-sandbox", "namespace": namespace},
            "spec": {
                "podSelector": {"matchLabels": pod_selector},
                "policyTypes": self._policy_types(),
                "egress": egress if egress else None,
                "ingress": ingress if ingress else None,
            },
        }

    def is_isolated(self) -> bool:
        return not self.allow_outbound and not self.allow_inbound

    def _policy_types(self) -> list[str]:
        types: list[str] = []
        if self.allow_outbound or self.allowed_hosts:
            types.append("Egress")
        if self.allow_inbound:
            types.append("Ingress")
        return types or ["Egress"]

    @classmethod
    def fully_isolated(cls) -> NetworkPolicy:
        return cls()

    @classmethod
    def allow_localhost(cls) -> NetworkPolicy:
        return cls(allowed_hosts=["127.0.0.1", "::1"], allowed_ports=[], allow_outbound=True)
