"""Tests for pattern_database.py — centralized binary RE pattern database (NF.3)."""

from __future__ import annotations

from plugins.module_utils.pattern_database import (
    ANTI_DEBUG_PATTERNS,
    DATABASE,
    MALWARE_FAMILY_PATTERNS,
    OBFUSCATION_PATTERNS,
    PACKER_PATTERNS,
    SHELLCODE_PATTERNS,
    PatternCategory,
    PatternDatabase,
    PatternEntry,
    PatternPlatform,
    Severity,
)


class TestEnums:
    def test_pattern_categories_complete(self):
        expected = {"packer", "anti_debug", "shellcode", "obfuscation", "malware_family"}
        values = {c.value for c in PatternCategory}
        assert values == expected

    def test_severity_levels(self):
        assert Severity.INFO.value == "info"
        assert Severity.LOW.value == "low"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.HIGH.value == "high"
        assert Severity.CRITICAL.value == "critical"

    def test_platforms(self):
        values = {p.value for p in PatternPlatform}
        assert "windows" in values
        assert "linux" in values
        assert "cross_platform" in values


class TestPatternEntry:
    def test_construction_defaults(self):
        entry = PatternEntry(
            id="test-1",
            category=PatternCategory.SHELLCODE,
            name="Test pattern",
            byte_patterns=[b"\x90\x90"],
            severity=Severity.LOW,
        )
        assert entry.id == "test-1"
        assert entry.platform == PatternPlatform.CROSS_PLATFORM
        assert entry.description == ""
        assert entry.references == ()

    def test_matches_bytes_positive(self):
        entry = PatternEntry(
            id="nop-sled",
            category=PatternCategory.SHELLCODE,
            name="NOP sled",
            byte_patterns=[b"\x90\x90\x90"],
            severity=Severity.LOW,
        )
        assert entry.matches_bytes(b"\xff\x90\x90\x90\x00")

    def test_matches_bytes_negative(self):
        entry = PatternEntry(
            id="nop-sled",
            category=PatternCategory.SHELLCODE,
            name="NOP sled",
            byte_patterns=[b"\x90\x90\x90"],
            severity=Severity.LOW,
        )
        assert not entry.matches_bytes(b"\xff\x00\x00")

    def test_matches_string(self):
        entry = PatternEntry(
            id="upx-marker",
            category=PatternCategory.PACKER,
            name="UPX",
            string_markers=["UPX!"],
            severity=Severity.MEDIUM,
        )
        assert entry.matches_bytes(b"data\x00UPX! \x00end")
        assert not entry.matches_bytes(b"data\x00UPX0\x00")

    def test_matches_any_pattern(self):
        entry = PatternEntry(
            id="multi",
            category=PatternCategory.MALWARE_FAMILY,
            name="Multi",
            byte_patterns=[b"\xde\xad", b"\xbe\xef"],
            string_markers=["EVIL", "MALWARE"],
            severity=Severity.HIGH,
        )
        assert entry.matches_bytes(b"\xde\xad")
        assert entry.matches_bytes(b"\xbe\xef")
        assert entry.matches_bytes(b"EVIL found")
        assert entry.matches_bytes(b"MALWARE detected")
        assert not entry.matches_bytes(b"clean")


class TestDatabaseContents:
    def test_shellcode_patterns_nonempty(self):
        assert len(SHELLCODE_PATTERNS) >= 4
        ids = {p.id for p in SHELLCODE_PATTERNS}
        assert "shellcode-nop-sled" in ids
        assert "shellcode-metasploit-stager" in ids

    def test_packer_patterns_cover_upx(self):
        names = {p.name.lower() for p in PACKER_PATTERNS}
        assert any("upx" in n for n in names)

    def test_anti_debug_patterns_nonempty(self):
        assert len(ANTI_DEBUG_PATTERNS) >= 3

    def test_obfuscation_patterns_nonempty(self):
        assert len(OBFUSCATION_PATTERNS) >= 2

    def test_malware_family_patterns_nonempty(self):
        assert len(MALWARE_FAMILY_PATTERNS) >= 2

    def test_all_entries_have_unique_ids(self):
        all_entries = DATABASE.all_entries()
        ids = [e.id for e in all_entries]
        assert len(ids) == len(set(ids)), "duplicate pattern IDs detected"

    def test_all_entries_have_at_least_one_signature(self):
        for entry in DATABASE.all_entries():
            assert entry.byte_patterns or entry.string_markers, (
                f"entry {entry.id} has no byte_patterns and no string_markers"
            )

    def test_critical_shellcode_severity(self):
        severities = {e.severity for e in SHELLCODE_PATTERNS}
        assert Severity.CRITICAL in severities, "at least one shellcode pattern must be CRITICAL"
        assert all(s.value not in ("info",) for s in severities), \
            "shellcode entries must never be INFO severity"


class TestPatternDatabase:
    def test_database_is_singleton_constant(self):
        assert DATABASE is PatternDatabase()

    def test_by_category(self):
        shellcode = DATABASE.by_category(PatternCategory.SHELLCODE)
        assert all(e.category == PatternCategory.SHELLCODE for e in shellcode)
        assert len(shellcode) == len(SHELLCODE_PATTERNS)

    def test_by_severity(self):
        critical = DATABASE.by_severity(Severity.CRITICAL)
        assert all(e.severity == Severity.CRITICAL for e in critical)

    def test_by_platform(self):
        windows = DATABASE.by_platform(PatternPlatform.WINDOWS)
        assert all(
            e.platform in (PatternPlatform.WINDOWS, PatternPlatform.CROSS_PLATFORM)
            for e in windows
        )

    def test_get_by_id(self):
        entry = DATABASE.get("shellcode-nop-sled")
        assert entry is not None
        assert entry.name == "NOP sled"

    def test_get_unknown_id_returns_none(self):
        assert DATABASE.get("does-not-exist") is None

    def test_scan_bytes_finds_match(self):
        data = b"\x90" * 16 + b"\xcc\xcc"
        matches = DATABASE.scan_bytes(data)
        ids = {m.entry.id for m in matches}
        assert "shellcode-nop-sled" in ids

    def test_scan_bytes_finds_packer(self):
        data = b"MZ\x90\x00UPX! padded with junk data"
        matches = DATABASE.scan_bytes(data)
        categories = {m.entry.category for m in matches}
        assert PatternCategory.PACKER in categories

    def test_scan_bytes_empty_input(self):
        assert DATABASE.scan_bytes(b"") == []

    def test_scan_bytes_no_matches(self):
        data = b"\x00" * 256
        matches = DATABASE.scan_bytes(data)
        nop_match = [m for m in matches if m.entry.id == "shellcode-nop-sled"]
        assert nop_match == []

    def test_scan_bytes_carries_offset(self):
        data = b"\x00" * 10 + b"\x90" * 16
        matches = DATABASE.scan_bytes(data)
        nop_matches = [m for m in matches if m.entry.id == "shellcode-nop-sled"]
        assert nop_matches
        for m in nop_matches:
            assert m.offset >= 10

    def test_scan_bytes_filter_by_category(self):
        data = b"\x90" * 16
        matches = DATABASE.scan_bytes(data, categories={PatternCategory.PACKER})
        assert all(m.entry.category == PatternCategory.PACKER for m in matches)
        nop = [m for m in matches if m.entry.id == "shellcode-nop-sled"]
        assert nop == []

    def test_scan_min_severity(self):
        data = b"\x90" * 16
        matches = DATABASE.scan_bytes(data, min_severity=Severity.CRITICAL)
        nop = [m for m in matches if m.entry.id == "shellcode-nop-sled"]
        assert nop == []

    def test_scan_result_fields(self):
        data = b"\x90" * 16
        matches = DATABASE.scan_bytes(data)
        assert matches
        m = matches[0]
        assert isinstance(m.offset, int)
        assert m.offset >= 0
        assert m.entry is not None
        assert m.matched_pattern

    def test_all_categories_represented(self):
        cats = {e.category for e in DATABASE.all_entries()}
        assert PatternCategory.SHELLCODE in cats
        assert PatternCategory.PACKER in cats
        assert PatternCategory.ANTI_DEBUG in cats
        assert PatternCategory.OBFUSCATION in cats
        assert PatternCategory.MALWARE_FAMILY in cats

    def test_total_entry_count(self):
        total = len(DATABASE.all_entries())
        assert total >= 15, f"expected ≥15 patterns, got {total}"

    def test_summary(self):
        summary = DATABASE.summary()
        assert "total" in summary
        assert "by_category" in summary
        assert summary["total"] == len(DATABASE.all_entries())
        for cat in PatternCategory:
            assert cat.value in summary["by_category"]

    def test_windows_antidebug_present(self):
        win_entries = [
            e for e in DATABASE.all_entries()
            if e.platform == PatternPlatform.WINDOWS and e.category == PatternCategory.ANTI_DEBUG
        ]
        assert win_entries

    def test_linux_antidebug_present(self):
        linux_entries = [
            e for e in DATABASE.all_entries()
            if e.platform == PatternPlatform.LINUX and e.category == PatternCategory.ANTI_DEBUG
        ]
        assert linux_entries


class TestConsolidation:
    """The pattern database must consolidate the existing obfuscation_techniques patterns."""

    def test_imports_obfuscation_techniques(self):
        from plugins.module_utils import pattern_database as pdb
        assert hasattr(pdb, "_OBFUSCATION_TECHNIQUES_AVAILABLE")
        assert pdb._OBFUSCATION_TECHNIQUES_AVAILABLE is True

    def test_upx_signature_consolidated(self):
        upx_entries = [e for e in PACKER_PATTERNS if "upx" in e.name.lower()]
        assert upx_entries
        all_upx_bytes = [p for e in upx_entries for p in e.byte_patterns]
        assert b"UPX!" in all_upx_bytes

    def test_vmp_signature_consolidated(self):
        vmp_entries = [e for e in DATABASE.all_entries() if "vmprotect" in e.name.lower()]
        assert vmp_entries
