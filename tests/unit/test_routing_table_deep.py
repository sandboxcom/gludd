"""Deep routing table tests: prefix trie, longest prefix match, ECMP."""

from __future__ import annotations

import ipaddress

import pytest

from general_ludd.network.routing_table import (
    RouteEntry,
    RoutingTable,
    TrieNode,
    _bit_at,
    _pack_addr,
)


def _v4(addr: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(addr)


def _v4net(net: str) -> ipaddress.IPv4Network:
    return ipaddress.IPv4Network(net)


# ── TrieNode ──────────────────────────────────────────────────────────────────


class TestTrieNode:
    def test_new_node_has_none_route(self):
        node = TrieNode()
        assert node.route is None

    def test_new_node_has_none_children(self):
        node = TrieNode()
        assert node.left is None
        assert node.right is None

    def test_left_child_mutable(self):
        node = TrieNode()
        child = TrieNode()
        node.left = child
        assert node.left is child

    def test_right_child_mutable(self):
        node = TrieNode()
        child = TrieNode()
        node.right = child
        assert node.right is child


# ── RouteEntry ────────────────────────────────────────────────────────────────


class TestRouteEntry:
    def test_defaults(self):
        entry = RouteEntry(prefix=_v4net("10.0.0.0/8"))
        assert entry.next_hops == []
        assert entry.metric == 1
        assert entry.metadata == {}

    def test_ecmp_weight_zero_hops(self):
        entry = RouteEntry(prefix=_v4net("10.0.0.0/8"))
        assert entry.ecmp_weight == 0

    def test_ecmp_weight_single_hop(self):
        entry = RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["192.168.1.1"])
        assert entry.ecmp_weight == 1

    def test_ecmp_weight_multi_hop(self):
        entry = RouteEntry(
            prefix=_v4net("10.0.0.0/8"),
            next_hops=["192.168.1.1", "192.168.1.2", "10.0.0.1"],
        )
        assert entry.ecmp_weight == 3

    def test_metric_must_be_positive(self):
        with pytest.raises(ValueError, match="metric must be >= 1"):
            RouteEntry(prefix=_v4net("10.0.0.0/8"), metric=0)

    def test_prefix_len(self):
        entry = RouteEntry(prefix=_v4net("10.0.0.0/8"))
        assert entry.prefix_len == 8


# ── Helper functions ──────────────────────────────────────────────────────────


class TestPackAddr:
    def test_v4_loopback(self):
        assert _pack_addr(ipaddress.IPv4Address("127.0.0.1")) == 2130706433

    def test_v4_zero(self):
        assert _pack_addr(ipaddress.IPv4Address("0.0.0.0")) == 0

    def test_v6_link_local(self):
        addr = ipaddress.IPv6Address("fe80::1")
        assert _pack_addr(addr) == int(addr)


class TestBitAt:
    def test_msb_of_128_is_1(self):
        assert _bit_at(128, 0, 8) is True

    def test_bit_1_of_128_is_0(self):
        assert _bit_at(128, 1, 8) is False

    def test_lsb(self):
        assert _bit_at(1, 31, 32) is True

    def test_zero_bit(self):
        assert _bit_at(0, 30, 32) is False


# ── RoutingTable: insert and basic properties ─────────────────────────────────


class TestRoutingTableInsert:
    def test_empty_table(self):
        table = RoutingTable()
        assert table.empty is True
        assert len(table) == 0

    def test_insert_increments_count(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert len(table) == 1
        assert table.empty is False

    def test_insert_multiple(self):
        table = RoutingTable()
        for net in ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]:
            table.insert(RouteEntry(prefix=_v4net(net), next_hops=[str(net)]))
        assert len(table) == 3

    def test_route_count_property(self):
        table = RoutingTable()
        assert table.route_count == 0
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert table.route_count == 1


# ── RoutingTable: lookup / LPM ────────────────────────────────────────────────


class TestRoutingTableLookup:
    def test_empty_table_returns_none(self):
        table = RoutingTable()
        assert table.lookup(_v4("192.168.1.1")) is None

    def test_exact_match(self):
        table = RoutingTable()
        entry = RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw1"])
        table.insert(entry)
        assert table.lookup(_v4("10.0.0.1")) is entry

    def test_longest_prefix_match_over_default(self):
        table = RoutingTable()
        default = RouteEntry(prefix=_v4net("0.0.0.0/0"), next_hops=["gw-default"])
        specific = RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw-specific"])
        table.insert(default)
        table.insert(specific)
        result = table.lookup(_v4("10.1.2.3"))
        assert result is specific

    def test_default_route_matches_any(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("0.0.0.0/0"), next_hops=["gw"]))
        assert table.lookup(_v4("8.8.8.8")) is not None
        assert table.lookup(_v4("1.1.1.1")) is not None

    def test_lpm_more_specific_wins(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw8"]))
        table.insert(RouteEntry(prefix=_v4net("10.1.0.0/16"), next_hops=["gw16"]))
        table.insert(RouteEntry(prefix=_v4net("10.1.2.0/24"), next_hops=["gw24"]))
        result = table.lookup(_v4("10.1.2.3"))
        assert result is not None
        assert result.next_hops == ["gw24"]

    def test_no_match_returns_none(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("192.168.0.0/16"), next_hops=["gw"]))
        assert table.lookup(_v4("10.0.0.1")) is None


# ── RoutingTable: ECMP ────────────────────────────────────────────────────────


class TestRoutingTableEcmp:
    def test_ecmp_paths_empty_no_match(self):
        table = RoutingTable()
        assert table.ecmp_paths(_v4("10.0.0.1")) == []

    def test_ecmp_paths_single_hop(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw1"]))
        assert table.ecmp_paths(_v4("10.0.0.1")) == ["gw1"]

    def test_ecmp_paths_multi_hop(self):
        table = RoutingTable()
        table.insert(
            RouteEntry(
                prefix=_v4net("10.0.0.0/8"),
                next_hops=["gw1", "gw2", "gw3"],
            )
        )
        paths = table.ecmp_paths(_v4("10.0.0.1"))
        assert paths == ["gw1", "gw2", "gw3"]

    def test_ecmp_weight_zero_no_match(self):
        table = RoutingTable()
        assert table.ecmp_weight(_v4("10.0.0.1")) == 0

    def test_ecmp_weight_multi(self):
        table = RoutingTable()
        table.insert(
            RouteEntry(
                prefix=_v4net("10.0.0.0/8"),
                next_hops=["gw1", "gw2"],
            )
        )
        assert table.ecmp_weight(_v4("10.0.0.88")) == 2


# ── RoutingTable: remove ──────────────────────────────────────────────────────


class TestRoutingTableRemove:
    def test_remove_existing(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert table.remove(_v4net("10.0.0.0/8")) is True
        assert table.empty

    def test_remove_nonexistent(self):
        table = RoutingTable()
        assert table.remove(_v4net("10.0.0.0/8")) is False

    def test_remove_leaves_other_routes(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        table.insert(RouteEntry(prefix=_v4net("192.168.0.0/16")))
        table.remove(_v4net("10.0.0.0/8"))
        assert len(table) == 1
        assert table.lookup(_v4("192.168.1.1")) is not None


# ── RoutingTable: all_prefixes / iteration ────────────────────────────────────


class TestRoutingTableAllPrefixes:
    def test_empty(self):
        table = RoutingTable()
        assert table.all_prefixes() == []

    def test_single(self):
        table = RoutingTable()
        entry = RouteEntry(prefix=_v4net("10.0.0.0/8"))
        table.insert(entry)
        assert table.all_prefixes() == [entry]

    def test_multiple(self):
        table = RoutingTable()
        entries = [
            RouteEntry(prefix=_v4net("10.0.0.0/8")),
            RouteEntry(prefix=_v4net("192.168.0.0/16")),
            RouteEntry(prefix=_v4net("172.16.0.0/12")),
        ]
        for e in entries:
            table.insert(e)
        result = table.all_prefixes()
        assert len(result) == 3
        prefixes = {str(r.prefix) for r in result}
        assert prefixes == {"10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"}


# ── RoutingTable: IPv6 ────────────────────────────────────────────────────────


class TestRoutingTableIPv6:
    def test_insert_and_lookup_ipv6(self):
        table = RoutingTable()
        net = ipaddress.IPv6Network("2001:db8::/32")
        entry = RouteEntry(prefix=net, next_hops=["gw-v6"])
        table.insert(entry)
        result = table.lookup(ipaddress.IPv6Address("2001:db8::1"))
        assert result is entry

    def test_lpm_ipv6(self):
        table = RoutingTable()
        table.insert(
            RouteEntry(
                prefix=ipaddress.IPv6Network("2001:db8::/32"),
                next_hops=["gw32"],
            )
        )
        table.insert(
            RouteEntry(
                prefix=ipaddress.IPv6Network("2001:db8:abcd::/48"),
                next_hops=["gw48"],
            )
        )
        result = table.lookup(ipaddress.IPv6Address("2001:db8:abcd::1"))
        assert result is not None
        assert result.next_hops == ["gw48"]


# ── RoutingTable: metric metadata ─────────────────────────────────────────────


class TestRoutingTableMetadata:
    def test_metadata_preserved(self):
        table = RoutingTable()
        entry = RouteEntry(
            prefix=_v4net("10.0.0.0/8"),
            next_hops=["gw"],
            metadata={"admin_distance": 110, "tag": 42},
        )
        table.insert(entry)
        result = table.lookup(_v4("10.1.1.1"))
        assert result is not None
        assert result.metadata == {"admin_distance": 110, "tag": 42}
