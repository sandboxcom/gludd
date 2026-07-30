"""TDD tests for language collection role integration.

All tests MUST FAIL — the integration test harness for language collection roles
does not exist yet (Phase E polyglot support).

Per collections/ansible_collections/general_ludd/language/galaxy.yml, the
language collection contains 8 roles:
  - bom_detect (byte-order mark detection)
  - encoding_detect (charset encoding detection via chardet)
  - font_analyze (font file analysis)
  - homoglyph_scan (homoglyph/confusable character scanning)
  - i18n_extract (internationalization string extraction)
  - locale_format (locale string formatting/normalization)
  - phonetic_transcribe (phonetic transcription)
  - unicode_analyze (Unicode character property analysis)

All 8 roles have tasks/main.yml + defaults/main.yml + vars/main.yml + meta/main.yml.
These tests assert they are callable via the Ansible runner with valid inputs.

Tests:
  1. bom_detect correctly identifies UTF-8 BOM in binary file
  2. encoding_detect identifies UTF-8 encoding from sample text
  3. font_analyze returns font metadata for a valid font file
  4. homoglyph_scan finds confusable Latin/Cyrillic pairs
  5. i18n_extract extracts translatable strings from Python source
  6. locale_format normalizes locale string variants
  7. phonetic_transcribe produces IPA for English text
  8. unicode_analyze reports Unicode properties for given characters
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def test_i18n_role_args_confine_default_output(tmp_path: Path) -> None:
    from general_ludd.ansible.runner import _convert_role_args

    args = _convert_role_args("i18n_extract", {"directory": str(tmp_path)})
    output_dir = Path(args[args.index("--output-dir") + 1])

    assert output_dir.is_absolute()
    assert output_dir.parent == Path(tempfile.gettempdir())
    assert output_dir.name.startswith("gludd-i18n-extract-")
    assert output_dir != Path.cwd()

# ── bom_detect ──────────────────────────────────────────────────────────────


class TestBomDetectRole:
    """general_ludd.language.bom_detect — BOM (Byte Order Mark) detection."""

    @pytest.mark.asyncio
    async def test_detect_utf8_bom_positive(self, tmp_path: Path) -> None:
        """bom_detect correctly identifies a file with UTF-8 BOM."""
        bom_file = tmp_path / "with_bom.txt"
        bom_file.write_bytes(b"\xef\xbb\xbfHello, world!\n")

        result = await self._run_role("bom_detect", {"file_path": str(bom_file)})

        assert result["encoding"] == "utf-8", (
            f"P5 gap: bom_detect must return encoding='utf-8' for BOM, "
            f"got {result.get('encoding')}"
        )
        assert result["has_bom"] is True, (
            "P5 gap: bom_detect must set has_bom=True when BOM present"
        )

    @pytest.mark.asyncio
    async def test_detect_no_bom_negative(self, tmp_path: Path) -> None:
        """bom_detect correctly identifies a file without BOM."""
        no_bom_file = tmp_path / "no_bom.txt"
        no_bom_file.write_text("No BOM here\n", encoding="utf-8")

        result = await self._run_role("bom_detect", {"file_path": str(no_bom_file)})

        assert result["has_bom"] is False, (
            "P5 gap: bom_detect must set has_bom=False when no BOM present"
        )

    @pytest.mark.asyncio
    async def test_detect_utf16le_bom(self, tmp_path: Path) -> None:
        """bom_detect correctly identifies UTF-16 LE BOM."""
        bom_file = tmp_path / "utf16le.txt"
        bom_file.write_bytes(b"\xff\xfeH\x00e\x00l\x00l\x00o\x00\n\x00")

        result = await self._run_role("bom_detect", {"file_path": str(bom_file)})

        assert result["encoding"] == "utf-16-le", (
            f"P5 gap: bom_detect must detect UTF-16 LE BOM, "
            f"got {result.get('encoding')}"
        )
        assert result["has_bom"] is True

    @pytest.mark.asyncio
    async def test_detect_on_empty_file(self, tmp_path: Path) -> None:
        """bom_detect gracefully handles empty files."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_bytes(b"")

        result = await self._run_role("bom_detect", {"file_path": str(empty_file)})

        assert result["has_bom"] is False, (
            "P5 gap: bom_detect must return has_bom=False for empty file"
        )

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        """Run a language collection role via AnsibleRunnerAdapter mock."""
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {
            "collection": "general_ludd.language",
            "role": role_name,
        }
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}


# ── encoding_detect ─────────────────────────────────────────────────────────


class TestEncodingDetectRole:
    """general_ludd.language.encoding_detect — charset encoding detection."""

    @pytest.mark.asyncio
    async def test_detect_utf8(self, tmp_path: Path) -> None:
        """encoding_detect correctly identifies UTF-8 text."""
        utf8_file = tmp_path / "utf8_sample.txt"
        utf8_file.write_text("Hello, world! こんにちは\n", encoding="utf-8")

        result = await self._run_role("encoding_detect", {"file_path": str(utf8_file)})

        assert result.get("encoding", "").lower().replace("-", "") in (
            "utf8", "utf_8", "utf-8", "ascii"
        ), (
            f"P5 gap: encoding_detect must identify UTF-8, got {result.get('encoding')}"
        )
        assert result.get("confidence", 0.0) > 0.5, (
            f"P5 gap: encoding_detect confidence must be >0.5, "
            f"got {result.get('confidence')}"
        )

    @pytest.mark.asyncio
    async def test_detect_latin1(self, tmp_path: Path) -> None:
        """encoding_detect correctly identifies Latin-1 text."""
        latin1_file = tmp_path / "latin1.txt"
        latin1_file.write_bytes(b"H\xe9llo, w\xf6rld!\n")

        result = await self._run_role("encoding_detect", {"file_path": str(latin1_file)})

        assert result.get("encoding") in (
            "ISO-8859-1", "latin1", "latin-1", "windows-1252"
        ), (
            f"P5 gap: encoding_detect must identify Latin-1, "
            f"got {result.get('encoding')}"
        )
        assert result.get("confidence", 0.0) > 0.5

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {"collection": "general_ludd.language", "role": role_name}
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}


# ── font_analyze ────────────────────────────────────────────────────────────


class TestFontAnalyzeRole:
    """general_ludd.language.font_analyze — font file analysis."""

    @pytest.mark.asyncio
    async def test_analyze_font_returns_metadata(self, tmp_path: Path) -> None:
        """font_analyze returns font name, family, and glyph count."""
        font_file = tmp_path / "test.ttf"
        font_file.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 996)

        result = await self._run_role("font_analyze", {"file_path": str(font_file)})

        assert "font_name" in result or "family" in result, (
            f"P5 gap: font_analyze must return font_name or family key, "
            f"got keys {list(result.keys())}"
        )

    @pytest.mark.asyncio
    async def test_analyze_non_font_fails_gracefully(self, tmp_path: Path) -> None:
        """font_analyze handles non-font files without crashing."""
        not_a_font = tmp_path / "not_a_font.txt"
        not_a_font.write_text("This is not a font file.", encoding="utf-8")

        result = await self._run_role("font_analyze", {"file_path": str(not_a_font)})

        assert "error" in result or result.get("skipped"), (
            f"P5 gap: font_analyze must fail gracefully on non-font input, "
            f"got {result}"
        )

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {"collection": "general_ludd.language", "role": role_name}
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}


# ── homoglyph_scan ──────────────────────────────────────────────────────────


class TestHomoglyphScanRole:
    """general_ludd.language.homoglyph_scan — confusable character detection."""

    @pytest.mark.asyncio
    async def test_scan_finds_cyrillic_latin_confusable(self) -> None:
        """homoglyph_scan detects Latin 'a' + Cyrillic U+0430 as confusable."""
        result = await self._run_role(
            "homoglyph_scan",
            {"text": "paypa\u0430l.com"},  # Cyrillic U+0430 instead of Latin 'a'
        )

        assert result.get("confusable_count", 0) > 0, (
            f"P5 gap: homoglyph_scan must detect Cyrillic/Latin confusables, "
            f"count={result.get('confusable_count', 0)}"
        )
        assert "confusables" in result, (
            "P5 gap: homoglyph_scan must list confusable pairs in 'confusables' key"
        )
        assert isinstance(result["confusables"], list)

    @pytest.mark.asyncio
    async def test_scan_clean_text_returns_zero(self) -> None:
        """homoglyph_scan returns zero confusables for clean ASCII text."""
        result = await self._run_role(
            "homoglyph_scan",
            {"text": "hello world this is clean text"},
        )

        assert result.get("confusable_count", -1) == 0, (
            f"P5 gap: clean ASCII text must have 0 confusables, "
            f"got {result.get('confusable_count')}"
        )

    @pytest.mark.asyncio
    async def test_scan_handles_empty_input(self) -> None:
        """homoglyph_scan handles empty string without error."""
        result = await self._run_role("homoglyph_scan", {"text": ""})

        assert result.get("confusable_count", -1) == 0, (
            f"P5 gap: empty input must return 0 confusables, "
            f"got {result.get('confusable_count')}"
        )

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {"collection": "general_ludd.language", "role": role_name}
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}


# ── i18n_extract ────────────────────────────────────────────────────────────


class TestI18nExtractRole:
    """general_ludd.language.i18n_extract — internationalization string extraction."""

    @pytest.mark.asyncio
    async def test_extract_from_python_source(self, tmp_path: Path) -> None:
        """i18n_extract finds gettext _() calls in Python source."""
        py_file = tmp_path / "sample.py"
        py_file.write_text(
            'from gettext import gettext as _\n\n'
            'def hello():\n'
            '    return _("Hello, world!")\n'
            '    return _("Goodbye")\n',
            encoding="utf-8",
        )

        result = await self._run_role("i18n_extract", {"directory": str(tmp_path)})

        assert result.get("string_count", 0) == 2, (
            f"P5 gap: i18n_extract must find 2 _() calls in sample.py, "
            f"got count={result.get('string_count', 0)}"
        )
        assert "strings" in result, (
            "P5 gap: i18n_extract must return extracted strings under 'strings' key"
        )

    @pytest.mark.asyncio
    async def test_extract_from_empty_directory(self, tmp_path: Path) -> None:
        """i18n_extract handles directories with no extractable strings."""
        result = await self._run_role("i18n_extract", {"directory": str(tmp_path)})

        assert result.get("string_count", 0) == 0, (
            "P5 gap: empty directory must return string_count=0"
        )

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {"collection": "general_ludd.language", "role": role_name}
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}


# ── locale_format ───────────────────────────────────────────────────────────


class TestLocaleFormatRole:
    """general_ludd.language.locale_format — locale string normalization."""

    @pytest.mark.asyncio
    async def test_normalize_short_locale(self) -> None:
        """locale_format normalizes 'en_US' to canonical form."""
        result = await self._run_role("locale_format", {"locale": "en_US"})

        assert "language" in result, (
            "P5 gap: locale_format must return 'language' key"
        )
        assert result.get("language") == "en", (
            f"P5 gap: locale_format language must be 'en', got {result.get('language')}"
        )
        assert result.get("territory") == "US", (
            f"P5 gap: locale_format territory must be 'US', got {result.get('territory')}"
        )

    @pytest.mark.asyncio
    async def test_normalize_long_locale(self) -> None:
        """locale_format normalizes 'en_US.UTF-8' to canonical form."""
        result = await self._run_role("locale_format", {"locale": "en_US.UTF-8"})

        assert result.get("language") == "en"
        assert result.get("territory") == "US"
        assert "utf-8" in str(result.get("codeset", "")).lower(), (
            f"P5 gap: locale_format must extract codeset=UTF-8, "
            f"got {result.get('codeset')}"
        )

    @pytest.mark.asyncio
    async def test_normalize_invalid_locale(self) -> None:
        """locale_format handles invalid locale string gracefully."""
        result = await self._run_role("locale_format", {"locale": "not_a_real_locale"})

        assert "error" in result or result.get("language") is not None, (
            f"P5 gap: locale_format must handle invalid input without crashing, "
            f"got {result}"
        )

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {"collection": "general_ludd.language", "role": role_name}
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}


# ── phonetic_transcribe ─────────────────────────────────────────────────────


class TestPhoneticTranscribeRole:
    """general_ludd.language.phonetic_transcribe — IPA phonetic transcription."""

    @pytest.mark.asyncio
    async def test_transcribe_english_to_ipa(self) -> None:
        """phonetic_transcribe produces IPA for English text."""
        result = await self._run_role(
            "phonetic_transcribe", {"text": "hello world", "language": "en"}
        )

        assert "ipa" in result, (
            "P5 gap: phonetic_transcribe must return 'ipa' key with transcription"
        )
        assert len(result["ipa"]) > 0, (
            "P5 gap: IPA transcription must be non-empty for 'hello world'"
        )

    @pytest.mark.asyncio
    async def test_transcribe_whitespace_only(self) -> None:
        """phonetic_transcribe handles whitespace-only input."""
        result = await self._run_role(
            "phonetic_transcribe", {"text": "   ", "language": "en"}
        )

        assert result.get("ipa") == "" or result.get("skipped"), (
            f"P5 gap: whitespace-only input must return empty IPA or skip, "
            f"got {result.get('ipa', 'no ipa key')}"
        )

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {"collection": "general_ludd.language", "role": role_name}
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}


# ── unicode_analyze ─────────────────────────────────────────────────────────


class TestUnicodeAnalyzeRole:
    """general_ludd.language.unicode_analyze — Unicode character analysis."""

    @pytest.mark.asyncio
    async def test_analyze_character_properties(self) -> None:
        """unicode_analyze returns codepoint, name, and category for characters."""
        result = await self._run_role("unicode_analyze", {"text": "A"})

        assert result.get("codepoint") == "U+0041", (
            f"P5 gap: unicode_analyze must return codepoint U+0041 for 'A', "
            f"got {result.get('codepoint')}"
        )
        assert (
            "LATIN CAPITAL LETTER A" in str(result.get("name", "")).upper()
        ), (
            "P5 gap: unicode_analyze name must include 'LATIN CAPITAL LETTER A', "
            f"got {result.get('name')}"
        )
        assert result.get("category") == "Lu", (
            f"P5 gap: category for 'A' must be 'Lu' (uppercase letter), "
            f"got {result.get('category')}"
        )

    @pytest.mark.asyncio
    async def test_analyze_character_count(self) -> None:
        """unicode_analyze handles multi-character text by counting."""
        result = await self._run_role("unicode_analyze", {"text": "\U0001f600hi"})  # 😀hi

        assert result.get("character_count") == 3, (
            f"P5 gap: '😀hi' has 3 characters, got count={result.get('character_count')}"
        )

    @pytest.mark.asyncio
    async def test_analyze_empty_string(self) -> None:
        """unicode_analyze handles empty input."""
        result = await self._run_role("unicode_analyze", {"text": ""})

        assert result.get("character_count", -1) == 0, (
            f"P5 gap: empty text must have character_count=0, "
            f"got {result.get('character_count')}"
        )

    @staticmethod
    async def _run_role(role_name: str, extra_vars: dict) -> dict:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        runner = AnsibleRunnerAdapter()
        task_args = {"collection": "general_ludd.language", "role": role_name}
        task_args.update(extra_vars)
        result = await runner.run_role(task_args)
        return result if isinstance(result, dict) else {"status": str(result)}
