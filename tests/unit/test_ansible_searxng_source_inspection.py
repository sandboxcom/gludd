"""Bounded, read-only inspection of the controller's exact SearXNG source."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TARGET = "inspect-ansible-searxng-source"


def _inspect(mode: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", TARGET,
         f"ANSIBLE_SEARXNG_SOURCE_INSPECT_VALIDATE_ONLY={mode}"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=15, check=False,
    )


@pytest.fixture
def curl_env(tmp_path: Path) -> dict[str, str]:
    """A local curl double proves that the read-only plan never accesses HTTP."""
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$GLUDD_INSPECT_CALLS\"\n"
        'case "$*" in *"${GLUDD_INSPECT_FAIL:-never-matches}"*) exit 22;; esac\n'
        'printf \'frozen-source-fixture\\n\'\n', encoding="utf-8",
    )
    curl.chmod(0o700)
    return {
        **os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GLUDD_INSPECT_CALLS": str(tmp_path / "calls"),
    }


def test_source_inspection_plan_never_fetches(curl_env: dict[str, str]) -> None:
    result = _inspect("1", curl_env)
    assert result.returncode == 0, result.stderr
    assert "plan_only=true files=9" in result.stdout
    assert not Path(curl_env["GLUDD_INSPECT_CALLS"]).exists()


def test_source_inspection_reads_only_pinned_bounded_files(
    curl_env: dict[str, str],
) -> None:
    result = _inspect("0", curl_env)
    assert result.returncode == 0, result.stderr
    calls = Path(curl_env["GLUDD_INSPECT_CALLS"]).read_text().splitlines()
    expected = ["setup.py", "searx/__init__.py", "searx/settings_loader.py",
                "searx/version.py", "requirements.txt", "searx/settings_defaults.py",
                "searx/unixthreadname.py", "searx/exceptions.py", "searx/sxng_locales.py"]
    assert len(calls) == len(expected)
    for call, filename in zip(calls, expected, strict=True):
        assert "--connect-timeout 10 --max-time 20" in call
        assert "--proto =https --tlsv1.2" in call
        assert call.endswith(
            "https://raw.githubusercontent.com/searxng/searxng/"
            f"7b4612e86250389dc9d5ee67e4cc2cd64d06602a/{filename}"
        )
        assert f"file={filename}" in result.stdout


def test_source_inspection_stops_after_one_fetch_failure(
    curl_env: dict[str, str],
) -> None:
    result = _inspect("0", {**curl_env, "GLUDD_INSPECT_FAIL": "/setup.py"})
    assert result.returncode != 0
    assert len(Path(curl_env["GLUDD_INSPECT_CALLS"]).read_text().splitlines()) == 1
    assert "file=searx/__init__.py" not in result.stdout


def test_source_inspection_rejects_invalid_mode(curl_env: dict[str, str]) -> None:
    result = _inspect("invalid", curl_env)
    assert result.returncode != 0
    assert "must be 0 or 1" in result.stdout + result.stderr
    assert not Path(curl_env["GLUDD_INSPECT_CALLS"]).exists()
