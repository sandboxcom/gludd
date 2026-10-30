"""Native duration reporting preserves compact admission outcomes and phase meaning."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from tests.unit.test_runtime_admission_scheduling import ROOT, _capture_owner, _native_test_files

_DURATION_FLAGS = ("--durations=20", "--durations-min=0")


@pytest.mark.parametrize("mode", ("success", "assertion", "warning", "setup", "import"))
def test_duration_report_preserves_native_two_worker_outcomes(tmp_path: Path, mode: str) -> None:
    """Compare actual admission flags against the same native invocation without reporting."""
    capture = tmp_path / "capture"
    capture.mkdir()
    captured, rows = _capture_owner(capture, "")
    assert captured.returncode == 0, captured.stdout + captured.stderr
    main_flags = shlex.split(str(rows[0]["flags"]))
    native_prefix = ["-W", "error", "-q", "-n", "2", "--maxprocesses", "2"]
    assert main_flags[:len(native_prefix)] == native_prefix
    flags = main_flags[len(native_prefix):]
    baseline = [flag for flag in flags if flag not in _DURATION_FLAGS]
    probe = tmp_path / "test_duration_native.py"
    (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        "def pytest_configure(config):\n"
        "    physical = Path(__file__).parent.resolve()\n"
        "    assert config.rootpath == physical\n"
        "    assert Path(config.getoption('confcutdir')).resolve() == physical\n"
        f"    assert Path(config.inipath) == Path({str(ROOT / 'pyproject.toml')!r})\n"
        "@pytest.fixture\n"
        "def duration_resource(request):\n"
        f"    if {mode!r} == 'setup' and request.node.name == 'test_native_outcome':\n"
        "        raise RuntimeError('duration-native-setup')\n"
        "    return 'fixture-ready'\n",
        encoding="utf-8",
    )
    probe.write_text(
        "import warnings\n"
        f"if {mode!r} == 'import': raise RuntimeError('duration-native-import')\n"
        "def test_native_success(duration_resource, worker_id, base_url):\n"
        "    assert duration_resource == 'fixture-ready'\n"
        "    assert worker_id.startswith('gw')\n"
        "    assert base_url is None or isinstance(base_url, str)\n"
        "def test_native_outcome(duration_resource):\n"
        "    assert duration_resource == 'fixture-ready'\n"
        f"    if {mode!r} == 'assertion': assert 1 == 2, 'duration-native-assertion'\n"
        f"    if {mode!r} == 'warning': warnings.warn('duration-native-warning', UserWarning)\n",
        encoding="utf-8",
    )
    physical = tmp_path.resolve()
    child_configuration = [
        "-c", str(ROOT / "pyproject.toml"),
        f"--rootdir={physical}", f"--confcutdir={physical}",
    ]
    results = [
        _native_test_files([probe], 2, extra_pytest_args=shlex.join([*child_configuration, *options]))
        for options in (baseline, flags)
    ]
    assert results[0].returncode == results[1].returncode
    expected = {
        "success": ("2 passed",),
        "assertion": ("1 failed", "1 passed"),
        "warning": ("1 failed", "1 passed"),
        "setup": ("1 passed", "1 error"),
        "import": ("2 errors",),
    }[mode]
    for result in results:
        assert (result.returncode == 0) == (mode == "success"), result.stdout + result.stderr
        assert "2 workers" in result.stdout, result.stdout + result.stderr
        for count in expected:
            assert re.search(rf"\b{count}\b", result.stdout), result.stdout + result.stderr
        if mode != "success":
            assert f"duration-native-{mode}" in result.stdout, result.stdout + result.stderr
        if result.returncode:
            assert re.fullmatch(
                r"make(?:\[\d+\])?: \*\*\* \[[^\n]*test-files\] Error [12]\n",
                result.stderr,
            ), result.stderr
        else:
            assert not result.stderr, result.stderr
    assert "slowest 20 durations" not in results[0].stdout
    if mode == "import":
        assert "slowest 20 durations" not in results[1].stdout
        return
    assert "slowest 20 durations" in results[1].stdout, results[1].stdout + results[1].stderr
    report = results[1].stdout.split("slowest 20 durations", 1)[1]
    phase_rows = re.findall(r"^\d+\.\d+s\s+(setup|call|teardown)\s+(.+)$", report, re.MULTILINE)
    assert {phase for phase, _node in phase_rows} == {"setup", "call", "teardown"}
    assert all(node.endswith(("::test_native_success", "::test_native_outcome")) for _phase, node in phase_rows)
    assert any(phase == "call" and node.endswith("::test_native_success") for phase, node in phase_rows)
