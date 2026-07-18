"""Corpus analysis: aggregate language-pattern statistics over text files.

Extends the Language Expert (NF.9) with collection-level analysis. While
:mod:`general_ludd.language.polyglot` answers "which programming languages
are in this tree?", this module answers "what are the textual patterns in
these files?" — character/word frequencies, n-gram distributions, and the
encoding shape of the corpus.

The analyzer is intentionally dependency-light: it reads each file once per
analysis pass, decodes as UTF-8 (skipping files that fail — those are
encoding-mismatch problems, surfaced separately by
:func:`polyglot.encoding_conflict_report`), and reuses the existing
:mod:`polyglot` helpers for language labeling and BOM sniffing rather than
re-implementing them.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from general_ludd.language.polyglot import (
    FileEncoding,
    _detect_encoding,
    _language_for_extension,
)

# Cap per-file read size so a stray huge file doesn't blow memory. 4 MiB
# covers essentially any source file we'd reasonably analyze; larger files
# are sampled (their prefix) rather than ingested wholesale.
_READ_LIMIT = 4 * 1024 * 1024

# Word tokenization: runs of "word-ish" characters. Keeps underscores and
# digits (so ``foo_bar`` and ``v8`` are single tokens), drops punctuation.
# Anchored on Unicode word chars plus ``_`` to support non-ASCII identifiers.
_WORD_RE = re.compile(r"[\w]+", re.UNICODE)

_VALID_UNITS: frozenset[str] = frozenset({"char", "word"})





class CorpusAnalyzer:
    """Analyze a collection of text files for language patterns.

    Construct once with the list of files; call the analysis methods to
    produce independent reports. Non-existent files are filtered at
    construction time so every analysis pass iterates a stable set.

    Parameters
    ----------
    files:
        Iterable of paths (``str`` or :class:`~pathlib.Path`) to analyze.
        Non-existent files are dropped; no error is raised for them.
    """

    def __init__(self, files: list[str | Path] | tuple[str | Path, ...]) -> None:
        # Filter to existing files only. We keep the original order so
        # downstream reports are deterministic w.r.t. caller intent, then
        # drop anything that isn't a regular file.
        seen: list[Path] = []
        for raw in files:
            path = Path(raw)
            if path.is_file():
                seen.append(path)
        self.files: list[Path] = seen

    # ── internal helpers ───────────────────────────────────────────────────

    def _read_text(self, path: Path) -> str | None:
        """Read up to :data:`_READ_LIMIT` bytes of ``path`` as UTF-8.

        Returns ``None`` for files that cannot be decoded (binary files,
        non-UTF-8 encodings without a BOM, OS read errors). Callers treat
        ``None`` as "skip this file for text analysis" — encoding problems
        are surfaced separately by :meth:`encoding_statistics`.
        """
        try:
            with path.open("r", encoding="utf-8", errors="strict") as fh:
                return fh.read(_READ_LIMIT)
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _top_n(counter: Counter[str], top_n: int | None) -> list[tuple[str, int]]:
        """Return ``counter.most_common(top_n)``, deterministic on ties.

        ``Counter.most_common`` already breaks ties by insertion order,
        which is stable within a single pass; we don't need extra sorting.
        """
        if top_n is None:
            return counter.most_common()
        return counter.most_common(top_n)

    # ── public API ─────────────────────────────────────────────────────────

    def frequency_analysis(self, top_n: int | None = None) -> dict[str, object]:
        """Count character and word frequencies across the corpus.

        Aggregates counts across all readable files. Character counts are
        raw (including whitespace); word counts use ``\\w+`` tokenization
        (Unicode-aware, keeps underscores/digits so identifiers stay whole).

        Parameters
        ----------
        top_n:
            If given, ``top_chars`` / ``top_words`` contain only the N most
            common items. ``char_counts`` / ``word_counts`` always carry
            the full counts.

        Returns
        -------
        FrequencyReport
            ``total_chars`` / ``total_words`` are the raw totals across
            the readable corpus (whitespace counts toward chars but not
            words).
        """
        char_counts: Counter[str] = Counter()
        word_counts: Counter[str] = Counter()
        total_chars = 0
        total_words = 0

        for path in self.files:
            text = self._read_text(path)
            if text is None:
                continue
            char_counts.update(text)
            words = _WORD_RE.findall(text)
            word_counts.update(words)
            total_chars += len(text)
            total_words += len(words)

        return {
            "char_counts": dict(char_counts),
            "word_counts": dict(word_counts),
            "total_chars": total_chars,
            "total_words": total_words,
            "top_chars": self._top_n(char_counts, top_n),
            "top_words": self._top_n(word_counts, top_n),
        }

    def extract_ngrams(self, n: int, unit: str) -> dict[str, int]:
        """Extract n-gram frequencies across the corpus.

        Parameters
        ----------
        n:
            Size of each gram. Must be ≥ 1.
        unit:
            ``"char"`` for character n-grams (consecutive characters,
            including whitespace, within each file), or ``"word"`` for
            word n-grams (consecutive ``\\w+`` tokens joined by a single
            space).

        Raises
        ------
        ValueError
            If ``unit`` is not ``"char"`` or ``"word"``, or ``n`` is < 1.
        """
        if unit not in _VALID_UNITS:
            raise ValueError(
                f"unit must be one of {sorted(_VALID_UNITS)}, got {unit!r}"
            )
        if n < 1:
            raise ValueError(f"n must be ≥ 1, got {n}")

        grams: Counter[str] = Counter()

        for path in self.files:
            text = self._read_text(path)
            if text is None:
                continue
            if unit == "char":
                # Slide a window of width n over the raw text.
                for i in range(len(text) - n + 1):
                    grams[text[i : i + n]] += 1
            else:
                # Word-unit: tokenize, then slide over the token list.
                tokens = _WORD_RE.findall(text)
                for i in range(len(tokens) - n + 1):
                    grams[" ".join(tokens[i : i + n])] += 1

        return dict(grams)

    def language_distribution(self) -> dict[str, int]:
        """Return file counts per programming language label.

        Uses :func:`polyglot._language_for_extension` so corpus analysis
        is consistent with the directory-level polyglot detection. Files
        whose extension is unknown are counted under ``"unknown"``.
        """
        counts: Counter[str] = Counter()
        for path in self.files:
            label = _language_for_extension(path.suffix) or "unknown"
            counts[label] += 1
        return dict(counts)

    def encoding_statistics(self) -> dict[str, object]:
        """Aggregate BOM/encoding stats across the corpus.

        Reuses :func:`polyglot._detect_encoding` for the per-file sniff
        (BOM prefix lookup with UTF-8 fallback), then aggregates.
        ``is_consistent`` is ``True`` only when all files share a single
        encoding.
        """
        rows: list[FileEncoding] = [_detect_encoding(p) for p in self.files]

        by_encoding: Counter[str] = Counter()
        boms_present: list[str] = []
        files_with_bom = 0

        for row in rows:
            by_encoding[row["encoding"]] += 1
            if row["has_bom"]:
                files_with_bom += 1
                assert row["bom"] is not None  # for the type checker
                if row["bom"] not in boms_present:
                    boms_present.append(row["bom"])

        files_without_bom = len(rows) - files_with_bom
        is_consistent = len(by_encoding) <= 1

        return {
            "total_files": len(rows),
            "by_encoding": dict(by_encoding),
            "boms_present": boms_present,
            "files_with_bom": files_with_bom,
            "files_without_bom": files_without_bom,
            "is_consistent": is_consistent,
            "files": rows,
        }


__all__ = [
    "CorpusAnalyzer",
]
