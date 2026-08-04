"""Deep Levenshtein automaton tests: NFA construction, DFA conversion,
fuzzy matching within edit distance k, cross-validation against naive
edit-distance, and structural properties of the automaton.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.levenshtein_automaton import (
    LevenshteinAutomaton,
    _epsilon_closure_simple,
    _levenshtein_distance_naive,
    _nfa_move,
    build_levenshtein_nfa,
    fuzzy_match,
    nfa_to_dfa,
)

AB = frozenset({"a", "b"})
DNA = frozenset({"A", "C", "G", "T"})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _naive_match(pattern: str, text: str, k: int) -> bool:
    return _levenshtein_distance_naive(pattern, text) <= k


# ---------------------------------------------------------------------------
# NFA construction
# ---------------------------------------------------------------------------


class TestBuildLevenshteinNFA:
    def test_state_count_is_k_plus_1_times_m_plus_1(self) -> None:
        for pat, k in [("ab", 0), ("ab", 1), ("abc", 2), ("abcde", 3)]:
            nfa = build_levenshtein_nfa(pat, k, AB)
            expected = (k + 1) * (len(pat) + 1)
            assert len(nfa["states"]) == expected

    def test_accept_states_are_all_m_pos_for_err_up_to_k(self) -> None:
        nfa = build_levenshtein_nfa("abc", 2, AB)
        accept = nfa["accept"]
        m = len("abc")
        assert accept == frozenset({(m, 0), (m, 1), (m, 2)})

    def test_start_closure_includes_self(self) -> None:
        nfa = build_levenshtein_nfa("hello", 1, AB)
        assert (0, 0) in nfa["start"]

    def test_epsilon_transitions_allow_deletion(self) -> None:
        nfa = build_levenshtein_nfa("abc", 1, AB)
        eps = nfa["epsilon"]
        assert (1, 1) in eps.get((0, 0), frozenset())

    def test_no_epsilon_when_k_is_zero(self) -> None:
        nfa = build_levenshtein_nfa("abc", 0, AB)
        for _key, targets in nfa["epsilon"].items():
            assert len(targets) == 0

    def test_delta_includes_match_insert_substitute(self) -> None:
        nfa = build_levenshtein_nfa("a", 1, AB)
        delta = nfa["delta"]
        match_targets = delta.get(((0, 0), "a"), frozenset())
        assert (1, 0) in match_targets
        sub_targets = delta.get(((0, 0), "b"), frozenset())
        assert (1, 1) in sub_targets
        ins_targets = delta.get(((0, 0), "b"), frozenset())
        assert (0, 1) in ins_targets


# ---------------------------------------------------------------------------
# epsilon-closure unit
# ---------------------------------------------------------------------------


class TestEpsilonClosureSimple:
    def test_single_step_closure(self) -> None:
        eps = {(0, 0): frozenset({(0, 1), (1, 1)})}
        result = _epsilon_closure_simple(frozenset({(0, 0)}), eps)
        assert result == frozenset({(0, 0), (0, 1), (1, 1)})

    def test_idempotent(self) -> None:
        eps = {(0, 0): frozenset({(0, 1)}), (0, 1): frozenset({(0, 2)})}
        c1 = _epsilon_closure_simple(frozenset({(0, 0)}), eps)
        c2 = _epsilon_closure_simple(c1, eps)
        assert c1 == c2


# ---------------------------------------------------------------------------
# NFA → DFA subset construction
# ---------------------------------------------------------------------------


class TestNFAToDFA:
    def test_dfa_same_language_as_nfa_exact(self) -> None:
        nfa = build_levenshtein_nfa("ab", 0, AB)
        dfa = nfa_to_dfa(nfa)
        nfa_accept = _nfa_accepts(nfa, "ab")
        nfa_reject = _nfa_accepts(nfa, "aa")
        assert nfa_accept is True
        assert nfa_reject is False
        dfa_auto = _dfa_from_dict(dfa)
        assert dfa_auto.accepts("ab") is True
        assert dfa_auto.accepts("aa") is False

    def test_dfa_preserves_language_for_edit_distance_1(self) -> None:
        nfa = build_levenshtein_nfa("abb", 1, AB)
        dfa = nfa_to_dfa(nfa)
        dfa_auto = _dfa_from_dict(dfa)
        cases = [
            ("abb", True),
            ("aba", True),
            ("ab", True),
            ("aab", True),
            ("abbb", True),
            ("", False),
            ("baa", False),
            ("aaabb", False),
        ]
        for text, expected in cases:
            assert dfa_auto.accepts(text) == expected, f"text={text!r}"

    def test_dfa_start_state_is_accept_for_k_ge_m(self) -> None:
        nfa = build_levenshtein_nfa("a", 1, AB)
        dfa = nfa_to_dfa(nfa)
        dfa_auto = _dfa_from_dict(dfa)
        assert dfa_auto.accepts("") is True


# ---------------------------------------------------------------------------
# LevenshteinAutomaton matching — cross-validated
# ---------------------------------------------------------------------------


class TestLevenshteinAutomatonMatching:
    def test_exact_match_k0(self) -> None:
        auto = LevenshteinAutomaton("hello", 0, DNA)
        assert auto.accepts("hello") is False
        auto2 = LevenshteinAutomaton("hello", 0)
        assert auto2.accepts("hello") is True
        assert auto2.accepts("hellx") is False

    def test_single_substitution_k1(self) -> None:
        auto = LevenshteinAutomaton("cat", 1)
        assert auto.accepts("cat") is True
        assert auto.accepts("bat") is True
        assert auto.accepts("cot") is True
        assert auto.accepts("car") is True
        assert auto.accepts("dog") is False

    def test_single_deletion_k1(self) -> None:
        auto = LevenshteinAutomaton("abcd", 1)
        assert auto.accepts("bcd") is True
        assert auto.accepts("acd") is True
        assert auto.accepts("abd") is True
        assert auto.accepts("abc") is True
        assert auto.accepts("cd") is False

    def test_single_insertion_k1(self) -> None:
        auto = LevenshteinAutomaton("xy", 1)
        assert auto.accepts("xyz") is True
        assert auto.accepts("xay") is True
        assert auto.accepts("axy") is True
        assert auto.accepts("x") is True
        assert auto.accepts("xyzz") is False

    def test_two_edits_k2(self) -> None:
        auto = LevenshteinAutomaton("hello", 2)
        assert auto.accepts("hallo") is True
        assert auto.accepts("helo") is True
        assert auto.accepts("hell") is True
        assert auto.accepts("hllo") is True
        assert auto.accepts("help") is True
        assert auto.accepts("hhh") is False

    def test_empty_pattern(self) -> None:
        auto = LevenshteinAutomaton("", 1)
        assert auto.accepts("") is True
        assert auto.accepts("a") is True
        assert auto.accepts("ab") is False

    def test_empty_text_k0(self) -> None:
        auto = LevenshteinAutomaton("abc", 0)
        assert auto.accepts("") is False
        auto2 = LevenshteinAutomaton("", 0)
        assert auto2.accepts("") is True

    @pytest.mark.parametrize("pattern", ["ab", "abc", "abcd", "abcde"])
    @pytest.mark.parametrize("k", [0, 1, 2])
    def test_brute_force_cross_validation(self, pattern: str, k: int) -> None:
        """Exhaustively compare automaton against naive O(|P|·|t|) DP."""
        auto = LevenshteinAutomaton(pattern, k, AB)
        words = _gen_all_strings(AB, max_len=5)
        mismatches = []
        for w in words:
            auto_result = auto.accepts(w)
            naive_result = _naive_match(pattern, w, k)
            if auto_result != naive_result:
                mismatches.append((w, auto_result, naive_result))
        assert not mismatches, f"k={k} pattern={pattern!r}: {len(mismatches)} mismatches; first 5: {mismatches[:5]}"


# ---------------------------------------------------------------------------
# fuzzy_match convenience
# ---------------------------------------------------------------------------


class TestFuzzyMatch:
    def test_convenience_wrapper(self) -> None:
        assert fuzzy_match("abc", "abc", 0) is True
        assert fuzzy_match("abc", "axc", 1) is True
        assert fuzzy_match("abc", "xyz", 1) is False


# ---------------------------------------------------------------------------
# accepted_language enumeration
# ---------------------------------------------------------------------------


class TestAcceptedLanguage:
    def test_language_matches_enumerate_vs_accepts(self) -> None:
        auto = LevenshteinAutomaton("ab", 1, AB)
        lang = auto.accepted_language(max_len=3)
        for w in lang:
            assert auto.accepts(w), f"lang word {w!r} should be accepted"
        all_words = _gen_all_strings(AB, max_len=3)
        accepted_from_scan = [w for w in all_words if auto.accepts(w)]
        assert sorted(lang) == sorted(accepted_from_scan)


# ---------------------------------------------------------------------------
# state_count structural property
# ---------------------------------------------------------------------------


class TestStateCount:
    def test_state_count_non_decreasing_with_k(self) -> None:
        counts = []
        for k in range(4):
            auto = LevenshteinAutomaton("abc", k, AB)
            counts.append(auto.state_count())
        assert counts == sorted(counts)

    def test_state_count_bounded_by_brute_force(self) -> None:
        auto = LevenshteinAutomaton("abcde", 2, AB)
        sc = auto.state_count()
        assert sc <= 2 ** ((2 + 1) * (5 + 1))


# ---------------------------------------------------------------------------
# k >= |pattern| edge case
# ---------------------------------------------------------------------------


class TestKLargerThanPattern:
    def test_k_larger_than_pattern_accepts_everything_short(self) -> None:
        auto = LevenshteinAutomaton("a", 5, AB)
        words = _gen_all_strings(AB, max_len=4)
        for w in words:
            assert auto.accepts(w) is True, f"should accept {w!r}"


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _nfa_accepts(nfa: dict, word: str) -> bool:
    """Simulate the NFA dict directly on *word*."""
    cur = nfa["start"]
    for ch in word:
        cur = _nfa_move(cur, ch, nfa["delta"], nfa["epsilon"])
    return bool(cur & nfa["accept"])


class _DFAWrapper:
    """Minimal DFA wrapper from the nfa_to_dfa result dict."""

    __slots__ = ("_accept", "_delta", "_start")

    def __init__(self, dfa: dict) -> None:
        self._start = dfa["start"]
        self._accept = dfa["accept"]
        self._delta = dfa["delta"]

    def accepts(self, text: str) -> bool:
        q = self._start
        for ch in text:
            q = self._delta.get((q, ch), self._start)
        return q in self._accept


def _dfa_from_dict(dfa: dict) -> _DFAWrapper:
    return _DFAWrapper(dfa)


def _gen_all_strings(alphabet: frozenset[str], max_len: int) -> list[str]:
    syms = sorted(alphabet)
    result: list[str] = [""]
    stack = [""]
    while stack:
        prefix = stack.pop()
        for ch in syms:
            nxt = prefix + ch
            result.append(nxt)
            if len(nxt) < max_len:
                stack.append(nxt)
    return result
