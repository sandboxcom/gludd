"""Tests for language/i18n_data.py — pseudolocalization, .po parsing, ICU."""

from __future__ import annotations

from general_ludd.language.i18n_data import (
    extract_icu_placeholders,
    parse_po,
    pseudolocalize,
    serialize_po,
)


class TestPseudolocalize:
    def test_accent_method_transforms_ascii_letters(self) -> None:
        out = pseudolocalize("hello", method="accent")

        assert out != "hello"
        assert len(out) >= len("hello")

    def test_empty_string_returns_falsy_output(self) -> None:
        assert pseudolocalize("", method="accent") == ""

    def test_preserves_placeholders(self) -> None:
        out = pseudolocalize("Hello {name}!", method="accent")

        assert "{name}" in out


class TestParsePo:
    def test_parses_simple_entry(self) -> None:
        content = 'msgid "Hello"\nmsgstr "Bonjour"\n'

        entries = parse_po(content)

        assert len(entries) == 1
        assert entries[0]["msgid"] == "Hello"
        assert entries[0]["msgstr"] == "Bonjour"

    def test_parses_multiple_entries(self) -> None:
        content = (
            'msgid "One"\nmsgstr "Un"\n\n'
            'msgid "Two"\nmsgstr "Deux"\n'
        )

        entries = parse_po(content)

        assert len(entries) == 2
        assert {e["msgid"] for e in entries} == {"One", "Two"}

    def test_empty_content_yields_no_entries(self) -> None:
        assert parse_po("") == []

    def test_roundtrip_through_serialize_po(self) -> None:
        content = 'msgid "Hello"\nmsgstr "Bonjour"\n'

        entries = parse_po(content)
        serialized = serialize_po(entries)
        reparsed = parse_po(serialized)

        assert reparsed == entries


class TestExtractIcuPlaceholders:
    def test_extracts_simple_placeholder(self) -> None:
        assert extract_icu_placeholders("Hello {name}!") == ["name"]

    def test_extracts_multiple_placeholders(self) -> None:
        result = extract_icu_placeholders("{greeting}, {name}!")

        assert "greeting" in result
        assert "name" in result

    def test_no_placeholders_returns_empty(self) -> None:
        assert extract_icu_placeholders("plain text") == []
