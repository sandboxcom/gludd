"""Extra-deep routing table tests: __contains__, _matching_entries, remove edge cases."""

from __future__ import annotations

import ipaddress

import pytest

from general_ludd.network.routing_table import (
    RouteEntry,
    RoutingTable,
)


def _v4(addr: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(addr)


def _v4net(net: str) -> ipaddress.IPv4Network:
    return ipaddress.IPv4Network(net)


class TestRoutingTableContains:
    def test_empty_table_contains_nothing(self):
        table = RoutingTable()
        assert _v4net("10.0.0.0/8") not in table

    def test_contains_inserted_prefix(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert _v4net("10.0.0.0/8") in table

    def test_contains_different_prefix(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert _v4net("192.168.0.0/16") not in table

    def test_contains_after_remove(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        table.remove(_v4net("10.0.0.0/8"))
        assert _v4net("10.0.0.0/8") not in table

    def test_contains_with_default_route(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("0.0.0.0/0")))
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert _v4net("0.0.0.0/0") in table
        assert _v4net("10.0.0.0/8") in table
        assert _v4net("192.168.0.0/16") not in table

    def test_contains_ipv6(self):
        table = RoutingTable()
        net = ipaddress.IPv6Network("2001:db8::/32")
        table.insert(RouteEntry(prefix=net))
        assert net in table
        assert ipaddress.IPv6Network("fe80::/10") not in table

    def test_contains_multiple_prefixes(self):
        table = RoutingTable()
        prefixes = [_v4net(n) for n in ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]]
        for p in prefixes:
            table.insert(RouteEntry(prefix=p))
        for p in prefixes:
            assert p in table

    def test_contains_subnet_not_inserted_directly(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert _v4net("10.1.0.0/16") not in table


class TestRoutingTableRemoveEdgeCases:
    def test_remove_from_empty(self):
        table = RoutingTable()
        assert table.remove(_v4net("10.0.0.0/8")) is False
        assert table.empty

    def test_remove_nonexistent_prefix_same_node(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert table.remove(_v4net("10.0.0.0/16")) is False
        assert _v4net("10.0.0.0/8") in table

    def test_remove_then_reinsert(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw1"]))
        assert table.remove(_v4net("10.0.0.0/8")) is True
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw2"]))
        result = table.lookup(_v4("10.1.1.1"))
        assert result is not None
        assert result.next_hops == ["gw2"]

    def test_remove_preserves_children(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        table.insert(RouteEntry(prefix=_v4net("10.1.0.0/16")))
        assert table.remove(_v4net("10.0.0.0/8")) is True
        assert table.lookup(_v4("10.1.1.1")) is not None

    def test_remove_ipv6(self):
        table = RoutingTable()
        net = ipaddress.IPv6Network("2001:db8::/32")
        table.insert(RouteEntry(prefix=net))
        assert table.remove(net) is True
        assert table.empty

    def test_remove_ipv6_nonexistent(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=ipaddress.IPv6Network("2001:db8::/32")))
        assert table.remove(ipaddress.IPv6Network("fe80::/10")) is False

    def test_remove_all_one_by_one(self):
        table = RoutingTable()
        prefixes = [_v4net(n) for n in ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]]
        for p in prefixes:
            table.insert(RouteEntry(prefix=p))
        for p in prefixes:
            assert table.remove(p) is True
        assert table.empty
        assert table.route_count == 0

    def test_remove_single_of_many(self):
        table = RoutingTable()
        p1 = _v4net("10.0.0.0/8")
        p2 = _v4net("192.168.0.0/16")
        table.insert(RouteEntry(prefix=p1))
        table.insert(RouteEntry(prefix=p2))
        assert table.remove(p1) is True
        assert table.route_count == 1
        assert table.lookup(_v4("192.168.1.1")) is not None
        assert table.lookup(_v4("10.1.1.1")) is None


class TestRoutingTableMatchingEntries:
    def test_empty_table_no_entries(self):
        table = RoutingTable()
        assert table._matching_entries(_v4("10.0.0.1")) is not None

    def test_matching_entries_yields_routes_on_path(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw8"]))
        table.insert(RouteEntry(prefix=_v4net("10.1.0.0/16"), next_hops=["gw16"]))
        entries = list(table._matching_entries(_v4("10.1.2.3")))
        unique_prefixes = {e.prefix for e in entries}
        assert len(unique_prefixes) == 2
        hops = sorted(set(e.next_hops[0] for e in entries))
        assert hops == ["gw16", "gw8"]

    def test_matching_entries_skips_nodes_without_routes(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("192.168.0.0/16"), next_hops=["gw16"]))
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw8"]))
        entries = list(table._matching_entries(_v4("192.168.1.1")))
        unique_prefixes = {e.prefix for e in entries}
        assert len(unique_prefixes) == 1
        assert unique_prefixes.pop() == _v4net("192.168.0.0/16")


class TestRoutingTableCountAndEmpty:
    def test_count_after_remove_then_reinsert(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        table.insert(RouteEntry(prefix=_v4net("192.168.0.0/16")))
        assert table.route_count == 2
        table.remove(_v4net("10.0.0.0/8"))
        assert table.route_count == 1
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert table.route_count == 2

    def test_empty_after_clear_via_removes(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        table.insert(RouteEntry(prefix=_v4net("192.168.0.0/16")))
        table.remove(_v4net("10.0.0.0/8"))
        assert not table.empty
        table.remove(_v4net("192.168.0.0/16"))
        assert table.empty

    def test_len_operator(self):
        table = RoutingTable()
        assert len(table) == 0
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        assert len(table) == 1


class TestRoutingTableMixedV4V6:
    def test_insert_both_v4_and_v6(self):
        table = RoutingTable()
        v4 = RouteEntry(prefix=_v4net("10.0.0.0/8"), next_hops=["gw-v4"])
        v6 = RouteEntry(prefix=ipaddress.IPv6Network("2001:db8::/32"), next_hops=["gw-v6"])
        table.insert(v4)
        table.insert(v6)
        assert table.route_count == 2
        assert table.lookup(_v4("10.1.1.1")).next_hops == ["gw-v4"]
        assert table.lookup(ipaddress.IPv6Address("2001:db8::1")).next_hops == ["gw-v6"]

    def test_remove_v6_leaves_v4(self):
        table = RoutingTable()
        table.insert(RouteEntry(prefix=_v4net("10.0.0.0/8")))
        v6_net = ipaddress.IPv6Network("2001:db8::/32")
        table.insert(RouteEntry(prefix=v6_net))
        assert table.remove(v6_net) is True
        assert table.route_count == 1
        assert table.lookup(_v4("10.1.1.1")) is not None

    def test_all_prefixes_mixed(self):
        table = RoutingTable()
        v4 = RouteEntry(prefix=_v4net("10.0.0.0/8"))
        v6 = RouteEntry(prefix=ipaddress.IPv6Network("2001:db8::/32"))
        table.insert(v4)
        table.insert(v6)
        result = table.all_prefixes()
        assert len(result) == 2
        prefix_strs = {str(r.prefix) for r in result}
        assert prefix_strs == {"10.0.0.0/8", "2001:db8::/32"}


class TestRouteEntryEdgeCases:
    def test_metric_negative_raises(self):
        with pytest.raises(ValueError, match="metric must be >= 1"):
            RouteEntry(prefix=_v4net("10.0.0.0/8"), metric=-1)

    def test_metric_large(self):
        entry = RouteEntry(prefix=_v4net("10.0.0.0/8"), metric=1000)
        assert entry.metric == 1000

    def test_ipv6_prefix_len(self):
        entry = RouteEntry(prefix=ipaddress.IPv6Network("2001:db8::/32"))
        assert entry.prefix_len == 32

    def test_host_route_prefix_len(self):
        entry = RouteEntry(prefix=_v4net("192.168.1.1/32"))
        assert entry.prefix_len == 32
