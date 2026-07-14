from __future__ import annotations

from general_ludd.code_intelligence.search import CodeSearch


def _blocks() -> list[dict[str, object]]:
    return [
        {"name": "MyFunc", "type": "function", "docstring": "Does things", "source": "def my_func(): pass"},
        {"name": "MyClass", "type": "class", "docstring": "A class", "source": "class MyClass: pass"},
        {"name": "helper", "type": "function", "docstring": "Helper function", "source": "def helper(): ..."},
    ]


class TestCodeSearch:
    def test_search_no_filters_returns_all(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search()
        assert len(results) == 3

    def test_type_filter(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(type_filter="class")
        assert len(results) == 1
        assert results[0]["name"] == "MyClass"

    def test_type_filter_nonexistent(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(type_filter="module")
        assert results == []

    def test_query_matches_name(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(query="myclass")
        assert len(results) == 1
        assert results[0]["name"] == "MyClass"

    def test_query_matches_docstring(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(query="does things")
        assert len(results) == 1
        assert results[0]["name"] == "MyFunc"

    def test_query_matches_source(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(query="def helper")
        assert len(results) == 1
        assert results[0]["name"] == "helper"

    def test_query_matches_multiple(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(query="def ")
        assert len(results) == 2

    def test_combined_filters(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(query="class", type_filter="function")
        assert results == []

    def test_query_case_insensitive(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(query="MYCLASS")
        assert len(results) == 1

    def test_empty_query(self) -> None:
        cs = CodeSearch(_blocks())
        results = cs.search(query="")
        assert len(results) == 3

    def test_empty_blocks(self) -> None:
        cs = CodeSearch([])
        assert cs.search() == []
        assert cs.search(query="anything") == []


class TestListTypes:
    def test_list_types_returns_sorted_unique(self) -> None:
        cs = CodeSearch(_blocks())
        types = cs.list_types()
        assert types == ["class", "function"]

    def test_list_types_empty_blocks(self) -> None:
        cs = CodeSearch([])
        assert cs.list_types() == []

    def test_list_types_handles_non_string_types(self) -> None:
        blocks: list[dict[str, object]] = [
            {"name": "x", "type": "function"},
            {"name": "y", "type": 123},
            {"name": "z", "type": None},
        ]
        cs = CodeSearch(blocks)
        types = cs.list_types()
        assert types == ["function"]
