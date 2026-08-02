"""
Document loaders for the general_ludd.agent collection.

Provides loaders for text, HTML, and PDF files, plus a directory walker
and a convenience auto-detection function.

Usage::

    from ansible_collections.general_ludd.agent.plugins.module_utils.document_loader import (
        Document,
        TextLoader,
        HTMLLoader,
        PDFLoader,
        DirectoryLoader,
        load_document,
    )

    docs = load_document("path/to/file.md")
    for doc in docs:
        print(doc.text)

    dir_docs = DirectoryLoader().load("path/to/docs/")
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


@dataclass
class Document:
    """A loaded document with text content and metadata.

    Attributes
    ----------
    text:
        The document's plain-text content.
    metadata:
        Arbitrary key-value metadata (format, author, page number, etc.).
    source:
        The file path or URL the document was loaded from.
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""


class TextLoader:
    """Load plain-text documents: ``.txt``, ``.md``, ``.rst``, ``.csv``.

    CSV files are read row-by-row and returned as a single
    ``text/row1\\ntext/row2\\n...`` so downstream chunkers can process
    individual rows.
    """

    def load(self, path: str, metadata: dict[str, Any] | None = None) -> list[Document]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")

        meta = dict(metadata or {})
        ext = pathlib.Path(path).suffix.lower()

        with open(path, encoding="utf-8") as fh:
            content = fh.read()

        meta.setdefault("format", ext.lstrip("."))
        return [Document(text=content, metadata=meta, source=path)]


class _HTMLTextExtractor(HTMLParser):
    """Extract visible text from HTML, stripping tags and skipping
    ``<script>`` and ``<style>`` content."""

    def __init__(self) -> None:
        self._output: list[str] = []
        self._skip = False
        self._skip_tags: frozenset[str] = frozenset({"script", "style"})
        super().__init__()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._output.append(data)

    def reset(self) -> None:
        super().reset()
        self._output.clear()
        self._skip = False

    def get_text(self) -> str:
        return "".join(self._output)


class HTMLLoader:
    """Load HTML documents, stripping all tags and extracting visible text.

    Uses stdlib :class:`html.parser.HTMLParser`.  ``<script>`` and
    ``<style>`` content is discarded.
    """

    def load(self, path: str, metadata: dict[str, Any] | None = None) -> list[Document]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")

        with open(path, encoding="utf-8") as fh:
            raw = fh.read()

        parser = _HTMLTextExtractor()
        parser.feed(raw)
        parser.close()
        text = parser.get_text()

        meta = dict(metadata or {})
        meta.setdefault("format", pathlib.Path(path).suffix.lower().lstrip("."))
        return [Document(text=text, metadata=meta, source=path)]


class PDFLoader:
    """Load PDF documents using ``pypdf`` (optional dependency).

    If ``pypdf`` is not installed, construction raises
    :class:`ImportError` with clear install instructions.
    """

    def __init__(self) -> None:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ImportError("pypdf is required to load PDF documents. Install it with: pip install pypdf") from None
        self._PdfReader = PdfReader

    def load(self, path: str, metadata: dict[str, Any] | None = None) -> list[Document]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")

        reader = self._PdfReader(path)
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)

        full_text = "\n\n".join(pages)
        meta = dict(metadata or {})
        meta.setdefault("format", "pdf")
        meta.setdefault("page_count", len(reader.pages))
        return [Document(text=full_text, metadata=meta, source=path)]


_FILE_EXTENSION_MAP: dict[str, type] = {}


def _build_extension_map() -> dict[str, type]:
    """Return a mapping of file extensions → loader class."""
    return {
        ".txt": TextLoader,
        ".md": TextLoader,
        ".rst": TextLoader,
        ".csv": TextLoader,
        ".html": HTMLLoader,
        ".htm": HTMLLoader,
    }


_SUPPORTED_EXTENSIONS: set[str] = {
    ".txt",
    ".md",
    ".rst",
    ".csv",
    ".html",
    ".htm",
    ".pdf",
}

_DEFAULT_SUFFIXES: set[str] = {
    ".txt",
    ".md",
    ".rst",
    ".csv",
    ".html",
    ".htm",
    ".pdf",
}
_DEFAULT_GLOB = "*.{txt,md,rst,csv,html,htm,pdf}"


class DirectoryLoader:
    """Recursively load all supported documents from a directory.

    Parameters
    ----------
    glob:
        Filesystem glob pattern (default matches all supported extensions).
    """

    @staticmethod
    def _collect_files(root: str, glob_pattern: str) -> list[str]:
        result: list[str] = []
        root_path = pathlib.Path(root)
        suffixes: set[str] = _DEFAULT_SUFFIXES
        if glob_pattern != _DEFAULT_GLOB:
            suffixes = {pathlib.PurePosixPath(g).suffix.lower() for g in glob_pattern.split(",")}
        for file_path in root_path.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in suffixes:
                result.append(str(file_path))
        return sorted(result)

    def __init__(self, glob: str | None = None) -> None:
        self.glob: str = glob if glob is not None else _DEFAULT_GLOB

    def load(self, path: str, metadata: dict[str, Any] | None = None) -> list[Document]:
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Directory not found: {path}")

        files = self._collect_files(path, self.glob)
        docs: list[Document] = []

        for file_path in files:
            try:
                doc = load_document(file_path, metadata=metadata)
                docs.extend(doc)
            except (OSError, ImportError):
                continue

        return docs


def _get_format(path: str) -> str:
    """Return the file-format key for *path* or empty string if unknown."""
    ext = pathlib.Path(path).suffix.lower()
    if not ext:
        return ""
    if ext in _SUPPORTED_EXTENSIONS:
        return ext.lstrip(".")
    return ""


def load_document(path: str, metadata: dict[str, Any] | None = None) -> list[Document]:
    """Auto-detect file format and load the document.

    Parameters
    ----------
    path:
        Path to a file or directory.
    metadata:
        Optional metadata merged into each returned :class:`Document`.

    Returns
    -------
    list[Document]
        One or more documents loaded from *path*.

    Raises
    ------
    ValueError
        If the file extension is unsupported or missing.
    FileNotFoundError
        If *path* does not exist.
    ImportError
        If loading a PDF without ``pypdf`` installed.
    """
    if os.path.isdir(path):
        return DirectoryLoader().load(path, metadata=metadata)

    if not os.path.isfile(path):
        raise FileNotFoundError(f"Path not found: {path}")

    ext = pathlib.Path(path).suffix.lower()
    if not ext:
        raise ValueError(f"Cannot determine file format (no extension): {path}")

    fmt = _get_format(path)
    if not fmt:
        raise ValueError(f"Unsupported file format '{ext}' for {path}. Supported: {sorted(_SUPPORTED_EXTENSIONS)}")

    if ext == ".pdf":
        loader = PDFLoader()
    elif ext in {".html", ".htm"}:
        loader = HTMLLoader()
    else:
        loader = TextLoader()

    return loader.load(path, metadata=metadata)
