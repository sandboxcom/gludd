"""Prefix-trie routing table with longest-prefix match and ECMP.

Core types:
  RouteEntry  — prefix, next hops, metric, metadata
  TrieNode    — internal trie node with optional route
  RoutingTable — insert, lookup (LPM), ECMP path selection
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RouteEntry:
    prefix: ipaddress.IPv4Network | ipaddress.IPv6Network
    next_hops: list[str] = field(default_factory=list)
    metric: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.metric < 1:
            raise ValueError(f"metric must be >= 1, got {self.metric}")

    @property
    def ecmp_weight(self) -> int:
        return len(self.next_hops)

    @property
    def prefix_len(self) -> int:
        return self.prefix.prefixlen


class TrieNode:
    __slots__ = ("left", "right", "route")

    def __init__(self) -> None:
        self.route: RouteEntry | None = None
        self.left: TrieNode | None = None
        self.right: TrieNode | None = None


def _pack_addr(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> int:
    return int(addr)


def _bit_at(value: int, position: int, max_bits: int) -> bool:
    return bool((value >> (max_bits - 1 - position)) & 1)


class RoutingTable:
    """Prefix-trie routing table with longest-prefix match and ECMP."""

    _ADDR_BITS: int = 32
    _DEFAULT_ROUTE: str = "0.0.0.0/0"

    def __init__(self) -> None:
        self._root = TrieNode()
        self._route_count: int = 0

    def insert(self, entry: RouteEntry) -> None:
        node = self._root
        addr_int = _pack_addr(entry.prefix.network_address)
        max_bits = entry.prefix.max_prefixlen

        for pos in range(entry.prefix_len):
            bit = _bit_at(addr_int, pos, max_bits)
            if bit:
                if node.right is None:
                    node.right = TrieNode()
                node = node.right
            else:
                if node.left is None:
                    node.left = TrieNode()
                node = node.left

        node.route = entry
        self._route_count += 1

    def lookup(self, addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> RouteEntry | None:
        addr_int = _pack_addr(addr)
        max_bits = addr.max_prefixlen
        node = self._root
        best: RouteEntry | None = None

        for pos in range(max_bits):
            if node.route is not None:
                best = node.route
            bit = _bit_at(addr_int, pos, max_bits)
            if bit:
                if node.right is None:
                    break
                node = node.right
            else:
                if node.left is None:
                    break
                node = node.left

        if node.route is not None:
            best = node.route

        return best

    def ecmp_paths(self, addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[str]:
        entry = self.lookup(addr)
        if entry is None:
            return []
        return list(entry.next_hops)

    def ecmp_weight(self, addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> int:
        entry = self.lookup(addr)
        if entry is None:
            return 0
        return entry.ecmp_weight

    def remove(self, prefix: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
        addr_int = _pack_addr(prefix.network_address)
        max_bits = prefix.max_prefixlen
        node = self._root

        for pos in range(prefix.prefixlen):
            bit = _bit_at(addr_int, pos, max_bits)
            if bit:
                if node.right is None:
                    return False
                node = node.right
            else:
                if node.left is None:
                    return False
                node = node.left

        if node.route is not None and node.route.prefix == prefix:
            node.route = None
            self._route_count -= 1
            return True
        return False

    def all_prefixes(self) -> list[RouteEntry]:
        entries: list[RouteEntry] = []

        def _collect(n: TrieNode) -> None:
            if n.route is not None:
                entries.append(n.route)
            if n.left is not None:
                _collect(n.left)
            if n.right is not None:
                _collect(n.right)

        _collect(self._root)
        return entries

    @property
    def route_count(self) -> int:
        return self._route_count

    @property
    def empty(self) -> bool:
        return self._route_count == 0

    def __len__(self) -> int:
        return self._route_count

    def __contains__(self, prefix: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
        return self.lookup(prefix.network_address) is not None and any(
            r.prefix == prefix for r in self._matching_entries(prefix.network_address)
        )

    def _matching_entries(self, addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> Iterator[RouteEntry]:
        addr_int = _pack_addr(addr)
        max_bits = addr.max_prefixlen
        node = self._root

        for pos in range(max_bits):
            if node.route is not None:
                yield node.route
            bit = _bit_at(addr_int, pos, max_bits)
            if bit:
                if node.right is None:
                    break
                node = node.right
            else:
                if node.left is None:
                    break
                node = node.left

        if node.route is not None:
            yield node.route
