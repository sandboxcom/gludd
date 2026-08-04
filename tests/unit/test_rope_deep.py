"""Deep tests for Rope data structure: node-weight aware split/merge tree for strings.

Covers: construction from str, concat, insert, delete, report (substring),
split, balanced merge, edge cases (empty, single-char, large), weight
invariant, and traversal order consistency.
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.rope import (
    RopeNode,
    rope_balance,
    rope_concat,
    rope_delete,
    rope_from_str,
    rope_insert,
    rope_report,
    rope_split,
    rope_to_string,
    rope_weight,
)

# ---------------------------------------------------------------------------
# 1. Construction
# ---------------------------------------------------------------------------


def test_from_str_empty() -> None:
    root = rope_from_str("")
    assert root is None


def test_from_str_single_char() -> None:
    root = rope_from_str("a")
    assert root is not None
    assert root.data == "a"
    assert root.weight == 1
    assert root.left is None
    assert root.right is None


def test_from_str_short() -> None:
    root = rope_from_str("ab")
    assert root is not None
    s = rope_to_string(root)
    assert s == "ab"


def test_from_str_medium() -> None:
    s = "hello world"
    root = rope_from_str(s)
    assert rope_to_string(root) == s
    assert rope_weight(root) == len(s)


def test_from_str_large() -> None:
    s = "x" * 1000 + "y" * 1000 + "z" * 1000
    root = rope_from_str(s)
    assert rope_to_string(root) == s
    assert rope_weight(root) == 3000


# ---------------------------------------------------------------------------
# 2. Report (substring extraction)
# ---------------------------------------------------------------------------


def test_report_full_string() -> None:
    root = rope_from_str("abcdefgh")
    assert rope_report(root, 0, 8) == "abcdefgh"


@pytest.mark.parametrize(
    "s,lo,hi,expect",
    [
        ("abcdef", 1, 4, "bcd"),
        ("abcdef", 0, 0, ""),
        ("abcdef", 0, 6, "abcdef"),
        ("abcdef", 2, 5, "cde"),
        ("hello world", 6, 11, "world"),
        ("hello world", 0, 5, "hello"),
    ],
)
def test_report_substring(s: str, lo: int, hi: int, expect: str) -> None:
    root = rope_from_str(s)
    assert rope_report(root, lo, hi) == expect


def test_report_invalid_bounds_none() -> None:
    assert rope_report(None, 0, 5) == ""


# ---------------------------------------------------------------------------
# 3. Concat
# ---------------------------------------------------------------------------


def test_concat_two_non_empty() -> None:
    a = rope_from_str("hello")
    b = rope_from_str("world")
    c = rope_concat(a, b)
    assert rope_to_string(c) == "helloworld"
    assert rope_weight(c) == 10


def test_concat_with_empty() -> None:
    a = rope_from_str("hello")
    c = rope_concat(a, None)
    assert rope_to_string(c) == "hello"
    d = rope_concat(None, a)
    assert rope_to_string(d) == "hello"


def test_concat_both_none() -> None:
    assert rope_concat(None, None) is None


def test_concat_three() -> None:
    a = rope_from_str("a")
    b = rope_from_str("b")
    c = rope_from_str("c")
    r = rope_concat(rope_concat(a, b), c)
    assert rope_to_string(r) == "abc"


# ---------------------------------------------------------------------------
# 4. Split
# ---------------------------------------------------------------------------


def test_split_middle() -> None:
    root = rope_from_str("abcdefgh")
    left, right = rope_split(root, 4)
    assert rope_to_string(left) == "abcd"
    assert rope_to_string(right) == "efgh"


def test_split_at_zero() -> None:
    root = rope_from_str("abcdef")
    left, right = rope_split(root, 0)
    assert left is None
    assert rope_to_string(right) == "abcdef"


def test_split_at_end() -> None:
    root = rope_from_str("abcdef")
    left, right = rope_split(root, 6)
    assert rope_to_string(left) == "abcdef"
    assert right is None


def test_split_at_one() -> None:
    root = rope_from_str("abc")
    left, right = rope_split(root, 1)
    assert rope_to_string(left) == "a"
    assert rope_to_string(right) == "bc"


def test_split_deep() -> None:
    root = rope_from_str("a" * 500 + "b" * 500)
    left, right = rope_split(root, 500)
    assert rope_to_string(left) == "a" * 500
    assert rope_to_string(right) == "b" * 500


# ---------------------------------------------------------------------------
# 5. Insert
# ---------------------------------------------------------------------------


def test_insert_at_start() -> None:
    root = rope_from_str("world")
    root = rope_insert(root, "hello", 0)
    assert rope_to_string(root) == "helloworld"


def test_insert_at_end() -> None:
    root = rope_from_str("hello")
    root = rope_insert(root, " world", 5)
    assert rope_to_string(root) == "hello world"


def test_insert_middle() -> None:
    root = rope_from_str("heworld")
    root = rope_insert(root, "llo ", 2)
    assert rope_to_string(root) == "hello world"


def test_insert_into_empty() -> None:
    root = rope_insert(None, "hello", 0)
    assert rope_to_string(root) == "hello"


# ---------------------------------------------------------------------------
# 6. Delete
# ---------------------------------------------------------------------------


def test_delete_middle() -> None:
    root = rope_from_str("hello cruel world")
    root = rope_delete(root, 6, 12)
    assert rope_to_string(root) == "hello world"


def test_delete_prefix() -> None:
    root = rope_from_str("helloworld")
    root = rope_delete(root, 0, 5)
    assert rope_to_string(root) == "world"


def test_delete_suffix() -> None:
    root = rope_from_str("helloworld")
    root = rope_delete(root, 5, 10)
    assert rope_to_string(root) == "hello"


def test_delete_all() -> None:
    root = rope_from_str("hello")
    root = rope_delete(root, 0, 5)
    assert root is None


def test_delete_empty_range() -> None:
    root = rope_from_str("hello")
    root = rope_delete(root, 3, 3)
    assert rope_to_string(root) == "hello"


# ---------------------------------------------------------------------------
# 7. Balanced build
# ---------------------------------------------------------------------------


def test_balance_preserves_content() -> None:
    s = "a" * 100 + "b" * 200 + "c" * 150
    root = rope_from_str(s)
    balanced = rope_balance(root)
    assert rope_to_string(balanced) == s
    assert rope_weight(balanced) == len(s)


def test_balance_empty() -> None:
    assert rope_balance(None) is None


def test_balance_identity_small() -> None:
    root = rope_from_str("abc")
    balanced = rope_balance(root)
    assert rope_to_string(balanced) == "abc"


# ---------------------------------------------------------------------------
# 8. Weight invariant
# ---------------------------------------------------------------------------


def test_weight_after_insert() -> None:
    root = rope_from_str("abc")
    root = rope_insert(root, "def", 3)
    assert rope_weight(root) == 6


def test_weight_after_delete() -> None:
    root = rope_from_str("abcdef")
    root = rope_delete(root, 1, 4)
    assert rope_weight(root) == 3


def test_weight_tracks_total_len() -> None:
    root = rope_from_str("x" * 10000)
    assert rope_weight(root) == 10000


# ---------------------------------------------------------------------------
# 9. Large-scale round-trips
# ---------------------------------------------------------------------------


def test_large_insert_delete_round_trip() -> None:
    s = "".join(chr(32 + (i % 95)) for i in range(10000))
    root = rope_from_str(s)
    root = rope_insert(root, "INSERTED", 5000)
    root = rope_delete(root, 5000, 5008)
    expected = s[:5000] + s[5000:10000]
    assert rope_to_string(root) == expected


def test_many_splits_and_concats() -> None:
    s = "abcdefghijklmnopqrstuvwxyz" * 100
    root = rope_from_str(s)
    for _i in range(10):
        left, right = rope_split(root, 100)
        root = rope_concat(right, left)
    assert sorted(rope_to_string(root)) == sorted(s)
    assert rope_weight(root) == len(s)


# ---------------------------------------------------------------------------
# 10. Edge cases
# ---------------------------------------------------------------------------


def test_report_oob_clamp() -> None:
    root = rope_from_str("abc")
    assert rope_report(root, 0, 10) == "abc"
    assert rope_report(root, 1, 10) == "bc"


def test_split_none() -> None:
    left, right = rope_split(None, 0)
    assert left is None
    assert right is None


def test_concat_identity() -> None:
    root = rope_from_str("test")
    assert rope_to_string(rope_concat(root, None)) == "test"
    assert rope_to_string(rope_concat(None, root)) == "test"


def test_insert_past_end() -> None:
    root = rope_from_str("ab")
    root = rope_insert(root, "c", 10)
    assert rope_to_string(root) == "abc"


def test_delete_past_end() -> None:
    root = rope_from_str("ab")
    root = rope_delete(root, 1, 10)
    assert rope_to_string(root) == "a"


def test_node_slots() -> None:
    n = RopeNode("x")
    assert hasattr(n, "data")
    assert hasattr(n, "weight")
    assert hasattr(n, "left")
    assert hasattr(n, "right")
    with pytest.raises(AttributeError):
        n.bogus = 1  # type: ignore[attr-defined]
