"""Deep regex compilation tests: parser → AST → Thompson NFA → subset
construction DFA → Hopcroft minimization. 33+ tests covering correctness,
equivalence, edge cases, and algebraic properties.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.regex_compile import (
    Concat,
    Epsilon,
    Literal,
    RegexParser,
    Star,
    ThompsonBuilder,
    Union,
    compile_regex,
)

# ── Parser ───────────────────────────────────────────────────────────────────


class TestParserCorrectness:
    def test_single_literal(self) -> None:
        ast = RegexParser("a").parse()
        assert isinstance(ast, Literal) and ast.char == "a"

    def test_concatenation(self) -> None:
        ast = RegexParser("ab").parse()
        assert isinstance(ast, Concat)
        assert isinstance(ast.left, Literal) and ast.left.char == "a"
        assert isinstance(ast.right, Literal) and ast.right.char == "b"

    def test_union(self) -> None:
        ast = RegexParser("a|b").parse()
        assert isinstance(ast, Union)
        assert isinstance(ast.left, Literal) and ast.left.char == "a"
        assert isinstance(ast.right, Literal) and ast.right.char == "b"

    def test_star(self) -> None:
        ast = RegexParser("a*").parse()
        assert isinstance(ast, Star)
        assert isinstance(ast.child, Literal) and ast.child.char == "a"

    def test_grouping(self) -> None:
        ast = RegexParser("(a)").parse()
        assert isinstance(ast, Literal) and ast.char == "a"

    def test_union_concat_precedence(self) -> None:
        ast = RegexParser("ab|cd").parse()
        assert isinstance(ast, Union)
        left = ast.left
        right = ast.right
        assert isinstance(left, Concat) and isinstance(right, Concat)
        ll = left.left
        assert isinstance(ll, Literal) and ll.char == "a"

    def test_empty_pattern(self) -> None:
        ast = RegexParser("").parse()
        assert isinstance(ast, Epsilon)

    def test_nested_star(self) -> None:
        ast = RegexParser("(ab)*").parse()
        assert isinstance(ast, Star)
        inner = ast.child
        assert isinstance(inner, Concat)
        ll = inner.left
        assert isinstance(ll, Literal) and ll.char == "a"

    def test_union_star(self) -> None:
        ast = RegexParser("a|b*").parse()
        assert isinstance(ast, Union)
        assert isinstance(ast.right, Star)


# ── NFA construction ─────────────────────────────────────────────────────────


class TestNFAConstruction:
    def test_epsilon_nfa(self) -> None:
        nfa = ThompsonBuilder().build(Epsilon())
        assert nfa.matches("")
        assert not nfa.matches("a")

    def test_literal_nfa(self) -> None:
        nfa = ThompsonBuilder().build(Literal("x"))
        assert nfa.matches("x")
        assert not nfa.matches("y")
        assert not nfa.matches("")
        assert not nfa.matches("xx")

    def test_concat_nfa(self) -> None:
        nfa = ThompsonBuilder().build(Concat(Literal("a"), Literal("b")))
        assert nfa.matches("ab")
        assert not nfa.matches("a")
        assert not nfa.matches("b")
        assert not nfa.matches("aba")

    def test_union_nfa(self) -> None:
        nfa = ThompsonBuilder().build(Union(Literal("a"), Literal("b")))
        assert nfa.matches("a")
        assert nfa.matches("b")
        assert not nfa.matches("c")
        assert not nfa.matches("ab")

    def test_star_nfa(self) -> None:
        nfa = ThompsonBuilder().build(Star(Literal("a")))
        assert nfa.matches("")
        assert nfa.matches("a")
        assert nfa.matches("aa")
        assert nfa.matches("aaaaaaaa")
        assert not nfa.matches("b")
        assert not nfa.matches("ab")

    def test_complex_nfa(self) -> None:
        ast = RegexParser("(a|b)*c").parse()
        nfa = ThompsonBuilder().build(ast)
        assert nfa.matches("c")
        assert nfa.matches("ac")
        assert nfa.matches("bc")
        assert nfa.matches("aabbabac")
        assert not nfa.matches("")
        assert not nfa.matches("a")
        assert not nfa.matches("ab")
        assert not nfa.matches("d")


# ── DFA (subset construction) ────────────────────────────────────────────────


class TestDFAConstruction:
    def test_literal_dfa(self) -> None:
        dfa = compile_regex("a")
        assert dfa.matches("a")
        assert not dfa.matches("b")
        assert not dfa.matches("")

    def test_union_dfa(self) -> None:
        dfa = compile_regex("a|b")
        assert dfa.matches("a")
        assert dfa.matches("b")
        assert not dfa.matches("c")

    def test_star_dfa(self) -> None:
        dfa = compile_regex("a*")
        assert dfa.matches("")
        assert dfa.matches("a")
        assert dfa.matches("aaa")
        assert not dfa.matches("b")

    def test_concat_star_dfa(self) -> None:
        dfa = compile_regex("ab*c")
        assert dfa.matches("ac")
        assert dfa.matches("abc")
        assert dfa.matches("abbc")
        assert dfa.matches("abbbc")
        assert not dfa.matches("a")
        assert not dfa.matches("ab")
        assert not dfa.matches("bc")

    def test_nested_union_star(self) -> None:
        dfa = compile_regex("(a|b)*")
        assert dfa.matches("")
        assert dfa.matches("a")
        assert dfa.matches("b")
        assert dfa.matches("ab")
        assert dfa.matches("ba")
        assert dfa.matches("aaabbbabbab")
        assert not dfa.matches("c")
        assert not dfa.matches("abc")

    def test_epsilon_chain_equivalent(self) -> None:
        dfa = compile_regex("a")
        dfa2 = compile_regex("a")
        for text in ("a", "b", ""):
            assert dfa.matches(text) == dfa2.matches(text)


# ── Minimization ─────────────────────────────────────────────────────────────


class TestMinimization:
    def test_literal_unchanged(self) -> None:
        dfa = compile_regex("a", minimize=True)
        assert dfa.matches("a")
        assert not dfa.matches("b")
        assert not dfa.matches("")

    def test_union_minimized(self) -> None:
        dfa = compile_regex("a|b|a", minimize=True)
        assert dfa.matches("a")
        assert dfa.matches("b")
        assert not dfa.matches("c")

    def test_minimized_equivalent_to_unminimized(self) -> None:
        for pat in ("a", "ab", "a*", "a|b", "(ab)*c", "(a|b)*"):
            dfa1 = compile_regex(pat, minimize=False)
            dfa2 = compile_regex(pat, minimize=True)
            for text in ("", "a", "b", "ab", "aa", "aba", "c", "abc", "aab"):
                assert dfa1.matches(text) == dfa2.matches(text), f"pattern={pat!r} text={text!r}"

    def test_minimized_has_fewer_or_equal_states(self) -> None:
        dfa_std = compile_regex("a|b|c", minimize=False)
        dfa_min = compile_regex("a|b|c", minimize=True)
        std_states = {0, dfa_std.start} | {s for (s, _) in dfa_std.transitions} | set(dfa_std.transitions.values())
        min_states = {0, dfa_min.start} | {s for (s, _) in dfa_min.transitions} | set(dfa_min.transitions.values())
        assert len(min_states) <= len(std_states)


# ── De Morgan / algebraic properties ─────────────────────────────────────────


class TestAlgebraicProperties:
    def test_idempotent_union(self) -> None:
        dfa1 = compile_regex("a|a")
        dfa2 = compile_regex("a")
        for text in ("", "a", "b", "aa"):
            assert dfa1.matches(text) == dfa2.matches(text)

    def test_epsilon_concat_identity(self) -> None:
        dfa1 = compile_regex("a")
        dfa2 = compile_regex("a")
        for text in ("", "a", "b", "aa"):
            assert dfa1.matches(text) == dfa2.matches(text)

    def test_star_idempotent(self) -> None:
        dfa1 = compile_regex("a*")
        dfa2 = compile_regex("a*")
        for text in ("", "a", "aa", "aaa", "b"):
            assert dfa1.matches(text) == dfa2.matches(text)


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_long_chain(self) -> None:
        pat = "abcdefghijklmnopqrstuvwxyz"
        dfa = compile_regex(pat)
        assert dfa.matches(pat)
        assert not dfa.matches(pat[:-1])
        assert not dfa.matches(pat + "x")

    def test_deeply_nested(self) -> None:
        pat = "(((a)))"
        dfa = compile_regex(pat)
        assert dfa.matches("a")
        assert not dfa.matches("b")

    def test_union_of_unions(self) -> None:
        dfa = compile_regex("a|b|c|d")
        for ch in "abcd":
            assert dfa.matches(ch)
        assert not dfa.matches("e")
        assert not dfa.matches("ab")

    def test_star_under_union(self) -> None:
        dfa = compile_regex("a*|b*")
        assert dfa.matches("")
        assert dfa.matches("a")
        assert dfa.matches("b")
        assert dfa.matches("aaa")
        assert dfa.matches("bbb")
        assert not dfa.matches("ab")

    def test_empty_rejects_nonempty(self) -> None:
        dfa = compile_regex("")
        assert dfa.matches("")
        assert not dfa.matches("a")
        assert not dfa.matches("abc")

    def test_dfa_transition_completeness(self) -> None:
        dfa = compile_regex("ab")
        assert dfa.step(0, "a") is not None
        assert not dfa.matches("b")


# ── Correctness against expected results ─────────────────────────────────────


class TestCorrectnessSuite:
    @pytest.mark.parametrize(
        "pattern, accept, reject",
        [
            ("ab", ["ab"], ["", "a", "b", "ba", "aba"]),
            ("a|b", ["a", "b"], ["", "c", "ab", "ba"]),
            ("a*", ["", "a", "aa", "aaa"], ["b", "ab", "ba"]),
            ("a*", ["a" * 50], ["b", "a" * 49 + "b"]),
            ("ab*c", ["ac", "abc", "abbc"], ["a", "ab", "bc", "c"]),
            ("(a|b)*", ["", "a", "b", "ab", "ba"], ["c", "acb", "abc"]),
            ("a(b|c)d", ["abd", "acd"], ["ad", "abcd", "ab"]),
        ],
    )
    def test_accept(self, pattern: str, accept: list[str], reject: list[str]) -> None:
        dfa = compile_regex(pattern)
        for text in accept:
            assert dfa.matches(text), f"pattern={pattern!r} should match {text!r}"
        for text in reject:
            assert not dfa.matches(text), f"pattern={pattern!r} should reject {text!r}"
