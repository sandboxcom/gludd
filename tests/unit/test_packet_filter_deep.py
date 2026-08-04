"""Deep BPF packet filter tests: parsing, matching, optimization."""

from __future__ import annotations

import pytest
from src.general_ludd.network.packet_filter import (
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
