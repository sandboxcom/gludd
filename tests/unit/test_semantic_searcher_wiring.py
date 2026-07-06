"""Prove SemanticSearcher is wired into the production daemon path."""

from __future__ import annotations

import ast
import tempfile


class TestSemanticSearcherImportAndConstruction:
    def test_can_import_semantic_searcher(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        assert SemanticSearcher is not None

    def test_can_construct_semantic_searcher(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        with tempfile.TemporaryDirectory() as tmpdir:
            searcher = SemanticSearcher(cache_dir=tmpdir)
            try:
                assert searcher is not None
                assert hasattr(searcher, "search")
                assert hasattr(searcher, "close")
            finally:
                searcher.close()

    def test_search_returns_list(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        with tempfile.TemporaryDirectory() as tmpdir:
            searcher = SemanticSearcher(cache_dir=tmpdir)
            try:
                results = searcher.search("test query")
                assert isinstance(results, list)
            finally:
                searcher.close()

    def test_close_does_not_raise(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        with tempfile.TemporaryDirectory() as tmpdir:
            searcher = SemanticSearcher(cache_dir=tmpdir)
            searcher.close()
            searcher.close()


class TestSemanticSearcherWiredInDaemon:
    def test_daemon_imports_semantic_searcher(self):

        daemon_path = "src/general_ludd/daemon.py"
        with open(daemon_path) as f:
            tree = ast.parse(f.read(), filename=daemon_path)

        import_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None and "retrieval" in node.module:
                for alias in node.names:
                    if "SemanticSearcher" in alias.name:
                        import_found = True
                        break

        assert import_found, (
            "daemon.py must import SemanticSearcher from general_ludd.retrieval"
        )

    def test_daemon_passes_searcher_to_execution_engine(self):

        daemon_path = "src/general_ludd/daemon.py"
        with open(daemon_path) as f:
            tree = ast.parse(f.read(), filename=daemon_path)

        searcher_kwarg_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in getattr(node, "keywords", []):
                    if kw.arg == "searcher" and isinstance(kw.value, ast.Name):
                        searcher_kwarg_found = True
                        break

        assert searcher_kwarg_found, (
            "daemon.py must pass searcher=... keyword to ExecutionEngine constructor"
        )

    def test_semantic_searcher_variable_instantiated_in_daemon(self):

        daemon_path = "src/general_ludd/daemon.py"
        with open(daemon_path) as f:
            tree = ast.parse(f.read(), filename=daemon_path)

        semantic_searcher_assign = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and "searcher" in target.id.lower()
                        and isinstance(node.value, ast.Call)
                        and (
                            (
                                hasattr(node.value.func, "attr")
                                and "Searcher" in node.value.func.attr
                            )
                            or (
                                hasattr(node.value.func, "id")
                                and "Searcher" in node.value.func.id
                            )
                        )
                    ):
                        semantic_searcher_assign = True
                        break

        assert semantic_searcher_assign, (
            "daemon.py must instantiate SemanticSearcher into a variable"
        )
