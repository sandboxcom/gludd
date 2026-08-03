"""Language CLI removed from core — collection only.

The ``gludd language`` subcommand was removed from core in favour of the
language expert collection at collections/ansible_collections/general_ludd/language/.
"""

from __future__ import annotations

import subprocess
import sys


def _gludd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "general_ludd.cli", *list(args)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestLanguageCLIRemoved:
    def test_language_command_removed(self) -> None:
        result = _gludd("language", "--help")
        assert result.returncode != 0

    def test_language_not_in_top_level_help(self) -> None:
        result = _gludd("--help")
        assert result.returncode == 0
        assert "language" not in result.stdout.lower()

    def test_all_language_subcommands_removed(self) -> None:
        for subcommand in (
            "detect-encoding",
            "scan-homoglyphs",
            "detect-bom",
            "phonetic-transcribe",
            "unicode-analyze",
            "locale-format",
            "i18n-extract",
            "font-analyze",
            "analyze-text",
        ):
            result = _gludd(subcommand)
            assert result.returncode != 0, f"{subcommand} should not be available"
