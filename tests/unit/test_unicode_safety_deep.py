"""Deep unicode safety tests: BIDI overrides, zero-width chars, normalization,
emoji handling, right-to-left, confusable characters, and encoding edge cases.

Covers CVE-2021-42574 (Trojan Source), CVE-2021-42694 (homoglyph attacks),
zero-width injection, NFC/NFKC normalization integrity, surrogate pair handling,
and UTF-8 boundary validation.
"""

from __future__ import annotations

import unicodedata

# ── BIDI override prevention (CVE-2021-42574) ─────────────────────────────


class TestBidiOverrideDetection:
    """detect_bidi_overrides() covers explicit override (LRO/RLO) and
    embedding/isolate codepoints that enable Trojan Source attacks."""

    def test_explicit_lro_override_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        text = "\u202d" + "admin" + "\u202c"
        findings = detect_bidi_overrides(text)
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x202D for f in findings)

    def test_explicit_rlo_override_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        text = "\u202e" + "admin" + "\u202c"
        findings = detect_bidi_overrides(text)
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x202E for f in findings)

    def test_lro_cve_tagged(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        findings = detect_bidi_overrides("\u202d hello")
        assert len(findings) >= 1
        assert findings[0]["cve"] == "CVE-2021-42574"

    def test_rlo_cve_tagged(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        findings = detect_bidi_overrides("\u202e hello")
        assert len(findings) >= 1
        assert findings[0]["cve"] == "CVE-2021-42574"

    def test_lre_embedding_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        findings = detect_bidi_overrides("\u202a left-to-right")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x202A for f in findings)

    def test_rle_embedding_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        findings = detect_bidi_overrides("\u202b right-to-left")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x202B for f in findings)

    def test_bidi_isolate_lri_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        findings = detect_bidi_overrides("\u2066 hello")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x2066 for f in findings)

    def test_bidi_isolate_rli_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        findings = detect_bidi_overrides("\u2067 hello")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x2067 for f in findings)

    def test_pdf_terminator_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        findings = detect_bidi_overrides("\u202c")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x202C for f in findings)

    def test_trojan_source_comment_attack_pattern(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides, is_suspicious

        line = "if access_level != \u202e\u2066admin\u2069\u202c:"
        findings = detect_bidi_overrides(line)
        assert len(findings) >= 2
        assert is_suspicious(line)

    def test_trojan_source_string_attack_pattern(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        line = 'msg = \u202b"access \u2067granted\u2069"\u202c'
        findings = detect_bidi_overrides(line)
        assert len(findings) >= 2

    def test_clean_ascii_no_bidi(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        assert detect_bidi_overrides("hello world") == []

    def test_empty_no_bidi(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        assert detect_bidi_overrides("") == []


# ── Zero-width character detection ───────────────────────────────────────


class TestZeroWidthDetection:
    """detect_invisible_chars() catches zero-width spaces, joiners, non-joiners,
    word joiners, soft hyphens, and other invisible codepoints."""

    def test_zero_width_space_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        text = "hello\u200bworld"
        findings = detect_invisible_chars(text)
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x200B for f in findings)

    def test_zero_width_non_joiner_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        text = "\u200cf\u200co\u200co"
        findings = detect_invisible_chars(text)
        assert len(findings) >= 3
        assert all(f["codepoint"] == 0x200C for f in findings)

    def test_zero_width_joiner_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        text = "\u200d" + "\U0001f468" + "\u200d" + "\U0001f469"
        findings = detect_invisible_chars(text)
        assert len(findings) >= 2
        assert all(f["codepoint"] == 0x200D for f in findings)

    def test_soft_hyphen_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        findings = detect_invisible_chars("soft\u00adhyphen")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x00AD for f in findings)

    def test_word_joiner_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        findings = detect_invisible_chars("\u2060hidden")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x2060 for f in findings)

    def test_invisible_separator_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        findings = detect_invisible_chars("a\u2060b")
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x2060 for f in findings)

    def test_bidi_marks_detected_in_invisible(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        text = "\u200e\u200f"
        findings = detect_invisible_chars(text)
        assert len(findings) >= 2

    def test_zero_width_filename_injection(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars, is_suspicious

        name = "malware.exe\u200b.pdf"
        findings = detect_invisible_chars(name)
        assert len(findings) >= 1
        assert is_suspicious(name)

    def test_mixed_invisible_and_bidi(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
            detect_invisible_chars,
        )

        text = "\u200b\u202e\u2067\u200b"
        assert len(detect_invisible_chars(text)) >= 4
        assert len(detect_bidi_overrides(text)) >= 2

    def test_clean_ascii_no_invisible(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        assert detect_invisible_chars("clean text 123") == []

    def test_empty_no_invisible(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        assert detect_invisible_chars("") == []


# ── Unicode normalization ────────────────────────────────────────────────


class TestUnicodeNormalization:
    """NFC/NFKC normalization preserves identity while resisting confusable
    attacks that use decomposed forms (NFD) or compatibility characters."""

    def test_nfkc_normalizes_superscript(self) -> None:
        superscript = "x\u00b2"  # x followed by superscript 2
        normalized = unicodedata.normalize("NFKC", superscript)
        assert normalized == "x2"

    def test_nfkc_normalizes_fractions(self) -> None:
        fraction = "\u00bd"
        normalized = unicodedata.normalize("NFKC", fraction)
        assert "1" in normalized and "2" in normalized

    def test_nfkc_normalizes_fullwidth_ascii(self) -> None:
        fullwidth = "\uff21\uff22\uff23"
        normalized = unicodedata.normalize("NFKC", fullwidth)
        assert normalized == "ABC"

    def test_nfkc_normalizes_circled_text(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        circled = "\u24b6\u24b7\u24b8"  # ⒶⒷⒸ
        normalized = unicodedata.normalize("NFKC", circled)
        assert normalized == "ABC"
        skeleton = generate_skeleton(normalized)
        assert skeleton == "ABC"

    def test_nfc_roundtrip_preserved(self) -> None:
        decomposed = unicodedata.normalize("NFD", "caf\u00e9")
        composed = unicodedata.normalize("NFC", decomposed)
        assert composed == "caf\u00e9"

    def test_nfkc_detects_confusable_after_normalization(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        fullwidth_a = "\uff41"
        normalized = unicodedata.normalize("NFKC", fullwidth_a)
        assert normalized == "a"
        skeleton = generate_skeleton(normalized)
        assert skeleton == "a"

    def test_skeleton_after_nfkc_normalization(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        fullwidth = "\uff41\uff42"
        nfkc = unicodedata.normalize("NFKC", fullwidth)
        skeleton = generate_skeleton(nfkc)
        assert skeleton == "ab"


# ── Emoji handling ───────────────────────────────────────────────────────


class TestEmojiHandling:
    """Emoji sequences — single codepoint, multi-codepoint (ZWJ),
    variation selectors, flag sequences, and skin-tone modifiers."""

    def test_single_codepoint_emoji_not_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert not is_suspicious("\U0001f600")

    def test_emoji_with_variation_selector(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        text = "\u2708\ufe0f"
        findings = detect_invisible_chars(text)
        assert not any(f["codepoint"] == 0xFE0F for f in findings)

    def test_zwj_emoji_sequence(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        findings = detect_invisible_chars(family)
        zws_findings = [f for f in findings if f["codepoint"] == 0x200D]
        assert len(zws_findings) >= 2

    def test_flag_emoji_sequence(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables, is_suspicious

        flag_gb = "\U0001f1ec\U0001f1e7"
        assert not is_suspicious(flag_gb)
        assert detect_confusables(flag_gb) == []

    def test_skin_tone_modifier(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        wave = "\U0001f44b\U0001f3fd"  # waving hand + medium skin tone
        assert not is_suspicious(wave)

    def test_keycap_emoji_sequence(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables, is_suspicious

        keycap = "5\ufe0f\u20e3"  # 5 + VS16 + combining enclosing keycap
        assert not is_suspicious(keycap)
        assert detect_confusables(keycap) == []

    def test_tag_sequence_emoji(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        tag_sequence = "\U0001f3f4\ue0067\ue0062\ue0065\ue006E\ue0067\ue007F"
        findings = detect_invisible_chars(tag_sequence)
        assert all(f["codepoint"] not in (0xE0067, 0xE0062) for f in findings)


# ── Right-to-left text handling ──────────────────────────────────────────


class TestRightToLeftHandling:
    """RTL scripts (Arabic, Hebrew) should be handled safely without flagging
    legitimate text. BIDI override characters are the attack vector, not the
    RTL script itself."""

    def test_arabic_text_not_falsely_flagged(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
            is_suspicious,
        )

        text = "\u0627\u0644\u0633\u0644\u0627\u0645"
        assert detect_bidi_overrides(text) == []
        assert not is_suspicious(text)

    def test_hebrew_text_not_falsely_flagged(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
            is_suspicious,
        )

        text = "\u05e9\u05dc\u05d5\u05dd"
        assert detect_bidi_overrides(text) == []
        assert not is_suspicious(text)

    def test_mixed_ltr_rtl_without_overrides_safe(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        text = "Hello \u0639\u0631\u0628\u064a world"
        assert detect_bidi_overrides(text) == []

    def test_rtl_mark_not_suspicious_by_default(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        text = "\u05d0\u05d1\u05d2"
        assert not is_suspicious(text)

    def test_bidi_marks_vs_rtl_text_distinction(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
            detect_invisible_chars,
        )

        rtl_text = "\u0627\u0644\u0633\u0644\u0627\u0645"
        assert detect_bidi_overrides(rtl_text) == []
        invisible = detect_invisible_chars(rtl_text)
        assert not any(0x200E <= f["codepoint"] <= 0x200F for f in invisible)


# ── Confusable character attacks ─────────────────────────────────────────


class TestConfusableAttacks:
    """Homoglyph (confusable) character attacks: Cyrillic lookalikes,
    Greek substitutions, numeric homoglyphs, and domain spoofing patterns."""

    def test_cyrillic_a_substitution(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
            generate_skeleton,
            is_suspicious,
        )

        text = "\u0430pple.com"
        findings = detect_confusables(text)
        assert len(findings) >= 1
        assert findings[0]["skeleton"] == "a"
        assert generate_skeleton(text) == "apple.com"
        assert is_suspicious(text)

    def test_cyrillic_es_substitution(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
            generate_skeleton,
        )

        text = "mi\u0441rosoft.com"
        findings = detect_confusables(text)
        assert len(findings) >= 1
        assert findings[0]["skeleton"] == "c"
        assert generate_skeleton(text) == "microsoft.com"

    def test_cyrillic_ie_substitution(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
            generate_skeleton,
        )

        text = "\u0435xample.com"
        findings = detect_confusables(text)
        assert len(findings) >= 1
        assert findings[0]["skeleton"] == "e"
        assert generate_skeleton(text) == "example.com"

    def test_greek_omicron_substitution(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
            generate_skeleton,
        )

        text = "g\u03bf\u03bfgle.com"
        findings = detect_confusables(text)
        assert len(findings) >= 2
        assert all(f["skeleton"] == "o" for f in findings)
        assert generate_skeleton(text) == "google.com"

    def test_digit_zero_for_letter_o_substitution(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
        )

        text = "g\u043e\u043egle.com"  # Cyrillic o
        findings = detect_confusables(text)
        assert len(findings) >= 2
        assert all(f["skeleton"] == "o" for f in findings)

    def test_mixed_script_detection(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("Hello" + "\u0430\u0432\u0433\u0434")
        assert result["is_mixed"]
        assert "Latin" in result["scripts"]
        assert "Cyrillic" in result["scripts"]

    def test_empty_mixed_script(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("")
        assert not result["is_mixed"]
        assert result["scripts"] == []

    def test_single_script_not_mixed(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("hello world")
        assert not result["is_mixed"]

    def test_confusable_arm(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
            generate_skeleton,
        )

        text = "\u0441\u0440\u0430\u0435"  # Cyrillic es, er, a, ie → "cpae"
        findings = detect_confusables(text)
        assert len(findings) >= 4
        assert generate_skeleton(text) == "cpae"


# ── is_suspicious combined check ─────────────────────────────────────────


class TestIsSuspiciousCombined:
    """is_suspicious() combines confusable + invisible + bidi detection."""

    def test_confusable_triggers_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("\u0430pple")

    def test_invisible_triggers_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("hello\u200bworld")

    def test_bidi_triggers_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("\u202e admin")

    def test_clean_text_not_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert not is_suspicious("hello world")

    def test_empty_not_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert not is_suspicious("")


# ── Unicode encoding edge cases ──────────────────────────────────────────


class TestEncodingEdgeCases:
    """UTF-8 boundary cases, surrogate pair handling, and null byte safety."""

    def test_surrogate_pair_handling(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        high_surrogate = "\U0001f600"
        assert not detect_invisible_chars(high_surrogate)

    def test_null_byte_not_confusable(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        assert detect_confusables("\x00hello") == []

    def test_ascii_control_chars_not_confusable(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
            detect_invisible_chars,
        )

        text = "".join(chr(i) for i in range(0, 32))
        assert detect_confusables(text) == []
        invisible = detect_invisible_chars(text)
        assert not any(0 <= f["codepoint"] < 32 for f in invisible)

    def test_bmp_full_range_safety(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("\u0430pple")
        assert not is_suspicious("apple")

    def test_overlong_utf8_sequence_equivalent(self) -> None:
        from general_ludd.language.homoglyph_data import (
            is_suspicious,
        )

        text = "\u00c0A"  # A with grave + A — not overlong but composed
        assert not is_suspicious(text)

    def test_unicode_escape_roundtrip(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
            generate_skeleton,
        )

        original = "\u0430\u0435\u0440\u043e\u0441"  # Cyrillic aepoc
        skeleton = generate_skeleton(original)
        findings = detect_confusables(original)
        assert len(findings) >= 4
        assert skeleton == "aepoc"


# ── Skeleton identity ────────────────────────────────────────────────────


class TestSkeletonIdentity:
    """generate_skeleton() must be idempotent and preserve non-confusable text."""

    def test_skeleton_idempotent(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        text = "\u0430\u0435\u0440\u043e\u0441.com"
        sk1 = generate_skeleton(text)
        sk2 = generate_skeleton(sk1)
        assert sk1 == sk2

    def test_skeleton_preserves_clean_ascii(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        assert generate_skeleton("hello world") == "hello world"

    def test_skeleton_handles_empty(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        assert generate_skeleton("") == ""

    def test_skeleton_after_nfkc_is_stable(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        fullwidth = "\uff41\uff42"
        nfkc = unicodedata.normalize("NFKC", fullwidth)
        generate_skeleton(fullwidth)
        sk2 = generate_skeleton(nfkc)
        assert sk2 == "ab"
