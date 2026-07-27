"""Regression guard for the real-binary OpenCode boot probe."""

from pathlib import Path

BOOT_TEST = Path(__file__).resolve().parents[1] / "e2e" / "test_opencode_binary_boot.py"


def test_binary_boot_probe_uses_serve_not_known_broken_run_mode() -> None:
    content = BOOT_TEST.read_text(encoding="utf-8")

    assert 'OPENCODE_BIN,\n        "serve",' in content
    assert 'OPENCODE_BIN, "run"' not in content


def test_binary_boot_captures_one_module_scoped_boot_result() -> None:
    content = BOOT_TEST.read_text(encoding="utf-8")

    assert 'scope="module"' in content
    assert "xdist_group" in content
