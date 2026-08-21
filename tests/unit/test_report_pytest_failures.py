"""Tests for the bounded, read-only prior-failure reporter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_missing_cache_is_an_observable_empty_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.report_pytest_failures import main

    cache = tmp_path / "missing-lastfailed"
    assert main(["--cache", str(cache), "--limit", "5"]) == 0
    output = capsys.readouterr().out
    assert "test-failures: reading prior-failure cache" in output
    assert "no prior failures recorded" in output


def test_report_is_sorted_bounded_and_does_not_modify_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.report_pytest_failures import main

    cache = tmp_path / "lastfailed"
    cache.write_text(
        json.dumps(
            {
                "tests/test_z.py::test_z": True,
                "tests/test_a.py::test_a": True,
                "tests/test_passed.py::test_ok": False,
                "tests/test_m.py::test_m": True,
            }
        ),
        encoding="utf-8",
    )
    before = cache.read_bytes()

    assert main(["--cache", str(cache), "--limit", "2"]) == 0

    output = capsys.readouterr().out
    assert "showing 2 of 3 prior failures" in output
    assert "tests/test_a.py::test_a" in output
    assert "tests/test_m.py::test_m" in output
    assert "tests/test_z.py::test_z" not in output
    assert cache.read_bytes() == before


def test_malformed_cache_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.report_pytest_failures import main

    cache = tmp_path / "lastfailed"
    cache.write_text("not-json", encoding="utf-8")

    assert main(["--cache", str(cache), "--limit", "5"]) == 2
    error = capsys.readouterr().err
    assert "invalid prior-failure cache" in error


def test_non_object_cache_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.report_pytest_failures import main

    cache = tmp_path / "lastfailed"
    cache.write_text("[]", encoding="utf-8")

    assert main(["--cache", str(cache), "--limit", "5"]) == 2
    error = capsys.readouterr().err
    assert "must contain a JSON object" in error


def test_invalid_entry_schema_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.report_pytest_failures import main

    cache = tmp_path / "lastfailed"
    cache.write_text('{"tests/test_a.py::test_a": "yes"}', encoding="utf-8")

    assert main(["--cache", str(cache), "--limit", "5"]) == 2
    assert "must map node IDs to booleans" in capsys.readouterr().err


def test_oversized_cache_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.report_pytest_failures import MAX_CACHE_BYTES, main

    cache = tmp_path / "lastfailed"
    cache.write_bytes(b" " * (MAX_CACHE_BYTES + 1))

    assert main(["--cache", str(cache), "--limit", "5"]) == 2
    assert "cache exceeds" in capsys.readouterr().err


def test_limit_is_positive_and_capped(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.report_pytest_failures import main

    assert main(["--limit", "0"]) == 2
    assert "between 1 and 200" in capsys.readouterr().err
    assert main(["--limit", "201"]) == 2
    assert "between 1 and 200" in capsys.readouterr().err
