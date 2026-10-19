"""Pin narrow native secret admission and non-destructive index checkpointing."""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _make(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_scoped_secrets_target_reuses_readonly_native_owner() -> None:
    """Keep both explicit input paths and the canonical baseline unmodified."""
    result = _make(
        "-n", "check-secrets-files",
        "SECRETS_FILES=tests/unit/test_ansible_ee_searx_acceptance.py scripts/check_clean_tree.py",
        "SECRETS_BASELINE_FILE=.secrets.baseline",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/detect_secrets_readonly.py" in result.stdout
    assert '--baseline ".secrets.baseline" --' in result.stdout
    assert '"tests/unit/test_ansible_ee_searx_acceptance.py"' in result.stdout
    assert '"scripts/check_clean_tree.py"' in result.stdout


def test_unstage_target_preserves_worktree_content() -> None:
    """Require the native index-only operation and exact explicit paths."""
    result = _make("-n", "git-unstage-files", "FILES=docs/features/RELEASE_PROOF_THROUGHPUT.md")
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'git restore --staged -- "docs/features/RELEASE_PROOF_THROUGHPUT.md"' in result.stdout
    assert "--worktree" not in result.stdout
    assert "git reset" not in result.stdout


def test_normal_content_admission_requires_native_staged_secrets_hook() -> None:
    """Catch secret failures before repeated collection and push work."""
    source = (ROOT / "make/40-cross-version-and-worktrees.mk").read_text(encoding="utf-8")
    prerequisites = source.split("_pre-commit-content-guard:", 1)[1].splitlines()[0]
    assert "_pre-commit-secrets-guard" in prerequisites


def test_staged_secrets_guard_uses_native_precommit_selection() -> None:
    """Preserve the configured hook's native file filtering and baseline policy."""
    result = _make("-n", "_pre-commit-secrets-guard")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pre-commit run detect-secrets-readonly" in result.stdout
    assert "--all-files" not in result.stdout
    assert "--no-verify" not in result.stdout


@pytest.mark.parametrize(
    ("target", "variable", "message"),
    [
        ("check-secrets-files", "SECRETS_FILES", "SECRETS_FILES must name explicit files"),
        ("git-unstage-files", "FILES", "FILES must name explicit files"),
    ],
)
def test_scoped_targets_refuse_empty_scope(target: str, variable: str, message: str) -> None:
    """Do not silently scan or unstage the whole project."""
    result = _make(target, f"{variable}=")
    assert result.returncode != 0
    assert message in result.stdout + result.stderr


def test_native_scoped_scan_rejects_a_new_finding_without_mutating_baseline(tmp_path: Path) -> None:
    """Run the maintained scanner against a generated non-credential fixture."""
    baseline = ROOT / ".secrets.baseline"
    original = baseline.read_bytes()
    fixture = tmp_path / "keyword.py"
    fixture.write_text(f"password = {secrets.token_hex(32)!r}\n", encoding="utf-8")
    result = _make(
        "check-secrets-files", f"SECRETS_FILES={fixture}",
        "SECRETS_BASELINE_FILE=.secrets.baseline",
    )
    assert result.returncode != 0
    assert "Potential secrets" in result.stdout + result.stderr
    assert baseline.read_bytes() == original


def test_nested_test_files_uses_declared_environment_without_uv_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run real nested UV/pytest without inheriting an unrelated active env."""
    probe = tmp_path / "test_runtime_identity.py"
    probe.write_text(
        "import sys\n"
        "def test_runtime_identity():\n"
        f"    assert sys.prefix == {sys.prefix!r}\n"
        f"    assert sys.version_info[:2] == {sys.version_info[:2]!r}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "unrelated-active-environment"))
    result = _make(
        "test-files", f"TESTFILES={probe}", "PYTEST_ARGS=-q",
        "NPROC=1", "GLUDD_XDIST_WORKERS=1", f"UV_PROJECT_ENVIRONMENT={sys.prefix}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not match the project environment" not in result.stdout + result.stderr
    assert "will be ignored" not in result.stdout + result.stderr


def _native_cprofile_probe(
    tmp_path: Path, source: str, *, arguments: str = "-q -W error -n 0",
) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "test_native_profile_probe.py"
    probe.write_text(source, encoding="utf-8")
    return _make(
        "test-files-cprofile", f"TESTFILES={probe}",
        f"PYTEST_ARGS={arguments}", "NPROC=1", "GLUDD_XDIST_WORKERS=1",
        f"UV_PROJECT_ENVIRONMENT={sys.prefix}",
    )


def test_native_cprofile_alias_publishes_real_full_path_stats(tmp_path: Path) -> None:
    import pstats

    result = _native_cprofile_probe(
        tmp_path,
        "def profiled_native_call():\n    for _ in range(100000):\n        value = sum(range(100))\n    return value\n"
        "def test_original_pass():\n    assert profiled_native_call() == 4950\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    prefix = "pytest-cprofile: directory="
    assert prefix in result.stdout, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith(prefix))
    directory = Path(line.removeprefix(prefix))
    assert directory.parent == ROOT / ".gate-logs" / "test-files-cprofile"
    stats = pstats.Stats(str(directory / "pytest.pstats"))
    functions = stats.get_stats_profile().func_profiles
    assert "profiled_native_call" in functions
    assert Path(functions["profiled_native_call"].file_name) == tmp_path / "test_native_profile_probe.py"
    report = (directory / "report.txt").read_text(encoding="utf-8")
    assert "CUMULATIVE" in report and "TIME" in report
    assert str(tmp_path / "test_native_profile_probe.py") in report
    assert "pytest_exitstatus=0" in report
    assert (directory / "pytest.pstats").stat().st_size <= 16 * 1024 * 1024
    assert (directory / "report.txt").stat().st_size <= 1024 * 1024


@pytest.mark.parametrize(
    ("source", "arguments", "expected", "exitstatus"),
    [
        ("def test_original_failure():\n    assert False, 'native-profile-assertion'\n",
         "-q -W error -n 0", "native-profile-assertion", 1),
        ("import warnings\ndef test_original_warning():\n"
         "    warnings.warn('native-profile-warning', UserWarning)\n",
         "-q -W error -n 0", "UserWarning: native-profile-warning", 1),
        ("def test_broken(:\n    pass\n", "-q -W error -n 0", "SyntaxError", 2),
        ("def test_pass():\n    pass\n", "-q -W error -n 0 --native-profile-unknown", "unrecognized arguments", 4),
        ("VALUE = 'no native tests'\n", "-q -W error -n 0", "no tests ran", 5),
        ("def test_interrupt():\n    raise KeyboardInterrupt\n",
         "-q -W error -n 0", "KeyboardInterrupt", 2),
    ],
)
def test_native_cprofile_keeps_real_pytest_failure_nonzero(
    tmp_path: Path, source: str, arguments: str, expected: str, exitstatus: int,
) -> None:
    result = _native_cprofile_probe(tmp_path, source, arguments=arguments)
    assert result.returncode != 0, result.stdout + result.stderr
    assert expected in result.stdout + result.stderr
    assert f"Error {exitstatus}" in result.stderr


@pytest.mark.parametrize("carrier", ["command-line", "environment"])
@pytest.mark.parametrize("value", ["1", "0", "literal"])
def test_native_cprofile_refuses_external_internal_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, carrier: str, value: str,
) -> None:
    tripwire = tmp_path / "expanded-internal-mode"
    literal = f"$(file >{tripwire},unexpected)" if value == "literal" else value
    if carrier == "environment":
        monkeypatch.setenv("_TEST_FILES_CPROFILE", literal)
        arguments: tuple[str, ...] = ()
    else:
        arguments = (f"_TEST_FILES_CPROFILE={literal}",)
    result = _make("-n", "test-files-cprofile", "TESTFILES=unused.py", *arguments)
    assert result.returncode != 0
    assert "_TEST_FILES_CPROFILE is internal" in result.stderr
    assert not tripwire.exists()
    assert "python -m pytest" not in result.stdout


def test_native_cprofile_off_preserves_original_recipe() -> None:
    ordinary = _make("-n", "test-files", "TESTFILES=fixture.py", "PYTEST_ARGS=-q -n 0")
    profiled = _make("-n", "test-files-cprofile", "TESTFILES=fixture.py", "PYTEST_ARGS=-q -n 0")
    assert ordinary.returncode == profiled.returncode == 0
    assert "_TEST_FILES_CPROFILE=1" not in ordinary.stdout
    assert "-p scripts.xdist_trace_plugin" not in ordinary.stdout
    assert "run python -m pytest fixture.py" in ordinary.stdout
    assert profiled.stdout.replace("_TEST_FILES_CPROFILE=1 ", "").replace(
        " -p scripts.xdist_trace_plugin", "",
    ) == ordinary.stdout


def test_native_cprofile_request_is_consumed_before_collection_and_nested_make(tmp_path: Path) -> None:
    child = tmp_path / "test_profile_child.py"
    child.write_text(
        "import os, sys\n"
        "assert '_TEST_FILES_CPROFILE' not in os.environ\n"
        "def test_unprofiled_child():\n"
        "    assert sys.getprofile() is None\n",
        encoding="utf-8",
    )
    source = (
        "import os, subprocess, sys\n"
        "assert '_TEST_FILES_CPROFILE' not in os.environ\n"
        "def test_nested_native_make():\n"
        "    assert sys.getprofile() is not None\n"
        f"    result = subprocess.run(['make', '--no-print-directory', 'test-files', 'TESTFILES={child}', "
        "'PYTEST_ARGS=-q -W error -n 0', 'NPROC=1', 'GLUDD_XDIST_WORKERS=1', "
        f"'UV_PROJECT_ENVIRONMENT={sys.prefix}'], cwd={str(ROOT)!r}, "
        "capture_output=True, text=True, timeout=10, check=False)\n"
        "    assert result.returncode == 0, result.stdout + result.stderr\n"
        "    assert 'pytest-cprofile: directory=' not in result.stdout\n"
    )
    result = _native_cprofile_probe(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("pytest-cprofile: directory=") == 1


def test_native_cprofile_does_not_profile_xdist_workers(tmp_path: Path) -> None:
    result = _native_cprofile_probe(
        tmp_path,
        "import os, sys\n"
        "assert '_TEST_FILES_CPROFILE' not in os.environ\n"
        "def test_native_worker():\n"
        "    assert os.environ.get('PYTEST_XDIST_WORKER', '').startswith('gw')\n"
        "    assert sys.getprofile() is None\n",
        arguments="-q -W error -n 2",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("pytest-cprofile: directory=") == 1
