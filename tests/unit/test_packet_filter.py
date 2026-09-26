"""Unit tests for packet_filter BPF parser and matcher."""

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


class TestTokenize:
    def test_simple(self):
        node = parse_bpf("tcp")
        assert isinstance(node, MatchNode)
        assert node.protocol == "tcp"

    def test_host(self):
        node = parse_bpf("host 192.168.1.1")
        assert isinstance(node, BinaryNode)
        assert node.op == Op.OR

    def test_src_host(self):
        node = parse_bpf("src host 10.0.0.1")
        assert isinstance(node, BinaryNode)
        assert node.left.value == "src_ip"

    def test_port(self):
        node = parse_bpf("port 80")
        assert isinstance(node, BinaryNode)

    def test_net(self):
        node = parse_bpf("net 192.168.0.0/24")
        assert isinstance(node, BinaryNode)


class TestParseErrors:
    def test_empty(self):
        with pytest.raises(ParseError, match="empty expression"):
            parse_bpf("")

    def test_unexpected_token(self):
        with pytest.raises(ParseError, match="unexpected token"):
            parse_bpf("foo")

    def test_unclosed_paren(self):
        with pytest.raises(ParseError, match="unclosed parenthesis"):
            parse_bpf("(tcp")

    def test_missing_value(self):
        with pytest.raises(ParseError, match="expected value after"):
            parse_bpf("host")


class TestMatchBpf:
    def test_protocol_match(self):
        node = parse_bpf("tcp")
        assert match_bpf(node, {"protocol": "tcp"}) is True
        assert match_bpf(node, {"protocol": "udp"}) is False

    def test_host_match(self):
        node = parse_bpf("host 192.168.1.1")
        packet = {"src_ip": "192.168.1.1", "dst_ip": "10.0.0.1"}
        assert match_bpf(node, packet) is True

    def test_src_port_match(self):
        node = parse_bpf("src port 443")
        assert match_bpf(node, {"src_port": 443}) is True
        assert match_bpf(node, {"src_port": 80}) is False

    def test_not(self):
        node = parse_bpf("not tcp")
        assert match_bpf(node, {"protocol": "udp"}) is True
        assert match_bpf(node, {"protocol": "tcp"}) is False

    def test_and_or(self):
        node = parse_bpf("tcp and port 80")
        assert match_bpf(node, {"protocol": "tcp", "src_port": 80}) is True
        assert match_bpf(node, {"protocol": "tcp", "src_port": 443}) is False

    def test_net_match(self):
        node = parse_bpf("net 192.168.0.0/24")
        packet = {"src_ip": "192.168.0.55", "dst_ip": "10.0.0.1"}
        assert match_bpf(node, packet) is True


class TestOptimizeBpf:
    def test_double_negation(self):
        node = UnaryNode(UnaryNode(MatchNode("tcp")))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)

    def test_not_true(self):
        node = UnaryNode(BoolNode(True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode) and optimized.value is False

    def test_and_with_false(self):
        node = BinaryNode(Op.AND, BoolNode(True), BoolNode(False))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode) and optimized.value is False

    def test_or_with_true(self):
        node = BinaryNode(Op.OR, BoolNode(False), BoolNode(True))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode) and optimized.value is True

    def test_eq_same_value(self):
        node = BinaryNode(Op.EQ, ValueNode("x"), ValueNode("x"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, BoolNode) and optimized.value is True

    def test_and_same_protocol(self):
        node = BinaryNode(Op.AND, MatchNode("tcp"), MatchNode("tcp"))
        optimized = optimize_bpf(node)
        assert isinstance(optimized, MatchNode)
