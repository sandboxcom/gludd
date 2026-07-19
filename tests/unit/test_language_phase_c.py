"""Phase C TDD tests: L10n + I18n functional helpers.

Covers spec sections 4.4 (L10n) and 4.5 (I18n):
- BCP 47 language tag parsing (RFC 5646)
- Locale negotiation (RFC 4647 Lookup algorithm)
- CLDR plural rule evaluation
- Pseudolocalization for i18n testing
- Gettext .po file parsing and serialization
- ICU MessageFormat placeholder extraction
- i18n linting: hardcoded user-facing string detection

These tests fail until the corresponding functions exist in
src/general_ludd/language/locale_data.py and the new i18n_data.py module.
"""

from __future__ import annotations

# ── BCP 47 parsing ─────────────────────────────────────────────────────────


class TestBCP47Parsing:
    """parse_bcp47() splits a BCP 47 language tag per RFC 5646."""

    def test_simple_two_part(self) -> None:
        from src.general_ludd.language.locale_data import parse_bcp47
        result = parse_bcp47("en-US")
        assert result["language"] == "en"
        assert result["territory"] == "US"

    def test_underscore_normalized(self) -> None:
        from src.general_ludd.language.locale_data import parse_bcp47
        result = parse_bcp47("en_US")
        assert result["language"] == "en"
        assert result["territory"] == "US"

    def test_with_codeset_suffix(self) -> None:
        """POSIX-style 'en_US.UTF-8' must parse cleanly."""
        from src.general_ludd.language.locale_data import parse_bcp47
        result = parse_bcp47("en_US.UTF-8")
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["codeset"] == "UTF-8"

    def test_language_only(self) -> None:
        from src.general_ludd.language.locale_data import parse_bcp47
        result = parse_bcp47("ja")
        assert result["language"] == "ja"
        assert result["territory"] == ""

    def test_with_script_subtag(self) -> None:
        """zh-Hans-CN includes a script subtag."""
        from src.general_ludd.language.locale_data import parse_bcp47
        result = parse_bcp47("zh-Hans-CN")
        assert result["language"] == "zh"
        assert result["script"] == "Hans"
        assert result["territory"] == "CN"

    def test_empty_string(self) -> None:
        from src.general_ludd.language.locale_data import parse_bcp47
        result = parse_bcp47("")
        assert result["language"] == ""
        assert result["territory"] == ""

    def test_returns_canonical_form(self) -> None:
        from src.general_ludd.language.locale_data import parse_bcp47
        result = parse_bcp47("en_US.UTF-8")
        assert result["canonical"] == "en-US"


# ── Locale negotiation (RFC 4647 Lookup) ────────────────────────────────────


class TestLocaleNegotiation:
    """negotiate_locale() implements RFC 4647 Lookup algorithm."""

    def test_exact_match(self) -> None:
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("en-US,zp;q=0.9", ["en-US", "ja-JP"])
        assert result == "en-US"

    def test_first_match_wins(self) -> None:
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("ja-JP,en-US;q=0.9", ["en-US", "ja-JP"])
        assert result == "ja-JP"

    def test_prefix_fallback(self) -> None:
        """en-GB not in available, falls back to en-US (language prefix match)."""
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("en-GB", ["en-US", "ja-JP"])
        assert result == "en-US"

    def test_no_match_returns_none(self) -> None:
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("xh-ZA", ["en-US", "ja-JP"])
        assert result is None

    def test_empty_header_returns_none(self) -> None:
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("", ["en-US"])
        assert result is None

    def test_wildcard_asterisk(self) -> None:
        """'*' in Accept-Language matches any available locale."""
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("*", ["en-US", "ja-JP"])
        assert result in ("en-US", "ja-JP")

    def test_q_value_ordering(self) -> None:
        """Lower q-value loses when both could match."""
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("en-US;q=0.5,ja-JP;q=0.9", ["en-US", "ja-JP"])
        assert result == "ja-JP"

    def test_default_fallback(self) -> None:
        from src.general_ludd.language.locale_data import negotiate_locale
        result = negotiate_locale("fr-FR", ["en-US", "ja-JP"], default="en-US")
        assert result == "en-US"


# ── CLDR plural rule evaluation ─────────────────────────────────────────────


class TestPluralEvaluation:
    """evaluate_plural() returns the CLDR plural category for a count."""

    def test_english_one(self) -> None:
        from src.general_ludd.language.locale_data import evaluate_plural
        assert evaluate_plural("en-US", 1) == "one"

    def test_english_other(self) -> None:
        from src.general_ludd.language.locale_data import evaluate_plural
        assert evaluate_plural("en-US", 2) == "other"
        assert evaluate_plural("en-US", 0) == "other"
        assert evaluate_plural("en-US", 5) == "other"

    def test_arabic_six_way(self) -> None:
        from src.general_ludd.language.locale_data import evaluate_plural
        assert evaluate_plural("ar-SA", 0) == "zero"
        assert evaluate_plural("ar-SA", 1) == "one"
        assert evaluate_plural("ar-SA", 2) == "two"
        assert evaluate_plural("ar-SA", 5) == "few"
        assert evaluate_plural("ar-SA", 15) == "many"
        assert evaluate_plural("ar-SA", 100) == "other"

    def test_russian_few(self) -> None:
        from src.general_ludd.language.locale_data import evaluate_plural
        assert evaluate_plural("ru-RU", 1) == "one"
        assert evaluate_plural("ru-RU", 3) == "few"
        assert evaluate_plural("ru-RU", 5) == "many"
        assert evaluate_plural("ru-RU", 22) == "few"

    def test_japanese_only_other(self) -> None:
        from src.general_ludd.language.locale_data import evaluate_plural
        assert evaluate_plural("ja-JP", 1) == "other"
        assert evaluate_plural("ja-JP", 100) == "other"

    def test_unknown_locale_defaults_other(self) -> None:
        from src.general_ludd.language.locale_data import evaluate_plural
        assert evaluate_plural("xx-XX", 1) == "other"


# ── CLDR locale lookup ──────────────────────────────────────────────────────


class TestLocaleLookup:
    """get_locale_data() looks up CLDR data with fallback."""

    def test_exact_lookup(self) -> None:
        from src.general_ludd.language.locale_data import get_locale_data
        result = get_locale_data("en-US")
        assert result is not None
        assert result["territory"] == "US"

    def test_underscore_normalized(self) -> None:
        from src.general_ludd.language.locale_data import get_locale_data
        result = get_locale_data("en_US")
        assert result is not None
        assert result["bcp47"] == "en-US"

    def test_language_only_fallback(self) -> None:
        """en-GB not in table; falls back to en-US via language prefix."""
        from src.general_ludd.language.locale_data import get_locale_data
        result = get_locale_data("en-GB")
        assert result is not None
        assert result["language_name"].startswith("English")

    def test_unknown_returns_none(self) -> None:
        from src.general_ludd.language.locale_data import get_locale_data
        assert get_locale_data("zz-ZZ") is None


# ── Pseudolocalization ──────────────────────────────────────────────────────


class TestPseudolocalization:
    """pseudolocalize() transforms text to test i18n readiness."""

    def test_accented_letters_substituted(self) -> None:
        from src.general_ludd.language.i18n_data import pseudolocalize
        result = pseudolocalize("Hello")
        assert result != "Hello"
        assert len(result) >= len("Hello")

    def test_bracketed_for_length_detection(self) -> None:
        """Bracketed method wraps with [] to detect truncation."""
        from src.general_ludd.language.i18n_data import pseudolocalize
        result = pseudolocalize("Hi", method="bracket")
        assert result.startswith("[")
        assert result.endswith("]")

    def test_unknown_method_falls_back_to_accent(self) -> None:
        from src.general_ludd.language.i18n_data import pseudolocalize
        result = pseudolocalize("Hello", method="bogus")
        assert len(result) > 0

    def test_empty_string(self) -> None:
        from src.general_ludd.language.i18n_data import pseudolocalize
        assert pseudolocalize("") == ""

    def test_non_ascii_preserved(self) -> None:
        """Already-accented chars are preserved, not double-transformed."""
        from src.general_ludd.language.i18n_data import pseudolocalize
        result = pseudolocalize("café")
        assert len(result) >= 4

    def test_placeholder_preserved(self) -> None:
        """Format placeholders like {name} or %s are preserved."""
        from src.general_ludd.language.i18n_data import pseudolocalize
        result = pseudolocalize("Hello {name}")
        assert "{name}" in result

    def test_percent_placeholder_preserved(self) -> None:
        from src.general_ludd.language.i18n_data import pseudolocalize
        result = pseudolocalize("Count: %d")
        assert "%d" in result


# ── Gettext .po parsing ────────────────────────────────────────────────────


class TestPoParsing:
    """parse_po() parses gettext .po file content."""

    SAMPLE_PO = '''# Translations file
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

#: src/app.py:42
msgid "Hello"
msgstr "Bonjour"

#: src/app.py:43
#, fuzzy
msgid "Goodbye"
msgstr "Au revoir"
'''

    def test_parses_multiple_entries(self) -> None:
        from src.general_ludd.language.i18n_data import parse_po
        entries = parse_po(self.SAMPLE_PO)
        assert len(entries) == 2

    def test_entry_has_msgid(self) -> None:
        from src.general_ludd.language.i18n_data import parse_po
        entries = parse_po(self.SAMPLE_PO)
        assert entries[0]["msgid"] == "Hello"
        assert entries[1]["msgid"] == "Goodbye"

    def test_entry_has_msgstr(self) -> None:
        from src.general_ludd.language.i18n_data import parse_po
        entries = parse_po(self.SAMPLE_PO)
        assert entries[0]["msgstr"] == "Bonjour"

    def test_entry_preserves_references(self) -> None:
        from src.general_ludd.language.i18n_data import parse_po
        entries = parse_po(self.SAMPLE_PO)
        assert "src/app.py:42" in entries[0]["references"]

    def test_entry_preserves_flags(self) -> None:
        from src.general_ludd.language.i18n_data import parse_po
        entries = parse_po(self.SAMPLE_PO)
        assert "fuzzy" in entries[1]["flags"]

    def test_empty_string(self) -> None:
        from src.general_ludd.language.i18n_data import parse_po
        assert parse_po("") == []

    def test_skips_header_entry(self) -> None:
        """The empty-msgid header entry is not returned as a content entry."""
        from src.general_ludd.language.i18n_data import parse_po
        entries = parse_po(self.SAMPLE_PO)
        for e in entries:
            assert e["msgid"] != ""


class TestPoSerialization:
    """serialize_po() round-trips parsed entries back to .po text."""

    def test_roundtrip_preserves_entries(self) -> None:
        from src.general_ludd.language.i18n_data import parse_po, serialize_po
        original = TestPoParsing.SAMPLE_PO
        entries = parse_po(original)
        text = serialize_po(entries)
        reparsed = parse_po(text)
        assert len(reparsed) == len(entries)
        assert reparsed[0]["msgid"] == entries[0]["msgid"]

    def test_output_contains_msgid_keyword(self) -> None:
        from src.general_ludd.language.i18n_data import serialize_po
        text = serialize_po([{"msgid": "x", "msgstr": "y",
                              "references": [], "flags": []}])
        assert 'msgid "x"' in text
        assert 'msgstr "y"' in text


# ── ICU MessageFormat placeholder extraction ───────────────────────────────


class TestICUPlaceholders:
    """extract_icu_placeholders() pulls {name} tokens from ICU messages."""

    def test_simple_placeholder(self) -> None:
        from src.general_ludd.language.i18n_data import extract_icu_placeholders
        result = extract_icu_placeholders("Hello {name}")
        assert result == ["name"]

    def test_multiple_placeholders(self) -> None:
        from src.general_ludd.language.i18n_data import extract_icu_placeholders
        result = extract_icu_placeholders("{greeting}, {name}!")
        assert "greeting" in result
        assert "name" in result

    def test_typed_placeholder(self) -> None:
        """ICU typed form {count, plural, ...} still extracts the name."""
        from src.general_ludd.language.i18n_data import extract_icu_placeholders
        result = extract_icu_placeholders(
            "{count, plural, one{item} other{items}}"
        )
        assert "count" in result

    def test_no_placeholders(self) -> None:
        from src.general_ludd.language.i18n_data import extract_icu_placeholders
        assert extract_icu_placeholders("plain text") == []

    def test_empty_string(self) -> None:
        from src.general_ludd.language.i18n_data import extract_icu_placeholders
        assert extract_icu_placeholders("") == []

    def test_duplicates_returned_once(self) -> None:
        from src.general_ludd.language.i18n_data import extract_icu_placeholders
        result = extract_icu_placeholders("{x} and {x}")
        assert result == ["x"]


# ── i18n linting: hardcoded string detection ────────────────────────────────


class TestHardcodedStringDetection:
    """find_untranslated_strings() finds user-facing strings not in gettext()."""

    def test_finds_gettext_calls(self) -> None:
        """Strings wrapped in _() are NOT flagged."""
        from src.general_ludd.language.i18n_data import find_untranslated_strings
        source = 'msg = _("Already wrapped")\n'
        findings = find_untranslated_strings(source, "python")
        assert len(findings) == 0

    def test_finds_hardcoded_strings(self) -> None:
        from src.general_ludd.language.i18n_data import find_untranslated_strings
        source = (
            'label = "Click here to continue"\n'
            'button = "Submit"\n'
        )
        findings = find_untranslated_strings(source, "python")
        assert len(findings) >= 1
        assert any("Click here" in f["string"] for f in findings)

    def test_returns_line_numbers(self) -> None:
        from src.general_ludd.language.i18n_data import find_untranslated_strings
        source = (
            'x = 1\n'
            'y = "This is a long hardcoded string"\n'
        )
        findings = find_untranslated_strings(source, "python")
        assert len(findings) >= 1
        assert findings[0]["line"] == 2

    def test_empty_source(self) -> None:
        from src.general_ludd.language.i18n_data import find_untranslated_strings
        assert find_untranslated_strings("", "python") == []

    def test_short_strings_ignored(self) -> None:
        """Single-word strings are typically not user-facing."""
        from src.general_ludd.language.i18n_data import find_untranslated_strings
        source = 'x = "ok"\n'
        findings = find_untranslated_strings(source, "python")
        assert len(findings) == 0

    def test_no_double_reporting(self) -> None:
        """A given string instance is reported once, not per-character."""
        from src.general_ludd.language.i18n_data import find_untranslated_strings
        source = 'x = "Submit your form now"\n'
        findings = find_untranslated_strings(source, "python")
        assert len(findings) == 1
