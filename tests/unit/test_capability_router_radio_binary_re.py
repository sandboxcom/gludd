"""Verify capability router discovers radio and binary_re collections from
galaxy.yml declarations."""

from __future__ import annotations

import pytest

from general_ludd.dispatch.capabilities import discover_capabilities


class TestRadioBinaryReCapabilities:
    @pytest.fixture
    def registry(self):
        return discover_capabilities()

    # ── Radio collection ──────────────────────────────────────────────────

    def test_radio_collection_discovered(self, registry):
        assert "radio" in registry.collections

    def test_radio_model_capabilities(self, registry):
        radio = registry.collections["radio"]
        caps = radio.raw_tags
        for cap in (
            "spectrum_scan",
            "sdr_capture",
            "decode_digital",
            "signal_identify",
            "regulation_lookup",
            "link_budget",
            "propagation_model",
            "antenna_design",
            "exam_quiz",
            "marine_decode",
        ):
            assert cap in caps, f"radio missing capability tag: {cap}"

    def test_radio_tags(self, registry):
        radio = registry.collections["radio"]
        assert "radio" in radio.tags
        assert "sdr" in radio.tags
        assert "rf" in radio.tags

    def test_radio_roles_discovered(self, registry):
        radio = registry.collections["radio"]
        role_names = {r["name"] for r in radio.roles}
        for role in (
            "spectrum_scan",
            "sdr_capture",
            "decode_digital",
            "signal_identify",
            "regulation_lookup",
            "link_budget",
            "propagation_model",
            "antenna_design",
            "exam_quiz",
            "marine_decode",
        ):
            assert role in role_names, f"radio role not discovered: {role}"

    def test_radio_tag_index_lookups(self, registry):
        for tag in (
            "spectrum_scan",
            "decode_digital",
            "link_budget",
            "antenna_design",
            "marine_decode",
            "rf",
            "sdr",
            "ham",
            "propagation",
        ):
            matching = registry.lookup_by_tag(tag)
            assert matching, f"no collections found for radio tag: {tag}"

    # ── Binary RE collection ──────────────────────────────────────────────

    def test_binary_re_collection_discovered(self, registry):
        assert "binary_re" in registry.collections

    def test_binary_re_model_capabilities(self, registry):
        bre = registry.collections["binary_re"]
        caps = bre.raw_tags
        for cap in (
            "ghidra_analyze",
            "gdb_analyze",
            "radare2_analyze",
            "frida_instrument",
            "deobfuscate",
            "fuzz_target",
            "cyberchef_transform",
            "prompt_injection_scan",
            "pe_analyze",
            "elf_analyze",
            "macho_analyze",
            "disassembly",
        ):
            assert cap in caps, f"binary_re missing capability tag: {cap}"

    def test_binary_re_parser_capabilities(self, registry):
        bre = registry.collections["binary_re"]
        caps = bre.raw_tags
        for parser in ("pe_analyze", "elf_analyze", "macho_analyze", "disassembly"):
            assert parser in caps, f"binary_re missing parser capability: {parser}"

    def test_binary_re_tags(self, registry):
        bre = registry.collections["binary_re"]
        assert "binary" in bre.tags
        assert "reverse-engineering" in bre.tags
        assert "security" in bre.tags

    def test_binary_re_roles_discovered(self, registry):
        bre = registry.collections["binary_re"]
        role_names = {r["name"] for r in bre.roles}
        for role in (
            "ghidra_analyze",
            "gdb_analyze",
            "radare2_analyze",
            "frida_instrument",
            "deobfuscate",
            "fuzz_target",
            "cyberchef_transform",
            "prompt_injection_scan",
        ):
            assert role in role_names, f"binary_re role not discovered: {role}"

    def test_binary_re_tag_index_lookups(self, registry):
        for tag in (
            "ghidra_analyze",
            "frida_instrument",
            "deobfuscate",
            "fuzz_target",
            "prompt_injection_scan",
            "pe_analyze",
            "elf_analyze",
            "disassembly",
            "binary",
            "reverse-engineering",
        ):
            matching = registry.lookup_by_tag(tag)
            assert matching, f"no collections found for binary_re tag: {tag}"

    # ── Cross-collection routing ──────────────────────────────────────────

    def test_radio_tags_dont_leak_to_binary_re(self, registry):
        bre = registry.collections["binary_re"]
        assert "sdr" not in bre.tags
        assert "antenna" not in bre.tags
        assert "ham" not in bre.tags

    def test_binary_re_tags_dont_leak_to_radio(self, registry):
        radio = registry.collections["radio"]
        assert "pe_analyze" not in radio.tags
        assert "ghidra" not in radio.tags
        assert "fuzz_target" not in radio.tags

    def test_cross_collection_no_overlap(self, registry):
        radio_caps = registry.lookup_by_tag("spectrum_scan")
        bre_caps = registry.lookup_by_tag("disassembly")
        assert "binary_re" not in radio_caps
        assert "radio" not in bre_caps
