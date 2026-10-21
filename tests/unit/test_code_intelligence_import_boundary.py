"""Preserve code-intelligence exports without loading unrelated services for extraction."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "general_ludd.code_intelligence"
EXPORTS = {
    "ASTBlockExtractor": "extractor",
    "CallGraph": "callgraph",
    "CodeComplexityScorer": "complexity_scorer",
    "CodeSearch": "search",
    "CodebaseIntrospector": "introspect",
    "ComplexityScore": "complexity_scorer",
    "DirectoryComplexityReport": "complexity_scorer",
    "GitIntelligence": "git_intel",
}
DEFERRED = (
    f"{PACKAGE}.introspect",
    "general_ludd.self_improve",
    "general_ludd.self_improve.live_candidate_wiring",
    "general_ludd.self_improve._candidate_execution_types",
    "sqlalchemy",
)


def _probe(source: str) -> None:
    """Run the real package in a fresh interpreter without pytest's autouse imports."""
    bootstrap = (
        "import importlib, pickle, sys, typing\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        f"package = {PACKAGE!r}\nexports = {EXPORTS!r}\ndeferred = {DEFERRED!r}\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", bootstrap + textwrap.dedent(source)],
        cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_extractor_import_does_not_load_introspection_or_database() -> None:
    """Keep the real autouse parser reset independent of self-improvement services."""
    _probe("""
        from general_ludd.code_intelligence import extractor
        api = importlib.import_module(package)
        extractor._LANGUAGE_PARSERS.clear()
        assert api.ASTBlockExtractor is extractor.ASTBlockExtractor
        assert api.__all__ == tuple(exports)
        assert "CodebaseIntrospector" not in vars(api)
        assert "CodebaseIntrospector" in dir(api)
        assert not set(deferred).intersection(sys.modules)
    """)


def test_public_introspector_resolves_once_and_preserves_canonical_exports() -> None:
    """Retain from/star imports, canonical identity, construction and serialization."""
    _probe("""
        api = importlib.import_module(package)
        original = api.__getattr__
        calls = []
        def record(name):
            calls.append(name)
            return original(name)
        api.__getattr__ = record
        from general_ludd.code_intelligence import CodebaseIntrospector
        from general_ludd.code_intelligence import CodebaseIntrospector as again
        from general_ludd.code_intelligence import *
        assert calls == ["CodebaseIntrospector"]
        assert api.CodebaseIntrospector is again is CodebaseIntrospector
        assert api.__all__ == tuple(exports)
        for name, module in exports.items():
            canonical = getattr(importlib.import_module(package + "." + module), name)
            assert getattr(api, name) is canonical is globals()[name]
            assert name in dir(api)
        canonical = importlib.import_module(package + ".introspect").CodebaseIntrospector
        assert pickle.loads(pickle.dumps(CodebaseIntrospector)) is canonical
        instance = CodebaseIntrospector("isolated-repository")
        restored = pickle.loads(pickle.dumps(instance))
        assert type(restored) is canonical
        assert restored.repo_root == instance.repo_root == "isolated-repository"
        assert typing.get_type_hints(again.__init__) == typing.get_type_hints(canonical.__init__)
    """)


def test_direct_introspector_import_preserves_public_class_identity() -> None:
    """Support callers importing the canonical submodule before the package export."""
    _probe("""
        direct = importlib.import_module(package + ".introspect").CodebaseIntrospector
        api = importlib.import_module(package)
        from general_ludd.code_intelligence import CodebaseIntrospector
        assert api.CodebaseIntrospector is direct is CodebaseIntrospector
    """)


def test_failed_introspector_import_propagates_without_caching_and_can_retry() -> None:
    """Expose the original failure and allow a later real import after it is repaired."""
    _probe("""
        import importlib.abc
        api = importlib.import_module(package)
        failure = RuntimeError("real introspector import unavailable")
        class RefuseIntrospection(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == package + ".introspect":
                    raise failure
                return None
        blocker = RefuseIntrospection()
        sys.meta_path.insert(0, blocker)
        try:
            try:
                api.CodebaseIntrospector
            except RuntimeError as error:
                assert error is failure
            else:
                raise AssertionError("lazy import hid its failure")
            assert "CodebaseIntrospector" not in vars(api)
            assert package + ".introspect" not in sys.modules
        finally:
            sys.meta_path.remove(blocker)
        direct = importlib.import_module(package + ".introspect").CodebaseIntrospector
        assert api.CodebaseIntrospector is direct
    """)


def test_package_reload_refreshes_patched_and_restored_canonical_class() -> None:
    """Retain the eager re-export's reload behavior for module patch/restore users."""
    _probe("""
        api = importlib.import_module(package)
        native = importlib.import_module(package + ".introspect")
        original = native.CodebaseIntrospector
        assert api.CodebaseIntrospector is original
        class Replacement:
            pass
        native.CodebaseIntrospector = Replacement
        try:
            importlib.reload(api)
            assert api.CodebaseIntrospector is Replacement
        finally:
            native.CodebaseIntrospector = original
            importlib.reload(api)
        assert api.CodebaseIntrospector is original
    """)


def test_concurrent_first_access_preserves_canonical_identity() -> None:
    """Use Python's native import lock for two simultaneous first consumers."""
    _probe("""
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        api = importlib.import_module(package)
        ready = Barrier(2)
        def resolve():
            ready.wait(timeout=5)
            return api.CodebaseIntrospector
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = [pool.submit(resolve) for _ in range(2)]
            left, right = first.result(timeout=10), second.result(timeout=10)
        direct = importlib.import_module(package + ".introspect").CodebaseIntrospector
        assert left is right is direct is api.CodebaseIntrospector
    """)


def test_unknown_export_does_not_import_introspector() -> None:
    """Reject undeclared attributes without invoking the deferred service import."""
    _probe("""
        api = importlib.import_module(package)
        try:
            api.NotAnExport
        except AttributeError as error:
            assert package in str(error) and "NotAnExport" in str(error)
        else:
            raise AssertionError("unknown export unexpectedly resolved")
        assert not hasattr(api, "NotAnExport")
        assert "NotAnExport" not in vars(api)
        assert not set(deferred).intersection(sys.modules)
    """)
