"""Levenshtein automaton: NFA construction, DFA conversion, and fuzzy
matching within edit distance k.

The classic construction models each state as a pair ``(pos, err)`` where
``pos`` is the position matched in the pattern and ``err`` is the
accumulated edit count.  On each input character the automaton can
traverse four kinds of edges: match, substitution, insertion, and deletion
(via an epsilon transition).
"""

from __future__ import annotations

from collections import deque
from typing import Any


def _levenshtein_distance_naive(s: str, t: str) -> int:
    """Classic O(|s|·|t|) dynamic-programming edit distance (reference)."""
    m, n = len(s), len(t)
    prev = list(range(n + 1))
    cur = [0] * (n + 1)
    for i in range(1, m + 1):
        cur[0] = i
        for j in range(1, n + 1):
            if s[i - 1] == t[j - 1]:
                cur[j] = prev[j - 1]
            else:
                cur[j] = 1 + min(prev[j], cur[j - 1], prev[j - 1])
        prev, cur = cur, prev
    return prev[n]


# ---------------------------------------------------------------------------
# Levenshtein NFA
# ---------------------------------------------------------------------------


def build_levenshtein_nfa(
    pattern: str,
    k: int,
    alphabet: frozenset[str],
) -> dict[str, Any]:
    """Construct a Levenshtein NFA for *pattern* and max edit distance *k*.

    Returns a plain dict (not an NFA object) to keep the implementation
    self-contained.  Key fields:

    * **states** — ``frozenset[(int,int)]`` of ``(pos, err)`` pairs
    * **start** — ``frozenset[(int,int)]`` after epsilon-closure of ``(0,0)``
    * **accept** — ``frozenset[(int,int)]`` where ``pos == |pattern|`` and
      ``err <= k``
    * **delta** — ``dict[((int,int), str), frozenset[(int,int)]]`` — the NFA
      transition function (including epsilon as key ``""``)
    * **pattern** / **k** / **alphabet** — book-keeping

    The construction follows the standard parameterized model (Schulz &
    Mihov 2002, simplified to a dense NFA that is then subset-converted).
    """
    m = len(pattern)
    states = frozenset((pos, err) for pos in range(m + 1) for err in range(k + 1))
    accept = frozenset((m, err) for err in range(k + 1))

    epsilon: dict[tuple[int, int], frozenset[tuple[int, int]]] = {}
    char_delta: dict[tuple[tuple[int, int], str], frozenset[tuple[int, int]]] = {}

    for pos in range(m + 1):
        for err in range(k + 1):
            key = (pos, err)

            eps_targets: set[tuple[int, int]] = set()
            if pos < m and err < k:
                eps_targets.add((pos + 1, err + 1))
            epsilon[key] = frozenset(eps_targets)

            for ch in alphabet:
                targets: set[tuple[int, int]] = set()
                if pos < m and ch == pattern[pos]:
                    targets.add((pos + 1, err))
                if pos < m and err < k:
                    targets.add((pos + 1, err + 1))
                if err < k:
                    targets.add((pos, err + 1))
                if targets:
                    char_delta[(key, ch)] = frozenset(targets)

    start_closure = _epsilon_closure_simple(frozenset([(0, 0)]), epsilon)

    return {
        "states": states,
        "start": start_closure,
        "accept": accept,
        "delta": char_delta,
        "epsilon": epsilon,
        "pattern": pattern,
        "k": k,
        "alphabet": alphabet,
    }


def _epsilon_closure_simple(
    states: frozenset[tuple[int, int]],
    epsilon: dict[tuple[int, int], frozenset[tuple[int, int]]],
) -> frozenset[tuple[int, int]]:
    """BFS epsilon-closure over the given *epsilon* relation."""
    stack = list(states)
    result = set(states)
    while stack:
        q = stack.pop()
        for t in epsilon.get(q, frozenset()):
            if t not in result:
                result.add(t)
                stack.append(t)
    return frozenset(result)


def _nfa_move(
    states: frozenset[tuple[int, int]],
    symbol: str,
    delta: dict[tuple[tuple[int, int], str], frozenset[tuple[int, int]]],
    epsilon: dict[tuple[int, int], frozenset[tuple[int, int]]],
) -> frozenset[tuple[int, int]]:
    """One NFA step: consume *symbol*, then apply epsilon-closure."""
    reached: set[tuple[int, int]] = set()
    for q in states:
        reached.update(delta.get((q, symbol), frozenset()))
    return _epsilon_closure_simple(frozenset(reached), epsilon)


# ---------------------------------------------------------------------------
# Subset construction → DFA
# ---------------------------------------------------------------------------


def nfa_to_dfa(
    nfa: dict[str, Any],
) -> dict[str, Any]:
    """Subset-construction: convert a Levenshtein NFA dict to a DFA dict.

    The returned dict has keys: ``states`` (frozenset of canonical names),
    ``start``, ``accept``, ``delta`` (dict of ``(name, symbol) → name``),
    ``alphabet``.
    """
    start_set = nfa["start"]
    alphabet: frozenset[str] = nfa["alphabet"]
    accept: frozenset[tuple[int, int]] = nfa["accept"]
    epsilon = nfa["epsilon"]
    delta_nfa = nfa["delta"]

    name_of: dict[frozenset[tuple[int, int]], str] = {}
    dfa_accept: set[str] = set()
    dfa_delta: dict[tuple[str, str], str] = {}
    counter = 0

    start_name = f"q{counter}"
    name_of[start_set] = start_name
    if start_set & accept:
        dfa_accept.add(start_name)
    counter += 1

    q: deque[frozenset[tuple[int, int]]] = deque([start_set])
    while q:
        nfa_set = q.popleft()
        src_name = name_of[nfa_set]
        for sym in sorted(alphabet):
            targets = _nfa_move(nfa_set, sym, delta_nfa, epsilon)
            if not targets:
                continue
            if targets not in name_of:
                tgt_name = f"q{counter}"
                name_of[targets] = tgt_name
                if targets & accept:
                    dfa_accept.add(tgt_name)
                counter += 1
                q.append(targets)
            else:
                tgt_name = name_of[targets]
            dfa_delta[(src_name, sym)] = tgt_name

    dead_name: str | None = None
    dfa_state_names_set = set(name_of.values())
    dead_needed = any((qname, sym) not in dfa_delta for qname in dfa_state_names_set for sym in sorted(alphabet))

    if dead_needed:
        dead_name = f"q{counter}"
        for sym in sorted(alphabet):
            dfa_delta[(dead_name, sym)] = dead_name

    for qname in sorted(dfa_state_names_set):
        for sym in sorted(alphabet):
            dfa_delta.setdefault((qname, sym), dead_name if dead_name else qname)

    dfa_state_names = frozenset(dfa_state_names_set | ({dead_name} if dead_name else set()))

    return {
        "states": dfa_state_names,
        "start": start_name,
        "accept": frozenset(dfa_accept),
        "delta": dfa_delta,
        "alphabet": alphabet,
        "pattern": nfa["pattern"],
        "k": nfa["k"],
    }


# ---------------------------------------------------------------------------
# LevenshteinAutomaton — public API
# ---------------------------------------------------------------------------


class LevenshteinAutomaton:
    """Deterministic Levenshtein automaton for fuzzy matching.

    Builds the NFA for *pattern* with max edit distance *k* over
    *alphabet*, converts it to a DFA, and exposes O(|text|) matching.
    """

    __slots__ = ("_accept", "_alphabet", "_delta", "_k", "_pattern", "_start")

    def __init__(self, pattern: str, k: int, alphabet: frozenset[str] | None = None) -> None:
        self._pattern = pattern
        self._k = k

        if alphabet is None:
            alpha: set[str] = set(pattern)
            if k > 0:
                alpha.update(chr(c) for c in range(97, 123))
            self._alphabet: frozenset[str] = frozenset(alpha)
        else:
            self._alphabet = alphabet

        nfa = build_levenshtein_nfa(pattern, k, self._alphabet)
        dfa = nfa_to_dfa(nfa)

        self._start = dfa["start"]
        self._accept = dfa["accept"]
        self._delta = dfa["delta"]

    @property
    def pattern(self) -> str:
        return self._pattern

    @property
    def k(self) -> int:
        return self._k

    def accepts(self, text: str) -> bool:
        """Return True if *text* is within edit distance *k* of the pattern."""
        q = self._start
        for ch in text:
            nxt = self._delta.get((q, ch))
            if nxt is None:
                return False
            q = nxt
        return q in self._accept

    def accepted_language(self, max_len: int) -> list[str]:
        """BFS enumerate all accepted words up to *max_len*."""
        result: list[str] = []
        q: deque[tuple[str, str]] = deque()
        q.append((self._start, ""))
        while q:
            state, prefix = q.popleft()
            if prefix and state in self._accept:
                result.append(prefix)
            if len(prefix) < max_len:
                for sym in sorted(self._alphabet):
                    nxt = self._delta.get((state, sym), self._start)
                    q.append((nxt, prefix + sym))
        return sorted(result, key=lambda w: (len(w), w))

    def state_count(self) -> int:
        states: set[str] = set()
        q: deque[str] = deque([self._start])
        while q:
            s = q.popleft()
            if s in states:
                continue
            states.add(s)
            for ch in self._alphabet:
                tgt = self._delta.get((s, ch), s)
                if tgt not in states:
                    q.append(tgt)
        return len(states)


def fuzzy_match(pattern: str, text: str, k: int) -> bool:
    """Convenience: return True if *text* is within edit distance *k* of
    *pattern* (using a Levenshtein automaton).
    """
    auto = LevenshteinAutomaton(pattern, k)
    return auto.accepts(text)
