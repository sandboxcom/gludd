"""Tests for CodeIntelligence — AST extraction, call graph, code search, git intelligence."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

SAMPLE_PY_CODE = '''
"""Sample module for testing."""

import os
import sys
from pathlib import Path
from typing import Any, Optional


class DataProcessor:
    """Processes data records."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._cache: dict[str, Any] = {}

    def process(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Main processing method."""
        results = []
        for record in data:
            result = self._transform(record)
            if self._validate(result):
                results.append(result)
        return results

    def _transform(self, record: dict[str, Any]) -> dict[str, Any]:
        return {k: str(v).upper() for k, v in record.items()}

    @staticmethod
    def _validate(record: dict[str, Any]) -> bool:
        return len(record) > 0


def helper_function(x: int, y: int) -> int:
    """A standalone helper."""
    return x + y


class SubProcessor(DataProcessor):
    """Extended processor."""

    def process(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = super().process(data)
        return [r for r in result if r]
'''


class TestASTBlockExtractor:
    def test_extract_functions_from_python(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        functions = [b for b in blocks if b["type"] == "function"]
        assert len(functions) == 1
        assert functions[0]["name"] == "helper_function"
        assert "standalone helper" in functions[0].get("docstring", "")

    def test_extract_classes_from_python(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        classes = [b for b in blocks if b["type"] == "class"]
        assert len(classes) == 2
        names = {c["name"] for c in classes}
        assert names == {"DataProcessor", "SubProcessor"}

    def test_extract_methods_from_python(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        methods = [b for b in blocks if b["type"] == "method"]
        names = {m["name"] for m in methods}
        assert "process" in names
        assert "__init__" in names
        assert "_transform" in names
        assert "_validate" in names

    def test_extract_includes_line_numbers(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        for b in blocks:
            assert "start_line" in b, f"Block {b.get('name')} missing start_line"
            assert "end_line" in b, f"Block {b.get('name')} missing end_line"
            assert b["start_line"] <= b["end_line"]

    def test_extract_includes_parent_info(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        methods = [b for b in blocks if b["type"] == "method"]
        for m in methods:
            assert "parent" in m, f"Method {m['name']} missing parent"
            if m["name"] != "helper_function":
                assert m["parent"] is not None

    def test_extract_empty_code(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks("", language="python")
        assert blocks == []

    def test_extract_invalid_code_graceful(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks("def broken(", language="python")
        assert blocks == []


class TestCallGraph:
    def test_build_call_graph_from_blocks(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        graph = CallGraph()
        graph.build_from_blocks(blocks)

        assert graph.has_node("helper_function")
        assert graph.has_node("DataProcessor")

    def test_find_callees(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        graph = CallGraph()
        graph.build_from_blocks(blocks)

        callees = graph.get_callees("DataProcessor.process")
        assert len(callees) > 0

    def test_find_callers(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        graph = CallGraph()
        graph.build_from_blocks(blocks)

        callers = graph.get_callers("DataProcessor._transform")
        assert len(callers) > 0
        assert "DataProcessor.process" in callers

    def test_subclass_relationship(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        graph = CallGraph()
        graph.build_from_blocks(blocks)

        assert graph.is_subclass("SubProcessor", "DataProcessor")

    def test_empty_graph(self):
        from general_ludd.code_intelligence.callgraph import CallGraph

        graph = CallGraph()
        assert graph.get_callees("nonexistent") == []
        assert graph.get_callers("nonexistent") == []
        assert not graph.is_subclass("a", "b")

    def test_graph_export_dict(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        graph = CallGraph()
        graph.build_from_blocks(blocks)

        d = graph.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert len(d["nodes"]) > 0


class TestCodeSearch:
    def test_search_by_name(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        searcher = CodeSearch(blocks)

        results = searcher.search("process", type_filter="method")
        assert len(results) > 0
        assert any("process" in r["name"].lower() for r in results)

    def test_search_by_type_function(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        searcher = CodeSearch(blocks)

        results = searcher.search(type_filter="function")
        assert len(results) == 1
        assert results[0]["name"] == "helper_function"

    def test_search_by_type_class(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        searcher = CodeSearch(blocks)

        results = searcher.search(type_filter="class")
        assert len(results) == 2

    def test_search_empty(self):
        from general_ludd.code_intelligence.search import CodeSearch

        searcher = CodeSearch([])
        results = searcher.search("anything")
        assert results == []

    def test_list_types(self):
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")
        searcher = CodeSearch(blocks)

        types = searcher.list_types()
        assert "function" in types
        assert "class" in types
        assert "method" in types


class TestGitIntelligence:
    def test_files_changed_together_empty(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/nonexistent")
        result = git_intel.files_changed_together()
        assert result == []

    def test_blame_analysis_empty(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/nonexistent")
        result = git_intel.blame_analysis("src/main.py")
        assert result == {}

    def test_recent_contributors_empty(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/nonexistent")
        result = git_intel.recent_contributors()
        assert result == []

    def test_git_intel_with_mock(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/some/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "abc123\tFix login bug\tdev\t2026-01-01\n\ndef456\tAdd feature\tdev\t2026-01-02\n"
            mock_run.return_value = mock_result

            commits = git_intel.recent_commits(limit=2)
            assert len(commits) == 2
            assert commits[0]["hash"] == "abc123"
            assert commits[0]["message"] == "Fix login bug"

    def test_diff_analysis(self):
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        git_intel = GitIntelligence("/repo")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "file1.py\nfile1.py\nfile1.py\nfile2.py\n"
            mock_run.return_value = mock_result

            hot = git_intel.hot_files(limit=5)
            assert len(hot) == 2
            assert hot[0]["path"] == "file1.py"
            assert hot[0]["changes"] == 3


class TestCodeIntelligenceIntegration:
    def test_full_pipeline(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")

        graph = CallGraph()
        graph.build_from_blocks(blocks)

        searcher = CodeSearch(blocks)

        assert len(blocks) >= 5
        assert len(graph.to_dict()["nodes"]) > 0

        methods = searcher.search(type_filter="method")
        assert len(methods) >= 2

        classes = searcher.search(type_filter="class")
        assert len(classes) == 2

    def test_model_friendly_output(self):
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(SAMPLE_PY_CODE, language="python")

        graph = CallGraph()
        graph.build_from_blocks(blocks)

        searcher = CodeSearch(blocks)

        results = searcher.search("DataProcessor.process")
        for r in results:
            assert "name" in r
            assert "type" in r
            assert "start_line" in r
            assert "end_line" in r
            if r.get("docstring"):
                assert isinstance(r["docstring"], str)
