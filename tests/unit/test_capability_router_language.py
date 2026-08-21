"""Verify capability router discovers the language collection from galaxy.yml declarations."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from general_ludd.dispatch.capabilities import CapabilityRegistry, discover_capabilities

ROOT = Path(__file__).resolve().parents[2]
LANGUAGE_COLLECTION = ROOT / "collections/ansible_collections/general_ludd/language"


class TestLanguageCapabilities:
    @pytest.fixture
    def registry(self) -> CapabilityRegistry:
        return discover_capabilities()

    def test_language_galaxy_metadata_contains_only_supported_keys(self) -> None:
        galaxy = yaml.safe_load((LANGUAGE_COLLECTION / "galaxy.yml").read_text(encoding="utf-8"))
        assert "model_capabilities" not in galaxy
        assert "role_capabilities" not in galaxy

    def test_language_capability_contract_is_loaded_from_canonical_file(
        self, registry: CapabilityRegistry
    ) -> None:
        language = registry.collections["language"]
        assert {str(cap["name"]) for cap in language.model_capabilities} == {
            "language_detection",
            "translation",
            "transliteration",
            "unicode_analyze",
            "encoding_detect",
            "font_analyze",
            "homoglyph_scan",
            "phonetic_transcribe",
            "locale_format",
            "i18n_extract",
            "bom_detect",
        }
        assert language.role_capabilities["translate"] == ["translation"]

    def test_language_collection_discovered(self, registry: CapabilityRegistry) -> None:
        assert "language" in registry.collections

    def test_language_model_capabilities(self, registry: CapabilityRegistry) -> None:
        lang = registry.collections["language"]
        caps = lang.raw_tags
        for cap in (
            "language_detection",
            "translation",
            "transliteration",
            "unicode_analyze",
            "encoding_detect",
            "font_analyze",
            "homoglyph_scan",
            "phonetic_transcribe",
            "locale_format",
            "i18n_extract",
            "bom_detect",
        ):
            assert cap in caps, f"language missing capability tag: {cap}"

    def test_language_tags(self, registry: CapabilityRegistry) -> None:
        lang = registry.collections["language"]
        assert "unicode" in lang.tags
        assert "encoding" in lang.tags
        assert "i18n" in lang.tags
        assert "l10n" in lang.tags
        assert "fonts" in lang.tags
        assert "phonetics" in lang.tags
        assert "language" in lang.tags

    def test_language_roles_discovered(self, registry: CapabilityRegistry) -> None:
        lang = registry.collections["language"]
        role_names = {r["name"] for r in lang.roles}
        for role in (
            "language_detect",
            "translate",
            "transliterate",
            "unicode_analyze",
            "encoding_detect",
            "font_analyze",
            "homoglyph_scan",
            "phonetic_transcribe",
            "locale_format",
            "i18n_extract",
            "bom_detect",
        ):
            assert role in role_names, f"language role not discovered: {role}"

    def test_language_tag_index_lookups(self, registry: CapabilityRegistry) -> None:
        for tag in (
            "language_detection",
            "translation",
            "transliteration",
            "unicode_analyze",
            "homoglyph_scan",
            "phonetic_transcribe",
            "locale_format",
            "bom_detect",
            "encoding",
            "fonts",
            "text-processing",
        ):
            matching = registry.lookup_by_tag(tag)
            assert matching, f"no collections found for language tag: {tag}"

    def test_module_utils_discovered(self, registry: CapabilityRegistry) -> None:
        lang = registry.collections["language"]
        caps = lang.raw_tags
        for util in ("encoding_detect", "font_analyze", "homoglyph_scan", "phonetic_transcribe"):
            assert util in caps, f"language module_util not in tag index: {util}"

    def test_language_tags_dont_leak_to_radio(self, registry: CapabilityRegistry) -> None:
        radio = registry.collections["radio"]
        assert "language_detection" not in radio.tags
        assert "translation" not in radio.tags
        assert "homoglyph_scan" not in radio.tags

    def test_language_tags_dont_leak_to_binary_re(self, registry: CapabilityRegistry) -> None:
        bre = registry.collections["binary_re"]
        assert "language_detection" not in bre.tags
        assert "transliteration" not in bre.tags
        assert "locale_format" not in bre.tags

    def test_cross_collection_no_overlap(self, registry: CapabilityRegistry) -> None:
        lang_caps = registry.lookup_by_tag("language_detection")
        radio_caps = registry.lookup_by_tag("spectrum_scan")
        bre_caps = registry.lookup_by_tag("disassembly")
        assert "radio" not in lang_caps
        assert "binary_re" not in lang_caps
        assert "language" not in radio_caps
        assert "language" not in bre_caps
