"""Deep JSONPath and query safety tests.

Covers injection prevention, path validation, nested traversal limits,
wildcard safety, and edge-case handling for JSON-based path queries.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Minimal JSONPath evaluator — safe by construction
# ---------------------------------------------------------------------------

_MAX_DEPTH = 32
_MAX_KEYS_PER_WILDCARD = 1_000


class PathValidationError(ValueError):
    pass


class TraversalLimitExceeded(ValueError):
    pass


def _tokenize(expr: str) -> list[str]:
    if not expr or not expr.strip():
        raise PathValidationError("empty path expression")
    if len(expr) > 4096:
        raise PathValidationError("path expression too long")
    if not expr.startswith("$"):
        raise PathValidationError("path must start with $")

    after_root = expr[1:]
    parts: list[str] = []
    i = 0
    while i < len(after_root):
        ch = after_root[i]
        if ch == ".":
            if i + 1 < len(after_root) and after_root[i + 1] == ".":
                parts.append("..")
                i += 2
                continue
            i += 1
            continue
        if ch == "[":
            end = after_root.find("]", i)
            if end == -1:
                raise PathValidationError("unclosed bracket")
            parts.append(after_root[i : end + 1])
            i = end + 1
            continue
        start = i
        while i < len(after_root) and after_root[i] not in ".[":
            i += 1
        parts.append(after_root[start:i])
    return parts


_UNSAFE_RX = re.compile(r"[\x00-\x1f\x7f\\/;|><&$`\"'\[\].]")


def _is_safe_identifier(key: str) -> bool:
    if not key:
        return False
    return not bool(_UNSAFE_RX.search(key))


def _resolve_key(data: dict[str, Any], key: str) -> Any:
    return data.get(key)


def _resolve_index(seg: str, data: list[Any]) -> Any:
    inner = seg[1:-1]
    if inner.lstrip("-").isdigit():
        idx = int(inner)
        if idx < 0 or idx >= len(data):
            raise IndexError(f"index {idx} out of range")
        return data[idx]
    if inner == "*":
        if len(data) > _MAX_KEYS_PER_WILDCARD:
            raise TraversalLimitExceeded(f"wildcard matches {len(data)} > {_MAX_KEYS_PER_WILDCARD}")
        return data
    raise PathValidationError(f"unsupported index expression: {seg}")


def _evaluate(parts: list[str], pos: int, current: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise TraversalLimitExceeded(f"depth {depth} exceeds max {_MAX_DEPTH}")

    if pos >= len(parts):
        return current

    seg = parts[pos]

    if seg == "..":
        collected: list[Any] = []
        with contextlib.suppress(PathValidationError, TraversalLimitExceeded):
            collected.append(_evaluate(parts, pos + 1, current, depth + 1))
        if isinstance(current, dict):
            for _k, v in current.items():
                collected.append(_evaluate(parts, pos, v, depth + 1))
        elif isinstance(current, list):
            for item in current:
                collected.append(_evaluate(parts, pos, item, depth + 1))
        return collected

    if seg.startswith("[") and seg.endswith("]"):
        if not isinstance(current, list):
            raise PathValidationError("index applied to non-list")
        resolved = _resolve_index(seg, current)
        if isinstance(resolved, list):
            return [_evaluate(parts, pos + 1, item, depth + 1) for item in resolved]
        return _evaluate(parts, pos + 1, resolved, depth + 1)

    if isinstance(current, dict):
        key = seg
        if not _is_safe_identifier(key):
            raise PathValidationError(f"unsafe identifier: {key!r}")
        return _evaluate(parts, pos + 1, _resolve_key(current, key), depth + 1)

    raise PathValidationError(f"cannot apply {seg!r} to {type(current).__name__}")


def safe_jsonpath(expr: str, data: dict[str, Any]) -> Any:
    tokens = _tokenize(expr)
    return _evaluate(tokens, 0, data, 0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def nested_data() -> dict[str, Any]:
    return {
        "store": {
            "book": [
                {"title": "Sayings of the Century", "price": 8.95, "author": "M. Apple"},
                {"title": "Sword of Honour", "price": 12.99, "author": "E. Waugh"},
                {"title": "Moby Dick", "price": 8.99, "author": "H. Melville"},
                {"title": "The Lord of the Rings", "price": 22.99, "author": "J.R.R. Tolkien"},
            ],
            "bicycle": {"color": "red", "price": 19.95},
        },
        "expensive": 10,
    }


# ---------------------------------------------------------------------------
# 1. Path Validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_valid_dot_notation(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$.store.book[0].title", nested_data)
        assert result == "Sayings of the Century"

    def test_valid_wildcard_array(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$.store.book[*].author", nested_data)
        authors = [r["author"] for r in nested_data["store"]["book"]]
        assert result == authors

    def test_empty_expression_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="empty path"):
            safe_jsonpath("", nested_data)

    def test_whitespace_only_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="empty path"):
            safe_jsonpath("   ", nested_data)

    def test_path_not_starting_with_dollar_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="path must start"):
            safe_jsonpath("store.book", nested_data)

    def test_path_too_long_rejected(self, nested_data: dict[str, Any]) -> None:
        long_path = "$." + "a" * 5000
        with pytest.raises(PathValidationError, match="too long"):
            safe_jsonpath(long_path, nested_data)

    def test_nonexistent_key_returns_none(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$.store.nonexistent", nested_data)
        assert result is None

    def test_invalid_identifier_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe identifier"):
            safe_jsonpath("$.store.book[0].,;DROP", nested_data)

    def test_non_list_index_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="index applied to non-list"):
            safe_jsonpath("$.store[0]", nested_data)

    def test_bare_dollar_is_valid(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$", nested_data)
        assert result == nested_data

    def test_single_level(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$.expensive", nested_data)
        assert result == 10

    def test_nested_dict_traversal(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$.store.bicycle.color", nested_data)
        assert result == "red"

    def test_array_element_by_index(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$.store.book[2].title", nested_data)
        assert result == "Moby Dick"

    def test_array_out_of_bounds_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(IndexError, match="out of range"):
            safe_jsonpath("$.store.book[999]", nested_data)

    def test_negative_array_index_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(IndexError, match="out of range"):
            safe_jsonpath("$.store.book[-1]", nested_data)

    def test_unclosed_bracket_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unclosed"):
            safe_jsonpath("$.store.book[0", nested_data)


# ---------------------------------------------------------------------------
# 2. Injection Prevention
# ---------------------------------------------------------------------------


class TestInjectionPrevention:
    def test_script_tag_in_key_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.store.<script>alert(1)</script>", nested_data)

    def test_sql_injection_in_key_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.'; DROP TABLE users;--", nested_data)

    def test_command_injection_dollar_paren_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError):
            safe_jsonpath("$.store.$(whoami)", nested_data)

    def test_pipe_in_key_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.store.a|b", nested_data)

    def test_newline_in_expression_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError):
            safe_jsonpath("$.store\n.evil", nested_data)

    def test_null_byte_in_expression_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError):
            safe_jsonpath("$.store.\x00hidden", nested_data)

    def test_backtick_in_expression_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.`whoami`.a", nested_data)

    def test_semicolon_in_key_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.store.a;rm -rf /", nested_data)

    def test_dash_in_key_is_safe(self) -> None:
        data: dict[str, Any] = {"my-key": 42}
        result = safe_jsonpath("$.my-key", data)
        assert result == 42

    def test_angle_bracket_in_key_rejected(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.store.a<b", nested_data)


# ---------------------------------------------------------------------------
# 3. Nested Traversal Limits
# ---------------------------------------------------------------------------


class TestNestedTraversalLimits:
    def test_deeply_nested_within_limit(self) -> None:
        data: dict[str, Any] = {}
        current = data
        for _i in range(10):
            current["child"] = {}
            current = current["child"]
        current["leaf"] = "found"

        path = "$." + "child." * 10 + "leaf"
        result = safe_jsonpath(path, data)
        assert result == "found"

    def test_depth_exceeds_max_rejected(self) -> None:
        data: dict[str, Any] = {}
        current = data
        for _i in range(50):
            current["child"] = {}
            current = current["child"]
        current["leaf"] = "found"

        path = "$." + "child." * 50 + "leaf"
        with pytest.raises(TraversalLimitExceeded, match="depth"):
            safe_jsonpath(path, data)

    def test_recursive_descent_depth_limit_enforced(self) -> None:
        data: dict[str, Any] = {}
        current = data
        for _i in range(40):
            current["a"] = {}
            current = current["a"]
        current["b"] = "value"

        with pytest.raises(TraversalLimitExceeded, match="depth"):
            safe_jsonpath("$..b", data)

    def test_many_keys_recursive_survives_shallow_wide(self) -> None:
        payload: dict[str, Any] = {str(i): {"a": i} for i in range(300)}
        result = safe_jsonpath("$..a", payload)
        assert isinstance(result, list)
        assert len(result) == 301  # 1 root search (None) + 300 children


# ---------------------------------------------------------------------------
# 4. Wildcard Safety
# ---------------------------------------------------------------------------


class TestWildcardSafety:
    def test_wildcard_on_known_array(self, nested_data: dict[str, Any]) -> None:
        result = safe_jsonpath("$.store.book[*].price", nested_data)
        prices = [b["price"] for b in nested_data["store"]["book"]]
        assert result == prices

    def test_wildcard_array_limit_enforced(self) -> None:
        data: dict[str, Any] = {"items": list(range(2000))}
        with pytest.raises(TraversalLimitExceeded, match="wildcard"):
            safe_jsonpath("$.items[*]", data)

    def test_wildcard_on_small_array_works(self) -> None:
        data: dict[str, Any] = {"items": [1, 2, 3]}
        result = safe_jsonpath("$.items[*]", data)
        assert result == [1, 2, 3]

    def test_recursive_descent_on_large_dict(self) -> None:
        data: dict[str, Any] = {str(i): {"v": i} for i in range(200)}
        result = safe_jsonpath("$..v", data)
        assert isinstance(result, list)
        assert len(result) == 201  # 1 root + 200 children


# ---------------------------------------------------------------------------
# 5. Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_dict(self) -> None:
        result = safe_jsonpath("$.a", {})
        assert result is None

    def test_dict_with_none_value(self) -> None:
        data: dict[str, Any] = {"key": None}
        result = safe_jsonpath("$.key", data)
        assert result is None

    def test_dict_with_empty_string_value(self) -> None:
        data: dict[str, Any] = {"key": ""}
        result = safe_jsonpath("$.key", data)
        assert result == ""

    def test_dict_with_numeric_zero(self) -> None:
        data: dict[str, Any] = {"key": 0, "other": "value"}
        result = safe_jsonpath("$.key", data)
        assert result == 0

    def test_dict_with_bool_false(self) -> None:
        data: dict[str, Any] = {"active": False}
        result = safe_jsonpath("$.active", data)
        assert result is False

    def test_nonexistent_then_existing_key(self) -> None:
        data: dict[str, Any] = {"a": {"b": {"c": 1}}}
        result = safe_jsonpath("$.a.b.d", data)
        assert result is None

    def test_array_wildcard_empty(self) -> None:
        data: dict[str, Any] = {"empty": []}
        result = safe_jsonpath("$.empty[*].id", data)
        assert result == []


# ---------------------------------------------------------------------------
# 6. Tokenization Invariants
# ---------------------------------------------------------------------------


class TestTokenization:
    def test_all_tokens_for_complex_expression(self) -> None:
        tokens = _tokenize("$.store.book[*].author")
        assert tokens == ["store", "book", "[*]", "author"]

    def test_bare_dollar_tokenizes_empty(self) -> None:
        tokens = _tokenize("$")
        assert tokens == []

    def test_bracket_notation_key_survives_tokenize(self) -> None:
        tokens = _tokenize("$.store.['a-b'].val")
        assert len(tokens) >= 2
        assert "store" in tokens[0]

    def test_recursive_descent_tokenizes(self) -> None:
        tokens = _tokenize("$..leaf")
        assert tokens == ["..", "leaf"]

    def test_multiple_nested_indexes(self) -> None:
        tokens = _tokenize("$.a[0].b[1].c")
        assert tokens == ["a", "[0]", "b", "[1]", "c"]


# ---------------------------------------------------------------------------
# 7. Query String Invariants
# ---------------------------------------------------------------------------


class TestQueryStringInvariants:
    def test_valid_query_works(self) -> None:
        assert safe_jsonpath("$.a", {"a": 1}) == 1

    def test_query_must_not_contain_os_separators(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.store./etc/passwd", nested_data)

    def test_query_must_not_contain_shell_ampersand(self, nested_data: dict[str, Any]) -> None:
        with pytest.raises(PathValidationError, match="unsafe"):
            safe_jsonpath("$.store.a&&b", nested_data)

    def test_large_key_name_is_identifier_safe(self) -> None:
        big_key = "k" * 200
        data: dict[str, Any] = {big_key: "found"}
        result = safe_jsonpath(f"$.{big_key}", data)
        assert result == "found"

    def test_unicode_in_key_is_safe(self) -> None:
        data: dict[str, Any] = {"Stra\u00dfe": "ok"}
        result = safe_jsonpath("$.Stra\u00dfe", data)
        assert result == "ok"

    def test_invalid_start_char_rejected(self) -> None:
        with pytest.raises(PathValidationError, match="path must start"):
            safe_jsonpath(".a", {"a": 1})


# ---------------------------------------------------------------------------
# 8. Large Payload Protection
# ---------------------------------------------------------------------------


class TestLargePayloadProtection:
    def test_deeply_nested_json_as_payload_survives(self) -> None:
        payload: dict[str, Any] = {}
        cur = payload
        for _ in range(8):
            cur["n"] = {}
            cur = cur["n"]
        cur["v"] = "leaf"
        result = safe_jsonpath("$.n.n.n.n.n.n.n.n.v", payload)
        assert result == "leaf"

    def test_extremely_wide_array_wildcard_rejected(self) -> None:
        payload: dict[str, Any] = {"rows": [{"id": i} for i in range(5000)]}
        with pytest.raises(TraversalLimitExceeded, match="wildcard"):
            safe_jsonpath("$.rows[*].id", payload)

    def test_wide_array_by_index_works(self) -> None:
        payload: dict[str, Any] = {"rows": [{"id": i} for i in range(5000)]}
        result = safe_jsonpath("$.rows[3].id", payload)
        assert result == 3

    def test_exactly_at_wildcard_limit(self) -> None:
        payload: dict[str, Any] = {"rows": [{"id": i} for i in range(_MAX_KEYS_PER_WILDCARD)]}
        result = safe_jsonpath("$.rows[*].id", payload)
        assert len(result) == _MAX_KEYS_PER_WILDCARD

    def test_one_over_wildcard_limit_rejected(self) -> None:
        limit = _MAX_KEYS_PER_WILDCARD
        payload: dict[str, Any] = {"rows": [{"id": i} for i in range(limit + 1)]}
        with pytest.raises(TraversalLimitExceeded, match="wildcard"):
            safe_jsonpath("$.rows[*].id", payload)
