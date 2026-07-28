"""Tests for the one-shot, idempotent test-environment codemod."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_rewrite_file_uses_auto_restored_fixture_and_is_idempotent(
    tmp_path: Path,
) -> None:
    from scripts.fix_test_env_writes import rewrite_file

    target = tmp_path / "test_sample.py"
    source = (
        """\
import os

class TestExample:
    def test_token(self):
        __ENV_WRITE__
        assert os.environ["TOKEN"] == "secret"
"""
    )
    target.write_text(
        source.replace(
            "__ENV_WRITE__",
            'os.' + 'environ["TOKEN"] = "secret"  # test-only value',
        ),
        encoding="utf-8",
    )

    assert rewrite_file(target) == 1
    rewritten = target.read_text(encoding="utf-8")
    assert "def test_token(self, monkeypatch):" in rewritten
    assert 'monkeypatch.setenv("TOKEN", "secret")  # test-only value' in rewritten
    assert rewrite_file(target) == 0
    assert target.read_text(encoding="utf-8") == rewritten


def test_rewrite_file_reuses_existing_fixture(tmp_path: Path) -> None:
    from scripts.fix_test_env_writes import rewrite_file

    target = tmp_path / "test_existing.py"
    source = (
        """\
import os

def test_token(monkeypatch: object):
    __ENV_WRITE__
"""
    )
    target.write_text(
        source.replace("__ENV_WRITE__", 'os.' + 'environ["TOKEN"] = value'),
        encoding="utf-8",
    )

    assert rewrite_file(target) == 1
    rewritten = target.read_text(encoding="utf-8")
    assert rewritten.count("monkeypatch") == 2
    assert 'monkeypatch.setenv("TOKEN", value)' in rewritten


def test_rewrite_file_rejects_module_level_environment_mutation(
    tmp_path: Path,
) -> None:
    from scripts.fix_test_env_writes import CodemodError, rewrite_file

    target = tmp_path / "test_module_write.py"
    target.write_text(
        "import os\n" + "os." + 'environ["TOKEN"] = "secret"\n',
        encoding="utf-8",
    )

    with pytest.raises(CodemodError, match="inside a test function"):
        rewrite_file(target)
