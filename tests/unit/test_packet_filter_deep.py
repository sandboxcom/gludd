"""Deep BPF packet filter tests: parsing, matching, optimization."""

from __future__ import annotations

import pytest

from general_ludd.network.packet_filter import (
    BinaryNode,
    BoolNode,
    MatchNode,
    Op,
    ParseError,
    UnaryNode,
    ValueNode,
    match_bpf,
    optimize_bpf,
    parse_bpf,
)


class TestParse:
    def test_parse_protocol_literal(self) -> None:
        node = parse_bpf("tcp")
        assert isinstance(node, MatchNode)
        assert node.protocol == "tcp"

    def test_parse_host_expr(self) -> None:
        node = parse_bpf("host 192.168.1.1")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_parse_src_host(self) -> None:
        node = parse_bpf("src host 10.0.0.1")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.EQ
        assert isinstance(node.left, ValueNode)
        assert node.left.value == "src_ip"

    def test_parse_dst_port(self) -> None:
        node = parse_bpf("dst port 80")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.EQ
        assert isinstance(node.left, ValueNode)
        assert node.left.value == "dst_port"

    def test_parse_port_expr(self) -> None:
        node = parse_bpf("port 443")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_parse_net_expr(self) -> None:
        node = parse_bpf("net 10.0.0.0/8")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_parse_and_expr(self) -> None:
        node = parse_bpf("tcp and port 80")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND

    def test_parse_or_expr(self) -> None:
        node = parse_bpf("tcp or udp")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_parse_not_expr(self) -> None:
        node = parse_bpf("not tcp")
        assert isinstance(node, UnaryNode)
        assert isinstance(node.operand, MatchNode)

    def test_parse_parenthesized(self) -> None:
        node = parse_bpf("(tcp or udp)")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_parse_complex_nested(self) -> None:
        node = parse_bpf("tcp and (port 80 or port 443)")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND
        assert isinstance(node.right, BinaryNode)
        assert node.right.op == Op.OR

    def test_parse_empty_raises(self) -> None:
        with pytest.raises(ParseError, match="empty"):
            parse_bpf("")

    def test_parse_trailing_token_raises(self) -> None:
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf("tcp extra")

    def test_parse_unclosed_parens_raises(self) -> None:
        with pytest.raises(ParseError, match="unclosed parenthesis"):
            parse_bpf("(tcp and port 80")

    def test_parse_host_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after 'host'"):
            parse_bpf("host")

    def test_parse_src_host_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after"):
            parse_bpf("src host")

    def test_parse_dst_net(self) -> None:
        node = parse_bpf("dst net 192.168.0.0/24")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.EQ
        assert isinstance(node.left, ValueNode)
        assert node.left.value == "dst_net"

    def test_parse_all_protocols(self) -> None:
        for proto in ("tcp", "udp", "icmp", "arp", "ip", "ip6"):
            node = parse_bpf(proto)
            assert isinstance(node, MatchNode)
            assert node.protocol == proto


class TestMatch:
    def test_match_protocol(self) -> None:
        node = parse_bpf("tcp")
        assert match_bpf(node, {"protocol": "tcp"})
        assert not match_bpf(node, {"protocol": "udp"})

    def test_match_host_exact(self) -> None:
        node = parse_bpf("host 10.0.0.1")
        assert match_bpf(node, {"src_ip": "10.0.0.1", "dst_ip": "192.168.1.1"})
        assert match_bpf(node, {"src_ip": "192.168.1.1", "dst_ip": "10.0.0.1"})
        assert not match_bpf(node, {"src_ip": "10.0.0.2", "dst_ip": "10.0.0.3"})

    def test_match_src_host(self) -> None:
        node = parse_bpf("src host 10.0.0.1")
        assert match_bpf(node, {"src_ip": "10.0.0.1"})
        assert not match_bpf(node, {"src_ip": "10.0.0.2"})
        assert not match_bpf(node, {"dst_ip": "10.0.0.1"})

    def test_match_dst_port(self) -> None:
        node = parse_bpf("dst port 443")
        assert match_bpf(node, {"dst_port": "443"})
        assert not match_bpf(node, {"dst_port": "80"})
        assert not match_bpf(node, {"src_port": "443"})

    def test_match_port_either(self) -> None:
        node = parse_bpf("port 53")
        assert match_bpf(node, {"src_port": "53"})
        assert match_bpf(node, {"dst_port": "53"})
        assert not match_bpf(node, {"src_port": "80", "dst_port": "443"})

    def test_match_and(self) -> None:
        node = parse_bpf("tcp and dst port 80")
        assert match_bpf(node, {"protocol": "tcp", "dst_port": "80"})
        assert not match_bpf(node, {"protocol": "udp", "dst_port": "80"})
        assert not match_bpf(node, {"protocol": "tcp", "dst_port": "443"})

    def test_match_or(self) -> None:
        node = parse_bpf("tcp or udp")
        assert match_bpf(node, {"protocol": "tcp"})
        assert match_bpf(node, {"protocol": "udp"})
        assert not match_bpf(node, {"protocol": "icmp"})

    def test_match_not(self) -> None:
        node = parse_bpf("not tcp")
        assert match_bpf(node, {"protocol": "udp"})
        assert not match_bpf(node, {"protocol": "tcp"})

    def test_match_complex(self) -> None:
        node = parse_bpf("tcp and (src port 443 or dst port 443)")
        assert match_bpf(node, {"protocol": "tcp", "src_port": "443"})
        assert match_bpf(node, {"protocol": "tcp", "dst_port": "443"})
        assert not match_bpf(node, {"protocol": "udp", "src_port": "443"})
        assert not match_bpf(node, {"protocol": "tcp", "src_port": "80", "dst_port": "80"})

    def test_match_net_range(self) -> None:
        node = parse_bpf("src net 10.0.0.0/8")
        assert match_bpf(node, {"src_net": "10.0.0.0/8", "src_ip": "10.1.2.3"})
        assert not match_bpf(node, {"src_net": "10.0.0.0/8", "src_ip": "192.168.1.1"})

    def test_match_no_protocol_field(self) -> None:
        node = parse_bpf("tcp")
        assert not match_bpf(node, {})


class TestOptimize:
    def test_double_negation_eliminated(self) -> None:
        node = parse_bpf("not not tcp")
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"

    def test_true_and_node_simplified(self) -> None:
        node = BinaryNode(op=Op.AND, left=BoolNode(value=True), right=MatchNode(protocol="tcp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"

    def test_false_and_node_collapsed(self) -> None:
        node = BinaryNode(op=Op.AND, left=BoolNode(value=False), right=MatchNode(protocol="tcp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is False

    def test_true_or_node_simplified(self) -> None:
        node = BinaryNode(op=Op.OR, left=BoolNode(value=True), right=MatchNode(protocol="udp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True

    def test_false_or_node_simplified(self) -> None:
        node = BinaryNode(op=Op.OR, left=BoolNode(value=False), right=MatchNode(protocol="tcp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)

    def test_self_contradiction_and(self) -> None:
        tcp = MatchNode(protocol="tcp")
        node = BinaryNode(op=Op.AND, left=tcp, right=UnaryNode(operand=tcp))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is False

    def test_self_contradiction_or(self) -> None:
        tcp = MatchNode(protocol="tcp")
        node = BinaryNode(op=Op.OR, left=tcp, right=UnaryNode(operand=tcp))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True

    def test_not_true_false(self) -> None:
        node = UnaryNode(operand=BoolNode(value=True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is False

    def test_not_false_true(self) -> None:
        node = UnaryNode(operand=BoolNode(value=False))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True

    def test_duplicate_protocol_or_collapsed(self) -> None:
        node = BinaryNode(op=Op.OR, left=MatchNode(protocol="tcp"), right=MatchNode(protocol="tcp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"

    def test_duplicate_protocol_and_collapsed(self) -> None:
        node = BinaryNode(op=Op.AND, left=MatchNode(protocol="udp"), right=MatchNode(protocol="udp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)

    def test_identical_eq_collapses_to_true(self) -> None:
        v = ValueNode(value="src_ip")
        node = BinaryNode(op=Op.EQ, left=v, right=v)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True

    def test_parse_then_optimize_roundtrip(self) -> None:
        node = parse_bpf("not not tcp and (port 80 or port 80)")
        optimized = optimize_bpf(node)
        assert match_bpf(optimized, {"protocol": "tcp", "dst_port": "80"})
        assert not match_bpf(optimized, {"protocol": "udp", "dst_port": "80"})

    def test_value_node_passthrough(self) -> None:
        v = ValueNode(value="any")
        assert optimize_bpf(v) == v

    def test_bool_node_passthrough(self) -> None:
        b = BoolNode(value=True)
        assert optimize_bpf(b) == b


# ── Deeper parse coverage ─────────────────────────────────────────────────


class TestParseDeeper:
    def test_not_host_expr(self) -> None:
        node = parse_bpf("not host 10.0.0.1")
        assert isinstance(node, UnaryNode)
        inner = node.operand
        assert isinstance(inner, BinaryNode)
        assert inner.op == Op.OR

    def test_not_src_host(self) -> None:
        node = parse_bpf("not src host 10.0.0.1")
        assert isinstance(node, UnaryNode)
        inner = node.operand
        assert isinstance(inner, BinaryNode)
        assert inner.op == Op.EQ
        assert isinstance(inner.left, ValueNode)
        assert inner.left.value == "src_ip"

    def test_not_port(self) -> None:
        node = parse_bpf("not port 80")
        assert isinstance(node, UnaryNode)
        inner = node.operand
        assert isinstance(inner, BinaryNode)
        assert inner.op == Op.OR

    def test_not_dst_port(self) -> None:
        node = parse_bpf("not dst port 22")
        assert isinstance(node, UnaryNode)
        inner = node.operand
        assert isinstance(inner, BinaryNode)
        assert inner.op == Op.EQ
        assert isinstance(inner.left, ValueNode)
        assert inner.left.value == "dst_port"

    def test_triple_negation(self) -> None:
        node = parse_bpf("not not not tcp")
        assert isinstance(node, UnaryNode)
        inner = node.operand
        assert isinstance(inner, UnaryNode)
        assert isinstance(inner.operand, UnaryNode)

    def test_precedence_and_over_or_left(self) -> None:
        node = parse_bpf("tcp and udp or icmp")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR
        assert isinstance(node.left, BinaryNode)
        assert node.left.op == Op.AND

    def test_precedence_and_over_or_right(self) -> None:
        node = parse_bpf("icmp or tcp and udp")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR
        assert isinstance(node.right, BinaryNode)
        assert node.right.op == Op.AND

    def test_precedence_not_over_and(self) -> None:
        node = parse_bpf("not tcp and udp")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND
        assert isinstance(node.left, UnaryNode)

    def test_deeply_nested_parens(self) -> None:
        node = parse_bpf("(((tcp)))")
        assert isinstance(node, MatchNode)
        assert node.protocol == "tcp"

    def test_nested_and_or_in_parens(self) -> None:
        node = parse_bpf("(tcp or udp) and (port 80 or port 443)")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND
        assert isinstance(node.left, BinaryNode)
        assert node.left.op == Op.OR
        assert isinstance(node.right, BinaryNode)
        assert node.right.op == Op.OR

    def test_not_with_parens(self) -> None:
        node = parse_bpf("not (tcp or udp)")
        assert isinstance(node, UnaryNode)
        inner = node.operand
        assert isinstance(inner, BinaryNode)
        assert inner.op == Op.OR

    def test_port_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after 'port'"):
            parse_bpf("port")

    def test_net_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after 'net'"):
            parse_bpf("net")

    def test_dst_net_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after"):
            parse_bpf("dst net")

    def test_unexpected_token_raises(self) -> None:
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf("!!!")

    def test_ip_and_ip6_distinct(self) -> None:
        n4 = parse_bpf("ip")
        n6 = parse_bpf("ip6")
        assert n4.protocol == "ip"
        assert n6.protocol == "ip6"

    def test_src_net_with_prefix(self) -> None:
        node = parse_bpf("src net 172.16.0.0/12")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.EQ
        assert isinstance(node.left, ValueNode)
        assert node.left.value == "src_net"
        assert isinstance(node.right, ValueNode)
        assert node.right.value == "172.16.0.0/12"

    def test_leading_whitespace(self) -> None:
        node = parse_bpf("  tcp  ")
        assert isinstance(node, MatchNode)
        assert node.protocol == "tcp"

    def test_net_any_ip(self) -> None:
        node = parse_bpf("src net 0.0.0.0/0")
        assert isinstance(node, BinaryNode)
        assert node.left.value == "src_net"


# ── Deeper match coverage ──────────────────────────────────────────────────


class TestMatchDeeper:
    def test_match_neq_host(self) -> None:
        node = BinaryNode(
            op=Op.NE,
            left=ValueNode(value="src_ip"),
            right=ValueNode(value="10.0.0.1"),
        )
        assert match_bpf(node, {"src_ip": "10.0.0.2"})
        assert not match_bpf(node, {"src_ip": "10.0.0.1"})

    def test_match_neq_port(self) -> None:
        node = BinaryNode(
            op=Op.NE,
            left=ValueNode(value="dst_port"),
            right=ValueNode(value="80"),
        )
        assert match_bpf(node, {"dst_port": "443"})
        assert not match_bpf(node, {"dst_port": "80"})

    def test_match_net_with_invalid_ip(self) -> None:
        node = parse_bpf("src net 10.0.0.0/8")
        assert not match_bpf(node, {"src_net": "10.0.0.0/8", "src_ip": "not_an_ip"})

    def test_match_net_uses_parse_tree_not_packet_net_field(self) -> None:
        node = parse_bpf("src net 10.0.0.0/8")
        assert match_bpf(node, {"src_net": "ignored", "src_ip": "10.1.2.3"})

    def test_match_net_missing_ip_field(self) -> None:
        node = parse_bpf("src net 10.0.0.0/8")
        assert not match_bpf(node, {"src_net": "10.0.0.0/8"})

    def test_match_multiple_and_conditions(self) -> None:
        node = parse_bpf("tcp and src port 80 and dst port 80")
        assert match_bpf(node, {"protocol": "tcp", "src_port": "80", "dst_port": "80"})
        assert not match_bpf(node, {"protocol": "tcp", "src_port": "80", "dst_port": "443"})

    def test_match_not_host(self) -> None:
        node = parse_bpf("not host 10.0.0.1")
        assert match_bpf(node, {"src_ip": "10.0.0.2", "dst_ip": "10.0.0.2"})
        assert not match_bpf(node, {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.1"})
        assert not match_bpf(node, {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"})

    def test_match_not_protocol_with_or(self) -> None:
        node = parse_bpf("not tcp and not udp")
        assert match_bpf(node, {"protocol": "icmp"})
        assert not match_bpf(node, {"protocol": "tcp"})
        assert not match_bpf(node, {"protocol": "udp"})

    def test_match_field_not_in_packet_returns_literal(self) -> None:
        node = parse_bpf("src host 10.0.0.1")
        assert not match_bpf(node, {})
        assert not match_bpf(node, {"src_ip": "10.0.0.2"})

    def test_match_unknown_node_type_returns_false(self) -> None:
        class BogusNode:
            pass

        assert match_bpf(BogusNode(), {"protocol": "tcp"}) is False  # type: ignore[arg-type]

    def test_match_ipv6_in_net_supported_by_ipaddress(self) -> None:
        assert match_bpf(
            parse_bpf("src net 2001:db8::/32"),
            {"src_net": "2001:db8::/32", "src_ip": "2001:db8::1"},
        )

    def test_match_ipv6_in_different_subnet(self) -> None:
        assert not match_bpf(
            parse_bpf("src net 2001:db8:1::/48"),
            {"src_net": "2001:db8:1::/48", "src_ip": "2001:db9::1"},
        )

    def test_match_net_with_valid_ipv6_outside_prefix(self) -> None:
        assert not match_bpf(
            parse_bpf("src net 2001:db8::/32"),
            {"src_net": "2001:db8::/32", "src_ip": "2002::1"},
        )


# ── Deeper optimizer coverage ──────────────────────────────────────────────


class TestOptimizeDeeper:
    def test_and_with_false_on_right(self) -> None:
        node = BinaryNode(op=Op.AND, left=MatchNode(protocol="tcp"), right=BoolNode(value=False))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is False

    def test_or_with_true_on_right(self) -> None:
        node = BinaryNode(op=Op.OR, left=MatchNode(protocol="tcp"), right=BoolNode(value=True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True

    def test_ne_passthrough(self) -> None:
        node = BinaryNode(op=Op.NE, left=ValueNode(value="a"), right=ValueNode(value="b"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BinaryNode)
        assert optimized.op == Op.NE

    def test_triple_negation_stops_at_double(self) -> None:
        inner_match = MatchNode(protocol="tcp")
        node = UnaryNode(operand=UnaryNode(operand=UnaryNode(operand=inner_match)))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, UnaryNode)

    def test_deeply_nested_and_or(self) -> None:
        node = BinaryNode(
            op=Op.AND,
            left=BinaryNode(op=Op.OR, left=BoolNode(value=False), right=MatchNode(protocol="tcp")),
            right=MatchNode(protocol="udp"),
        )
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BinaryNode)
        assert optimized.op == Op.AND
        assert isinstance(optimized.left, MatchNode)
        assert optimized.left.protocol == "tcp"

    def test_and_left_duplicate_collapses(self) -> None:
        tcp = MatchNode(protocol="tcp")
        node = BinaryNode(op=Op.AND, left=tcp, right=tcp)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"

    def test_or_left_duplicate_collapses(self) -> None:
        udp = MatchNode(protocol="udp")
        node = BinaryNode(op=Op.OR, left=udp, right=udp)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "udp"

    def test_not_not_value_identity(self) -> None:
        v = ValueNode(value="x")
        node = UnaryNode(operand=UnaryNode(operand=v))
        optimized = optimize_bpf(node)
        assert optimized == v

    def test_full_expression_parse_optimize_match(self) -> None:
        node = parse_bpf("not (not tcp and not udp) and (port 80 or port 443)")
        optimized = optimize_bpf(node)
        pkt_tcp_80 = {"protocol": "tcp", "dst_port": "80"}
        pkt_icmp_53 = {"protocol": "icmp", "dst_port": "53"}
        assert match_bpf(optimized, pkt_tcp_80)
        assert not match_bpf(optimized, pkt_icmp_53)

    def test_optimize_with_large_and_chain(self) -> None:
        node = BinaryNode(
            op=Op.AND,
            left=BinaryNode(
                op=Op.AND,
                left=BoolNode(value=True),
                right=MatchNode(protocol="tcp"),
            ),
            right=BinaryNode(
                op=Op.AND,
                left=MatchNode(protocol="tcp"),
                right=BoolNode(value=True),
            ),
        )
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"
