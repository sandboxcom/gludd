from pathlib import Path

import pytest
from scripts.check_hot_reload_fresh import is_stale_content
from scripts.check_test_env_writes import MAX_REPORTED_VIOLATIONS, main, scan_file


def test_e2e_environment_writes_use_fixture(tmp_path: Path) -> None:
    path = tmp_path / "test_sample.py"
    path.write_text(
        "def test_example(monkeypatch):\n"
        "    monkeypatch.setenv('TOKEN', 'value')\n"
    )
    assert scan_file(path) == []


def test_env_write_failure_report_is_bounded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "test_many_writes.py"
    violation_count = MAX_REPORTED_VIOLATIONS + 3
    path.write_text(
        "".join(
            "os." + f"environ['LEAK_{index}'] = 'value'\n"
            for index in range(violation_count)
        )
    )

    assert main(["check_test_env_writes.py", str(path)]) == 1
    output = capsys.readouterr().out

    assert output.count("test_many_writes.py:") == MAX_REPORTED_VIOLATIONS
    assert "3 additional violation(s) omitted" in output
    assert f"{violation_count} violation(s) found." in output


def test_env_write_main_scans_test_and_conftest_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    violation = "os." + "environ['LEAK'] = 'value'\n"
    (nested / "test_example.py").write_text(violation)
    (nested / "conftest.py").write_text(violation)
    (nested / "helper.py").write_text(violation)

    assert main(["check_test_env_writes.py", str(tmp_path)]) == 1
    output = capsys.readouterr().out

    assert "test_example.py:1" in output
    assert "conftest.py:1" in output
    assert "helper.py" not in output
    assert "2 violation(s) found." in output


def test_env_write_main_clean_directory_reports_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "test_clean.py").write_text(
        "def test_clean(monkeypatch):\n"
        "    monkeypatch.setenv('TOKEN', 'value')\n"
    )

    assert main(["check_test_env_writes.py", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "OK: no bare os.environ writes in tests/\n"


def test_hot_reload_checker_ignores_type_pattern_in_string() -> None:
    assert is_stale_content('const pattern = r":\\s*(string|number)\\b";') == []
