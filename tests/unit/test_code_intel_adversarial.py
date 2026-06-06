"""Adversarial and edge-case tests for CodeIntelligence: bad data, error paths, type violations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestExtractorAdversarial:
    def test_extract_none_source(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        with pytest.raises((TypeError, AttributeError)):
            extractor.extract_blocks(None)  # type: ignore[arg-type]

    def test_extract_binary_data(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks("\x00\x01\x02\x03", language="python")
        assert isinstance(blocks, list)

    def test_extract_very_long_line(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        long_code = "x = " + "1 + " * 10000 + "0"
        blocks = extractor.extract_blocks(long_code, language="python")
        assert isinstance(blocks, list)

    def test_extract_nested_classes(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        code = """
class Outer:
    class Inner:
        def method(self):
            pass
"""
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(code, language="python")
        classes = [b for b in blocks if b["type"] == "class"]
        methods = [b for b in blocks if b["type"] == "method"]
        assert len(classes) >= 2
        assert len(methods) >= 1

    def test_extract_decorated_methods(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        code = """
class Foo:
    @staticmethod
    def static_method():
        pass

    @classmethod
    def class_method(cls):
        pass

    @property
    def prop(self):
        return 42
"""
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(code, language="python")
        methods = [b for b in blocks if b["type"] == "method"]
        names = {m["name"] for m in methods}
        assert "static_method" in names
        assert "class_method" in names
        assert "prop" in names

    def test_extract_async_functions(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        code = """
async def fetch_data():
    return await some_coro()

class Worker:
    async def process(self):
        pass
"""
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(code, language="python")
        funcs = [b for b in blocks if b["type"] == "function"]
        methods = [b for b in blocks if b["type"] == "method"]
        assert any(b["name"] == "fetch_data" for b in funcs)
        assert any(b["name"] == "process" for b in methods)

    def test_extract_lambda_functions_ignored(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        code = """
x = lambda a: a + 1
def real_func():
    f = lambda b: b * 2
    return f
"""
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(code, language="python")
        funcs = [b for b in blocks if b["type"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "real_func"

    def test_extract_with_try_except_blocks(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        code = """
def risky_operation():
    try:
        do_thing()
    except ValueError as e:
        handle_error(e)
    finally:
        cleanup()
"""
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(code, language="python")
        funcs = [b for b in blocks if b["type"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "risky_operation"

    def test_extract_multiline_docstrings(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        code = '''
def documented():
    """First line.
    Second line.
    Third line."""
    pass
'''
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(code, language="python")
        funcs = [b for b in blocks if b["type"] == "function"]
        assert len(funcs) == 1
        doc = funcs[0].get("docstring", "")
        assert "First line" in doc


class TestCallGraphAdversarial:
    def test_build_from_empty_blocks(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        graph = CallGraph()
        graph.build_from_blocks([])
        assert len(graph.to_dict()["nodes"]) == 0

    def test_build_from_malformed_blocks(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        graph = CallGraph()
        graph.build_from_blocks([
            {"name": 123, "type": "function"},  # type: ignore[dict-item]
            {},
            {"name": "valid", "type": "function"},
        ])
        assert graph.has_node("valid")

    def test_get_callees_unknown_node(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        graph = CallGraph()
        assert graph.get_callees("nonexistent") == []

    def test_get_callers_unknown_node(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        graph = CallGraph()
        assert graph.get_callers("nonexistent") == []

    def test_is_subclass_unknown(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        graph = CallGraph()
        assert not graph.is_subclass("a", "b")

    def test_to_dict_empty(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        graph = CallGraph()
        d = graph.to_dict()
        assert d["nodes"] == []
        assert d["edges"] == []

    def test_full_name_with_none_parent(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        name = CallGraph._full_name({"name": "foo", "parent": None})
        assert name == "foo"

    def test_full_name_missing_name(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        name = CallGraph._full_name({})
        assert name == "unknown"


class TestCodeSearchAdversarial:
    def test_search_none_query(self):
        from general_ludd.code_intelligence.search import CodeSearch

        searcher = CodeSearch([{"name": "test", "type": "function"}])
        results = searcher.search(None, type_filter="function")  # type: ignore[arg-type]
        assert len(results) == 1

    def test_search_blocks_with_missing_fields(self):
        from general_ludd.code_intelligence.search import CodeSearch

        searcher = CodeSearch([
            {"name": "valid", "type": "function"},
            {},
            {"name": "also_valid", "type": "method"},
            {"type": "class"},
        ])
        results = searcher.search()
        assert len(results) == 4

    def test_list_types_with_none_types(self):
        from general_ludd.code_intelligence.search import CodeSearch

        searcher = CodeSearch([
            {"name": "a", "type": "function"},
            {"name": "b", "type": None},
            {"name": "c"},
        ])
        types = searcher.list_types()
        assert "function" in types


class TestGitIntelligenceAdversarial:
    def test_files_changed_together_corrupt_output(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "garbled\n\nmore garbled\n"
            mock_run.return_value = mock_result
            result = git_intel.files_changed_together()
            assert result == []

    def test_files_changed_together_many_files(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "a.py\nb.py\n\nb.py\nc.py\n\na.py\nb.py\n"
            mock_run.return_value = mock_result
            result = git_intel.files_changed_together()
            assert len(result) >= 1

    def test_blame_analysis_git_failure(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_run.return_value = mock_result
            result = git_intel.blame_analysis("nonexistent.py")
            assert result == {}

    def test_blame_analysis_malformed(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "author alice\nauthor bob\n\tcode line\n"
            mock_run.return_value = mock_result
            result = git_intel.blame_analysis("file.py")
            assert result["total_lines"] == 1
            assert len(result["author_breakdown"]) == 2

    def test_recent_contributors_subprocess_error(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("git not found")
            result = git_intel.recent_contributors()
            assert result == []

    def test_recent_contributors_malformed(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "5\tAlice\n3\tBob <bob@e.com>\n"
            mock_run.return_value = mock_result
            result = git_intel.recent_contributors()
            assert len(result) == 2
            assert result[0]["commits"] == 5
            assert result[0]["name"] == "Alice"

    def test_recent_commits_subprocess_error(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("timeout")
            result = git_intel.recent_commits()
            assert result == []

    def test_recent_commits_partial_format(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "abc123\tmsg1\tauthor1\t2026-01-01\nshort\n"
            mock_run.return_value = mock_result
            result = git_intel.recent_commits()
            assert len(result) == 1
            assert result[0]["hash"] == "abc123"

    def test_hot_files_subprocess_error(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 128
            mock_run.return_value = mock_result
            result = git_intel.hot_files()
            assert result == []

    def test_hot_files_with_tabs(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "src/main.py\n\tsrc/main.py\nsrc/util.py\n"
            mock_run.return_value = mock_result
            result = git_intel.hot_files(limit=5)
            assert len(result) >= 2

    def test_all_methods_empty_repo(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/empty")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 128
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            assert git_intel.files_changed_together() == []
            assert git_intel.blame_analysis("x.py") == {}
            assert git_intel.recent_contributors() == []
            assert git_intel.recent_commits() == []
            assert git_intel.hot_files() == []


class TestTypeShapeValidation:
    def test_extract_block_has_all_required_fields(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks("def foo(): pass", language="python")
        assert len(blocks) == 1
        b = blocks[0]
        required = {"name", "type", "start_line", "end_line", "parent", "docstring", "source"}
        for field in required:
            assert field in b, f"Missing field: {field}"
        assert isinstance(b["name"], str)
        assert b["type"] in ("function", "method", "class")
        assert isinstance(b["start_line"], int)
        assert isinstance(b["end_line"], int)
        assert b["start_line"] <= b["end_line"]

    def test_callgraph_node_has_all_fields(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks("def bar(): pass", language="python")
        graph = CallGraph()
        graph.build_from_blocks(blocks)
        d = graph.to_dict()
        assert isinstance(d["nodes"], list)
        assert isinstance(d["edges"], list)
        for node in d["nodes"]:
            assert "name" in node
            assert isinstance(node["name"], str)

    def test_git_intel_result_shapes(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "a.py\nb.py\n"
            mock_run.return_value = mock_result

            result = git_intel.files_changed_together(limit=2)
            for item in result:
                assert "files" in item
                assert "count" in item
                assert isinstance(item["files"], list)
                assert isinstance(item["count"], int)

            mock_result.stdout = "abc123\tmsg\tauth\t2026-01-01\n"
            commits = git_intel.recent_commits(limit=1)
            for c in commits:
                assert "hash" in c
                assert "message" in c
                assert "author" in c
                assert "date" in c
                assert len(c["hash"]) <= 7
