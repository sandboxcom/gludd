"""Regex-to-automaton compiler: AST parse → Thompson NFA → subset-construction
DFA → Hopcroft minimization. Pure-Python, stdlib only.

Supports: literal chars, concatenation, union (|), Kleene star (*),
grouping ( ), epsilon (ε).
"""

from __future__ import annotations

from collections import deque

# ── AST ──────────────────────────────────────────────────────────────────────


class RegexNode:
    """Base class for regex AST nodes."""


class Epsilon(RegexNode):
    """Matches the empty string."""

    def __repr__(self) -> str:
        return "ε"


class Literal(RegexNode):
    """Matches a single character."""

    def __init__(self, char: str) -> None:
        self.char = char

    def __repr__(self) -> str:
        return repr(self.char)


class Concat(RegexNode):
    """Concatenation of two sub-expressions."""

    def __init__(self, left: RegexNode, right: RegexNode) -> None:
        self.left = left
        self.right = right

    def __repr__(self) -> str:
        return f"({self.left}{self.right})"


class Union(RegexNode):
    """Alternation of two sub-expressions."""

    def __init__(self, left: RegexNode, right: RegexNode) -> None:
        self.left = left
        self.right = right

    def __repr__(self) -> str:
        return f"({self.left}|{self.right})"


class Star(RegexNode):
    """Kleene star — zero or more repetitions."""

    def __init__(self, child: RegexNode) -> None:
        self.child = child

    def __repr__(self) -> str:
        return f"({self.child})*"


# ── Parser ───────────────────────────────────────────────────────────────────


class RegexParser:
    """Recursive-descent parser for a subset of regex syntax.

    Grammar::

        regex   → concat ("|" concat)*
        concat  → factor+
        factor  → atom "*"?
        atom    → "(" regex ")" | literal
        literal → any char except "(", ")", "|", "*"
    """

    def __init__(self, pattern: str) -> None:
        self._s = pattern
        self._pos = 0

    def parse(self) -> RegexNode:
        node = self._parse_regex()
        if self._pos < len(self._s):
            raise ValueError(f"Unexpected character at position {self._pos}: {self._s[self._pos :]!r}")
        return node

    # ── regex → concat ("|" concat)* ────────────────────────────────────

    def _parse_regex(self) -> RegexNode:
        left = self._parse_concat()
        while self._pos < len(self._s) and self._s[self._pos] == "|":
            self._pos += 1
            right = self._parse_concat()
            left = Union(left, right)
        return left

    # ── concat → factor+ ─────────────────────────────────────────────────

    def _parse_concat(self) -> RegexNode:
        if self._pos >= len(self._s) or self._s[self._pos] in (")", "|"):
            return Epsilon()
        left = self._parse_factor()
        while self._pos < len(self._s) and self._s[self._pos] not in (")", "|"):
            right = self._parse_factor()
            left = Concat(left, right)
        return left

    # ── factor → atom "*"? ───────────────────────────────────────────────

    def _parse_factor(self) -> RegexNode:
        node = self._parse_atom()
        if self._pos < len(self._s) and self._s[self._pos] == "*":
            self._pos += 1
            node = Star(node)
        return node

    # ── atom → "(" regex ")" | literal ───────────────────────────────────

    def _parse_atom(self) -> RegexNode:
        if self._pos >= len(self._s):
            return Epsilon()
        ch = self._s[self._pos]
        if ch == "(":
            self._pos += 1
            node = self._parse_regex()
            if self._pos >= len(self._s) or self._s[self._pos] != ")":
                raise ValueError(f"Expected ')' at position {self._pos}")
            self._pos += 1
            return node
        if ch in (")", "|", "*"):
            raise ValueError(f"Unexpected character at position {self._pos}: {ch!r}")
        self._pos += 1
        return Literal(ch)


# ── NFA ──────────────────────────────────────────────────────────────────────


class NFA:
    """Thompson NFA: a set of states and epsilon/character transitions.

    State 0 is always the start state; state ``accept`` is the single
    accepting state. Every Thompson construction step creates exactly one
    start and one accept state.

    All transitions are stored as ``dict[int, dict[str | None, set[int]]]``
    where ``None`` keys represent epsilon transitions.
    """

    def __init__(self, transitions: dict[int, dict[str | None, set[int]]], accept: int) -> None:
        self.transitions: dict[int, dict[str | None, set[int]]] = transitions
        self.accept: int = accept

    # ── helpers ─────────────────────────────────────────────────────────

    def _add_state(self) -> int:
        sid = len(self.transitions)
        self.transitions[sid] = {}
        return sid

    def _add_transition(self, src: int, symbol: str | None, dst: int) -> None:
        self.transitions[src].setdefault(symbol, set()).add(dst)

    # ── epsilon closure ──────────────────────────────────────────────────

    def epsilon_closure(self, states: set[int]) -> set[int]:
        stack: list[int] = list(states)
        closure: set[int] = set(states)
        while stack:
            s = stack.pop()
            for nxt in self.transitions.get(s, {}).get(None, set()):
                if nxt not in closure:
                    closure.add(nxt)
                    stack.append(nxt)
        return closure

    # ── step ────────────────────────────────────────────────────────────

    def step(self, states: set[int], symbol: str) -> set[int]:
        result: set[int] = set()
        for s in states:
            for nxt in self.transitions.get(s, {}).get(symbol, set()):
                result.add(nxt)
        return self.epsilon_closure(result)

    # ── simulate ────────────────────────────────────────────────────────

    def matches(self, text: str) -> bool:
        current = self.epsilon_closure({0})
        for ch in text:
            current = self.step(current, ch)
            if not current:
                return False
        return self.accept in current


class ThompsonBuilder:
    """Builds a Thompson NFA from a regex AST."""

    def build(self, node: RegexNode) -> NFA:
        return self._build(node)

    def _build(self, node: RegexNode) -> NFA:
        if isinstance(node, Epsilon):
            return self._epsilon()
        if isinstance(node, Literal):
            return self._literal(node.char)
        if isinstance(node, Concat):
            return self._concat(self._build(node.left), self._build(node.right))
        if isinstance(node, Union):
            return self._union(self._build(node.left), self._build(node.right))
        if isinstance(node, Star):
            return self._star(self._build(node.child))
        raise TypeError(f"Unknown node type: {type(node)}")

    def _epsilon(self) -> NFA:
        nfa = NFA({}, 1)
        nfa._add_state()
        nfa._add_state()
        nfa._add_transition(0, None, 1)
        return nfa

    def _literal(self, char: str) -> NFA:
        nfa = NFA({}, 1)
        nfa._add_state()
        nfa._add_state()
        nfa._add_transition(0, char, 1)
        return nfa

    def _concat(self, left: NFA, right: NFA) -> NFA:
        offset = len(left.transitions)
        trans: dict[int, dict[str | None, set[int]]] = {}
        for sid, d in left.transitions.items():
            trans[sid] = {k: v.copy() for k, v in d.items()}
        for sid, d in right.transitions.items():
            trans[sid + offset] = {k: {x + offset for x in v} for k, v in d.items()}
        trans[left.accept].setdefault(None, set()).add(offset + 0)
        new_accept = right.accept + offset
        return NFA(trans, new_accept)

    def _union(self, left: NFA, right: NFA) -> NFA:
        offset_left = 1
        offset_right = 1 + len(left.transitions)
        trans: dict[int, dict[str | None, set[int]]] = {0: {}}
        for sid, d in left.transitions.items():
            trans[sid + offset_left] = {k: {x + offset_left for x in v} for k, v in d.items()}
        for sid, d in right.transitions.items():
            trans[sid + offset_right] = {k: {x + offset_right for x in v} for k, v in d.items()}
        new_accept = offset_right + len(right.transitions)
        trans[new_accept] = {}
        trans[0].setdefault(None, set()).update({offset_left, offset_right})
        trans[left.accept + offset_left].setdefault(None, set()).add(new_accept)
        trans[right.accept + offset_right].setdefault(None, set()).add(new_accept)
        return NFA(trans, new_accept)

    def _star(self, inner: NFA) -> NFA:
        offset = 1
        trans: dict[int, dict[str | None, set[int]]] = {0: {}}
        for sid, d in inner.transitions.items():
            trans[sid + offset] = {k: {x + offset for x in v} for k, v in d.items()}
        new_accept = offset + len(inner.transitions)
        trans[new_accept] = {}
        trans[0].setdefault(None, set()).update({offset, new_accept})
        trans[inner.accept + offset].setdefault(None, set()).update({offset, new_accept})
        return NFA(trans, new_accept)


# ── DFA (subset construction) ────────────────────────────────────────────────


class DFA:
    """Deterministic finite automaton built via subset construction.

    ``transitions`` maps ``(state, symbol) → next_state`` (total function).
    ``state <=> frozenset<int>`` of NFA states.
    """

    def __init__(self, transitions: dict[tuple[int, str], int], start: int, accepts: set[int]) -> None:
        self.transitions: dict[tuple[int, str], int] = transitions
        self.start: int = start
        self.accepts: set[int] = accepts

    def step(self, state: int, symbol: str) -> int | None:
        return self.transitions.get((state, symbol))

    def matches(self, text: str) -> bool:
        state = self.start
        for ch in text:
            nxt = self.transitions.get((state, ch))
            if nxt is None:
                return False
            state = nxt
        return state in self.accepts


def subset_construction(nfa: NFA) -> DFA:
    """Convert an NFA to an equivalent DFA via subset construction."""
    alphabet: set[str] = set()
    for d in nfa.transitions.values():
        for sym in d:
            if sym is not None:
                alphabet.add(sym)

    start_set = nfa.epsilon_closure({0})
    start_set_frozen = frozenset(start_set)

    state_id: dict[frozenset[int], int] = {start_set_frozen: 0}
    transitions: dict[tuple[int, str], int] = {}
    accepts: set[int] = set()
    queue: deque[frozenset[int]] = deque([start_set_frozen])
    next_id = 1

    while queue:
        current_frozen = queue.popleft()
        current_id = state_id[current_frozen]
        if nfa.accept in current_frozen:
            accepts.add(current_id)

        for sym in sorted(alphabet):
            nxt_set: set[int] = set()
            for s in current_frozen:
                for t in nfa.transitions.get(s, {}).get(sym, set()):
                    nxt_set.add(t)
            nxt_closure = nfa.epsilon_closure(nxt_set)
            nxt_frozen = frozenset(nxt_closure)
            if nxt_frozen not in state_id:
                state_id[nxt_frozen] = next_id
                next_id += 1
                queue.append(nxt_frozen)
            transitions[(current_id, sym)] = state_id[nxt_frozen]

    return DFA(transitions, 0, accepts)


# ── DFA minimization (Hopcroft) ──────────────────────────────────────────────


def minimize_dfa(dfa: DFA) -> DFA:
    """Minimize a DFA using Hopcroft's partition-refinement algorithm."""
    alphabet: set[str] = set()
    for _, sym in dfa.transitions:
        alphabet.add(sym)

    if not dfa.transitions:
        return dfa

    state_count = max(max(s for (s, _) in dfa.transitions), max(t for t in dfa.transitions.values())) + 1

    accept_set = frozenset(dfa.accepts)
    reachable = _reachable(dfa)
    non_accept_set = frozenset(s for s in range(state_count) if s not in dfa.accepts and s in reachable)

    partition: set[frozenset[int]] = set()
    if non_accept_set:
        partition.add(non_accept_set)
    partition.add(accept_set)

    worklist: list[frozenset[int]] = list(partition)

    while worklist:
        splitter = worklist.pop()
        for sym in sorted(alphabet):
            sources: dict[int, set[int]] = {}
            for block in list(partition):
                for s in block:
                    nxt = dfa.transitions.get((s, sym))
                    if nxt is not None and nxt in splitter:
                        sources.setdefault(id(block), set()).add(s)
            for block_id, affected in sources.items():
                for block in list(partition):
                    if id(block) == block_id:
                        intersection = block & affected
                        difference = block - affected
                        if intersection and difference:
                            partition.remove(block)
                            partition.add(frozenset(intersection))
                            partition.add(frozenset(difference))
                            if block in worklist:
                                worklist.remove(block)
                                worklist.append(frozenset(intersection))
                                worklist.append(frozenset(difference))
                            else:
                                worklist.append(frozenset(intersection))
                                worklist.append(frozenset(difference))
                            break

    new_id: dict[frozenset[int], int] = {}
    for i, block in enumerate(partition):
        new_id[block] = i

    new_transitions: dict[tuple[int, str], int] = {}
    new_accepts: set[int] = set()

    for block in partition:
        src_id = new_id[block]
        if any(s in dfa.accepts for s in block):
            new_accepts.add(src_id)
        rep = next(iter(block))
        for sym in sorted(alphabet):
            nxt = dfa.transitions.get((rep, sym))
            if nxt is not None:
                for target_block in partition:
                    if nxt in target_block:
                        new_transitions[(src_id, sym)] = new_id[target_block]
                        break

    start_block = None
    for block in partition:
        if dfa.start in block:
            start_block = block
            break
    new_start = new_id[start_block] if start_block is not None else 0

    return DFA(new_transitions, new_start, new_accepts)


def _reachable(dfa: DFA) -> set[int]:
    visited: set[int] = set()
    stack = [dfa.start]
    while stack:
        s = stack.pop()
        if s in visited:
            continue
        visited.add(s)
        for (src, _), dst in dfa.transitions.items():
            if src == s:
                stack.append(dst)
    return visited


# ── Top-level compile ────────────────────────────────────────────────────────


def compile_regex(pattern: str, minimize: bool = False) -> DFA:
    """Parse *pattern*, build a Thompson NFA, convert to DFA via subset
    construction, and optionally minimize the DFA with Hopcroft's algorithm.

    Returns a ``DFA`` whose ``matches(text)`` method answers membership.
    """
    ast = RegexParser(pattern).parse()
    nfa = ThompsonBuilder().build(ast)
    dfa = subset_construction(nfa)
    if minimize:
        dfa = minimize_dfa(dfa)
    return dfa
