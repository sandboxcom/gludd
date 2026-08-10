"""Deep edge-case tests for BPF packet filter: parser, matcher, optimizer."""

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

# ── Parse: whitespace / empty edge cases ───────────────────────────────────


class TestParseWhitespaceEdge:
    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ParseError, match="empty"):
            parse_bpf("   ")

    def test_tabs_and_newlines(self) -> None:
        node = parse_bpf("\ttcp\nand\n\tport 80")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND

    def test_leading_trailing_whitespace_ignored(self) -> None:
        node = parse_bpf("  host 10.0.0.1  ")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR


# ── Parse: unexpected token / malformed input ──────────────────────────────


class TestParseUnexpectedToken:
    def test_operator_as_start_raises(self) -> None:
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf("and host")

    def test_unknown_keyword_raises(self) -> None:
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf("xyz")

    def test_numeric_literal_only(self) -> None:
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf("42")

    def test_port_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after 'port'"):
            parse_bpf("port")

    def test_net_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after 'net'"):
            parse_bpf("net")

    def test_src_net_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after"):
            parse_bpf("src net")

    def test_dst_net_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after"):
            parse_bpf("dst net")

    def test_src_port_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after"):
            parse_bpf("src port")

    def test_dst_port_missing_value_raises(self) -> None:
        with pytest.raises(ParseError, match="expected value after"):
            parse_bpf("dst port")

    def test_trailing_paren_raises(self) -> None:
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf("tcp )")

    def test_extra_paren_only(self) -> None:
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf(") tcp")


# ── Parse: deep / recursive nesting ────────────────────────────────────────


class TestParseDeep:
    def test_deeply_nested_and(self) -> None:
        expr = "tcp and port 1 and port 2 and port 3 and port 4"
        node = parse_bpf(expr)
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND

    def test_deeply_nested_or(self) -> None:
        expr = "tcp or udp or icmp or arp or ip or ip6"
        node = parse_bpf(expr)
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_nested_nots(self) -> None:
        node = parse_bpf("not not not tcp")
        assert isinstance(node, UnaryNode)
        inner: object = node.operand
        assert isinstance(inner, UnaryNode)
        assert isinstance(inner.operand, UnaryNode)  # type: ignore[union-attr]

    def test_deep_paren_nesting(self) -> None:
        node = parse_bpf("(((tcp)))")
        assert isinstance(node, MatchNode)
        assert node.protocol == "tcp"

    def test_mixed_precedence_depth(self) -> None:
        expr = "tcp and (udp or (icmp and arp))"
        node = parse_bpf(expr)
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND


# ── Parse: src/dst combinators ─────────────────────────────────────────────


class TestParseSrcDst:
    def test_src_port_expr(self) -> None:
        node = parse_bpf("src port 8080")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.EQ
        assert isinstance(node.left, ValueNode)
        assert node.left.value == "src_port"

    def test_src_net_expr(self) -> None:
        node = parse_bpf("src net 172.16.0.0/12")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.EQ
        assert isinstance(node.left, ValueNode)
        assert node.left.value == "src_net"

    def test_src_host_complex_value(self) -> None:
        node = parse_bpf("src host 10.0.0.1 and dst port 443")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.AND


# ── Parse: value token edge cases ──────────────────────────────────────────


class TestParseValues:
    def test_ipv6_host(self) -> None:
        node = parse_bpf("host ::1")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_hostname_as_host(self) -> None:
        node = parse_bpf("host example.com")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_large_port_number(self) -> None:
        node = parse_bpf("port 65535")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_cidr_net_value(self) -> None:
        node = parse_bpf("net 192.168.0.0/16")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR


# ── Match: not-equal (NE) operator ─────────────────────────────────────────


class TestMatchNE:
    def test_ne_single(self) -> None:
        left = ValueNode(value="src_ip")
        right = ValueNode(value="10.0.0.1")
        node = BinaryNode(op=Op.NE, left=left, right=right)
        assert match_bpf(node, {"src_ip": "10.0.0.2"})
        assert not match_bpf(node, {"src_ip": "10.0.0.1"})

    def test_ne_in_complex_expr(self) -> None:
        left = ValueNode(value="dst_port")
        right = ValueNode(value="80")
        ne_node = BinaryNode(op=Op.NE, left=left, right=right)
        tcp = MatchNode(protocol="tcp")
        node = BinaryNode(op=Op.AND, left=tcp, right=ne_node)
        assert match_bpf(node, {"protocol": "tcp", "dst_port": "443"})
        assert not match_bpf(node, {"protocol": "tcp", "dst_port": "80"})
        assert not match_bpf(node, {"protocol": "udp", "dst_port": "443"})


# ── Match: net/subnet resolution edge cases ────────────────────────────────


class TestMatchNet:
    def test_net_with_missing_ip_field(self) -> None:
        node = parse_bpf("src net 10.0.0.0/8")
        assert not match_bpf(node, {"src_net": "10.0.0.0/8"})

    def test_dst_net_match(self) -> None:
        node = parse_bpf("dst net 192.168.0.0/24")
        assert match_bpf(node, {"dst_net": "192.168.0.0/24", "dst_ip": "192.168.0.42"})
        assert not match_bpf(node, {"dst_net": "192.168.0.0/24", "dst_ip": "10.0.0.1"})

    def test_net_matches_both_src_dst(self) -> None:
        node = parse_bpf("net 10.0.0.0/8")
        assert match_bpf(node, {"src_net": "10.0.0.0/8", "src_ip": "10.1.2.3"})
        assert match_bpf(node, {"dst_net": "10.0.0.0/8", "dst_ip": "10.100.200.1"})
        assert not match_bpf(
            node, {"src_net": "10.0.0.0/8", "src_ip": "192.168.1.1", "dst_net": "10.0.0.0/8", "dst_ip": "192.168.2.2"}
        )

    def test_invalid_ip_in_net_check(self) -> None:
        node = parse_bpf("src net 10.0.0.0/8")
        assert not match_bpf(node, {"src_net": "10.0.0.0/8", "src_ip": "not-an-ip"})

    def test_ip_exactly_at_net_boundary(self) -> None:
        node = parse_bpf("src net 10.0.0.0/8")
        assert match_bpf(node, {"src_net": "10.0.0.0/8", "src_ip": "10.0.0.0"})
        assert match_bpf(node, {"src_net": "10.0.0.0/8", "src_ip": "10.255.255.255"})


# ── Match: packet dict edge cases ──────────────────────────────────────────


class TestMatchPacketEdges:
    def test_empty_packet(self) -> None:
        node = parse_bpf("tcp")
        assert not match_bpf(node, {})

    def test_missing_field_returns_false(self) -> None:
        node = parse_bpf("host 10.0.0.1")
        assert not match_bpf(node, {"protocol": "tcp"})

    def test_extra_fields_ignored(self) -> None:
        node = parse_bpf("tcp and port 80")
        assert match_bpf(node, {"protocol": "tcp", "src_port": "80", "dst_port": "9999", "extra": "ignored"})
        assert not match_bpf(node, {"protocol": "tcp", "src_port": "9999", "dst_port": "9999", "extra": "ignored"})

    def test_integer_port_values(self) -> None:
        node = parse_bpf("dst port 80")
        assert match_bpf(node, {"dst_port": 80})
        assert not match_bpf(node, {"dst_port": 443})

    def test_integer_vs_string_port_mismatch(self) -> None:
        node = parse_bpf("dst port 80")
        assert match_bpf(node, {"dst_port": "80"})
        assert match_bpf(node, {"dst_port": 80})


# ── Match: BoolNode / fallback ─────────────────────────────────────────────


class TestMatchBool:
    def test_true_node_always_matches(self) -> None:
        assert match_bpf(BoolNode(value=True), {})

    def test_false_node_never_matches(self) -> None:
        assert not match_bpf(BoolNode(value=False), {"protocol": "tcp"})

    def test_true_node_with_complex_packet(self) -> None:
        packet: dict[str, str | int] = {
            "protocol": "tcp",
            "src_ip": "1.2.3.4",
            "dst_port": "443",
            "src_net": "0.0.0.0/0",
            "src_port": "12345",
        }
        assert match_bpf(BoolNode(value=True), packet)
        assert not match_bpf(BoolNode(value=False), packet)


# ── Match: deep recursive expression matching ──────────────────────────────


class TestMatchDeep:
    def test_triple_nested_and(self) -> None:
        inner = BinaryNode(
            op=Op.AND,
            left=MatchNode(protocol="tcp"),
            right=BinaryNode(op=Op.EQ, left=ValueNode(value="dst_port"), right=ValueNode(value="80")),
        )
        src_eq = BinaryNode(op=Op.EQ, left=ValueNode(value="src_ip"), right=ValueNode(value="10.0.0.1"))
        node = BinaryNode(op=Op.AND, left=src_eq, right=inner)
        assert match_bpf(node, {"src_ip": "10.0.0.1", "protocol": "tcp", "dst_port": "80"})
        assert not match_bpf(node, {"src_ip": "10.0.0.1", "protocol": "tcp", "dst_port": "443"})

    def test_deep_or_short_circuit(self) -> None:
        left = MatchNode(protocol="tcp")
        right = MatchNode(protocol="udp")
        node = BinaryNode(op=Op.OR, left=left, right=right)
        assert match_bpf(node, {"protocol": "tcp"})
        assert match_bpf(node, {"protocol": "udp"})
        assert not match_bpf(node, {"protocol": "icmp"})

    def test_unary_not_chain(self) -> None:
        tcp = MatchNode(protocol="tcp")
        node = UnaryNode(operand=UnaryNode(operand=UnaryNode(operand=tcp)))
        assert not match_bpf(node, {"protocol": "tcp"})
        assert match_bpf(node, {"protocol": "udp"})


# ── Optimize: deeper structural reductions ─────────────────────────────────


class TestOptimizeDeep:
    def test_triple_negation(self) -> None:
        tcp = MatchNode(protocol="tcp")
        node = UnaryNode(operand=UnaryNode(operand=UnaryNode(operand=tcp)))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, UnaryNode)
        assert isinstance(optimized.operand, MatchNode)
        assert optimized.operand.protocol == "tcp"

    def test_quadruple_negation(self) -> None:
        tcp = MatchNode(protocol="tcp")
        node = UnaryNode(operand=UnaryNode(operand=UnaryNode(operand=UnaryNode(operand=tcp))))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"

    def test_true_on_right_and(self) -> None:
        node = BinaryNode(op=Op.AND, left=MatchNode(protocol="tcp"), right=BoolNode(value=True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"

    def test_false_on_left_or(self) -> None:
        node = BinaryNode(op=Op.OR, left=BoolNode(value=False), right=MatchNode(protocol="udp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "udp"

    def test_both_true_and(self) -> None:
        node = BinaryNode(op=Op.AND, left=BoolNode(value=True), right=BoolNode(value=True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True

    def test_both_false_or(self) -> None:
        node = BinaryNode(op=Op.OR, left=BoolNode(value=False), right=BoolNode(value=False))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is False

    def test_false_and_true(self) -> None:
        node = BinaryNode(op=Op.AND, left=BoolNode(value=False), right=BoolNode(value=True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is False

    def test_deep_nested_identity_elimination(self) -> None:
        tcp = MatchNode(protocol="tcp")
        inner = BinaryNode(op=Op.AND, left=tcp, right=BoolNode(value=True))
        node = BinaryNode(op=Op.AND, left=inner, right=BoolNode(value=True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "tcp"

    def test_contradiction_preserved_at_outer(self) -> None:
        tcp = MatchNode(protocol="tcp")
        not_tcp = UnaryNode(operand=tcp)
        node = BinaryNode(op=Op.AND, left=tcp, right=not_tcp)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is False

    def test_tautology_preserved_at_outer(self) -> None:
        tcp = MatchNode(protocol="tcp")
        not_tcp = UnaryNode(operand=tcp)
        node = BinaryNode(op=Op.OR, left=tcp, right=not_tcp)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True


# ── Optimize: idempotency ──────────────────────────────────────────────────


class TestOptimizeIdempotent:
    def test_idempotent_single_protocol(self) -> None:
        node = MatchNode(protocol="icmp")
        assert optimize_bpf(optimize_bpf(node)) == optimize_bpf(node)

    def test_idempotent_unary(self) -> None:
        node = UnaryNode(operand=MatchNode(protocol="arp"))
        assert optimize_bpf(optimize_bpf(node)) == optimize_bpf(node)

    def test_idempotent_complex_expression(self) -> None:
        node = parse_bpf("not not tcp and (port 80 or port 80)")
        once = optimize_bpf(node)
        twice = optimize_bpf(once)
        assert twice == once

    def test_idempotent_false_collapse(self) -> None:
        tcp = MatchNode(protocol="tcp")
        not_tcp = UnaryNode(operand=tcp)
        node = BinaryNode(op=Op.AND, left=tcp, right=not_tcp)
        o1 = optimize_bpf(node)
        o2 = optimize_bpf(o1)
        assert isinstance(o2, BoolNode) and o2.value is False


# ── Optimize: non-tcp/udp protocol matching ────────────────────────────────


class TestOptimizeProtocols:
    def test_icmp_duplicate_and(self) -> None:
        node = BinaryNode(op=Op.AND, left=MatchNode(protocol="icmp"), right=MatchNode(protocol="icmp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "icmp"

    def test_arp_duplicate_or(self) -> None:
        node = BinaryNode(op=Op.OR, left=MatchNode(protocol="arp"), right=MatchNode(protocol="arp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "arp"

    def test_ip_duplicate_and(self) -> None:
        node = BinaryNode(op=Op.AND, left=MatchNode(protocol="ip"), right=MatchNode(protocol="ip"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "ip"

    def test_ip6_duplicate_or(self) -> None:
        node = BinaryNode(op=Op.OR, left=MatchNode(protocol="ip6"), right=MatchNode(protocol="ip6"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
        assert optimized.protocol == "ip6"

    def test_different_protocols_not_merged_and(self) -> None:
        node = BinaryNode(op=Op.AND, left=MatchNode(protocol="tcp"), right=MatchNode(protocol="icmp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BinaryNode)
        assert optimized.op == Op.AND

    def test_different_protocols_not_merged_or(self) -> None:
        node = BinaryNode(op=Op.OR, left=MatchNode(protocol="tcp"), right=MatchNode(protocol="udp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BinaryNode)
        assert optimized.op == Op.OR


# ── Optimize: EQ with NE interaction ───────────────────────────────────────


class TestOptimizeEQandNE:
    def test_eq_identical_values(self) -> None:
        a = ValueNode(value="foo")
        node = BinaryNode(op=Op.EQ, left=a, right=a)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode)
        assert optimized.value is True

    def test_eq_different_values_preserved(self) -> None:
        a = ValueNode(value="src_ip")
        b = ValueNode(value="dst_ip")
        node = BinaryNode(op=Op.EQ, left=a, right=b)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BinaryNode)
        assert optimized.op == Op.EQ

    def test_ne_preserved(self) -> None:
        a = ValueNode(value="src_ip")
        b = ValueNode(value="10.0.0.1")
        node = BinaryNode(op=Op.NE, left=a, right=b)
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BinaryNode)
        assert optimized.op == Op.NE


# ── Parse + Match + Optimize integration (roundtrip) ───────────────────────


class TestRoundtrip:
    def test_parse_match_optimize_parse_host(self) -> None:
        node = parse_bpf("host 192.168.1.1")
        packet: dict[str, str | int] = {"src_ip": "192.168.1.1", "dst_ip": "10.0.0.1"}
        assert match_bpf(node, packet)
        optimized = optimize_bpf(node)
        assert match_bpf(optimized, packet)

    def test_parse_match_optimize_parse_and_not(self) -> None:
        expr = "tcp and not (port 80 or port 443)"
        node = parse_bpf(expr)
        assert match_bpf(node, {"protocol": "tcp", "src_port": "22", "dst_port": "22"})
        assert not match_bpf(node, {"protocol": "tcp", "src_port": "80", "dst_port": "22"})
        assert not match_bpf(node, {"protocol": "tcp", "src_port": "22", "dst_port": "443"})
        optimized = optimize_bpf(node)
        assert match_bpf(optimized, {"protocol": "tcp", "src_port": "22"})

    def test_src_dst_host_composition(self) -> None:
        node = parse_bpf("src host 10.0.0.1 and dst host 10.0.0.2")
        assert match_bpf(node, {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"})
        assert not match_bpf(node, {"src_ip": "10.0.0.2", "dst_ip": "10.0.0.1"})
        assert not match_bpf(node, {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.3"})

    def test_all_protocols_match_and_optimize(self) -> None:
        for proto in ("tcp", "udp", "icmp", "arp", "ip", "ip6"):
            node = parse_bpf(proto)
            assert match_bpf(node, {"protocol": proto})
            assert not match_bpf(node, {"protocol": "unknown"})
            opt = optimize_bpf(node)
            assert isinstance(opt, MatchNode)
            assert opt.protocol == proto
