"""Structural tests for the beta4 build-and-release artifact smoke matrix."""

from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).parents[2] / ".github" / "workflows" / "build.yml"
).read_text(encoding="utf-8")


def _step_script(job: str, name: str) -> str:
    workflow = yaml.safe_load(WORKFLOW)
    return next(
        str(step["run"])
        for step in workflow["jobs"][job]["steps"]
        if step.get("name") == name
    )


def test_linux_packages_are_extracted_and_executed_before_upload() -> None:
    assert "Smoke packaged Linux artifacts" in WORKFLOW
    assert "dpkg-deb -x" in WORKFLOW
    assert "rpm2cpio" in WORKFLOW
    assert "gludd-linux-tar-smoke" in WORKFLOW


def test_macos_tar_and_dmg_are_executed_before_upload() -> None:
    assert "Smoke packaged macOS artifacts" in WORKFLOW
    assert "hdiutil attach -nobrowse -readonly" in WORKFLOW
    assert "gludd-macos-dmg-smoke" in WORKFLOW


def test_windows_zip_and_nsis_installer_are_executed_before_upload() -> None:
    assert "Smoke packaged Windows artifacts" in WORKFLOW
    assert "Expand-Archive" in WORKFLOW
    assert "/S" in WORKFLOW
    assert "gludd-windows-nsis-smoke" in WORKFLOW


def test_windows_zip_uses_powershell_error_semantics() -> None:
    """A cmdlet must not inherit a stale native ``LASTEXITCODE`` value."""
    script = _step_script("windows", "Package zip")

    assert "Compress-Archive" in script
    assert "-ErrorAction Stop" in script
    assert "$LASTEXITCODE" not in script
    assert "Test-Path -LiteralPath $zipPath -PathType Leaf" in script


def test_aarch64_tar_is_extracted_and_executed_before_upload() -> None:
    assert "Smoke packaged Linux aarch64 artifact" in WORKFLOW
    assert "gludd-linux-aarch64-tar-smoke" in WORKFLOW


def test_container_is_smoked_before_push_and_digest_metadata() -> None:
    region = WORKFLOW[WORKFLOW.index("\n  container:\n") : WORKFLOW.index("\n  ansible-ee:\n")]
    build = region.index("- name: Build container image")
    smoke = region.index("- name: Smoke container health endpoint")
    publish = region.index("- name: Publish verified container image")
    metadata = region.index("- name: Write container digest metadata")

    assert build < smoke < publish < metadata
    assert "push: false" in region
    assert "load: true" in region
    assert "RepoDigests" in region
    assert "gludd-container-${VERSION}.json" in region
    assert "steps.build.outputs.digest" not in region


def test_container_health_smoke_allows_only_the_exact_docker_bridge_gateway() -> None:
    """The host probe must cross Docker's bridge without opening the daemon."""
    script = _step_script("container", "Smoke container health endpoint")

    assert "docker network inspect bridge" in script
    assert '"%s/32"' in script
    assert '"$bridge_gateway"' in script
    assert "GLUDD_NETWORK__ALLOWED_CIDR" in script
    assert "0.0.0.0/0" not in script


def test_execution_environment_is_built_smoked_and_digest_addressed() -> None:
    assert "\n  ansible-ee:\n" in WORKFLOW
    assert "build-ansible-execution-environment" in WORKFLOW
    assert "ANSIBLE_EE_SMOKE_PASS" in WORKFLOW
    assert "gludd-ee-image-${VERSION}.json" in WORKFLOW


def test_execution_environment_smoke_uses_the_managed_python() -> None:
    """Ansible belongs to the EE interpreter, never ambient ``python3``."""
    script = _step_script("ansible-ee", "Smoke Ansible execution environment")

    assert "/usr/bin/python3.11 -c" in script
    assert ' python3 -c "import ansible' not in script


def test_python_and_collection_distributions_are_built_and_smoked() -> None:
    assert "Build and smoke Python distributions" in WORKFLOW
    assert "uv build --wheel --sdist" in WORKFLOW
    assert "build-ansible-execution-environment" in WORKFLOW
    assert "ansible-galaxy collection build" not in WORKFLOW


def test_canonical_runtime_boundary_inputs_are_release_assets() -> None:
    for name in (
        "ansible-ee-execution-environment.yml",
        "ansible-ee-requirements.yml",
        "ansible-ee-requirements.txt",
        "ansible-ee-bindep.txt",
        "ansible-ee-runtime-lock.json",
        "ansible-managed-host-python.lock.json",
        "ansible-collection-python-boundary-inventory.json",
    ):
        assert name in WORKFLOW


def test_deep_matrix_verifier_runs_before_release_publish() -> None:
    verifier = WORKFLOW.index("verify_release_asset_matrix.py")
    publisher = WORKFLOW.index("softprops/action-gh-release")
    assert verifier < publisher
    assert "write-manifest release-assets" in WORKFLOW
    assert "SHA256SUMS" in WORKFLOW


def test_every_release_artifact_upload_fails_on_missing_files() -> None:
    release_region = WORKFLOW[WORKFLOW.index("\n  linux:\n") :]
    upload_count = release_region.count("actions/upload-artifact@")
    fail_closed_count = release_region.count("if-no-files-found: error")
    assert upload_count >= 6
    assert fail_closed_count >= upload_count
