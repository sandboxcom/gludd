"""BPF expression parsing, matching, and optimization.

Parses a subset of tcpdump/libpcap BPF filter expressions into an AST,
evaluates the AST against packet metadata dicts, and applies algebraic
simplifications (constant folding, identity elimination, double negation).

Supported primitives: host, net, port, src, dst, tcp, udp, icmp, arp, ip, ip6.
Supported operators: and, or, not, parenthesized groups.
"""

from __future__ import annotations

import dataclasses
import enum
import ipaddress
import re


class Op(enum.StrEnum):
    """Comparison and Boolean operators represented in the filter AST."""

    EQ = "=="
    NE = "!="
    AND = "and"
    OR = "or"


@dataclasses.dataclass
class ValueNode:
    """Leaf: a field reference or literal value."""

    value: str  # e.g. "src_ip", "192.168.1.1", "80"


@dataclasses.dataclass
class BinaryNode:
    """Binary comparison: <left> <op> <right>."""

    op: Op
    left: ASTNode
    right: ASTNode


@dataclasses.dataclass
class UnaryNode:
    """Unary operation: not <operand>."""

    operand: ASTNode


@dataclasses.dataclass
class MatchNode:
    """Protocol match leaf: tcp, udp, icmp, arp, ip, ip6."""

    protocol: str  # e.g. "tcp", "udp"


@dataclasses.dataclass
class BoolNode:
    """Literal boolean: True or False (from constant folding)."""

    value: bool


ASTNode = BinaryNode | UnaryNode | MatchNode | BoolNode | ValueNode


# ── lexer ────────────────────────────────────────────────────────────────────

_TOKEN_RX = re.compile(
    r"""
    \s*(?:
        (?:src|dst)\s+host\b|
        (?:src|dst)\s+port\b|
        (?:src|dst)\s+net\b|
        \bhost\b|\bnet\b|\bport\b|\btcp\b|\budp\b|\bicmp\b|\barp\b|\bip6?\b|
        \band\b|\bor\b|\bnot\b|
        [()]|
        [^\s()]+
    )\s*
    """,
    re.VERBOSE,
)

_SRC_DST_HOST = re.compile(r"(src|dst)\s+host\b")
_SRC_DST_PORT = re.compile(r"(src|dst)\s+port\b")
_SRC_DST_NET = re.compile(r"(src|dst)\s+net\b")


def _tokenize(expr: str) -> list[str]:
    tokens: list[str] = []
    for m in _TOKEN_RX.finditer(expr):
        raw = m.group(0).strip()
        if raw:
            tokens.append(raw)
    return tokens


# ── parser ───────────────────────────────────────────────────────────────────


class ParseError(ValueError):
    """Raised when a packet-filter expression cannot be parsed safely."""


def _parse_expr(tokens: list[str], pos: int) -> tuple[ASTNode, int]:
    node, pos = _parse_or(tokens, pos)
    return node, pos


def _parse_or(tokens: list[str], pos: int) -> tuple[ASTNode, int]:
    left, pos = _parse_and(tokens, pos)
    while pos < len(tokens) and tokens[pos] == "or":
        pos += 1
        right, pos = _parse_and(tokens, pos)
        left = BinaryNode(op=Op.OR, left=left, right=right)
    return left, pos


def _parse_and(tokens: list[str], pos: int) -> tuple[ASTNode, int]:
    left, pos = _parse_not(tokens, pos)
    while pos < len(tokens) and tokens[pos] == "and":
        pos += 1
        right, pos = _parse_not(tokens, pos)
        left = BinaryNode(op=Op.AND, left=left, right=right)
    return left, pos


def _parse_not(tokens: list[str], pos: int) -> tuple[ASTNode, int]:
    if pos < len(tokens) and tokens[pos] == "not":
        pos += 1
        operand, pos = _parse_not(tokens, pos)
        return UnaryNode(operand=operand), pos
    return _parse_primary(tokens, pos)


def _parse_primary(tokens: list[str], pos: int) -> tuple[ASTNode, int]:
    if pos >= len(tokens):
        raise ParseError("unexpected end of expression")

    tok = tokens[pos]
    pos += 1

    if tok in ("tcp", "udp", "icmp", "arp", "ip", "ip6"):
        return MatchNode(protocol=tok), pos

    if tok == "(":
        node, pos = _parse_expr(tokens, pos)
        if pos >= len(tokens) or tokens[pos] != ")":
            raise ParseError("unclosed parenthesis")
        pos += 1
        return node, pos

    if _SRC_DST_HOST.match(tok):
        dir_, _ = tok.split()
        if pos >= len(tokens):
            raise ParseError(f"expected value after '{tok}'")
        val = tokens[pos]
        pos += 1
        field = f"{dir_}_ip"
        return BinaryNode(op=Op.EQ, left=ValueNode(value=field), right=ValueNode(value=val)), pos

    if _SRC_DST_PORT.match(tok):
        dir_, _ = tok.split()
        if pos >= len(tokens):
            raise ParseError(f"expected value after '{tok}'")
        val = tokens[pos]
        pos += 1
        field = f"{dir_}_port"
        return BinaryNode(op=Op.EQ, left=ValueNode(value=field), right=ValueNode(value=val)), pos

    if _SRC_DST_NET.match(tok):
        dir_, _ = tok.split()
        if pos >= len(tokens):
            raise ParseError(f"expected value after '{tok}'")
        val = tokens[pos]
        pos += 1
        field = f"{dir_}_net"
        return BinaryNode(op=Op.EQ, left=ValueNode(value=field), right=ValueNode(value=val)), pos

    if tok == "host":
        if pos >= len(tokens):
            raise ParseError("expected value after 'host'")
        val = tokens[pos]
        pos += 1
        src = BinaryNode(op=Op.EQ, left=ValueNode(value="src_ip"), right=ValueNode(value=val))
        dst = BinaryNode(op=Op.EQ, left=ValueNode(value="dst_ip"), right=ValueNode(value=val))
        return BinaryNode(op=Op.OR, left=src, right=dst), pos

    if tok == "port":
        if pos >= len(tokens):
            raise ParseError("expected value after 'port'")
        val = tokens[pos]
        pos += 1
        src = BinaryNode(op=Op.EQ, left=ValueNode(value="src_port"), right=ValueNode(value=val))
        dst = BinaryNode(op=Op.EQ, left=ValueNode(value="dst_port"), right=ValueNode(value=val))
        return BinaryNode(op=Op.OR, left=src, right=dst), pos

    if tok == "net":
        if pos >= len(tokens):
            raise ParseError("expected value after 'net'")
        val = tokens[pos]
        pos += 1
        src = BinaryNode(op=Op.EQ, left=ValueNode(value="src_net"), right=ValueNode(value=val))
        dst = BinaryNode(op=Op.EQ, left=ValueNode(value="dst_net"), right=ValueNode(value=val))
        return BinaryNode(op=Op.OR, left=src, right=dst), pos

    raise ParseError(f"unexpected token: '{tok}'")


def parse_bpf(expr: str) -> ASTNode:
    """Parse a supported BPF expression into a typed syntax tree.

    Args:
        expr: Filter expression using the supported BPF subset.

    Returns:
        The root of the parsed filter tree.

    Raises:
        ParseError: If the expression is empty, malformed, or contains an
            unsupported token.
    """
    tokens = _tokenize(expr)
    if not tokens:
        raise ParseError("empty expression")
    node, pos = _parse_expr(tokens, 0)
    if pos < len(tokens):
        raise ParseError(f"unexpected token after expression: '{tokens[pos]}'")
    return node


# ── matcher ──────────────────────────────────────────────────────────────────


def _ip_in_net(ip_str: str, net_str: str) -> bool:
    try:
        return ipaddress.ip_address(ip_str) in ipaddress.ip_network(net_str, strict=False)
    except ValueError:
        return False


def _resolve_value(val: str, packet: dict[str, str | int]) -> str | int:
    return packet.get(val, val)


def match_bpf_inner(node: ASTNode, packet: dict[str, str | int]) -> bool:
    """Recursively evaluate a filter node against normalized packet metadata."""
    if isinstance(node, BoolNode):
        return node.value
    if isinstance(node, MatchNode):
        return packet.get("protocol") == node.protocol
    if isinstance(node, UnaryNode):
        return not match_bpf_inner(node.operand, packet)
    if isinstance(node, BinaryNode):
        if node.op == Op.AND:
            return match_bpf_inner(node.left, packet) and match_bpf_inner(node.right, packet)
        if node.op == Op.OR:
            return match_bpf_inner(node.left, packet) or match_bpf_inner(node.right, packet)
        if node.op in (Op.EQ, Op.NE):
            left_val = node.left.value if isinstance(node.left, ValueNode) else ""
            right_val = node.right.value if isinstance(node.right, ValueNode) else ""
            if str(left_val).endswith("_net"):
                ip_field = str(left_val).replace("_net", "_ip")
                ip_val = _resolve_value(ip_field, packet)
                net_val = _resolve_value(right_val, packet)
                result: bool = _ip_in_net(str(ip_val), str(net_val))
            else:
                lv = _resolve_value(left_val, packet)
                rv = _resolve_value(right_val, packet)
                result = str(lv) == str(rv)
            if node.op == Op.NE:
                result = not result
            return result
    return False


def match_bpf(node: ASTNode, packet: dict[str, str | int]) -> bool:
    """Return whether packet metadata satisfies a parsed BPF filter."""
    return match_bpf_inner(node, packet)


# ── optimizer ────────────────────────────────────────────────────────────────


def _is_true(node: ASTNode) -> bool:
    return isinstance(node, BoolNode) and node.value is True


def _is_false(node: ASTNode) -> bool:
    return isinstance(node, BoolNode) and node.value is False


def _is_protocol_match(node: ASTNode, proto: str) -> bool:
    return isinstance(node, MatchNode) and node.protocol == proto


def optimize_bpf(node: ASTNode) -> ASTNode:
    """Return an equivalent filter tree simplified by Boolean identities."""
    if isinstance(node, ValueNode):
        return node
    if isinstance(node, MatchNode):
        return node
    if isinstance(node, BoolNode):
        return node
    if isinstance(node, UnaryNode):
        inner = optimize_bpf(node.operand)
        if isinstance(inner, UnaryNode):
            return optimize_bpf(inner.operand)
        if _is_true(inner):
            return BoolNode(value=False)
        if _is_false(inner):
            return BoolNode(value=True)
        return UnaryNode(operand=inner)
    if isinstance(node, BinaryNode):
        left = optimize_bpf(node.left)
        right = optimize_bpf(node.right)

        if node.op == Op.AND:
            if _is_true(left):
                return right
            if _is_true(right):
                return left
            if _is_false(left) or _is_false(right):
                return BoolNode(value=False)
            if left == right:
                return left
            if isinstance(left, UnaryNode) and left.operand == right:
                return BoolNode(value=False)
            if isinstance(right, UnaryNode) and right.operand == left:
                return BoolNode(value=False)
            if _is_protocol_match(left, "tcp") and _is_protocol_match(right, "tcp"):
                return left
            if _is_protocol_match(left, "udp") and _is_protocol_match(right, "udp"):
                return left
            return BinaryNode(op=Op.AND, left=left, right=right)

        if node.op == Op.OR:
            if _is_true(left) or _is_true(right):
                return BoolNode(value=True)
            if _is_false(left):
                return right
            if _is_false(right):
                return left
            if left == right:
                return left
            if isinstance(left, UnaryNode) and left.operand == right:
                return BoolNode(value=True)
            if isinstance(right, UnaryNode) and right.operand == left:
                return BoolNode(value=True)
            if _is_protocol_match(left, "tcp") and _is_protocol_match(right, "tcp"):
                return left
            if _is_protocol_match(left, "udp") and _is_protocol_match(right, "udp"):
                return left
            return BinaryNode(op=Op.OR, left=left, right=right)

        if node.op == Op.EQ:
            if left == right:
                return BoolNode(value=True)
            return BinaryNode(op=Op.EQ, left=left, right=right)

        if node.op == Op.NE:
            return BinaryNode(op=Op.NE, left=left, right=right)

    return node
