"""Exercise the real observed-command recipe with ordinary quoted Make arguments."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make") or "make"


def _recipe_fixture(tmp_path: Path) -> Path:
    source = (ROOT / "make/10-observability-and-tests.mk").read_text()
    recipe = source.split("\nrun-watched:", 1)[1].split("\n\n", 1)[0]
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    shim = tmp_path / "uv_fixture.py"
    shim.write_text(
        "import os, sys\n"
        "assert sys.argv[1:3] == ['run', 'python']\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[3:]])\n"
    )
    (tmp_path / "child.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path('arguments.json').write_text(json.dumps(sys.argv[1:]))\n"
        "print('child arguments recorded', flush=True)\n"
        "sys.exit(int(os.environ.get('FIXTURE_CHILD_EXIT', '0')))\n"
    )
    child_makefile = tmp_path / "child.mk"
    child_makefile.write_text(
        "child:\n\t@" + shlex.quote(sys.executable) + ' child.py "$(TESTFILES)" "$(PYTEST_ARGS)"\n'
    )
    recipe_file = tmp_path / "recipe.mk"
    recipe_file.write_text(
        "UV := " + shlex.quote(sys.executable) + " " + shlex.quote(str(shim)) + "\nrun-watched:" + recipe + "\n"
    )
    return recipe_file


def _run_recipe(tmp_path: Path, command: str, *, child_exit: int = 0) -> subprocess.CompletedProcess[str]:
    recipe_file = _recipe_fixture(tmp_path)
    environment = os.environ.copy()
    for variable in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
        environment.pop(variable, None)
    environment["FIXTURE_CHILD_EXIT"] = str(child_exit)
    return subprocess.run(
        [
            MAKE,
            "--no-print-directory",
            "-f",
            str(recipe_file),
            "run-watched",
            f"CMD={command}",
            "OBSERVED_LABEL=make-helper",
            "RUN_ID=fixture",
            "STALL_SECS=30",
            "MAX_SECS=60",
            f"LOG={tmp_path / 'child.log'}",
            f"OBSERVED_ROOT={tmp_path / 'observed'}",
            "OBSERVED_HEARTBEAT_SECS=1",
            "OBSERVED_RETAIN_RUNS=2",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def _status(tmp_path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((tmp_path / "observed/make-helper/fixture.json").read_text()))


@pytest.mark.parametrize("quote", ["'", '"'])
def test_real_recipe_preserves_quoted_make_arguments_without_shell_diagnostics(
    tmp_path: Path,
    quote: str,
) -> None:
    command = (
        f"{shlex.quote(MAKE)} --no-print-directory -f child.mk child "
        f"TESTFILES={quote}tests/unit/first.py tests/unit/second.py{quote} "
        f"PYTEST_ARGS={quote}-W error -q --tb=short{quote}"
    )
    result = _run_recipe(tmp_path, command)

    assert result.returncode == 0, result.stdout + result.stderr
    prompts = [line for line in result.stderr.splitlines() if line.startswith("AGENT_NEXT target=run-watched ")]
    assert len(prompts) == 1, result.stdout + result.stderr
    assert "outcome=passed " in prompts[0] and "native_status=0 " in prompts[0]
    assert [line for line in result.stderr.splitlines() if line not in prompts] == [], result.stderr
    assert json.loads((tmp_path / "arguments.json").read_text()) == [
        "tests/unit/first.py tests/unit/second.py",
        "-W error -q --tb=short",
    ]
    assert _status(tmp_path)["state"] == "passed"
    assert _status(tmp_path)["exit_code"] == 0


@pytest.mark.parametrize("command", ["", " ", "\t"])
def test_real_recipe_rejects_empty_commands_before_starting_observer(
    tmp_path: Path,
    command: str,
) -> None:
    result = _run_recipe(tmp_path, command)
    assert result.returncode != 0
    assert "Usage: make run-watched" in result.stdout
    assert not (tmp_path / "arguments.json").exists()
    assert not (tmp_path / "observed").exists()


@pytest.mark.parametrize("child_exit", [7, 143])
def test_real_recipe_preserves_failed_child_exit_in_observed_receipt(
    tmp_path: Path,
    child_exit: int,
) -> None:
    result = _run_recipe(tmp_path, f"{shlex.quote(sys.executable)} child.py", child_exit=child_exit)
    # GNU Make reports recipe failure as 2; the observer retains the real child code.
    assert result.returncode == 2
    assert _status(tmp_path)["state"] != "passed"
    assert _status(tmp_path)["exit_code"] == child_exit
    assert "too many arguments" not in result.stdout + result.stderr
