"""Structural contracts for early line-count and clone admission."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
FEATURE_DOC = ROOT / "docs" / "features" / "PRE_COMMIT_QUALITY_GUARDS.md"


def _target(makefile: str, name: str) -> str:
    return makefile.split(f"\n{name}:", 1)[1].split("\n\n", 1)[0]


def test_real_pre_commit_hooks_use_staged_content_guards() -> None:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {
        hook["id"]: hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    }

    line_guard = hooks["check-file-line-limits"]
    assert line_guard["entry"] == (
        "make check-file-line-limits "
        "FILE_LINE_LIMIT_POLICY=config/file_line_limits.json "
        "FILE_LINE_LIMIT_STAGED=1"
    )
    duplicate_guard = hooks["check-duplicate-code"]
    assert duplicate_guard["entry"] == (
        "make check-duplicate-code "
        "DUPLICATE_CODE_CONFIG=config/duplicate_code.json "
        "DUPLICATE_CODE_ENGINE=.opencode/node_modules/.bin/jscpd "
        "DUPLICATE_CODE_SOURCE=staged DUPLICATE_CODE_BASE_REF=HEAD "
        "DUPLICATE_CODE_CURRENT_REF=HEAD DUPLICATE_CODE_VALIDATE_ONLY=0"
    )
    for hook in (line_guard, duplicate_guard):
        assert hook["pass_filenames"] is False
        assert hook["always_run"] is True
        assert hook["require_serial"] is True
        assert hook["stages"] == ["pre-commit"]


def test_commit_targets_and_fast_preflight_cannot_bypass_guards() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    guard = _target(makefile, "_pre-commit-content-guard")
    assert "FILE_LINE_LIMIT_STAGED=1" in guard
    assert "DUPLICATE_CODE_SOURCE=staged" in guard
    for target in (
        "commit-no-verify",
        "git-commit-no-verify",
        "git-amend-msg",
        "repo-commit",
        "ship-commit",
    ):
        declaration = makefile.split(f"\n{target}:", 1)[1].splitlines()[0]
        assert "_pre-commit-content-guard" in declaration

    assert "pre-commit run --files" in _target(makefile, "git-commit")
    assert "git commit -F" in _target(makefile, "git-commit-file")

    pre_commit = _target(makefile, "pre-commit-check")
    assert pre_commit.index("check-file-line-limits") < pre_commit.index("lint")
    assert pre_commit.index("check-duplicate-code") < pre_commit.index("lint")


def test_integration_admission_runs_committed_duplicate_guard() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    admission = _target(makefile, "integration-admission")
    assert 'run_phase "check-duplicate-code"' in admission
    assert 'DUPLICATE_CODE_SOURCE="committed"' in admission
    assert 'DUPLICATE_CODE_BASE_REF="$(DUPLICATE_CODE_BASE_REF)"' in admission
    assert admission.index('run_phase "check-duplicate-code"') < admission.index(
        'run_phase "pre-commit-check"'
    )


def test_jscpd_is_exactly_pinned_in_manifest_and_lock() -> None:
    manifest = json.loads((ROOT / ".opencode/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / ".opencode/package-lock.json").read_text(encoding="utf-8"))

    assert manifest["devDependencies"]["jscpd"] == "5.4.0"
    assert lock["packages"][""]["devDependencies"]["jscpd"] == "5.4.0"
    assert lock["packages"]["node_modules/jscpd"]["version"] == "5.4.0"


def test_make_contract_has_safe_explicit_guard_examples() -> None:
    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    targets = {entry["name"]: entry for entry in contract["targets"]}

    assert targets["check-file-line-limits"] == {
        "name": "check-file-line-limits",
        "make_variables": ["FILE_LINE_LIMIT_POLICY", "FILE_LINE_LIMIT_STAGED"],
        "behavior": (
            "make check-file-line-limits "
            "FILE_LINE_LIMIT_POLICY=config/file_line_limits.json "
            "FILE_LINE_LIMIT_STAGED=0"
        ),
    }
    assert targets["check-duplicate-code"]["make_variables"] == [
        "DUPLICATE_CODE_CONFIG",
        "DUPLICATE_CODE_ENGINE",
        "DUPLICATE_CODE_SOURCE",
        "DUPLICATE_CODE_BASE_REF",
        "DUPLICATE_CODE_CURRENT_REF",
        "DUPLICATE_CODE_VALIDATE_ONLY",
    ]
    assert "DUPLICATE_CODE_VALIDATE_ONLY=1" in targets["check-duplicate-code"][
        "behavior"
    ]


def test_feature_document_records_practitioner_evidence_and_zdd() -> None:
    content = FEATURE_DOC.read_text(encoding="utf-8")

    for url in (
        "https://github.com/kucherenko/jscpd/issues/341",
        "https://github.com/kucherenko/jscpd/issues/363",
        "https://github.com/kucherenko/jscpd/issues/879",
        "https://github.com/pylint-dev/pylint/issues/214",
        "https://community.sonarsource.com/t/some-rules-do-not-work-in-mrs-prs-decoration-because-of-newcode-focus/152734",
    ):
        assert url in content
    for phrase in (
        "zero-downtime",
        "staged index",
        "existing findings remain visible",
        "no custom clone algorithm",
        "rollback",
    ):
        assert phrase in content


@pytest.mark.parametrize("recursive", (False, True))
@pytest.mark.parametrize("engine_name", ("jscpd", "engine with spaces/jscpd"))
def test_native_make_owns_engine_arguments_without_exporting_them_to_unrelated_children(
    tmp_path: Path, recursive: bool, engine_name: str,
) -> None:
    """Keep recursive Make options/overrides while forwarding the real checker CLI."""
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'arguments': sys.argv[1:], 'engine': "
        "os.environ.get('DUPLICATE_CODE_ENGINE'), 'flags': os.environ.get('MAKEFLAGS', ''), "
        "'level': os.environ.get('MAKELEVEL', '')}))\n",
    )
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        f'include {ROOT / "make" / "00-foundation.mk"}\n'
        f'PYTHON := {sys.executable}\n'
        f'UV := {sys.executable} {recorder} "$(FLAG_CANARY)"\n'
        "boundary-probe: check-duplicate-code test-count\n"
        "test-count:\n"
        "\t@$(UV) unrelated-collection\n"
        "recursive-probe:\n"
        f'\t@$(MAKE) --no-print-directory -f "{makefile}" check-duplicate-code '
        f'\n\t@$(MAKE) --no-print-directory -f "{makefile}" test-count\n',
    )
    engine = str(tmp_path / "canonical-main" / engine_name)
    canary = f"keep  two spaces DUPLICATE_CODE_ENGINE={engine} remain  exact"
    environment = os.environ.copy()
    environment.pop("DUPLICATE_CODE_ENGINE", None)
    result = subprocess.run(
        [
            "make", "--no-print-directory", "--keep-going", "-f", str(makefile),
            "recursive-probe" if recursive else "boundary-probe",
            f"DUPLICATE_CODE_ENGINE={engine}", "DUPLICATE_CODE_VALIDATE_ONLY=1",
            f"FLAG_CANARY={canary}",
        ],
        cwd=tmp_path, env=environment, check=False, text=True,
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    checker, collection = [json.loads(line) for line in result.stdout.splitlines()]
    arguments = checker["arguments"]
    assert arguments[0] == canary
    assert arguments[1:4] == ["run", "python", "scripts/check_duplicate_code.py"]
    assert arguments[arguments.index("--engine") + 1] == engine
    assert arguments[-1] == "--validate-only"
    assert f"DUPLICATE_CODE_ENGINE={engine}" in shlex.split(checker["flags"])
    assert collection["arguments"] == [canary, "unrelated-collection"]
    assert not any(
        item.startswith("DUPLICATE_CODE_ENGINE=") for item in shlex.split(collection["flags"])
    )
    for report in (checker, collection):
        assert report["engine"] is None
        assert f"FLAG_CANARY={canary}" in shlex.split(report["flags"])
        assert "-k" in report["flags"] or report["flags"].startswith("k")
        assert int(report["level"]) >= (2 if recursive else 1)


@pytest.mark.parametrize("engine_name", (
    "__GLUDD_DUPLICATE_CODE_ESCAPED_SPACE__/jscpd", "engine\twith-tab/jscpd",
))
def test_native_make_refuses_unsupported_override_encoding_before_any_recipe(
    tmp_path: Path, engine_name: str,
) -> None:
    """Ambiguous encoding must not guess at another override's boundaries."""
    sentry = tmp_path / "unexpected-recipe"
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        f'include {ROOT / "make" / "00-foundation.mk"}\n'
        "test-count:\n"
        f'\t@"{sys.executable}" -c "open(\'{sentry}\', \'w\').close()"\n',
    )
    result = subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(makefile), "test-count",
            f"DUPLICATE_CODE_ENGINE={tmp_path / engine_name}",
        ],
        cwd=tmp_path, check=False, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode != 0
    assert "unsupported duplicate-code Make override encoding" in result.stderr
    assert not sentry.exists()
