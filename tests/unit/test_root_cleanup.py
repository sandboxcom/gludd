"""Regression guards for root-directory hygiene and security documentation."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "node-deps-audit:" in makefile
    security_audit = makefile[makefile.index("security-audit:") : makefile.index("clean-artifacts:")]
    assert "node-deps-audit" in security_audit


def test_directory_documentation_does_not_describe_credentials_as_root_files() -> None:
    """Directory docs must direct operators to external credential storage."""
    content = (ROOT / "docs" / "DIRECTORY_STRUCTURE.md").read_text(encoding="utf-8")
    assert "`sandboxcom_github_rsa`" not in content
    assert "outside the repository" in content
