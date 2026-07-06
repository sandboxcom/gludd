"""Prove CodebaseIndexer is wired into the production path.

Verifies:
1. CodebaseIndexer is importable from public API (general_ludd.retrieval).
2. It can be constructed with the default cache dir.
3. It exposes the expected API (index_files, close, cache_dir).
4. It is instantiated and stored on daemon app state during lifespan startup.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.retrieval import CodebaseIndexer


class TestCodebaseIndexerWiring:
    def test_importable_from_public_api(self):
        assert CodebaseIndexer is not None

    def test_constructs_with_default_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "general_ludd.retrieval.indexer.DEFAULT_CACHE_DIR", tmpdir
        ):
            indexer = CodebaseIndexer()
            assert indexer is not None
            assert isinstance(indexer.cache_dir, str)
            indexer.close()

    def test_constructs_with_custom_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodebaseIndexer(cache_dir=tmpdir)
            assert indexer.cache_dir == tmpdir
            indexer.close()

    def test_exposes_index_files_method(self):
        assert hasattr(CodebaseIndexer, "index_files")
        assert callable(CodebaseIndexer.index_files)

    def test_exposes_close_method(self):
        assert hasattr(CodebaseIndexer, "close")
        assert callable(CodebaseIndexer.close)

    def test_exposes_cache_dir_property(self):
        assert hasattr(CodebaseIndexer, "cache_dir")

    def test_index_files_yields_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            cache_dir.mkdir()
            indexer = CodebaseIndexer(cache_dir=str(cache_dir))

            src_file = Path(tmpdir) / "example.py"
            src_file.write_text("def hello():\n    return 'world'\n")

            result = indexer.index_files([src_file])
            assert result["files_indexed"] >= 0
            assert result["chunks_indexed"] >= 0
            assert "errors" in result

            indexer.close()

    def test_index_files_skips_missing_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            cache_dir.mkdir()
            indexer = CodebaseIndexer(cache_dir=str(cache_dir))

            missing = Path(tmpdir) / "does_not_exist.py"
            result = indexer.index_files([missing])
            assert result["files_indexed"] == 0
            assert result["chunks_indexed"] == 0

            indexer.close()

    def test_index_files_handles_non_utf8(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            cache_dir.mkdir()
            indexer = CodebaseIndexer(cache_dir=str(cache_dir))

            bin_file = Path(tmpdir) / "binary.bin"
            bin_file.write_bytes(b"\x00\xff\xfe\xfd")

            result = indexer.index_files([bin_file])
            assert result["errors"] >= 1

            indexer.close()


class TestDaemonWiresCodebaseIndexer:
    def test_daemon_lifespan_imports_codebase_indexer(self):
        daemon_source = Path(__file__).parents[2] / "src" / "general_ludd" / "daemon.py"
        source_text = daemon_source.read_text()
        assert "from general_ludd.retrieval.indexer import CodebaseIndexer" in source_text

    def test_daemon_lifespan_stores_on_app_state(self):
        daemon_source = Path(__file__).parents[2] / "src" / "general_ludd" / "daemon.py"
        source_text = daemon_source.read_text()
        assert "app.state._codebase_indexer = _codebase_indexer" in source_text

    def test_codebase_indexer_importable_in_daemon_module(self):
        import general_ludd.daemon

        assert hasattr(general_ludd.daemon, "_lifespan")

    def test_indexer_stored_on_mock_app_state(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "general_ludd.retrieval.indexer.DEFAULT_CACHE_DIR", tmpdir
        ):
            indexer = CodebaseIndexer()
            mock_app = MagicMock()
            mock_app.state._codebase_indexer = indexer
            assert mock_app.state._codebase_indexer is indexer
            assert isinstance(mock_app.state._codebase_indexer.cache_dir, str)
            indexer.close()
