"""Behavioral contract for the repository-owned Markdown lint target."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

ROOT = Path(__file__).resolve().parents[2]
FEATURE_DOC = "docs/features/XMSS_BACKEND_SAFETY.md"
CONFIG = "config/rumdl.toml"
RUMDL_VERSION = "0.2.73"


def _run_make(
    *arguments: str, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def test_lint_markdown_runs_locked_cli_against_explicit_file() -> None:
    result = _run_make(
        "lint-markdown",
        f"MARKDOWN_FILES={FEATURE_DOC}",
        f"RUMDL_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output


def test_lint_markdown_requires_explicit_files() -> None:
    result = _run_make(
        "lint-markdown",
        "MARKDOWN_FILES=",
        f"RUMDL_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Usage: make lint-markdown" in output


def test_lint_markdown_keeps_legacy_config_alias() -> None:
    result = _run_make(
        "lint-markdown",
        f"MARKDOWN_FILES={FEATURE_DOC}",
        f"MARKDOWNLINT_CONFIG={CONFIG}",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output


def test_lint_markdown_rejects_conflicting_config_aliases() -> None:
    result = _run_make(
        "lint-markdown",
        f"MARKDOWN_FILES={FEATURE_DOC}",
        f"RUMDL_CONFIG={CONFIG}",
        "MARKDOWNLINT_CONFIG=config/not-the-rumdl-policy.toml",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "RUMDL_CONFIG and MARKDOWNLINT_CONFIG disagree" in output


def test_lint_markdown_rejects_inline_suppression_directives() -> None:
    directives = (
        "<!-- rumdl-disable-file -->",
        "<!-- markdownlint-disable MD009 -->",
        "<!-- prettier-ignore -->",
    )
    with TemporaryDirectory(prefix=".markdown-lint-test-", dir=ROOT) as directory:
        for index, directive in enumerate(directives):
            markdown_file = Path(directory) / f"directive-{index}.md"
            markdown_file.write_text(f"# Heading\n\n{directive}\n")
            result = _run_make(
                "lint-markdown",
                f"MARKDOWN_FILES={markdown_file}",
                f"RUMDL_CONFIG={CONFIG}",
            )
            output = result.stdout + result.stderr
            assert result.returncode == 2, output
            assert "inline Markdown lint directives are forbidden" in output


def test_lint_markdown_dependency_and_contract_are_exactly_pinned() -> None:
    package = json.loads((ROOT / ".opencode" / "package.json").read_text())
    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text())
    profile = tomllib.loads(
        (ROOT / "requirements" / "profiles" / "dev-quality" / "pyproject.toml")
        .read_text()
    )
    profile_lock = tomllib.loads(
        (ROOT / "requirements" / "profiles" / "dev-quality" / "uv.lock").read_text()
    )

    assert f"rumdl=={RUMDL_VERSION}" in profile["project"]["dependencies"]
    locked_rumdl = next(
        item for item in profile_lock["package"] if item["name"] == "rumdl"
    )
    assert locked_rumdl["version"] == RUMDL_VERSION
    assert "markdownlint-cli" not in package["devDependencies"]
    assert "markdownlint-cli2" not in package["devDependencies"]
    assert "node_modules/markdownlint-cli" not in lock["packages"]
    assert "node_modules/markdownlint-cli2" not in lock["packages"]

    makefile = (ROOT / "make" / "00-foundation.mk").read_text()
    stanza = makefile.split("\nlint-markdown:", 1)[1].split("\n\nlint-css:", 1)[0]
    assert "$(MAKE) --no-print-directory sync" not in stanza
    assert "Run: make sync DEPENDENCY_PROFILE_SET=development" in stanza

    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text()
    )
    entry = next(
        item for item in contract["targets"] if item["name"] == "lint-markdown"
    )
    assert entry["make_variables"] == [
        "MARKDOWN_FILES",
        "RUMDL_CONFIG",
        "MARKDOWNLINT_CONFIG",
    ]
    assert entry["behavior"] == (
        "make lint-markdown "
        "MARKDOWN_FILES=docs/features/XMSS_BACKEND_SAFETY.md "
        "RUMDL_CONFIG=config/rumdl.toml "
        "MARKDOWNLINT_CONFIG=config/rumdl.toml"
    )


def _owned_lint_environment() -> dict[str, str]:
    """Retain unrelated carriers while owning actual native Make control inputs."""
    environment = dict(os.environ)
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "GNUMAKEFLAGS"):
        environment.pop(name, None)
    return environment


@pytest.mark.parametrize("carrier", ("environment", "command-line"))
@pytest.mark.parametrize("environment_override", (False, True))
@pytest.mark.parametrize("tool_present", (False, True))
def test_selected_installed_rumdl_is_owned_without_path_fallback(
    tmp_path: Path, carrier: str, environment_override: bool, tool_present: bool,
) -> None:
    """Use the same real locked CLI through an owned selected-prefix witness."""
    assert importlib.metadata.version("rumdl") == RUMDL_VERSION
    native = Path(sys.executable).with_name("rumdl")
    assert native.is_file()
    selected = tmp_path / "selected-runtime"
    (selected / "bin").mkdir(parents=True)
    marker = tmp_path / "selected-cli"
    decoy_marker = tmp_path / "path-decoy"
    if tool_present:
        wrapper = selected / "bin" / "rumdl"
        wrapper.write_text(
            "#!/bin/sh\n"
            "printf selected > " + shlex.quote(str(marker)) + "\n"
            "exec " + shlex.quote(str(native)) + ' "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
    decoy_dir = tmp_path / "decoy-bin"
    decoy_dir.mkdir()
    decoy = decoy_dir / "rumdl"
    decoy.write_text(
        "#!/bin/sh\nprintf unexpected > " + shlex.quote(str(decoy_marker)) + "\nexit 97\n",
        encoding="utf-8",
    )
    decoy.chmod(0o700)
    document = tmp_path / "valid.md"
    document.write_text("# Owned Rumdl input\n", encoding="utf-8")
    environment = _owned_lint_environment()
    environment["PATH"] = str(decoy_dir) + os.pathsep + environment.get("PATH", os.defpath)
    arguments = ["-e"] if environment_override else []
    if carrier == "command-line":
        environment["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "foreign-missing-prefix")
        arguments.append("UV_PROJECT_ENVIRONMENT=" + str(selected))
    else:
        environment["UV_PROJECT_ENVIRONMENT"] = str(selected)
    parent_environment = dict(os.environ)
    result = _run_make(
        *arguments, "lint-markdown", "MARKDOWN_FILES=" + str(document),
        "RUMDL_CONFIG=" + CONFIG, environment=environment,
    )
    output = result.stdout + result.stderr
    assert result.returncode == (0 if tool_present else 2), output
    assert marker.exists() is tool_present
    assert not decoy_marker.exists()
    if not tool_present:
        assert "locked Rumdl executable not found" in output
    changed_names = {
        name for name in parent_environment.keys() | os.environ.keys()
        if parent_environment.get(name) != os.environ.get(name)
    }
    assert not changed_names, sorted(changed_names)


@pytest.mark.parametrize("carrier", ("environment", "command-line"))
@pytest.mark.parametrize("environment_override", (False, True))
def test_selected_rumdl_environment_remains_literal_with_active_native_witness(
    tmp_path: Path, carrier: str, environment_override: bool,
) -> None:
    """Prove the same payload/origin executes unguarded and stays data when scoped."""
    marker = tmp_path / "literal-environment-marker"
    payload = "$(shell printf owned > " + shlex.quote(str(marker)) + ")"
    context = tmp_path / "unguarded-rumdl-context.mk"
    context.write_text(
        ".PHONY: native-unguarded-rumdl-environment\n"
        "native-unguarded-rumdl-environment:\n\t@: \"$(UV_PROJECT_ENVIRONMENT)\"\n",
        encoding="utf-8",
    )
    environment = _owned_lint_environment()
    arguments = ["-e"] if environment_override else []
    if carrier == "command-line":
        environment["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "unselected")
        arguments.append("UV_PROJECT_ENVIRONMENT=" + payload)
    else:
        environment["UV_PROJECT_ENVIRONMENT"] = payload
    unguarded = _run_make(
        *arguments, "-f", "Makefile", "-f", str(context),
        "native-unguarded-rumdl-environment", environment=environment,
    )
    assert unguarded.returncode == 0, unguarded.stdout + unguarded.stderr
    assert marker.read_text() == "owned"
    marker.unlink()
    document = tmp_path / "literal.md"
    document.write_text("# Literal Rumdl input\n", encoding="utf-8")
    guarded = _run_make(
        *arguments, "lint-markdown", "MARKDOWN_FILES=" + str(document),
        "RUMDL_CONFIG=" + CONFIG, environment=environment,
    )
    assert guarded.returncode == 2, guarded.stdout + guarded.stderr
    assert "locked Rumdl executable not found" in guarded.stdout + guarded.stderr
    assert not marker.exists()


def test_selected_rumdl_retains_real_cli_failure_and_default_environment_plan(tmp_path: Path) -> None:
    """Keep native lint failure and the original unselected .venv default."""
    assert importlib.metadata.version("rumdl") == RUMDL_VERSION
    environment = _owned_lint_environment()
    environment["UV_PROJECT_ENVIRONMENT"] = str(Path(sys.executable).parent.parent)
    document = tmp_path / "invalid.md"
    document.write_text("# Heading\n\nTrailing whitespace   \n", encoding="utf-8")
    failed = _run_make(
        "lint-markdown", "MARKDOWN_FILES=" + str(document), "RUMDL_CONFIG=" + CONFIG,
        environment=environment,
    )
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert "MD009" in failed.stdout + failed.stderr
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    planned = _run_make(
        "-n", "lint-markdown", "MARKDOWN_FILES=" + str(document), "RUMDL_CONFIG=" + CONFIG,
        environment=environment,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    assert 'rumdl_root="${UV_PROJECT_ENVIRONMENT:-.venv}"' in planned.stdout
    assert "command -v" not in planned.stdout and "which rumdl" not in planned.stdout
