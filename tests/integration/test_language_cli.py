from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def _gludd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "general_ludd.cli", *list(args)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestLanguageCLIHomoglyphs:
    def test_scan_homoglyphs_help(self) -> None:
        result = _gludd("language", "scan-homoglyphs", "--help")
        assert result.returncode == 0
        assert "--min-severity" in result.stdout

    def test_scan_homoglyphs_empty_input(self) -> None:
        result = _gludd("language", "scan-homoglyphs", "")
        assert result.returncode == 0
        assert "safe" in result.stdout

    def test_scan_homoglyphs_normal_text(self) -> None:
        result = _gludd("language", "scan-homoglyphs", "Hello world")
        assert result.returncode == 0
        assert '"safe"' in result.stdout

    def test_scan_homoglyphs_min_severity_high(self) -> None:
        result = _gludd("language", "scan-homoglyphs", "Hello world", "--min-severity", "high")
        assert result.returncode == 0
        assert "severity_counts" in result.stdout

    def test_scan_homoglyphs_min_severity_critical(self) -> None:
        result = _gludd("language", "scan-homoglyphs", "test", "--min-severity", "critical")
        assert result.returncode == 0
        assert "input_length" in result.stdout

    def test_scan_homoglyphs_invalid_severity_fails(self) -> None:
        result = _gludd("language", "scan-homoglyphs", "test", "--min-severity", "unknown")
        assert result.returncode != 0

    def test_scan_homoglyphs_respects_default_min_severity(self) -> None:
        result = _gludd("language", "scan-homoglyphs", "hello world")
        assert result.returncode == 0
        assert "total_findings" in result.stdout


class TestLanguageCLIEncoding:
    @staticmethod
    def _sample_file(content: str, encoding: str = "utf-8") -> str:
        fd, path = tempfile.mkstemp(suffix=".txt", text=False)
        os.close(fd)
        with open(path, "wb") as f:
            f.write(content.encode(encoding))
        return path

    def test_detect_encoding_help(self) -> None:
        result = _gludd("language", "detect-encoding", "--help")
        assert result.returncode == 0
        assert "encoding" in result.stdout

    def test_detect_encoding_no_args(self) -> None:
        result = _gludd("language", "detect-encoding")
        assert result.returncode != 0

    def test_detect_encoding_nonexistent_file(self) -> None:
        result = _gludd("language", "detect-encoding", "/tmp/nonexistent_file_xyz.abc")
        assert result.returncode != 0

    def test_detect_encoding_valid_file(self) -> None:
        path = self._sample_file("Hello, world!")
        try:
            result = _gludd("language", "detect-encoding", path)
            assert result.returncode == 0
            assert "detected_encoding" in result.stdout
            assert "confidence" in result.stdout
        finally:
            os.unlink(path)

    def test_detect_encoding_with_mojibake(self) -> None:
        path = self._sample_file("Hello, world!")
        try:
            result = _gludd("language", "detect-encoding", path, "--detect-mojibake")
            assert result.returncode == 0
            assert "mojibake" in result.stdout.lower()
        finally:
            os.unlink(path)

    def test_detect_encoding_with_list_encodings(self) -> None:
        path = self._sample_file("Hello, world!")
        try:
            result = _gludd("language", "detect-encoding", path, "--list-encodings")
            assert result.returncode == 0
            assert "supported_encodings" in result.stdout
        finally:
            os.unlink(path)


class TestLanguageCLIBOM:
    @staticmethod
    def _sample_file(path: str, content: str) -> None:
        with open(path, "wb") as f:
            f.write(content.encode("utf-8"))

    @staticmethod
    def _bom_file(path: str) -> None:
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbfHello BOM")

    def test_detect_bom_help(self) -> None:
        result = _gludd("language", "detect-bom", "--help")
        assert result.returncode == 0
        assert "BOM" in result.stdout or "bom" in result.stdout.lower()

    def test_detect_bom_nonexistent_file(self) -> None:
        result = _gludd("language", "detect-bom", "/tmp/nonexistent_bom_test.xyz")
        assert result.returncode != 0

    def test_detect_bom_file_with_bom(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".txt", text=False)
        os.close(fd)
        self._bom_file(path)
        try:
            result = _gludd("language", "detect-bom", path)
            assert result.returncode == 0
            assert "bom_found" in result.stdout
        finally:
            os.unlink(path)

    def test_detect_bom_file_without_bom(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".txt", text=False)
        os.close(fd)
        self._sample_file(path, "No BOM here")
        try:
            result = _gludd("language", "detect-bom", path)
            assert result.returncode == 0
            assert "bom_found" in result.stdout
        finally:
            os.unlink(path)

    def test_detect_bom_strip_flag(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".txt", text=False)
        os.close(fd)
        self._bom_file(path)
        try:
            result = _gludd("language", "detect-bom", path, "--strip")
            assert result.returncode == 0
            assert "bom_found" in result.stdout
        finally:
            os.unlink(path)

    def test_detect_bom_audit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bom_path = os.path.join(tmpdir, "bom_file.txt")
            no_bom_path = os.path.join(tmpdir, "plain_file.txt")
            self._bom_file(bom_path)
            self._sample_file(no_bom_path, "no bom")
            result = _gludd("language", "detect-bom", bom_path, "--audit-directory", tmpdir)
            assert result.returncode == 0
            assert "audit_results" in result.stdout


class TestLanguageCLIPhonetic:
    def test_phonetic_transcribe_help(self) -> None:
        result = _gludd("language", "phonetic-transcribe", "--help")
        assert result.returncode == 0
        assert "--method" in result.stdout

    def test_phonetic_transcribe_arpabet(self) -> None:
        result = _gludd("language", "phonetic-transcribe", "hello world", "--method", "arpabet")
        assert result.returncode == 0
        assert "transcription" in result.stdout

    def test_phonetic_transcribe_ipa(self) -> None:
        result = _gludd("language", "phonetic-transcribe", "test", "--method", "ipa")
        assert result.returncode == 0
        assert "words" in result.stdout

    def test_phonetic_transcribe_soundex(self) -> None:
        result = _gludd("language", "phonetic-transcribe", "example", "--method", "soundex")
        assert result.returncode == 0
        assert "soundex_code" in result.stdout

    def test_phonetic_transcribe_metaphone(self) -> None:
        result = _gludd("language", "phonetic-transcribe", "test", "--method", "metaphone")
        assert result.returncode == 0
        assert "transcription" in result.stdout

    def test_phonetic_transcribe_double_metaphone(self) -> None:
        result = _gludd("language", "phonetic-transcribe", "hello", "--method", "double_metaphone")
        assert result.returncode == 0
        assert "metaphone_codes" in result.stdout


class TestLanguageCLIGroup:
    def test_language_help(self) -> None:
        result = _gludd("language", "--help")
        assert result.returncode == 0
        assert "detect-encoding" in result.stdout
        assert "scan-homoglyphs" in result.stdout
        assert "detect-bom" in result.stdout
        assert "phonetic-transcribe" in result.stdout

    def test_language_no_subcommand_shows_error(self) -> None:
        result = _gludd("language")
        assert result.returncode != 0 or "usage" in result.stderr.lower() or "usage" in result.stdout.lower()

    def test_help_entry_shows_language(self) -> None:
        result = _gludd("--help")
        assert result.returncode == 0
        assert "language" in result.stdout.lower()
