"""
Tests for module_utils/document_loader.py and module_utils/chunking.py.

Covers: Document, TextLoader, HTMLLoader, PDFLoader, DirectoryLoader,
load_document, ChunkingStrategy (sliding window, sentence boundary,
fixed size).

Run with:
    ANSIBLE_COLLECTIONS_PATH=collections uv run python -m pytest \
      collections/ansible_collections/general_ludd/agent/tests/unit/test_document_loader.py -v
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, "collections")

from ansible_collections.general_ludd.agent.plugins.module_utils.chunking import (
    ChunkingStrategy,
    FixedSizeStrategy,
    SentenceBoundaryStrategy,
    SlidingWindowStrategy,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.document_loader import (
    DirectoryLoader,
    Document,
    HTMLLoader,
    PDFLoader,
    TextLoader,
    load_document,
)


class TestDocument:
    def test_construction(self) -> None:
        doc = Document(text="hello", metadata={"page": 1}, source="test.txt")
        assert doc.text == "hello"
        assert doc.metadata == {"page": 1}
        assert doc.source == "test.txt"

    def test_defaults(self) -> None:
        doc = Document(text="hello")
        assert doc.metadata == {}
        assert doc.source == ""

    def test_equality(self) -> None:
        a = Document(text="x", metadata={"k": "v"}, source="s")
        b = Document(text="x", metadata={"k": "v"}, source="s")
        assert a == b

    def test_inequality(self) -> None:
        a = Document(text="x")
        b = Document(text="y")
        assert a != b


class TestTextLoader:
    def test_load_txt(self) -> None:
        loader = TextLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world\nsecond line\n")
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
            assert docs[0].text == "hello world\nsecond line\n"
            assert docs[0].source == path
        finally:
            os.unlink(path)

    def test_load_md(self) -> None:
        loader = TextLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Title\n\nbody text\n")
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
            assert "# Title" in docs[0].text
            assert docs[0].source == path
        finally:
            os.unlink(path)

    def test_load_rst(self) -> None:
        loader = TextLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rst", delete=False) as f:
            f.write("Title\n=====\n\nContent here.\n")
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
            assert "Content here." in docs[0].text
        finally:
            os.unlink(path)

    def test_load_csv(self) -> None:
        loader = TextLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("name,age\nAlice,30\nBob,25\n")
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
            assert "name,age" in docs[0].text
            assert "Alice" in docs[0].text
        finally:
            os.unlink(path)

    def test_load_csv_with_rows(self) -> None:
        loader = TextLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("name,age,city\nAlice,30,NYC\nBob,25,LA\n")
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
            assert docs[0].metadata.get("format") == "csv"
        finally:
            os.unlink(path)

    def test_load_empty_file(self) -> None:
        loader = TextLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
            assert docs[0].text == ""
        finally:
            os.unlink(path)

    def test_load_nonexistent_file_raises(self) -> None:
        loader = TextLoader()
        with pytest.raises(FileNotFoundError):
            loader.load("/nonexistent/path/file.txt")

    def test_metadata_attached(self) -> None:
        loader = TextLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            path = f.name
        try:
            docs = loader.load(path, metadata={"author": "test"})
            assert docs[0].metadata.get("author") == "test"
            assert docs[0].metadata.get("format") == "txt"
        finally:
            os.unlink(path)


class TestHTMLLoader:
    def test_load_simple_html(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<html><body><p>Hello world</p></body></html>")
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
            assert "Hello world" in docs[0].text
            assert "<p>" not in docs[0].text
        finally:
            os.unlink(path)

    def test_strips_all_tags(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<div><h1>Title</h1><p>Para <b>bold</b></p></div>")
            path = f.name
        try:
            docs = loader.load(path)
            text = docs[0].text
            assert "Title" in text
            assert "Para" in text
            assert "bold" in text
            assert "<" not in text
        finally:
            os.unlink(path)

    def test_preserves_whitespace_structure(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<p>First sentence.</p><p>Second sentence.</p>")
            path = f.name
        try:
            docs = loader.load(path)
            assert "First sentence." in docs[0].text
            assert "Second sentence." in docs[0].text
        finally:
            os.unlink(path)

    def test_handles_nested_tables(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<html><body><table><tr><td>A</td><td>B</td></tr></table></body></html>")
            path = f.name
        try:
            docs = loader.load(path)
            assert "A" in docs[0].text
            assert "B" in docs[0].text
        finally:
            os.unlink(path)

    def test_handles_html_entities(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<p>x &lt; y &amp; z &gt; w</p>")
            path = f.name
        try:
            docs = loader.load(path)
            assert "&lt;" not in docs[0].text
            assert "<" in docs[0].text
        finally:
            os.unlink(path)

    def test_empty_html(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<html><body></body></html>")
            path = f.name
        try:
            docs = loader.load(path)
            assert len(docs) == 1
        finally:
            os.unlink(path)

    def test_html_with_script_tags(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<html><head><script>console.log('x')</script></head><body><p>visible text</p></body></html>")
            path = f.name
        try:
            docs = loader.load(path)
            text = docs[0].text
            assert "visible text" in text
            assert "console.log" not in text
        finally:
            os.unlink(path)

    def test_html_with_style_tags(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<html><head><style>.x{color:red}</style></head><body><p>visible</p></body></html>")
            path = f.name
        try:
            docs = loader.load(path)
            text = docs[0].text
            assert "visible" in text
            assert "color" not in text
        finally:
            os.unlink(path)

    def test_source_set(self) -> None:
        loader = HTMLLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<p>test</p>")
            path = f.name
        try:
            docs = loader.load(path)
            assert docs[0].source == path
        finally:
            os.unlink(path)


class TestPDFLoader:
    def test_pypdf_not_installed_raises_clear_error(self) -> None:
        import builtins as _bi

        _real_import = _bi.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "pypdf" or name.startswith("pypdf."):
                raise ImportError("No module named 'pypdf'")
            return _real_import(name, *args, **kwargs)

        _bi.__import__ = _mock_import
        try:
            with pytest.raises(ImportError, match="pypdf"):
                PDFLoader()
        finally:
            _bi.__import__ = _real_import

    def test_construction_with_pypdf(self) -> None:
        try:
            loader = PDFLoader()
            assert loader is not None
        except ImportError:
            pytest.skip("pypdf not installed")


class TestDirectoryLoader:
    def test_loads_all_supported_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            (root / "a.txt").write_text("text a")
            (root / "b.md").write_text("text b")
            (root / "c.html").write_text("<p>text c</p>")
            (root / "data.csv").write_text("col1,col2\n1,2")
            (root / "skip.json").write_text('{"key": "val"}')

            loader = DirectoryLoader()
            docs = loader.load(tmpdir)

            texts = {d.text.strip() for d in docs}
            assert "text a" in texts
            assert "text b" in texts
            assert len(docs) >= 3

    def test_recursive_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            (root / "sub").mkdir()
            (root / "root.txt").write_text("root")
            (root / "sub" / "nested.txt").write_text("nested")

            loader = DirectoryLoader()
            docs = loader.load(tmpdir)

            texts = {d.text.strip() for d in docs}
            assert "root" in texts
            assert "nested" in texts
            assert len(docs) == 2

    def test_glob_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            (root / "a.txt").write_text("txt")
            (root / "b.md").write_text("md")

            loader = DirectoryLoader(glob="*.txt")
            docs = loader.load(tmpdir)

            assert len(docs) == 1
            assert docs[0].text.strip() == "txt"

    def test_excludes_files_outside_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            (root / "readme.md").write_text("readme")
            (root / "image.png").write_text("fake png")

            loader = DirectoryLoader()
            docs = loader.load(tmpdir)

            texts = {d.text.strip() for d in docs}
            assert "readme" in texts
            assert "fake png" not in texts

    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = DirectoryLoader()
            docs = loader.load(tmpdir)
            assert docs == []

    def test_nonexistent_directory_raises(self) -> None:
        loader = DirectoryLoader()
        with pytest.raises(FileNotFoundError):
            loader.load("/nonexistent/directory/path")

    def test_silently_skips_unreadable_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            (root / "good.txt").write_text("good")
            bad = root / "bad.html"
            bad.write_text("<p>bad</p>")
            os.chmod(bad, 0o000)

            try:
                loader = DirectoryLoader()
                docs = loader.load(tmpdir)
                assert len(docs) >= 1
                texts = {d.text.strip() for d in docs}
                assert "good" in texts
            finally:
                os.chmod(bad, 0o644)


class TestLoadDocument:
    def test_auto_detect_txt(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("plain text")
            path = f.name
        try:
            docs = load_document(path)
            assert len(docs) == 1
            assert docs[0].text == "plain text"
        finally:
            os.unlink(path)

    def test_auto_detect_html(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<p>html text</p>")
            path = f.name
        try:
            docs = load_document(path)
            assert len(docs) == 1
            assert docs[0].text.strip() == "html text"
        finally:
            os.unlink(path)

    def test_auto_detect_md(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# markdown")
            path = f.name
        try:
            docs = load_document(path)
            assert len(docs) == 1
            assert "# markdown" in docs[0].text
        finally:
            os.unlink(path)

    def test_auto_detect_csv(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("a,b\n1,2")
            path = f.name
        try:
            docs = load_document(path)
            assert len(docs) == 1
            assert "a,b" in docs[0].text
        finally:
            os.unlink(path)

    def test_auto_detect_rst(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rst", delete=False) as f:
            f.write("Title\n=====")
            path = f.name
        try:
            docs = load_document(path)
            assert len(docs) == 1
            assert "Title" in docs[0].text
        finally:
            os.unlink(path)

    def test_auto_detect_htm(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".htm", delete=False) as f:
            f.write("<p>htm text</p>")
            path = f.name
        try:
            docs = load_document(path)
            assert len(docs) == 1
            assert docs[0].text.strip() == "htm text"
        finally:
            os.unlink(path)

    def test_unknown_extension_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xyz", delete=False) as f:
            f.write("data")
            path = f.name
        try:
            with pytest.raises(ValueError, match="Unsupported file format"):
                load_document(path)
        finally:
            os.unlink(path)

    def test_no_extension_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("data")
            path = f.name
        try:
            with pytest.raises(ValueError, match="Cannot determine file format"):
                load_document(path)
        finally:
            os.unlink(path)

    def test_metadata_passed_through(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("text")
            path = f.name
        try:
            docs = load_document(path, metadata={"project": "gludd"})
            assert docs[0].metadata.get("project") == "gludd"
        finally:
            os.unlink(path)

    def test_directory_auto_detect(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            (root / "a.txt").write_text("hello")
            (root / "b.txt").write_text("world")

            docs = load_document(tmpdir)
            assert len(docs) == 2
            texts = {d.text.strip() for d in docs}
            assert texts == {"hello", "world"}


class TestSlidingWindowStrategy:
    def test_basic_split(self) -> None:
        strategy = SlidingWindowStrategy(chunk_size=10, overlap=3)
        text = "This is a test text for chunking."
        chunks = strategy.chunk(text)
        assert len(chunks) > 1

    def test_text_smaller_than_chunk_size(self) -> None:
        strategy = SlidingWindowStrategy(chunk_size=1000, overlap=200)
        text = "short"
        chunks = strategy.chunk(text)
        assert len(chunks) == 1
        assert chunks[0].text == "short"

    def test_overlap_preserves_context(self) -> None:
        strategy = SlidingWindowStrategy(chunk_size=20, overlap=10)
        text = "AAAAABBBBBCCCCC"
        chunks = strategy.chunk(text)
        assert len(chunks) >= 2
        combined = "".join(c.text for c in chunks)
        assert set(text) == set(combined.replace(" ", ""))

    def test_empty_text(self) -> None:
        strategy = SlidingWindowStrategy()
        chunks = strategy.chunk("")
        assert chunks == []

    def test_default_chunk_size(self) -> None:
        strategy = SlidingWindowStrategy()
        assert strategy.chunk_size == 1000
        assert strategy.overlap == 200

    def test_validation_chunk_size_positive(self) -> None:
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            SlidingWindowStrategy(chunk_size=0)

    def test_validation_overlap_non_negative(self) -> None:
        with pytest.raises(ValueError, match="overlap must be non-negative"):
            SlidingWindowStrategy(overlap=-1)

    def test_validation_overlap_less_than_size(self) -> None:
        with pytest.raises(ValueError, match="overlap must be less than chunk_size"):
            SlidingWindowStrategy(chunk_size=5, overlap=5)


class TestSentenceBoundaryStrategy:
    def test_splits_on_period(self) -> None:
        strategy = SentenceBoundaryStrategy(max_chunk_size=20)
        text = "First sentence. Second sentence. Third sentence."
        chunks = strategy.chunk(text)
        assert len(chunks) >= 2

    def test_splits_on_question_mark(self) -> None:
        strategy = SentenceBoundaryStrategy(max_chunk_size=20)
        text = "What is this? It is a test."
        chunks = strategy.chunk(text)
        assert len(chunks) == 2

    def test_splits_on_exclamation(self) -> None:
        strategy = SentenceBoundaryStrategy(max_chunk_size=12)
        text = "Hello! Goodbye!"
        chunks = strategy.chunk(text)
        assert len(chunks) >= 2

    def test_respects_max_chunk_size(self) -> None:
        strategy = SentenceBoundaryStrategy(max_chunk_size=30)
        text = "Short one. This is a much longer sentence that goes on and on beyond thirty chars. Another one."
        chunks = strategy.chunk(text)
        for c in chunks:
            assert len(c.text) <= 30

    def test_single_sentence(self) -> None:
        strategy = SentenceBoundaryStrategy()
        text = "Just one sentence."
        chunks = strategy.chunk(text)
        assert len(chunks) == 1
        assert chunks[0].text.strip() == "Just one sentence."

    def test_empty_text(self) -> None:
        strategy = SentenceBoundaryStrategy()
        assert strategy.chunk("") == []

    def test_no_sentence_terminators(self) -> None:
        strategy = SentenceBoundaryStrategy(max_chunk_size=10)
        text = "runontextwithnoterminator"
        chunks = strategy.chunk(text)
        assert len(chunks) >= 1
        for c in chunks:
            assert len(c.text) <= 10


class TestFixedSizeStrategy:
    def test_exact_split(self) -> None:
        strategy = FixedSizeStrategy(chunk_size=5)
        text = "1234567890"
        chunks = strategy.chunk(text)
        assert len(chunks) == 2
        assert chunks[0].text == "12345"
        assert chunks[1].text == "67890"

    def test_partial_last_chunk(self) -> None:
        strategy = FixedSizeStrategy(chunk_size=5)
        text = "1234567"
        chunks = strategy.chunk(text)
        assert len(chunks) == 2
        assert chunks[0].text == "12345"
        assert chunks[1].text == "67"

    def test_text_smaller_than_chunk(self) -> None:
        strategy = FixedSizeStrategy(chunk_size=100)
        text = "short"
        chunks = strategy.chunk(text)
        assert len(chunks) == 1

    def test_empty_text(self) -> None:
        strategy = FixedSizeStrategy()
        assert strategy.chunk("") == []

    def test_metadata_propagated(self) -> None:
        strategy = FixedSizeStrategy(chunk_size=5)
        chunks = strategy.chunk("hello world", metadata={"source": "test"})
        for c in chunks:
            assert c.metadata.get("source") == "test"

    def test_validation_positive_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            FixedSizeStrategy(chunk_size=0)


class TestChunkingStrategyProtocol:
    def test_all_strategies_produce_chunks(self) -> None:
        text = "Sentence one. Sentence two and three together. Fourth."
        strategies: list[ChunkingStrategy] = [
            SlidingWindowStrategy(chunk_size=20, overlap=5),
            SentenceBoundaryStrategy(max_chunk_size=30),
            FixedSizeStrategy(chunk_size=15),
        ]
        for s in strategies:
            chunks = s.chunk(text)
            assert len(chunks) >= 1
            assert all(isinstance(c.text, str) for c in chunks)
