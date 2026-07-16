"""TDD tests for yara_generator.py — YARA rule generation from PatternEntry records.

These tests are written BEFORE the implementation per the TDD policy. They pin
the public API (``YaraGenerator``, ``YaraRule``) and the rendering contract so
that the implementation has a failing target.
"""

from __future__ import annotations

import re

import pytest

from plugins.module_utils.pattern_database import (
    DATABASE,
    PatternCategory,
    PatternDatabase,
    PatternEntry,
    PatternPlatform,
    Severity,
)
from plugins.module_utils.yara_generator import (
    YaraGenerator,
    YaraRule,
    YaraString,
)


def _entry(**overrides: object) -> PatternEntry:
    base: dict[str, object] = {
        "id": "test-shellcode-nop",
        "category": PatternCategory.SHELLCODE,
        "name": "NOP sled",
        "byte_patterns": (b"\x90\x90\x90\x90\x90\x90\x90\x90",),
        "string_markers": ("nop_sled",),
        "severity": Severity.LOW,
        "platform": PatternPlatform.CROSS_PLATFORM,
        "description": "Long run of x86 NOP instructions",
    }
    base.update(overrides)
    return PatternEntry(**base)  # type: ignore[arg-type]


class TestYaraString:
    def test_ascii_string_kind(self) -> None:
        ys = YaraString(identifier="$s1", kind="string", value="UPX!")
        assert ys.identifier == "$s1"
        assert ys.kind == "string"
        assert ys.value == "UPX!"

    def test_byte_kind(self) -> None:
        ys = YaraString(identifier="$b1", kind="hex", value="90 90 90")
        assert ys.kind == "hex"


class TestYaraRuleShape:
    def test_rule_has_required_fields(self) -> None:
        rule = YaraRule(
            name="gludd_shellcode_nop_sled",
            strings=(YaraString("$s1", "string", "nop_sled"),),
            condition="any of them",
            meta={"description": "x", "severity": "low", "category": "shellcode"},
        )
        assert rule.name == "gludd_shellcode_nop_sled"
        assert rule.condition == "any of them"
        assert "description" in rule.meta


class TestGenerateForEntry:
    def test_rule_name_is_sanitized(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(_entry(id="shellcode-nop-sled"))
        # YARA identifiers: [A-Za-z_][A-Za-z0-9_]*  — hyphens forbidden.
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", rule.name), rule.name
        assert "-" not in rule.name

    def test_rule_name_has_collection_prefix(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(_entry())
        assert rule.name.startswith("gludd_"), rule.name

    def test_string_markers_become_string_kind(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(
            _entry(string_markers=("wnry", "@WanaDecryptor@"))
        )
        kinds = {ys.kind for ys in rule.strings}
        assert "string" in kinds
        marker_values = {ys.value for ys in rule.strings if ys.kind == "string"}
        assert "wnry" in marker_values
        assert "@WanaDecryptor@" in marker_values

    def test_byte_patterns_become_hex_kind(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(
            _entry(byte_patterns=(b"\x90\x90\x90\x90\x90\x90\x90\x90",))
        )
        hex_strings = [ys for ys in rule.strings if ys.kind == "hex"]
        assert len(hex_strings) == 1
        # Hex body: two-digit space-separated bytes.
        assert hex_strings[0].value == "90 90 90 90 90 90 90 90"

    def test_non_ascii_bytes_become_hex(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(
            _entry(byte_patterns=(b"\xde\xad\xbe\xef",), string_markers=())
        )
        hex_strings = [ys for ys in rule.strings if ys.kind == "hex"]
        assert len(hex_strings) == 1
        assert hex_strings[0].value == "DE AD BE EF"

    def test_meta_carries_category_severity_description(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(_entry())
        assert rule.meta["category"] == "shellcode"
        assert rule.meta["severity"] == "low"
        assert "NOP" in rule.meta["description"]

    def test_meta_includes_pattern_id(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(_entry(id="shellcode-nop-sled"))
        assert rule.meta["pattern_id"] == "shellcode-nop-sled"

    def test_meta_includes_platform(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(_entry(platform=PatternPlatform.LINUX))
        assert rule.meta["platform"] == "linux"

    def test_meta_includes_references_when_present(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(
            _entry(references=("https://example.com/ref",))
        )
        assert "https://example.com/ref" in rule.meta["references"]

    def test_condition_uses_any_of_them(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(
            _entry(
                byte_patterns=(b"\x90\x90", b"\xeb\x10"),
                string_markers=("a", "b"),
            )
        )
        # Multiple strings → "any of them" (concise, avoids identifier bookkeeping)
        assert rule.condition == "any of them"

    def test_condition_single_string_uses_identifier(self) -> None:
        gen = YaraGenerator()
        rule = gen.generate_for_entry(
            _entry(byte_patterns=(), string_markers=("only_marker",))
        )
        assert rule.condition.startswith("$")
        assert rule.condition in {ys.identifier for ys in rule.strings}

    def test_entry_with_no_patterns_raises(self) -> None:
        gen = YaraGenerator()
        with pytest.raises(ValueError):
            gen.generate_for_entry(
                _entry(byte_patterns=(), string_markers=())
            )


class TestRenderRule:
    def test_render_has_rule_header(self) -> None:
        gen = YaraGenerator()
        text = gen.render_rule(gen.generate_for_entry(_entry()))
        assert text.startswith("rule gludd_"), text[:80]

    def test_render_has_meta_block(self) -> None:
        gen = YaraGenerator()
        text = gen.render_rule(gen.generate_for_entry(_entry()))
        assert "meta:" in text
        assert 'description =' in text
        assert 'severity =' in text

    def test_render_has_strings_block(self) -> None:
        gen = YaraGenerator()
        text = gen.render_rule(gen.generate_for_entry(_entry()))
        assert "strings:" in text
        # String marker rendered as quoted string
        assert '$' in text

    def test_render_has_condition_block(self) -> None:
        gen = YaraGenerator()
        text = gen.render_rule(gen.generate_for_entry(_entry()))
        assert "condition:" in text

    def test_render_hex_strings_in_braces(self) -> None:
        gen = YaraGenerator()
        text = gen.render_rule(
            gen.generate_for_entry(
                _entry(byte_patterns=(b"\xde\xad\xbe\xef",), string_markers=())
            )
        )
        # YARA hex string syntax: $id = { DE AD BE EF }
        assert "{ DE AD BE EF }" in text

    def test_render_escapes_quotes_in_string_markers(self) -> None:
        gen = YaraGenerator()
        entry = _entry(
            byte_patterns=(),
            string_markers=('@WanaDecryptor@ says "hi"'),
        )
        text = gen.render_rule(gen.generate_for_entry(entry))
        # Embedded double quotes must be escaped
        assert '\\"' in text

    def test_render_is_parseable_shape(self) -> None:
        """Smoke check: braces balanced, ends with closing brace + newline."""
        gen = YaraGenerator()
        text = gen.render_rule(gen.generate_for_entry(_entry()))
        assert text.endswith("}\n")
        # Every '{' (rule body + hex string bodies) must have a matching '}'.
        assert text.count("{") == text.count("}")

    def test_render_rule_name_is_valid_identifier(self) -> None:
        gen = YaraGenerator()
        name = gen.generate_for_entry(_entry(id="malware-wannacry-v2")).name
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name), name


class TestRenderAllAndGenerateAll:
    def test_generate_all_covers_database(self) -> None:
        gen = YaraGenerator(database=DATABASE)
        rules = gen.generate_all()
        assert len(rules) == len(DATABASE.all_entries())

    def test_render_all_concatenates_rules(self) -> None:
        gen = YaraGenerator(database=DATABASE)
        text = gen.render_all()
        rule_count = text.count("rule gludd_")
        assert rule_count >= 5  # at least the seed shellcode + malware entries

    def test_render_all_rules_have_unique_names(self) -> None:
        gen = YaraGenerator(database=DATABASE)
        rules = gen.generate_all()
        names = [r.name for r in rules]
        assert len(names) == len(set(names)), "duplicate rule names"

    def test_works_with_custom_database(self) -> None:
        # Ensure YaraGenerator does not depend on the singleton.
        custom = PatternDatabase()
        gen = YaraGenerator(database=custom)
        rules = gen.generate_all()
        assert len(rules) == len(custom.all_entries())


class TestKnownPatternsProduceValidRules:
    """Round-trip: every pattern in the seed DATABASE generates a valid rule."""

    def test_all_database_entries_generate(self) -> None:
        gen = YaraGenerator(database=DATABASE)
        for entry in DATABASE.all_entries():
            # Entries with zero patterns (shouldn't happen, but guard) skip.
            if not entry.byte_patterns and not entry.string_markers:
                continue
            rule = gen.generate_for_entry(entry)
            assert rule.name.startswith("gludd_")
            text = gen.render_rule(rule)
            assert text.startswith("rule ")
            assert text.endswith("}\n")

    def test_wannacry_rule_has_known_markers(self) -> None:
        gen = YaraGenerator(database=DATABASE)
        wannacry = DATABASE.get("malware-wannacry")
        assert wannacry is not None
        rule = gen.generate_for_entry(wannacry)
        marker_values = {
            ys.value for ys in rule.strings if ys.kind == "string"
        }
        assert "wnry" in marker_values
        assert "@WanaDecryptor@" in marker_values

    def test_metasploit_stager_rule_has_hex(self) -> None:
        gen = YaraGenerator(database=DATABASE)
        msf = DATABASE.get("shellcode-metasploit-stager")
        assert msf is not None
        rule = gen.generate_for_entry(msf)
        hex_strings = [ys for ys in rule.strings if ys.kind == "hex"]
        assert len(hex_strings) >= 1
        # FC E8 89 00 00 00 60 is a known stager prologue
        assert "FC E8 89" in hex_strings[0].value
