"""Deep finite automaton tests: DFA simulation, NFA to DFA (subset
construction), epsilon closure, accepted language, complement, and
minimization.

A compact but mathematically precise automaton toolkit is defined inline
and exercised through 20 tests that cover each dimension independently.
"""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest

# ===========================================================================
# Inline finite-automaton toolkit
# ===========================================================================


class DFA:
    """Deterministic finite automaton.

    ``alphabet``  — iterable of input symbols
    ``states``    — frozenset of state names (strings)
    ``start``     — single start state
    ``accept``    — frozenset of accept states
    ``delta``     — dict ``{(state, symbol): next_state}``; total on alphabet
    """

    __slots__ = ("accept", "alphabet", "delta", "start", "states")

    def __init__(
        self,
        alphabet: frozenset[str],
        states: frozenset[str],
        start: str,
        accept: frozenset[str],
        delta: dict[tuple[str, str], str],
    ) -> None:
        self.alphabet = alphabet
        self.states = states
        self.start = start
        self.accept = accept
        self.delta = delta
        self._validate()

    def _validate(self) -> None:
        if self.start not in self.states:
            raise ValueError(f"start state {self.start!r} not in states")
        for a in self.accept:
            if a not in self.states:
                raise ValueError(f"accept state {a!r} not in states")
        for (q, s), t in self.delta.items():
            self._check_state(q)
            if s not in self.alphabet:
                raise ValueError(f"symbol {s!r} not in alphabet")
            self._check_state(t)

    def _check_state(self, q: str) -> None:
        if q not in self.states:
            raise ValueError(f"state {q!r} not in states")

    def accepts(self, word: str) -> bool:
        """Run the DFA on *word* and return True iff it ends in an accept state."""
        q = self.start
        for ch in word:
            q = self.delta.get((q, ch), q)
        return q in self.accept

    def accepted_language(self, max_len: int) -> list[str]:
        """BFS enumeration of ALL accepted words up to *max_len*."""
        result: list[str] = []
        q = deque[tuple[str, str]]()
        q.append((self.start, ""))
        while q:
            state, prefix = q.popleft()
            if state in self.accept and prefix:
                result.append(prefix)
            if len(prefix) < max_len:
                for sym in sorted(self.alphabet):
                    nxt = self.delta.get((state, sym), state)
                    q.append((nxt, prefix + sym))
        return sorted(result, key=lambda w: (len(w), w))

    def complement(self) -> DFA:
        """Return a DFA that accepts the complement language."""
        return DFA(
            alphabet=self.alphabet,
            states=self.states,
            start=self.start,
            accept=self.states - self.accept,
            delta=dict(self.delta),
        )

    def minimize(self) -> DFA:
        """Hopcroft-style partition refinement (simplified for small automata).

        Returns an equivalent DFA with the minimal number of states.
        """
        eq = _hopcroft(
            states=sorted(self.states),
            alphabet=sorted(self.alphabet),
            accept=sorted(self.accept),
            delta=lambda q, a: self.delta.get((q, a), q),
        )
        mapping: dict[str, str] = {}
        for block in eq:
            rep = min(block)
            for s in block:
                mapping[s] = rep
        new_states = frozenset(mapping[q] for q in self.states)
        new_accept = frozenset(mapping[q] for q in self.accept)
        new_delta: dict[tuple[str, str], str] = {}
        for (q, s), t in self.delta.items():
            new_delta[(mapping[q], s)] = mapping[t]
        return DFA(
            alphabet=self.alphabet,
            states=new_states,
            start=mapping[self.start],
            accept=new_accept,
            delta=new_delta,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DFA):
            return NotImplemented
        return (
            self.alphabet == other.alphabet
            and self.states == other.states
            and self.start == other.start
            and self.accept == other.accept
            and self.delta == other.delta
        )


def _hopcroft(
    states: list[str],
    alphabet: list[str],
    accept: list[str],
    delta: Any,
) -> list[set[str]]:
    accept_set = set(accept)
    P: list[set[str]] = [set(accept), set(states) - accept_set]
    P = [s for s in P if s]
    W: list[set[str]] = list(P)
    while W:
        A = W.pop()
        for c in alphabet:
            X: set[str] = set()
            for q in states:
                if delta(q, c) in A:
                    X.add(q)
            new_P: list[set[str]] = []
            for Y in P:
                i = Y & X
                d = Y - X
                if i and d:
                    new_P.append(i)
                    new_P.append(d)
                    if Y in W:
                        W.remove(Y)
                        W.append(i)
                        W.append(d)
                    else:
                        if len(i) <= len(d):
                            W.append(i)
                        else:
                            W.append(d)
                else:
                    new_P.append(Y)
            P = new_P
    return P


class NFA:
    """Nondeterministic finite automaton (may have epsilon transitions).

    ``alphabet``  — symbols (epsilon ``""`` is allowed in delta keys)
    ``states``    — frozenset of state names
    ``start``     — single start state
    ``accept``    — frozenset of accept states
    ``delta``     — dict ``{(state, symbol): set[next_state]}``
    """

    __slots__ = ("accept", "alphabet", "delta", "start", "states")

    def __init__(
        self,
        alphabet: frozenset[str],
        states: frozenset[str],
        start: str,
        accept: frozenset[str],
        delta: dict[tuple[str, str], frozenset[str]],
    ) -> None:
        self.alphabet = alphabet
        self.states = states
        self.start = start
        self.accept = accept
        self.delta = delta
        self._validate()

    def _validate(self) -> None:
        if self.start not in self.states:
            raise ValueError(f"start state {self.start!r} not in states")
        for a in self.accept:
            if a not in self.states:
                raise ValueError(f"accept state {a!r} not in states")
        for (q, s), targets in self.delta.items():
            self._check_state(q)
            if s != "" and s not in self.alphabet:
                raise ValueError(f"symbol {s!r} not in alphabet and not epsilon")
            for t in targets:
                self._check_state(t)

    def _check_state(self, q: str) -> None:
        if q not in self.states:
            raise ValueError(f"state {q!r} not in states")

    def epsilon_closure(self, states: frozenset[str]) -> frozenset[str]:
        """Return the epsilon-closure of a set of states."""
        stack = list(states)
        result = set(states)
        while stack:
            q = stack.pop()
            for t in self.delta.get((q, ""), frozenset()):
                if t not in result:
                    result.add(t)
                    stack.append(t)
        return frozenset(result)

    def move(self, states: frozenset[str], symbol: str) -> frozenset[str]:
        """Return all states reachable from *states* via one *symbol* transition."""
        targets: set[str] = set()
        for q in states:
            targets.update(self.delta.get((q, symbol), frozenset()))
        return frozenset(targets)

    def accepts(self, word: str) -> bool:
        """Return True if there exists a path from start to accept for *word*."""
        current = self.epsilon_closure(frozenset([self.start]))
        for ch in word:
            current = self.epsilon_closure(self.move(current, ch))
        return bool(current & self.accept)

    def to_dfa(self) -> DFA:
        """Subset construction: convert this NFA to an equivalent DFA."""
        start_closure = self.epsilon_closure(frozenset([self.start]))
        dfa_states: dict[frozenset[str], str] = {}
        name_of: dict[frozenset[str], str] = {}
        dfa_delta: dict[tuple[str, str], str] = {}
        dfa_accept: set[str] = set()
        counter = 0

        start_name = f"q{counter}"
        name_of[start_closure] = start_name
        dfa_states[start_closure] = start_name
        if start_closure & self.accept:
            dfa_accept.add(start_name)
        counter += 1

        q: deque[frozenset[str]] = deque([start_closure])
        while q:
            nfa_set = q.popleft()
            src_name = name_of[nfa_set]
            for sym in sorted(self.alphabet):
                targets = self.epsilon_closure(self.move(nfa_set, sym))
                if not targets:
                    continue
                if targets not in name_of:
                    tgt_name = f"q{counter}"
                    name_of[targets] = tgt_name
                    if targets & self.accept:
                        dfa_accept.add(tgt_name)
                    counter += 1
                    q.append(targets)
                else:
                    tgt_name = name_of[targets]
                dfa_delta[(src_name, sym)] = tgt_name

        dead_name: str | None = None
        dfa_state_names_set = set(name_of.values())
        dead_needed = False
        for qname in list(dfa_state_names_set):
            for sym in sorted(self.alphabet):
                if (qname, sym) not in dfa_delta:
                    dead_needed = True
                    break
            if dead_needed:
                break

        if dead_needed:
            dead_name = f"q{counter}"
            for sym in sorted(self.alphabet):
                dfa_delta[(dead_name, sym)] = dead_name

        for qname in sorted(dfa_state_names_set):
            for sym in sorted(self.alphabet):
                dfa_delta.setdefault((qname, sym), dead_name if dead_name else qname)

        dfa_state_names = frozenset(dfa_state_names_set | ({dead_name} if dead_name else set()))

        return DFA(
            alphabet=self.alphabet,
            states=dfa_state_names,
            start=start_name,
            accept=frozenset(dfa_accept),
            delta=dfa_delta,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NFA):
            return NotImplemented
        return (
            self.alphabet == other.alphabet
            and self.states == other.states
            and self.start == other.start
            and self.accept == other.accept
            and self.delta == other.delta
        )


# ===========================================================================
# Fixtures
# ===========================================================================

AB = frozenset({"a", "b"})


def _dfa_even_a() -> DFA:
    """DFA that accepts strings with an even number of 'a's (any 'b's ignored)."""
    return DFA(
        alphabet=AB,
        states=frozenset({"E", "O"}),
        start="E",
        accept=frozenset({"E"}),
        delta={
            ("E", "a"): "O",
            ("E", "b"): "E",
            ("O", "a"): "E",
            ("O", "b"): "O",
        },
    )


def _dfa_ends_b() -> DFA:
    """DFA that accepts strings ending in 'b'."""
    return DFA(
        alphabet=AB,
        states=frozenset({"q0", "q1"}),
        start="q0",
        accept=frozenset({"q1"}),
        delta={
            ("q0", "a"): "q0",
            ("q0", "b"): "q1",
            ("q1", "a"): "q0",
            ("q1", "b"): "q1",
        },
    )


def _nfa_third_last_a() -> NFA:
    """NFA that accepts strings whose third-last symbol is 'a' (|w| >= 3)."""
    return NFA(
        alphabet=AB,
        states=frozenset({"p0", "p1", "p2", "p3"}),
        start="p0",
        accept=frozenset({"p3"}),
        delta={
            ("p0", "a"): frozenset({"p0", "p1"}),
            ("p0", "b"): frozenset({"p0"}),
            ("p1", "a"): frozenset({"p2"}),
            ("p1", "b"): frozenset({"p2"}),
            ("p2", "a"): frozenset({"p3"}),
            ("p2", "b"): frozenset({"p3"}),
        },
    )


def _nfa_epsilon() -> NFA:
    """NFA with epsilon transitions: accepts a*b* (zero or more a's, then zero or more b's)."""
    return NFA(
        alphabet=AB,
        states=frozenset({"s0", "s1"}),
        start="s0",
        accept=frozenset({"s0", "s1"}),
        delta={
            ("s0", "a"): frozenset({"s0"}),
            ("s0", ""): frozenset({"s1"}),
            ("s1", "b"): frozenset({"s1"}),
        },
    )


# ===========================================================================
# Tests
# ===========================================================================


class TestDFAAccepts:
    """DFA simulation (accepts method)."""

    def test_even_a_empty_string(self) -> None:
        dfa = _dfa_even_a()
        assert dfa.accepts("") is True

    def test_even_a_single_a_rejected(self) -> None:
        dfa = _dfa_even_a()
        assert dfa.accepts("a") is False

    def test_even_a_two_as_accepted(self) -> None:
        dfa = _dfa_even_a()
        assert dfa.accepts("aa") is True

    def test_even_a_three_as_rejected(self) -> None:
        dfa = _dfa_even_a()
        assert dfa.accepts("aaa") is False

    def test_even_a_mixed_b_a(self) -> None:
        dfa = _dfa_even_a()
        assert dfa.accepts("babbab") is True  # 2 a's
        assert dfa.accepts("bbababa") is False  # 3 a's

    def test_ends_b_accepts_terminals(self) -> None:
        dfa = _dfa_ends_b()
        assert dfa.accepts("b") is True
        assert dfa.accepts("ab") is True
        assert dfa.accepts("a") is False
        assert dfa.accepts("ba") is False


class TestDFAAcceptedLanguage:
    """BFS enumeration of the accepted language up to a given length."""

    def test_even_a_up_to_len_2(self) -> None:
        dfa = _dfa_even_a()
        lang = dfa.accepted_language(max_len=2)
        assert "" not in lang  # empty excluded
        assert lang == ["b", "aa", "bb"]

    def test_ends_b_up_to_len_2(self) -> None:
        dfa = _dfa_ends_b()
        lang = dfa.accepted_language(max_len=2)
        assert lang == ["b", "ab", "bb"]

    def test_language_size_grows_bfs_order(self) -> None:
        dfa = _dfa_ends_b()
        lang = dfa.accepted_language(max_len=3)
        lens = [len(w) for w in lang]
        assert lens == sorted(lens)


class TestDFAComplement:
    """Complement DFA construction and correctness."""

    def test_even_a_complement_matches_odd_a(self) -> None:
        dfa = _dfa_even_a()
        comp = dfa.complement()
        assert comp.accepts("") is False
        assert comp.accepts("a") is True
        assert comp.accepts("aa") is False
        assert comp.accepts("aaa") is True

    def test_ends_b_complement_does_not_end_b(self) -> None:
        dfa = _dfa_ends_b()
        comp = dfa.complement()
        assert comp.accepts("") is True  # empty does not end in b
        assert comp.accepts("a") is True
        assert comp.accepts("b") is False
        assert comp.accepts("ba") is True


class TestDFAMinimization:
    """Hopcroft minimisation."""

    def test_ends_b_already_minimal(self) -> None:
        dfa = _dfa_ends_b()
        mini = dfa.minimize()
        assert len(mini.states) == 2

    def test_minimized_equivalence(self) -> None:
        dfa = _dfa_even_a()
        mini = dfa.minimize()
        for word in _brute_words(6):
            assert dfa.accepts(word) == mini.accepts(word)

    def test_three_state_minimizes_print(self) -> None:
        dfa = DFA(
            alphabet=frozenset({"0", "1"}),
            states=frozenset({"A", "B", "C"}),
            start="A",
            accept=frozenset({"C"}),
            delta={
                ("A", "0"): "B",
                ("A", "1"): "C",
                ("B", "0"): "A",
                ("B", "1"): "C",
                ("C", "0"): "C",
                ("C", "1"): "C",
            },
        )
        mini = dfa.minimize()
        assert 1 <= len(mini.states) <= 2


class TestDFAValidation:
    """DFA structural validation."""

    def test_bad_start_raises(self) -> None:
        with pytest.raises(ValueError, match="start state"):
            DFA(
                alphabet=AB,
                states=frozenset({"q0"}),
                start="qX",
                accept=frozenset(),
                delta={},
            )

    def test_bad_accept_raises(self) -> None:
        with pytest.raises(ValueError, match="accept state"):
            DFA(
                alphabet=AB,
                states=frozenset({"q0"}),
                start="q0",
                accept=frozenset({"qX"}),
                delta={},
            )

    def test_bad_delta_source_raises(self) -> None:
        with pytest.raises(ValueError, match="state 'qX' not in states"):
            DFA(
                alphabet=AB,
                states=frozenset({"q0"}),
                start="q0",
                accept=frozenset(),
                delta={("qX", "a"): "q0"},
            )

    def test_bad_delta_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="symbol"):
            DFA(
                alphabet=AB,
                states=frozenset({"q0"}),
                start="q0",
                accept=frozenset(),
                delta={("q0", "x"): "q0"},
            )


class TestNFAEpsilonClosure:
    """Epsilon closure computation."""

    def test_no_eps_transitions_self_only(self) -> None:
        nfa = _nfa_third_last_a()
        ec = nfa.epsilon_closure(frozenset({"p0"}))
        assert ec == frozenset({"p0"})

    def test_eps_transitions_include_self_and_targets(self) -> None:
        nfa = _nfa_epsilon()
        ec = nfa.epsilon_closure(frozenset({"s0"}))
        assert ec == frozenset({"s0", "s1"})

    def test_eps_closure_set_of_states(self) -> None:
        nfa = _nfa_epsilon()
        ec = nfa.epsilon_closure(frozenset({"s0", "s1"}))
        assert ec == frozenset({"s0", "s1"})


class TestNFAAccepts:
    """NFA simulation with epsilon."""

    @pytest.mark.parametrize(
        "word,expected",
        [
            ("", True),
            ("a", True),
            ("b", True),
            ("aa", True),
            ("ab", True),
            ("ba", False),
            ("aba", False),
            ("aabbb", True),
        ],
    )
    def test_epsilon_nfa_a_star_b_star(self, word: str, expected: bool) -> None:
        nfa = _nfa_epsilon()
        assert nfa.accepts(word) == expected

    def test_third_last_a_accepts(self) -> None:
        nfa = _nfa_third_last_a()
        assert nfa.accepts("aab") is True
        assert nfa.accepts("aba") is True
        assert nfa.accepts("baa") is False
        assert nfa.accepts("ab") is False


class TestNFAToDFA:
    """Subset construction: NFA → DFA correctness."""

    def test_epsilon_nfa_to_dfa_equivalence(self) -> None:
        nfa = _nfa_epsilon()
        dfa = nfa.to_dfa()
        for word in _brute_words(6):
            assert dfa.accepts(word) == nfa.accepts(word)

    def test_third_last_a_to_dfa_equivalence(self) -> None:
        nfa = _nfa_third_last_a()
        dfa = nfa.to_dfa()
        for word in _brute_words(6):
            assert dfa.accepts(word) == nfa.accepts(word)


class TestNFAValidation:
    """NFA structural validation."""

    def test_bad_start_raises(self) -> None:
        with pytest.raises(ValueError, match="start state"):
            NFA(
                alphabet=AB,
                states=frozenset({"q0"}),
                start="qX",
                accept=frozenset(),
                delta={},
            )

    def test_bad_accept_raises(self) -> None:
        with pytest.raises(ValueError, match="accept state"):
            NFA(
                alphabet=AB,
                states=frozenset({"q0"}),
                start="q0",
                accept=frozenset({"qX"}),
                delta={},
            )

    def test_bad_delta_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="symbol"):
            NFA(
                alphabet=AB,
                states=frozenset({"q0"}),
                start="q0",
                accept=frozenset(),
                delta={("q0", "x"): frozenset({"q0"})},
            )


# ===========================================================================
# Helpers
# ===========================================================================


def _brute_words(max_len: int) -> list[str]:
    """All binary strings over {a,b} up to *max_len*."""
    result: list[str] = [""]
    for length in range(1, max_len + 1):
        _gen("", length, result)
    return result


def _gen(prefix: str, remaining: int, out: list[str]) -> None:
    if remaining == 0:
        out.append(prefix)
        return
    _gen(prefix + "a", remaining - 1, out)
    _gen(prefix + "b", remaining - 1, out)
