"""Unit tests for code_intelligence/callgraph."""

from __future__ import annotations

from general_ludd.code_intelligence.callgraph import CallGraph


class TestCallGraphInit:
    def test_empty_graph(self):
        cg = CallGraph()
        assert len(cg._nodes) == 0
        assert len(cg._edges) == 0
        assert len(cg._parents) == 0


class TestFullName:
    def test_name_without_parent(self):
        result = CallGraph._full_name({"name": "my_func"})
        assert result == "my_func"

    def test_name_with_parent(self):
        result = CallGraph._full_name({"name": "my_func", "parent": "MyClass"})
        assert result == "MyClass.my_func"

    def test_unknown_name_fallback(self):
        result = CallGraph._full_name({})
        assert result == "unknown"


class TestBuildFromBlocks:
    def test_contains_relationships(self):
        cg = CallGraph()
        blocks = [
            {"name": "MyClass", "type": "class"},
            {"name": "my_func", "type": "function", "parent": "MyClass"},
        ]
        cg.build_from_blocks(blocks)
        assert cg.has_node("MyClass")
        assert cg.has_node("MyClass.my_func")
        assert any(e["relation"] == "contains" and e["from"] == "MyClass" for e in cg._edges)

    def test_calls_relationships(self):
        cg = CallGraph()
        blocks = [
            {"name": "func_a", "type": "function", "source": "func_b()"},
            {"name": "func_b", "type": "function", "source": ""},
        ]
        cg.build_from_blocks(blocks)
        callees = cg.get_callees("func_a")
        assert "func_b" in callees

    def test_no_self_calls(self):
        cg = CallGraph()
        blocks = [
            {"name": "func_a", "type": "function", "source": "func_a()"},
        ]
        cg.build_from_blocks(blocks)
        callees = cg.get_callees("func_a")
        assert "func_a" not in callees

    def test_inherits_relationships(self):
        cg = CallGraph()
        blocks = [
            {"name": "Base", "type": "class"},
            {"name": "Child", "type": "class", "base_classes": ["Base"]},
        ]
        cg.build_from_blocks(blocks)
        assert cg.is_subclass("Child", "Base")

    def test_no_calls_when_no_source(self):
        cg = CallGraph()
        blocks = [
            {"name": "func_a", "type": "function"},
            {"name": "func_b", "type": "function"},
        ]
        cg.build_from_blocks(blocks)
        callees = cg.get_callees("func_a")
        assert callees == []

    def test_no_duplicate_edges(self):
        cg = CallGraph()
        blocks = [
            {"name": "func_a", "type": "function", "source": "func_b func_b func_b"},
            {"name": "func_b", "type": "function", "source": ""},
        ]
        cg.build_from_blocks(blocks)
        callees = cg.get_callees("func_a")
        assert callees == ["func_b"]

    def test_inherits_unknown_base_skipped(self):
        cg = CallGraph()
        blocks = [
            {"name": "Child", "type": "class", "base_classes": ["UnknownBase"]},
        ]
        cg.build_from_blocks(blocks)
        assert not cg.is_subclass("Child", "UnknownBase")
        inherits_edges = [e for e in cg._edges if e["relation"] == "inherits"]
        assert len(inherits_edges) == 0


class TestHasNode:
    def test_existing_node(self):
        cg = CallGraph()
        blocks = [{"name": "func_a", "type": "function"}]
        cg.build_from_blocks(blocks)
        assert cg.has_node("func_a")

    def test_missing_node(self):
        cg = CallGraph()
        assert not cg.has_node("nonexistent")


class TestGetCallees:
    def test_multiple_callees(self):
        cg = CallGraph()
        blocks = [
            {"name": "main", "type": "function", "source": "foo bar baz"},
            {"name": "foo", "type": "function", "source": ""},
            {"name": "bar", "type": "function", "source": ""},
            {"name": "baz", "type": "function", "source": ""},
        ]
        cg.build_from_blocks(blocks)
        callees = cg.get_callees("main")
        assert sorted(callees) == ["bar", "baz", "foo"]

    def test_no_callees(self):
        cg = CallGraph()
        blocks = [{"name": "func_a", "type": "function", "source": ""}]
        cg.build_from_blocks(blocks)
        assert cg.get_callees("func_a") == []


class TestGetCallers:
    def test_finds_callers(self):
        cg = CallGraph()
        blocks = [
            {"name": "caller_a", "type": "function", "source": "target"},
            {"name": "target", "type": "function", "source": ""},
        ]
        cg.build_from_blocks(blocks)
        callers = cg.get_callers("target")
        assert "caller_a" in callers

    def test_no_callers(self):
        cg = CallGraph()
        blocks = [{"name": "isolated", "type": "function", "source": ""}]
        cg.build_from_blocks(blocks)
        assert cg.get_callers("isolated") == []

    def test_multiple_callers(self):
        cg = CallGraph()
        blocks = [
            {"name": "caller_a", "type": "function", "source": "target"},
            {"name": "caller_b", "type": "function", "source": "target"},
            {"name": "target", "type": "function", "source": ""},
        ]
        cg.build_from_blocks(blocks)
        callers = cg.get_callers("target")
        assert sorted(callers) == ["caller_a", "caller_b"]


class TestIsSubclass:
    def test_direct_inheritance(self):
        cg = CallGraph()
        blocks = [
            {"name": "Base", "type": "class"},
            {"name": "Child", "type": "class", "base_classes": ["Base"]},
        ]
        cg.build_from_blocks(blocks)
        assert cg.is_subclass("Child", "Base")

    def test_not_subclass(self):
        cg = CallGraph()
        blocks = [
            {"name": "A", "type": "class"},
            {"name": "B", "type": "class", "base_classes": ["A"]},
            {"name": "C", "type": "class"},
        ]
        cg.build_from_blocks(blocks)
        assert not cg.is_subclass("C", "A")
        assert not cg.is_subclass("B", "C")


class TestToDict:
    def test_empty_graph(self):
        cg = CallGraph()
        result = cg.to_dict()
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_with_data(self):
        cg = CallGraph()
        blocks = [
            {"name": "func_a", "type": "function", "source": "func_b"},
            {"name": "func_b", "type": "function", "source": ""},
        ]
        cg.build_from_blocks(blocks)
        result = cg.to_dict()
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["edges"][0]["relation"] == "calls"

    def test_contains_all_edge_types(self):
        cg = CallGraph()
        blocks = [
            {"name": "MyClass", "type": "class"},
            {"name": "my_method", "type": "function", "parent": "MyClass", "source": "helper"},
            {"name": "helper", "type": "function", "source": ""},
            {"name": "Child", "type": "class", "base_classes": ["MyClass"]},
        ]
        cg.build_from_blocks(blocks)
        result = cg.to_dict()

        edge_relations = {e["relation"] for e in result["edges"]}
        assert "contains" in edge_relations
        assert "calls" in edge_relations
        assert "inherits" in edge_relations

    def test_nodes_dicts(self):
        cg = CallGraph()
        blocks = [
            {"name": "MyClass", "type": "class", "extra": "data"},
        ]
        cg.build_from_blocks(blocks)
        result = cg.to_dict()
        assert result["nodes"][0]["name"] == "MyClass"
        assert result["nodes"][0]["type"] == "class"
        assert result["nodes"][0]["extra"] == "data"
