"""Tests for language/i18n_data.py — pseudolocalization, .po parsing, ICU."""

from __future__ import annotations

from general_ludd.language.i18n_data import (
    extract_icu_placeholders,
    find_untranslated_strings,
    parse_po,
    pseudolocalize,
    serialize_po,
)


class TestPseudolocalize:
    def test_accent_method_transforms_ascii_letters(self) -> None:
        out = pseudolocalize("hello", method="accent")
        assert out != "hello"
        assert len(out) >= len("hello")

    def test_empty_string_returns_empty(self) -> None:
        assert pseudolocalize("", method="accent") == ""

    def test_preserves_placeholders(self) -> None:
        out = pseudolocalize("Hello {name}!", method="accent")
        assert "{name}" in out

    def test_preserves_percent_format(self) -> None:
        out = pseudolocalize("Count: %d items", method="accent")
        assert "%d" in out

    def test_preserves_named_percent_format(self) -> None:
        out = pseudolocalize("%(count)d items", method="accent")
        assert "%(count)d" in out

    def test_preserves_escaped_braces(self) -> None:
        out = pseudolocalize(r"Escaped \{braces}", method="accent")
        assert r"\{braces}" in out

    def test_bracket_method_wraps_text(self) -> None:
        out = pseudolocalize("Hello", method="bracket")
        assert out == "[Hello]"

    def test_bracket_empty_string(self) -> None:
        assert pseudolocalize("", method="bracket") == ""

    def test_bracket_with_placeholders(self) -> None:
        out = pseudolocalize("Hello {name}!", method="bracket")
        assert "{name}" in out
        assert out.startswith("[")
        assert out.endswith("]")

    def test_unknown_method_falls_back_to_accent(self) -> None:
        out = pseudolocalize("Hello", method="unknown")
        assert out != "Hello"
        assert len(out) >= len("Hello")

    def test_all_lowercase_ascii_mapped(self) -> None:
        for ch in "abcdefghijklmnopqrstuvwxyz":
            out = pseudolocalize(ch, method="accent")
            assert out != ch, f"{ch} was not transformed"

    def test_all_uppercase_ascii_mapped(self) -> None:
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            out = pseudolocalize(ch, method="accent")
            assert out != ch, f"{ch} was not transformed"

    def test_non_alpha_passthrough(self) -> None:
        out = pseudolocalize("123!@#", method="accent")
        assert out == "123!@#"

    def test_mixed_placeholder_position(self) -> None:
        out = pseudolocalize("%s at start, {mid} in middle, end", method="accent")
        assert out.startswith("%s")
        assert "{mid}" in out
        assert "end" not in out  # "end" should be accented


class TestParsePo:
    def test_parses_simple_entry(self) -> None:
        content = 'msgid "Hello"\nmsgstr "Bonjour"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert entries[0]["msgid"] == "Hello"
        assert entries[0]["msgstr"] == "Bonjour"

    def test_parses_multiple_entries(self) -> None:
        content = 'msgid "One"\nmsgstr "Un"\n\nmsgid "Two"\nmsgstr "Deux"\n'
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

    def test_skips_header_entry_with_empty_msgid(self) -> None:
        content = 'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr "Bonjour"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert entries[0]["msgid"] == "Hello"

    def test_parses_continuation_lines_in_msgid(self) -> None:
        content = 'msgid "Hello"\n"World"\nmsgstr "Bonjour"\n"Monde"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert entries[0]["msgid"] == "HelloWorld"
        assert entries[0]["msgstr"] == "BonjourMonde"

    def test_parses_references(self) -> None:
        content = '#: src/main.py:42 src/utils.py:7\nmsgid "Hello"\nmsgstr "Bonjour"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert "src/main.py:42" in entries[0]["references"]
        assert "src/utils.py:7" in entries[0]["references"]

    def test_parses_flags(self) -> None:
        content = '#, fuzzy, python-format\nmsgid "Hello %s"\nmsgstr "Bonjour %s"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert "fuzzy" in entries[0]["flags"]
        assert "python-format" in entries[0]["flags"]

    def test_parses_entry_with_both_references_and_flags(self) -> None:
        content = '#: src/main.py:42\n#, fuzzy\nmsgid "Welcome"\nmsgstr "Bienvenue"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert entries[0]["references"] == ["src/main.py:42"]
        assert entries[0]["flags"] == ["fuzzy"]

    def test_references_before_first_msgid_ignored(self) -> None:
        content = '#: orphan_ref.py:1\nmsgid "Hello"\nmsgstr "Bonjour"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert entries[0]["references"] == ["orphan_ref.py:1"]

    def test_flags_before_first_msgid_ignored(self) -> None:
        content = '#, orphan-flag\nmsgid "Hello"\nmsgstr "Bonjour"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert entries[0]["flags"] == ["orphan-flag"]

    def test_comments_not_parsed_as_metadata(self) -> None:
        content = '# This is a comment\nmsgid "Hello"\nmsgstr "Bonjour"\n'
        entries = parse_po(content)
        assert len(entries) == 1
        assert entries[0]["references"] == []
        assert entries[0]["flags"] == []

    def test_escaped_quotes_in_msgid(self) -> None:
        content = 'msgid "He said \\"hello\\""\nmsgstr "Il a dit \\"bonjour\\""\n'
        entries = parse_po(content)
        assert entries[0]["msgid"] == 'He said "hello"'
        assert entries[0]["msgstr"] == 'Il a dit "bonjour"'

    def test_escaped_newlines(self) -> None:
        content = 'msgid "Line1\\nLine2"\nmsgstr "Ligne1\\nLigne2"\n'
        entries = parse_po(content)
        assert entries[0]["msgid"] == "Line1\nLine2"
        assert entries[0]["msgstr"] == "Ligne1\nLigne2"

    def test_escaped_tabs(self) -> None:
        content = 'msgid "Col1\\tCol2"\nmsgstr "Col1\\tCol2"\n'
        entries = parse_po(content)
        assert entries[0]["msgid"] == "Col1\tCol2"

    def test_escaped_backslash(self) -> None:
        content = 'msgid "C:\\\\path\\\\to\\\\file"\nmsgstr "C:\\\\chemin\\\\vers\\\\fichier"\n'
        entries = parse_po(content)
        assert "C:" in entries[0]["msgid"]
        assert "path" in entries[0]["msgid"]
        assert "file" in entries[0]["msgid"]


class TestSerializePo:
    def test_serialize_single_entry(self) -> None:
        entries = [{"msgid": "Hello", "msgstr": "Bonjour", "references": [], "flags": []}]
        out = serialize_po(entries)
        assert 'msgid "Hello"' in out
        assert 'msgstr "Bonjour"' in out

    def test_serialize_with_references(self) -> None:
        entries = [
            {
                "msgid": "Hello",
                "msgstr": "Bonjour",
                "references": ["src/main.py:42"],
                "flags": [],
            }
        ]
        out = serialize_po(entries)
        assert "#: src/main.py:42" in out

    def test_serialize_with_flags(self) -> None:
        entries = [
            {
                "msgid": "Hello",
                "msgstr": "Bonjour",
                "references": [],
                "flags": ["fuzzy"],
            }
        ]
        out = serialize_po(entries)
        assert "#, fuzzy" in out

    def test_serialize_empty_list_includes_header(self) -> None:
        out = serialize_po([])
        assert 'msgid ""' in out
        assert "Generated by" in out

    def test_serialize_roundtrip_with_metadata(self) -> None:
        entries = [
            {
                "msgid": "Save",
                "msgstr": "Enregistrer",
                "references": ["src/ui.py:99"],
                "flags": ["fuzzy"],
            }
        ]
        serialized = serialize_po(entries)
        reparsed = parse_po(serialized)
        assert reparsed == entries

    def test_serialize_escapes_quotes(self) -> None:
        entries = [
            {
                "msgid": 'He said "hi"',
                "msgstr": 'Er sagte "hallo"',
                "references": [],
                "flags": [],
            }
        ]
        out = serialize_po(entries)
        assert r"\"" in out


class TestExtractIcuPlaceholders:
    def test_extracts_simple_placeholder(self) -> None:
        assert extract_icu_placeholders("Hello {name}!") == ["name"]

    def test_extracts_multiple_placeholders(self) -> None:
        result = extract_icu_placeholders("{greeting}, {name}!")
        assert "greeting" in result
        assert "name" in result
        assert len(result) == 2

    def test_no_placeholders_returns_empty(self) -> None:
        assert extract_icu_placeholders("plain text") == []

    def test_empty_string_returns_empty(self) -> None:
        assert extract_icu_placeholders("") == []

    def test_typed_placeholder_plural(self) -> None:
        result = extract_icu_placeholders("{count, plural, one {# item} other {# items}}")
        assert "count" in result

    def test_typed_placeholder_select(self) -> None:
        result = extract_icu_placeholders("{gender, select, male {He} female {She} other {They}}")
        assert "gender" in result

    def test_typed_number_format(self) -> None:
        result = extract_icu_placeholders("{price, number, currency}")
        assert "price" in result

    def test_typed_date_format(self) -> None:
        result = extract_icu_placeholders("{updated, date, long}")
        assert "updated" in result

    def test_deduplicates_repeated_placeholders(self) -> None:
        result = extract_icu_placeholders("{name} said {name} is here")
        assert result == ["name"]

    def test_whitespace_around_name_trimmed(self) -> None:
        result = extract_icu_placeholders("Hello {  name  }!")
        assert "name" in result

    def test_preserves_order_of_first_appearance(self) -> None:
        result = extract_icu_placeholders("{z} {a} {m}")
        assert result == ["z", "a", "m"]


class TestFindUntranslatedStrings:
    def test_empty_input_returns_empty(self) -> None:
        assert find_untranslated_strings("") == []

    def test_no_user_facing_strings_returns_empty(self) -> None:
        source = "x = 1\ny = 2\n"
        assert find_untranslated_strings(source) == []

    def test_short_string_not_flagged(self) -> None:
        source = 'short = "Hi"\n'
        findings = find_untranslated_strings(source)
        assert all("Hi" not in f["string"] for f in findings)

    def test_no_space_not_flagged(self) -> None:
        source = 'label = "HelloWorld"\n'
        findings = find_untranslated_strings(source)
        assert len(findings) == 0

    def test_flags_hardcoded_user_facing_string(self) -> None:
        source = 'message = "Welcome to the application"\n'
        findings = find_untranslated_strings(source)
        assert len(findings) >= 1
        assert any("Welcome to the application" in f["string"] for f in findings)

    def test_gettext_wrapped_not_flagged(self) -> None:
        source = 'message = _("Hello World message here")\n'
        findings = find_untranslated_strings(source)
        assert all("Hello World" not in f["string"] for f in findings)

    def test_gettext_function_not_flagged(self) -> None:
        source = 'message = gettext("This is a translated string")\n'
        findings = find_untranslated_strings(source)
        assert len(findings) == 0

    def test_ngettext_not_flagged(self) -> None:
        source = 'msg = ngettext("One item here", "%d items here", n)\n'
        findings = find_untranslated_strings(source)
        assert len(findings) == 0

    def test_pgettext_not_flagged(self) -> None:
        source = 'msg = pgettext("context", "Contextual message here")\n'
        findings = find_untranslated_strings(source)
        assert len(findings) == 0

    def test_reports_line_number(self) -> None:
        source = "x = 1\nmsg = 'Hardcoded message here'\n"
        findings = find_untranslated_strings(source)
        assert len(findings) >= 1
        assert findings[0]["line"] == 2

    def test_issue_field_is_present(self) -> None:
        source = 'msg = "Hardcoded user message"\n'
        findings = find_untranslated_strings(source)
        assert len(findings) >= 1
        assert "gettext" in findings[0]["issue"]

    def test_mixed_gettext_and_hardcoded(self) -> None:
        source = '_("Translated message")\n"Hardcoded message here"\n'
        findings = find_untranslated_strings(source)
        assert len(findings) >= 1
        assert all("Translated message" not in f["string"] for f in findings)
        assert any("Hardcoded" in f["string"] for f in findings)

    def test_single_quoted_strings_flagged(self) -> None:
        source = "msg = 'Hardcoded message in single quotes'\n"
        findings = find_untranslated_strings(source)
        assert len(findings) >= 1

    def test_multiple_findings_in_one_file(self) -> None:
        source = 'label1 = "First message here"\nlabel2 = "Second message here"\nlabel3 = _("Translated message")\n'
        findings = find_untranslated_strings(source)
        hardcoded = [f for f in findings if "gettext" in f["issue"]]
        assert len(hardcoded) == 2

    def test_language_parameter_accepted(self) -> None:
        source = '"Hardcoded message for Python"\n'
        findings_py = find_untranslated_strings(source, language="python")
        findings_js = find_untranslated_strings(source, language="javascript")
        assert len(findings_py) >= 1
        assert len(findings_py) == len(findings_js)
