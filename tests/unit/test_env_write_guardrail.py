from pathlib import Path

from scripts.check_hot_reload_fresh import is_stale_content
from scripts.check_test_env_writes import scan_file


def test_e2e_environment_writes_use_fixture(tmp_path: Path) -> None:
    path = tmp_path / "test_sample.py"
    path.write_text(
        "def test_example(monkeypatch):\n"
        "    monkeypatch.setenv('TOKEN', 'value')\n"
    )
    assert scan_file(path) == []


def test_hot_reload_checker_ignores_type_pattern_in_string() -> None:
    assert is_stale_content('const pattern = r":\\s*(string|number)\\b";') == []
