"""Regression guards for root-directory hygiene and security documentation.

Scoped archive ignores follow https://git-scm.com/docs/gitignore: wildcards do
not cross path separators and tracked files remain tracked. The long-lived
https://stackoverflow.com/questions/44155648 discussion likewise distinguishes
root-only ignore rules from hiding every directory with the same name.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from scripts.ansible_runtime_artifacts import COLLECTION_ARTIFACTS
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
COLLECTION_ARCHIVES = tuple(archive.relative_to(ROOT).as_posix() for _source, archive in COLLECTION_ARTIFACTS)


def _archive_fixture_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run bounded native Git only inside an owned fixture, with no inherited routing."""
    environment = {
        "PATH": os.defpath, "HOME": str(root), "XDG_CONFIG_HOME": str(root / "xdg"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
    }
    return subprocess.run(
        ["git", *arguments], cwd=root, env=environment, capture_output=True,
        text=True, check=False, timeout=10,
    )


@pytest.fixture
def archive_repository(tmp_path: Path) -> Path:
    """Never allow ambient GIT_DIR/index/config to redirect setup mutations."""
    result = _archive_fixture_git(tmp_path, "init", "--quiet", "--initial-branch=archive-fixture")
    assert result.returncode == 0, result.stderr
    return tmp_path


@pytest.mark.parametrize("command", ["status", "check-ignore"])
def test_generated_collection_archives_are_ignored_by_native_git(archive_repository: Path, command: str) -> None:
    """Every archive selected by the existing context owner is generated output."""
    (archive_repository / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    for relative in COLLECTION_ARCHIVES:
        archive = archive_repository / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"owned generated archive")
    if command == "status":
        result = _archive_fixture_git(archive_repository, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        assert result.returncode == 0, result.stderr
        assert {record[3:] for record in result.stdout.split("\0") if record.startswith("?? ")} == {".gitignore"}
    else:
        result = _archive_fixture_git(archive_repository, "check-ignore", "--", *COLLECTION_ARCHIVES)
        assert result.returncode == 0, result.stdout + result.stderr
        assert set(result.stdout.splitlines()) == set(COLLECTION_ARCHIVES)
    assert all(
        (archive_repository / relative).read_bytes() == b"owned generated archive" for relative in COLLECTION_ARCHIVES
    )


def test_collection_archive_ignore_keeps_unexpected_sources_and_build_inputs_visible(archive_repository: Path) -> None:
    """Neither the dist tree nor the source collection tree is blanket ignored."""
    visible = (
        "dist/install.sh", "dist/README.md", "dist/general-ludd.service", "dist/debian/control",
        "dist/rpm/gludd.spec", "dist/windows/gludd.nsi", "dist/collections/README.md",
        "dist/collections/unexpected.py", "dist/collections/unexpected.yml",
        "dist/collections/hand-authored.tar.gz", "dist/collections/other_namespace-agent-0.2.0.tar.gz",
        "dist/collections/general_ludd-agent-not-a-version.tar.gz",
        "dist/collections/general_ludd-agent-0.2.0.tar.gz.yml",
        "dist/collections/general_ludd-agent-0.2.0.tar.gz.sha256",
        "dist/collections/nested/general_ludd-agent-0.2.0.tar.gz",
        "elsewhere/dist/collections/general_ludd-agent-0.2.0.tar.gz",
        "collections/ansible_collections/general_ludd/agent/galaxy.yml",
        "collections/ansible_collections/general_ludd/agent/plugins/modules/unexpected.py",
    )
    (archive_repository / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    for relative in visible:
        path = archive_repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owned source input")
    checked = _archive_fixture_git(archive_repository, "check-ignore", "--", *visible)
    assert checked.returncode == 1 and checked.stdout == "", checked.stdout + checked.stderr
    status = _archive_fixture_git(archive_repository, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    assert status.returncode == 0, status.stderr
    untracked = {record[3:] for record in status.stdout.split("\0") if record.startswith("?? ")}
    assert untracked == set(visible) | {".gitignore"}
    assert all((archive_repository / relative).read_bytes() == b"owned source input" for relative in visible)


def test_collection_archive_ignore_does_not_hide_tracked_inputs_or_archives(archive_repository: Path) -> None:
    """An existing index entry remains visible even when its filename matches."""
    tracked = (COLLECTION_ARCHIVES[0], "dist/install.sh", "dist/README.md", "dist/collections/README.md")
    for relative in tracked:
        path = archive_repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owned tracked input")
    added = _archive_fixture_git(archive_repository, "add", "--", *tracked)
    assert added.returncode == 0, added.stderr
    before = _archive_fixture_git(archive_repository, "ls-files", "--stage").stdout
    (archive_repository / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    for relative in tracked:
        (archive_repository / relative).write_bytes(b"modified tracked input")
    checked = _archive_fixture_git(archive_repository, "check-ignore", "--", *tracked)
    assert checked.returncode == 1 and checked.stdout == "", checked.stdout + checked.stderr
    matched = _archive_fixture_git(archive_repository, "check-ignore", "--no-index", "--", COLLECTION_ARCHIVES[0])
    assert matched.returncode == 0 and matched.stdout.strip() == COLLECTION_ARCHIVES[0]
    modified = _archive_fixture_git(archive_repository, "diff", "--name-only")
    assert modified.returncode == 0 and set(modified.stdout.splitlines()) == set(tracked), modified.stderr
    assert _archive_fixture_git(archive_repository, "ls-files", "--stage").stdout == before
    assert all((archive_repository / relative).read_bytes() == b"modified tracked input" for relative in tracked)


def test_private_deploy_key_is_not_kept_at_repository_root() -> None:
    """Deploy credentials must live outside the checkout."""
    assert not (ROOT / "sandboxcom_github_rsa").exists()
    assert not (ROOT / "sandboxcom_github_rsa.pub").exists()


def test_coverage_audit_artifacts_are_not_kept_at_repository_root() -> None:
    """Per-run coverage databases belong in the audit scratch area."""
    leaked = sorted(ROOT.glob(".coverage.audit.*"))
    assert leaked == [], f"coverage audit artifacts leaked into repo root: {leaked}"


def test_platform_specific_node_dependencies_are_never_tracked() -> None:
    """Each host must install its own native Node dependency binaries."""
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    leaked = [path for path in tracked if "node_modules" in Path(path).parts]
    assert leaked == [], f"platform-specific node_modules files are tracked: {leaked}"

    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "node_modules/" in ignored


def test_ignored_runtime_state_is_never_tracked() -> None:
    """Cleanup-owned lock and status files must not be committed as source."""
    runtime_state = {".ansible/.lock", ".gate-status"}
    tracked = set(
        subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    assert runtime_state <= ignored
    assert tracked.isdisjoint(runtime_state), (
        f"cleanup-owned runtime state is tracked: {sorted(tracked & runtime_state)}"
    )


def test_hot_reload_node_dependencies_are_locked_and_installed_in_ci() -> None:
    """CI must install an integrity-locked native esbuild for its own platform."""
    manifest = json.loads((ROOT / ".opencode" / "package.json").read_text(encoding="utf-8"))
    assert manifest["private"] is True
    esbuild_version = manifest["devDependencies"]["esbuild"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", esbuild_version), "esbuild must use an exact version"

    lock = json.loads((ROOT / ".opencode" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"][""]["devDependencies"]["esbuild"] == esbuild_version
    assert lock["packages"]["node_modules/esbuild"]["version"] == esbuild_version
    non_public = {
        package: metadata["resolved"]
        for package, metadata in lock["packages"].items()
        if metadata.get("resolved")
        and not metadata["resolved"].startswith("https://registry.npmjs.org/")
    }
    assert non_public == {}, f"Node lockfile contains host-specific registries: {non_public}"

    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    sync = (
        "make node-deps-sync NODE_DEPS_VALIDATE_ONLY=0 "
        "NODE_DEPS_NPM_USERCONFIG=/dev/null NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1 "
        "NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org"
    )
    assert sync in workflow
    assert workflow.index(sync) < workflow.index("make hot-reload-plugins")


def test_node_package_manager_is_exactly_pinned() -> None:
    """Node installs must not silently select a host-global npm major."""
    manifest = json.loads((ROOT / ".opencode" / "package.json").read_text(encoding="utf-8"))
    assert manifest["packageManager"] == "npm@12.0.2"


def test_security_audit_covers_locked_node_dependencies() -> None:
    """The comprehensive audit must include the Node plugin/build supply chain."""
    makefile = compose_makefile(ROOT / "Makefile")
    assert "node-deps-audit:" in makefile
    security_audit = makefile[makefile.index("security-audit:") : makefile.index("clean-artifacts:")]
    assert "node-deps-audit" in security_audit


def test_directory_documentation_does_not_describe_credentials_as_root_files() -> None:
    """Directory docs must direct operators to external credential storage."""
    content = (ROOT / "docs" / "DIRECTORY_STRUCTURE.md").read_text(encoding="utf-8")
    assert "`sandboxcom_github_rsa`" not in content
    assert "outside the repository" in content
