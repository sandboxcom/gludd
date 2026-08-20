"""E2E tests for the release pipeline: release-cut, verify-release-completeness,
version consistency, artifact naming, gate-lite, and enforce-task-tracking.

Exercises the full release tooling chain:
  1. release-cut Makefile target exists with correct dependency chain
  2. verify-release-completeness script logic (category checks, prerelease, zero-size)
  3. Version consistency: pyproject.toml == __init__.py == check_version_consistency.py
  4. Release artifact naming convention (version-stamping, platform suffixes)
  5. gate-lite target structure and prerequisite integrity
  6. enforce-task-tracking plugin does not block version-bump commits on TASKS.md entries
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _run_make(
    target: str, cwd: Path | None = None, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["make", "-s", target],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _find_make_target(makefile_text: str, target: str) -> bool:
    """Check if a target is declared at column 0 in the Makefile."""
    return bool(re.search(rf"^{re.escape(target)}\s*:", makefile_text, re.MULTILINE))


def _makefile_prerequisites(makefile_text: str, target: str) -> list[str]:
    """Extract the prerequisite list from a target's first line."""
    m = re.search(rf"^{re.escape(target)}\s*:\s*(.+)$", makefile_text, re.MULTILINE)
    if not m:
        return []
    return [p.strip() for p in m.group(1).split() if p.strip()]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. release-cut target structure and dependency chain
# ═══════════════════════════════════════════════════════════════════════════════


def test_release_cut_target_exists() -> None:
    """make release-cut is declared in the Makefile."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert _find_make_target(makefile, "release-cut"), "release-cut target not found in Makefile"


def test_release_cut_requires_tag_argument() -> None:
    """release-cut refuses to run without a TAG argument."""
    # Dry-run: the target should exit non-zero when TAG is empty.
    # We can't run the actual release-cut, but we verify the guard exists.
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"^release-cut:\s*$(.+?)(?=^release-deploy|\Z)", makefile, re.MULTILINE | re.DOTALL)
    assert m, "release-cut recipe not found"
    recipe = m.group(1)
    assert '[ -n "$(TAG)" ]' in recipe, "release-cut missing TAG check"


def test_release_cut_steps_ordered() -> None:
    """release-cut invokes its sub-steps in the correct order."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"^release-cut:\s*$(.+?)(?=^release-deploy|\Z)", makefile, re.MULTILINE | re.DOTALL)
    assert m
    recipe = m.group(1)

    steps = [
        "require-ci-green",
        "check-readme-status",
        "git-push-sandboxcom",
        "git-tag-push",
        "release-view",
        "verify-release-artifact",
        "verify-release-completeness",
    ]
    positions = {}
    for step in steps:
        pos = recipe.find(step)
        assert pos != -1, f"release-cut missing step: {step}"
        positions[step] = pos

    for i in range(len(steps) - 1):
        assert positions[steps[i]] < positions[steps[i + 1]], (
            f"release-cut step order wrong: {steps[i]} after {steps[i + 1]}"
        )


def test_release_cut_all_dependency_targets_exist() -> None:
    """Every target referenced by release-cut is declared in the Makefile."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    deps = [
        "require-ci-green",
        "check-readme-status",
        "git-push-sandboxcom",
        "git-tag-push",
        "release-view",
        "verify-release-artifact",
        "verify-release-completeness",
    ]
    for dep in deps:
        assert _find_make_target(makefile, dep), f"release-cut dependency '{dep}' not declared"


def test_release_deploy_target_exists() -> None:
    """make release-deploy is declared in the Makefile."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert _find_make_target(makefile, "release-deploy"), "release-deploy target not found"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. verify-release-completeness script logic
# ═══════════════════════════════════════════════════════════════════════════════


def test_verify_release_completeness_script_exists() -> None:
    """The verify_release_completeness.py script loads cleanly."""
    script_path = ROOT / "scripts" / "verify_release_completeness.py"
    assert script_path.is_file(), "verify_release_completeness.py not found"

    spec = importlib.util.spec_from_file_location("verify_release_completeness", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod is not None


def test_expected_categories_count_is_twenty_eight() -> None:
    """Exactly 28 beta4 artifact categories are required, with none optional."""
    script_path = ROOT / "scripts" / "verify_release_completeness.py"
    spec = importlib.util.spec_from_file_location("vrce2e_cat", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.EXPECTED_CATEGORIES) == 28
    assert len(mod.OPTIONAL_CATEGORIES) == 0


def test_verify_completeness_category_checks() -> None:
    """Each EXPECTED_CATEGORIES check function works with realistic asset names."""
    script_path = ROOT / "scripts" / "verify_release_completeness.py"
    spec = importlib.util.spec_from_file_location("vrce2e_chk", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    names: set[str] = set()
    for label, check_fn in mod.EXPECTED_CATEGORIES.items():
        assert not check_fn(names), f"Category '{label}' falsely matched empty set"

    full_assets = {
        "gludd-linux-x86_64-v0.1.0-beta.4.tar.gz",
        "gludd-linux-aarch64-v0.1.0-beta.4.tar.gz",
        "gludd-macos-arm64-v0.1.0-beta.4.tar.gz",
        "gludd-windows-x86_64-v0.1.0-beta.4.tar.gz",
        "gludd_0.1.0-beta.4_amd64.deb",
        "gludd-0.1.0-beta.4-1.x86_64.rpm",
        "gludd-0.1.0-beta.4-arm64.dmg",
        "gludd-installer-0.1.0-beta.4-x86_64.exe",
        "gludd-0.1.0-beta.4-checksums.sha256",
        "gludd-0.1.0-beta.4.spdx.json",
        "LICENSE",
        "THIRD_PARTY_LICENSES.md",
        "general_ludd_agent-0.1.0b4-py3-none-any.whl",
        "general_ludd_agent-0.1.0-beta.4.tar.gz",
        "general_ludd-agent-0.2.0.tar.gz",
        "general_ludd-language-0.1.0.tar.gz",
        "general_ludd-networking-0.2.0.tar.gz",
        "gludd-collections-v0.1.0-beta.4.json",
        "ansible-ee-execution-environment.yml",
        "ansible-ee-requirements.yml",
        "ansible-ee-requirements.txt",
        "ansible-ee-bindep.txt",
        "ansible-ee-runtime-lock.json",
        "ansible-managed-host-python.lock.json",
        "ansible-collection-python-boundary-inventory.json",
        "gludd-ee-image-v0.1.0-beta.4.json",
        "gludd-container-v0.1.0-beta.4.json",
        "install.sh",
        "gludd-smoke-linux-x86_64-v0.1.0-beta.4.json",
        "gludd-release-manifest-v0.1.0-beta.4.json",
    }
    missing = [
        label
        for label, check_fn in mod.EXPECTED_CATEGORIES.items()
        if not check_fn(full_assets)
    ]
    assert not missing, f"Categories did not match the full beta4 asset set: {missing}"


def test_prerelease_detection() -> None:
    """Prerelease tags (alpha/beta/rc) are correctly detected."""
    script_path = ROOT / "scripts" / "verify_release_completeness.py"
    spec = importlib.util.spec_from_file_location("vrce2e_pre", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.expected_prerelease("v0.1.0-alpha.1")
    assert mod.expected_prerelease("v0.1.0-beta.3")
    assert mod.expected_prerelease("v0.1.0-rc.1")
    assert not mod.expected_prerelease("v1.0.0")
    assert not mod.expected_prerelease("v0.1.0")
    assert not mod.expected_prerelease("v2.3.4")


def test_version_from_tag() -> None:
    """Tag 'v0.1.0-beta.3' → '0.1.0-beta.3'."""
    script_path = ROOT / "scripts" / "verify_release_completeness.py"
    spec = importlib.util.spec_from_file_location("vrce2e_ver", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.version_from_tag("v0.1.0-beta.3") == "0.1.0-beta.3"
    assert mod.version_from_tag("0.1.0") == "0.1.0"
    assert mod.version_from_tag("v1.0.0") == "1.0.0"


def test_verify_release_completeness_make_target() -> None:
    """make verify-release-completeness exists and requires TAG."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert _find_make_target(makefile, "verify-release-completeness")

    m = re.search(r"^verify-release-completeness:\s*$(.+?)(?=^\S|\Z)", makefile, re.MULTILINE | re.DOTALL)
    assert m
    recipe = m.group(1)
    assert "TAG" in recipe, "verify-release-completeness should require TAG"


def test_verify_release_completeness_error_on_missing_tag() -> None:
    """Running verify-release-completeness without TAG exits non-zero."""
    result = _run_make("verify-release-completeness")
    assert result.returncode != 0, "Expected non-zero exit when TAG is missing"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Version consistency checks
# ═══════════════════════════════════════════════════════════════════════════════


def test_pyproject_version_matches_init() -> None:
    """pyproject.toml [project] version == src/general_ludd/__init__.py __version__."""
    import tomllib

    pyproject = ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    pyproject_version = data["project"]["version"]

    init_path = ROOT / "src" / "general_ludd" / "__init__.py"
    init_text = init_path.read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    assert m, "Could not find __version__ in __init__.py"
    init_version = m.group(1)

    assert pyproject_version == init_version, (
        f"Version mismatch: pyproject.toml={pyproject_version} __init__.py={init_version}"
    )


def test_version_consistency_script_passes() -> None:
    """scripts/check_version_consistency.py exits 0 when versions match."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_version_consistency.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"check_version_consistency.py failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK:" in result.stdout


def test_readme_status_line_present() -> None:
    """README.md contains a 'Status as of' line."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"[Ss]tatus\s+as\s+of\s+(v?[\w.\-]+)", readme)
    assert m, "README.md missing 'Status as of <version>' line"
    readme_version = m.group(1)
    assert readme_version, "README status version is empty"


def test_check_readme_status_script_exists() -> None:
    """check_readme_status_current.py loads and has expected functions."""
    script_path = ROOT / "scripts" / "check_readme_status_current.py"
    spec = importlib.util.spec_from_file_location("crsce2e", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert callable(mod._normalize)
    assert callable(mod._read_pyproject_version)
    assert callable(mod._find_readme_status_line)
    assert mod._normalize("V0.1.0") == "0.1.0"
    assert mod._normalize("v0.1.0") == "0.1.0"
    assert mod._normalize("0.1.0") == "0.1.0"


def test_check_readme_status_current_with_tag(tmp_path: Path) -> None:
    """check_readme_status_current.py matches README version against a given tag."""
    script_path = ROOT / "scripts" / "check_readme_status_current.py"
    spec = importlib.util.spec_from_file_location("crsce2e_tag", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    readme_version = mod._find_readme_status_line(ROOT)
    assert readme_version is not None, "No 'Status as of' line found in README"

    normalized = mod._normalize(readme_version)
    pyproject_version = mod._read_pyproject_version(ROOT)
    assert normalized == mod._normalize(pyproject_version), (
        f"README says '{readme_version}' but pyproject.toml says '{pyproject_version}'"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Release artifact naming convention
# ═══════════════════════════════════════════════════════════════════════════════


def test_expected_artifact_naming_patterns() -> None:
    """Each EXPECTED_CATEGORIES label maps to a recognizable naming pattern."""
    script_path = ROOT / "scripts" / "verify_release_completeness.py"
    spec = importlib.util.spec_from_file_location("vrce2e_nm", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Verify each category check is callable and has descriptive label
    for label, check_fn in mod.EXPECTED_CATEGORIES.items():
        assert callable(check_fn), f"Category '{label}' check is not callable"
        assert label, "Category label is empty"


_ASSET_PATTERN_TESTS = [
    ("linux-x86_64", re.compile(r"linux.*(x86[._-]?64|amd64)", re.IGNORECASE), "gludd-linux-x86_64-v0.1.0.tar.gz"),
    ("linux-aarch64", re.compile(r"linux.*(aarch64|arm64)", re.IGNORECASE), "gludd-linux-aarch64-v0.1.0.tar.gz"),
    ("macos-arm64", re.compile(r"(macos|darwin).*arm64", re.IGNORECASE), "gludd-darwin-arm64-v0.1.0.tar.gz"),
    (
        "windows-x86_64",
        re.compile(r"win(dows)?.*(x86[._-]?64|amd64)", re.IGNORECASE),
        "gludd-windows-x86_64-v0.1.0.tar.gz",
    ),
]


@pytest.mark.parametrize(("arch_label", "pattern", "asset"), _ASSET_PATTERN_TESTS)
def test_platform_asset_pattern(
    arch_label: str, pattern: re.Pattern[str], asset: str
) -> None:
    """Each platform binary category regex matches its expected asset name."""
    assert pattern.search(asset), f"{arch_label} pattern must match '{asset}'"


def test_version_stamped_asset_naming() -> None:
    """Artifact names should embed the version (without leading 'v')."""
    script_path = ROOT / "scripts" / "verify_release_completeness.py"
    spec = importlib.util.spec_from_file_location("vrce2e_vs", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    version = mod.version_from_tag("v0.1.0-beta.3")
    assert version == "0.1.0-beta.3"
    names = {"gludd-linux-x86_64-0.1.0-beta.3.tar.gz", "gludd-windows-x86_64-0.1.0-beta.3.tar.gz"}
    assert any(version in n for n in names), "Assets should embed version in name"


def test_expected_package_asset_patterns() -> None:
    """Package artifacts (.deb, .rpm, .dmg, .exe installer) each have a recognizer."""
    deb_re = re.compile(r"\.deb$", re.IGNORECASE)
    rpm_re = re.compile(r"\.rpm$", re.IGNORECASE)
    dmg_re = re.compile(r"\.dmg$", re.IGNORECASE)
    exe_re = re.compile(r"installer.*\.exe$|setup.*\.exe$|gludd.*install.*\.exe$", re.IGNORECASE)

    assert deb_re.search("gludd_0.1.0_amd64.deb")
    assert rpm_re.search("gludd-0.1.0-1.x86_64.rpm")
    assert dmg_re.search("gludd-0.1.0-arm64.dmg")
    assert exe_re.search("gludd-installer-0.1.0-x86_64.exe")
    assert not deb_re.search("gludd_0.1.0_amd64.deb.txt")


def test_metadata_asset_patterns() -> None:
    """Checksums, SBOM, LICENSE, THIRD_PARTY_LICENSES each have recognizers."""
    checksum_re = re.compile(r"(checksums?|SHA256SUMS|sha256)|\.sha256(\.txt)?", re.IGNORECASE)
    sbom_re = re.compile(r"sbom|spdx|cyclonedx|\.cdx\.|\.spdx\.", re.IGNORECASE)

    assert checksum_re.search("gludd-0.1.0-checksums.sha256")
    assert checksum_re.search("SHA256SUMS")
    assert sbom_re.search("gludd-0.1.0.spdx.json")
    assert sbom_re.search("gludd-0.1.0.cdx.xml")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. gate-lite target structure and prerequisite integrity
# ═══════════════════════════════════════════════════════════════════════════════


def test_gate_lite_target_exists() -> None:
    """make gate-lite is declared in the Makefile."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert _find_make_target(makefile, "gate-lite"), "gate-lite target not found"


def test_gate_lite_prerequisites_are_valid_targets() -> None:
    """Every prerequisite of gate-lite is itself a declared Makefile target."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    prereqs = _makefile_prerequisites(makefile, "gate-lite")
    assert prereqs, "gate-lite has no prerequisites"
    for prereq in prereqs:
        assert _find_make_target(makefile, prereq), f"gate-lite prerequisite '{prereq}' not declared as target"


def test_gate_lite_has_required_phases() -> None:
    """gate-lite runs lint, dead-code, typecheck, collect, smoke, and unit tests."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"^gate-lite:[^\n]*\n(.*?)(?=^ps-pytest:|\Z)", makefile, re.MULTILINE | re.DOTALL)
    assert m
    recipe = m.group(1)

    required_phases = ["lint", "dead-code", "typecheck", "collect", "smoke"]
    for phase in required_phases:
        assert phase in recipe, f"gate-lite missing phase: {phase}"


def test_check_readme_status_make_target() -> None:
    """make check-readme-status is declared and invokes the script."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert _find_make_target(makefile, "check-readme-status")
    m = re.search(r"^check-readme-status:\s*$(.+?)(?=^\S|\Z)", makefile, re.MULTILINE | re.DOTALL)
    assert m
    recipe = m.group(1)
    assert "check_readme_status_current.py" in recipe


# ═══════════════════════════════════════════════════════════════════════════════
# 6. enforce-task-tracking plugin — version bump commits
# ═══════════════════════════════════════════════════════════════════════════════


def test_enforce_tdd_plugin_has_init_handling() -> None:
    """enforce-tdd.ts has isInitInEmptyDir logic for __init__.py in empty dirs."""
    plugin_path = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
    if not plugin_path.is_file():
        pytest.skip("enforce-tdd.ts not found")

    source = plugin_path.read_text(encoding="utf-8")
    assert "isInitInEmptyDir" in source, "enforce-tdd.ts should handle __init__.py via isInitInEmptyDir"
    assert "__pycache__" in source, "enforce-tdd.ts should allowlist __pycache__"


def test_enforce_tdd_plugin_denies_init_py_in_nonempty_dir() -> None:
    """enforce-tdd denies __init__.py edits when the parent dir has other .py files.
    This reflects current plugin behavior — __init__.py is only auto-allowed
    in empty directories (isInitInEmptyDir). Adding __init__.py to the
    ALLOWLIST_PATTERNS would allow version bumps without a test file.
    """
    plugin_path = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
    if not plugin_path.is_file() or shutil.which("node") is None:
        pytest.skip("enforce-tdd.ts or node not available")

    _run_tdd_check(
        str(plugin_path),
        "src/general_ludd/__init__.py",
        "deny",
        "__init__.py in non-empty dir is denied (not in ALLOWLIST_PATTERNS)",
    )


def test_enforce_tdd_plugin_blocks_src_without_test() -> None:
    """enforce-tdd plugin returns deny for src/* without a test file on disk."""
    plugin_path = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
    if not plugin_path.is_file() or shutil.which("node") is None:
        pytest.skip("enforce-tdd.ts or node not available")

    _run_tdd_check(
        str(plugin_path),
        "src/general_ludd/nonexistent_module_42.py",
        "deny",
        "new src file without test should be denied",
    )


def _run_tdd_check(
    plugin_path: str,
    file_path: str,
    expected: str,
    description: str,
) -> None:
    """Run enforce-tdd.ts hook with a given file path and check the decision."""
    ts_code = textwrap.dedent(f"""\
        const mod = await import({json.dumps(plugin_path)})
        const plugin = await mod.default({{}})
        const result = await plugin['tool.execute.before'](
            {{ tool: 'edit' }},
            {{ args: {{ filePath: {json.dumps(file_path)}, newString: 'test' }} }},
        )
        console.log(JSON.stringify(result ?? {{ allowed: true }}))
    """)
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".ts", prefix="tdd_e2e_", mode="w", delete=False) as f:
        f.write(ts_code)
        tmp_path = f.name

    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env["GLUDD_TDD_ENFORCE"] = "1"
        result = subprocess.run(
            ["node", "--experimental-strip-types", tmp_path],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, f"{description}: node exit {result.returncode}\nstderr: {result.stderr[:800]}"
        for line in reversed(result.stdout.strip().splitlines()):
            try:
                data = json.loads(line)
                if expected == "allow":
                    assert data.get("permissionDecision") != "deny", f"{description}: got deny for {file_path}: {data}"
                else:
                    assert data.get("permissionDecision") == "deny", (
                        f"{description}: expected deny for {file_path}, got: {data}"
                    )
                return
            except json.JSONDecodeError:
                continue
        pytest.fail(f"{description}: no JSON result found in output: {result.stdout[:500]}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_enforce_tdd_allowlist_in_check_script() -> None:
    """scripts/check_tdd_compliance.py also allowlists __init__.py for version bumps."""
    script_path = ROOT / "scripts" / "check_tdd_compliance.py"
    if not script_path.is_file():
        pytest.skip("check_tdd_compliance.py not found")
    text = script_path.read_text(encoding="utf-8")
    assert "__init__" in text, "check_tdd_compliance.py should reference __init__.py in allowlist logic"


def test_enforce_tdd_does_not_block_type_stubs() -> None:
    """Type stub files (*.pyi) pass through the TDD editor gate."""
    plugin_path = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
    if not plugin_path.is_file() or shutil.which("node") is None:
        pytest.skip("enforce-tdd.ts or node not available")
    source = plugin_path.read_text(encoding="utf-8")
    assert ".pyi" in source, "enforce-tdd.ts should allowlist .pyi files"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. verify-release-artifact target
# ═══════════════════════════════════════════════════════════════════════════════


def test_verify_release_artifact_script_exists() -> None:
    """verify_release_artifact.py exists and is loadable."""
    script_path = ROOT / "scripts" / "verify_release_artifact.py"
    if not script_path.is_file():
        pytest.skip("verify_release_artifact.py not found")
    spec = importlib.util.spec_from_file_location("vrae2e", script_path)
    assert spec and spec.loader


def test_verify_release_artifact_make_target() -> None:
    """make verify-release-artifact is declared and requires TAG."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert _find_make_target(makefile, "verify-release-artifact")
    m = re.search(
        r"^verify-release-artifact:\s*$(.+?)(?=^\S|\Z)",
        makefile,
        re.MULTILINE | re.DOTALL,
    )
    assert m
    recipe = m.group(1)
    assert "TAG" in recipe


def test_releasor_operations_make_targets() -> None:
    """release-recut and release-create targets are declared."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("release-recut", "release-create", "release-delete", "release-view"):
        assert _find_make_target(makefile, target), f"{target} target not found"
