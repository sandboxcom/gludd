"""Real mypy must check the active worktree before inherited or installed sources."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from scripts.makefile_layout import makefile_sources

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class MypySourceFixture:
    root: Path
    decoy: Path
    carrier: Path
    uv: str
    make: str
    environment: dict[str, str] = field(repr=False)

    def invoke(
        self, goal: str, *, early_environment: bool = False, extra_makefile: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.make, "--no-print-directory"]
        if early_environment:
            command.append("-e")
        command.extend(("-f", str(self.root / "Makefile")))
        if extra_makefile is not None:
            command.extend(("-f", str(extra_makefile)))
        command.extend((
            goal, "UV=" + shlex.quote(self.uv),
            "PYTHON=" + shlex.quote(sys.executable),
            "SYSTEM_PYTHON=" + shlex.quote(sys.executable),
        ))
        return subprocess.run(
            command, cwd=self.root, env=dict(self.environment), capture_output=True,
            text=True, check=False, timeout=45,
        )


@pytest.fixture
def mypy_source_fixture(tmp_path: Path) -> MypySourceFixture:
    root = (tmp_path / "active worktree").resolve()
    root.mkdir()
    for source in makefile_sources(ROOT / "Makefile"):
        destination = root / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    files = {
        "pyproject.toml": (
            '[project]\nname = "gludd-owned-mypy-source"\nversion = "0.0.0"\n'
            'requires-python = ">=3.11"\n\n[tool.mypy]\n'
            'python_version = "3.11"\nstrict = true\n'
            'explicit_package_bases = true\nmypy_path = "typings"\n'
        ),
        "src/general_ludd/__init__.py": "",
        "src/general_ludd/current_source.py": (
            "from local_mypy_helper import local_value\n"
            "from retained_mypy_carrier import retained_value\n"
            "from owned_mypy_stub import stub_value\n"
            "CURRENT_SOURCE: int = local_value + retained_value + stub_value\n"
        ),
        "scripts/local_mypy_helper.py": "local_value: int = 11\n",
        "typings/owned_mypy_stub.pyi": "stub_value: int\n",
        "config/mypy-tests.toml": (ROOT / "config/mypy-tests.toml").read_text(encoding="utf-8"),
        "tests/unit/test_config_gaps.py": (
            "from general_ludd.current_source import CURRENT_SOURCE\n"
            "TEST_SOURCE: int = CURRENT_SOURCE\n"
        ),
    }
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    decoy = (tmp_path / "outside owned decoy").resolve()
    package = decoy / "general_ludd"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "wrong_checkout.py").write_text('DECOY_SOURCE: int = "wrong checkout"\n', encoding="utf-8")
    carrier = (tmp_path / "retained unrelated carrier").resolve()
    carrier.mkdir()
    (carrier / "retained_mypy_carrier.py").write_text("retained_value: int = 17\n", encoding="utf-8")

    real_uv = shutil.which("uv")
    real_make = shutil.which("make")
    assert real_uv is not None and real_make is not None
    environment = dict(os.environ)
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "PYTEST_ARGS"):
        environment.pop(name, None)
    environment.update({
        "MYPYPATH": str(decoy) + os.pathsep + str(carrier),
        "UV_PROJECT_ENVIRONMENT": sys.prefix, "VIRTUAL_ENV": sys.prefix,
        "UV_NO_SYNC": "1", "UV_PYTHON": sys.executable, "UV_PYTHON_DOWNLOADS": "never",
    })
    assert not decoy.is_relative_to(root) and not carrier.is_relative_to(root)
    return MypySourceFixture(root, decoy, carrier, real_uv, real_make, environment)


def test_real_unbound_mypy_witness_selects_owned_outside_decoy(
    mypy_source_fixture: MypySourceFixture,
) -> None:
    fixture = mypy_source_fixture
    witness = fixture.root / "owned-unbound-mypy.mk"
    witness.write_text(
        ".PHONY: owned-unbound-mypy\nowned-unbound-mypy:\n"
        '\t@$(UV) run mypy --cache-dir="$(MYPY_NULL_CACHE)" -p general_ludd\n',
        encoding="utf-8",
    )
    result = fixture.invoke("owned-unbound-mypy", extra_makefile=witness)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert str(fixture.decoy / "general_ludd/wrong_checkout.py") in output
    assert "Incompatible types" in output
    assert "current_source.py" not in output


@pytest.mark.parametrize("mypy_worktree_goal", ("typecheck", "_precommit-mypy"))
@pytest.mark.parametrize("mypy_worktree_early_environment", (False, True))
def test_real_package_mypy_binds_current_source_scripts_stubs_and_retained_carrier(
    mypy_source_fixture: MypySourceFixture, mypy_worktree_goal: str,
    mypy_worktree_early_environment: bool,
) -> None:
    fixture = mypy_source_fixture
    before = dict(fixture.environment)
    result = fixture.invoke(mypy_worktree_goal, early_environment=mypy_worktree_early_environment)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Success: no issues found" in output
    assert f"AGENT_NEXT target={mypy_worktree_goal} outcome=passed" in result.stderr
    assert "gate_evidence=false" in result.stderr and "release_ready=false" in result.stderr
    assert "wrong_checkout.py" not in output
    assert fixture.environment == before
    if mypy_worktree_goal == "_precommit-mypy":
        assert not (fixture.root / ".mypy_cache").exists()


@pytest.mark.parametrize("mypy_worktree_goal", ("typecheck", "_precommit-mypy"))
@pytest.mark.parametrize("mypy_worktree_early_environment", (False, True))
def test_real_package_mypy_checks_every_current_module_and_keeps_failure_status(
    mypy_source_fixture: MypySourceFixture, mypy_worktree_goal: str,
    mypy_worktree_early_environment: bool,
) -> None:
    fixture = mypy_source_fixture
    broken = fixture.root / "src/general_ludd/otherwise_unimported.py"
    broken.write_text(
        'def required_full_package_check() -> int:\n    return "current-source error"\n', encoding="utf-8",
    )
    result = fixture.invoke(mypy_worktree_goal, early_environment=mypy_worktree_early_environment)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "otherwise_unimported.py" in output
    assert "Incompatible return value type" in output
    assert "wrong_checkout.py" not in output
    assert f"AGENT_NEXT target={mypy_worktree_goal} outcome=failed" in result.stderr
    assert "native_status=1" in result.stderr and "phase=package" in result.stderr
    assert "outcome=passed" not in result.stderr


@pytest.mark.parametrize("mypy_worktree_early_environment", (False, True))
def test_typecheck_preserves_configured_test_child_and_its_failure_status(
    mypy_source_fixture: MypySourceFixture, mypy_worktree_early_environment: bool,
) -> None:
    fixture = mypy_source_fixture
    (fixture.root / "tests/unit/test_config_gaps.py").write_text(
        'TEST_SOURCE: int = "configured test error"\n', encoding="utf-8",
    )
    result = fixture.invoke("typecheck", early_environment=mypy_worktree_early_environment)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "test_config_gaps.py" in output
    assert "Incompatible types" in output
    assert "wrong_checkout.py" not in output
    assert "AGENT_NEXT target=typecheck outcome=failed" in result.stderr
    assert "native_status=1" in result.stderr and "phase=configured-test" in result.stderr
    assert "outcome=passed" not in result.stderr


@pytest.mark.parametrize("mypy_curdir_goal", ("typecheck", "_precommit-mypy"))
@pytest.mark.parametrize("mypy_curdir_origin", ("command-line", "environment", "early-environment"))
def test_physical_mypy_source_ignores_overridden_make_curdir(
    mypy_source_fixture: MypySourceFixture, mypy_curdir_goal: str,
    mypy_curdir_origin: str,
) -> None:
    fixture = mypy_source_fixture
    active_error = fixture.root / "src/general_ludd/otherwise_unimported.py"
    active_error.write_text(
        'def required_active_source() -> int:\n    return "active-source error"\n', encoding="utf-8",
    )
    healthy = fixture.decoy / "src/general_ludd"
    healthy.mkdir(parents=True)
    (healthy / "__init__.py").write_text("", encoding="utf-8")
    (healthy / "current_source.py").write_text("CURRENT_SOURCE: int = 23\n", encoding="utf-8")
    (fixture.decoy / "scripts").mkdir()
    origin_file = fixture.root / "owned-curdir-origin.mk"
    origin_file.write_text(
        ".PHONY: owned-curdir-origin\n"
        f"{mypy_curdir_goal}: owned-curdir-origin\n"
        "owned-curdir-origin:\n"
        "\t@printf 'OWNED_CURDIR_ORIGIN=%s\\n' '$(origin CURDIR)'\n",
        encoding="utf-8",
    )
    command = [fixture.make, "--no-print-directory"]
    child_environment = dict(fixture.environment)
    if mypy_curdir_origin == "early-environment":
        command.append("-e")
    if mypy_curdir_origin != "command-line":
        child_environment["CURDIR"] = str(fixture.decoy)
    command.extend(("-f", str(fixture.root / "Makefile"), "-f", str(origin_file)))
    command.extend((mypy_curdir_goal, "UV=" + shlex.quote(fixture.uv),
                    "PYTHON=" + shlex.quote(sys.executable),
                    "SYSTEM_PYTHON=" + shlex.quote(sys.executable)))
    if mypy_curdir_origin == "command-line":
        command.append("CURDIR=" + str(fixture.decoy))
    result = subprocess.run(
        command, cwd=fixture.root, env=child_environment, capture_output=True,
        text=True, check=False, timeout=45,
    )
    expected_origin = {
        "command-line": "command line", "environment": "file",
        "early-environment": "environment override",
    }[mypy_curdir_origin]
    assert f"OWNED_CURDIR_ORIGIN={expected_origin}" in result.stdout
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "otherwise_unimported.py" in output
    assert "Incompatible return value type" in output
    assert f"AGENT_NEXT target={mypy_curdir_goal} outcome=failed" in result.stderr
    assert "native_status=1" in result.stderr and "phase=package" in result.stderr
    assert "outcome=passed" not in result.stderr
