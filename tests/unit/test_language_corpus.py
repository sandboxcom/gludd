"""TDD tests for ``src/general_ludd/language/corpus.py``.

CorpusAnalyzer analyzes collections of text files for language patterns:

- :meth:`CorpusAnalyzer.frequency_analysis` — character + word frequency counts
- :meth:`CorpusAnalyzer.extract_ngrams` — n-gram extraction (char or word units)
- :meth:`CorpusAnalyzer.language_distribution` — programming-language labels
  across the corpus via the existing polyglot extension map
- :meth:`CorpusAnalyzer.encoding_statistics` — per-file + aggregate encoding
  stats, reusing the polyglot encoding sniffer

These tests fail until the module exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ── CorpusAnalyzer construction ────────────────────────────────────────────


class TestCorpusAnalyzerConstruction:
    def test_empty_file_list_yields_empty_corpus(self) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        analyzer = CorpusAnalyzer([])
        assert analyzer.files == []

    def test_files_attribute_holds_paths(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f1 = tmp_path / "a.py"
        f1.write_text("x = 1\n", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("y = 2\n", encoding="utf-8")

        analyzer = CorpusAnalyzer([f1, f2])
        assert len(analyzer.files) == 2

    def test_nonexistent_files_filtered_out(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        real = tmp_path / "real.py"
        real.write_text("x\n", encoding="utf-8")
        ghost = tmp_path / "ghost.py"

        analyzer = CorpusAnalyzer([real, ghost])
        existing = [p for p in analyzer.files if p.exists()]
        assert existing == [real]


# ── frequency_analysis ─────────────────────────────────────────────────────


class TestFrequencyAnalysis:
    def test_char_frequency_single_file(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("aaabbc\n", encoding="utf-8")
        report = CorpusAnalyzer([f]).frequency_analysis()

        assert report["char_counts"]["a"] == 3
        assert report["char_counts"]["b"] == 2
        assert report["char_counts"]["c"] == 1

    def test_char_frequency_aggregates_across_files(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.txt").write_text("aaa\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("aa\n", encoding="utf-8")

        report = CorpusAnalyzer([tmp_path / "a.txt", tmp_path / "b.txt"]).frequency_analysis()
        assert report["char_counts"]["a"] == 5

    def test_word_frequency_single_file(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("the quick the lazy\n", encoding="utf-8")
        report = CorpusAnalyzer([f]).frequency_analysis()

        assert report["word_counts"]["the"] == 2
        assert report["word_counts"]["quick"] == 1
        assert report["word_counts"]["lazy"] == 1

    def test_word_frequency_aggregates_across_files(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.txt").write_text("foo bar\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("foo baz\n", encoding="utf-8")

        report = CorpusAnalyzer([tmp_path / "a.txt", tmp_path / "b.txt"]).frequency_analysis()
        assert report["word_counts"]["foo"] == 2
        assert report["word_counts"]["bar"] == 1
        assert report["word_counts"]["baz"] == 1

    def test_total_chars_and_words_counted(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("ab cd\n", encoding="utf-8")  # 6 chars (a,b,space,c,d,\n), 2 words
        report = CorpusAnalyzer([f]).frequency_analysis()

        assert report["total_chars"] == 6
        assert report["total_words"] == 2

    def test_frequency_analysis_empty_corpus(self) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        report = CorpusAnalyzer([]).frequency_analysis()
        assert report["char_counts"] == {}
        assert report["word_counts"] == {}
        assert report["total_chars"] == 0
        assert report["total_words"] == 0

    def test_char_frequency_sorted_descending(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("aaabbc\n", encoding="utf-8")
        report = CorpusAnalyzer([f]).frequency_analysis(top_n=2)

        # top_n returns a (char, count) list sorted by count desc
        assert report["top_chars"][0] == ("a", 3)
        assert report["top_chars"][1] == ("b", 2)
        assert len(report["top_chars"]) == 2

    def test_word_frequency_sorted_descending(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("the the the cat\n", encoding="utf-8")
        report = CorpusAnalyzer([f]).frequency_analysis(top_n=2)

        assert report["top_words"][0] == ("the", 3)
        assert report["top_words"][1] == ("cat", 1)


# ── extract_ngrams ─────────────────────────────────────────────────────────


class TestExtractNgrams:
    def test_char_bigrams(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("abc\n", encoding="utf-8")  # bigrams: ab, bc
        grams = CorpusAnalyzer([f]).extract_ngrams(n=2, unit="char")

        assert grams["ab"] == 1
        assert grams["bc"] == 1

    def test_char_trigrams(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("abcd\n", encoding="utf-8")  # trigrams: abc, bcd
        grams = CorpusAnalyzer([f]).extract_ngrams(n=3, unit="char")

        assert grams["abc"] == 1
        assert grams["bcd"] == 1

    def test_word_bigrams(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("the quick brown\n", encoding="utf-8")
        grams = CorpusAnalyzer([f]).extract_ngrams(n=2, unit="word")

        assert grams["the quick"] == 1
        assert grams["quick brown"] == 1

    def test_ngram_aggregates_across_files(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.txt").write_text("ab\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("ab\n", encoding="utf-8")

        grams = CorpusAnalyzer([tmp_path / "a.txt", tmp_path / "b.txt"]).extract_ngrams(
            n=2, unit="char"
        )
        assert grams["ab"] == 2

    def test_ngram_invalid_unit_raises(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("abc\n", encoding="utf-8")
        with pytest.raises(ValueError):
            CorpusAnalyzer([f]).extract_ngrams(n=2, unit="byte")

    def test_ngram_invalid_n_raises(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.txt"
        f.write_text("abc\n", encoding="utf-8")
        with pytest.raises(ValueError):
            CorpusAnalyzer([f]).extract_ngrams(n=0, unit="char")

    def test_ngram_empty_corpus(self) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        assert CorpusAnalyzer([]).extract_ngrams(n=2, unit="char") == {}

    def test_ngram_word_unit_respects_whitespace(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        # Multiple spaces should collapse to single-token boundaries.
        f = tmp_path / "a.txt"
        f.write_text("foo   bar\n", encoding="utf-8")
        grams = CorpusAnalyzer([f]).extract_ngrams(n=2, unit="word")
        assert grams == {"foo bar": 1}


# ── language_distribution ──────────────────────────────────────────────────


class TestLanguageDistribution:
    def test_single_language(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

        dist = CorpusAnalyzer([tmp_path / "a.py", tmp_path / "b.py"]).language_distribution()

        assert dist["python"] == 2
        assert sum(dist.values()) == 2

    def test_multi_language_corpus(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
        (tmp_path / "b.js").write_text("x\n", encoding="utf-8")
        (tmp_path / "c.go").write_text("x\n", encoding="utf-8")

        dist = CorpusAnalyzer(
            [tmp_path / "a.py", tmp_path / "b.js", tmp_path / "c.go"]
        ).language_distribution()

        assert dist["python"] == 1
        assert dist["javascript"] == 1
        assert dist["go"] == 1

    def test_unknown_extension_labeled_unknown(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.weirdext").write_text("x\n", encoding="utf-8")
        dist = CorpusAnalyzer([tmp_path / "a.weirdext"]).language_distribution()
        assert dist["unknown"] == 1

    def test_distribution_empty_corpus(self) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        assert CorpusAnalyzer([]).language_distribution() == {}


# ── encoding_statistics ────────────────────────────────────────────────────


class TestEncodingStatistics:
    def test_utf8_files_no_bom(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("y\n", encoding="utf-8")

        stats = CorpusAnalyzer([tmp_path / "a.py", tmp_path / "b.py"]).encoding_statistics()

        assert stats["by_encoding"]["UTF-8"] == 2
        assert stats["files_with_bom"] == 0
        assert stats["files_without_bom"] == 2
        assert stats["total_files"] == 2

    def test_utf8_with_bom_detected(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "a.py"
        f.write_bytes(b"\xef\xbb\xbfx\n")
        stats = CorpusAnalyzer([f]).encoding_statistics()

        assert stats["files_with_bom"] == 1
        assert stats["boms_present"] == ["UTF-8"]

    def test_mixed_encodings_reported(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
        (tmp_path / "b.py").write_bytes(b"\xff\xfe" + "y\n".encode("utf-16-le"))

        stats = CorpusAnalyzer([tmp_path / "a.py", tmp_path / "b.py"]).encoding_statistics()
        assert "UTF-8" in stats["by_encoding"]
        assert "UTF-16-LE" in stats["by_encoding"]
        assert stats["is_consistent"] is False

    def test_encoding_stats_empty_corpus(self) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        stats = CorpusAnalyzer([]).encoding_statistics()
        assert stats["total_files"] == 0
        assert stats["by_encoding"] == {}
        assert stats["is_consistent"] is True


# ── robustness ─────────────────────────────────────────────────────────────


class TestCorpusRobustness:
    def test_binary_file_skipped_gracefully(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        f = tmp_path / "bin.dat"
        f.write_bytes(b"\x00\x01\x02\xff\xfe\x00")
        report = CorpusAnalyzer([f]).frequency_analysis()
        # Binary file skipped (no decodable text), report stays empty
        assert report["total_chars"] == 0

    def test_nonexistent_file_in_corpus_skipped(self, tmp_path: Path) -> None:
        from src.general_ludd.language.corpus import CorpusAnalyzer

        real = tmp_path / "real.txt"
        real.write_text("abc\n", encoding="utf-8")
        ghost = tmp_path / "ghost.txt"

        report = CorpusAnalyzer([real, ghost]).frequency_analysis()
        assert report["char_counts"].get("a") == 1
