"""PK.13 — verify the macos build job creates a .dmg with correct contents.

These tests statically inspect `.github/workflows/build.yml` to assert that the
macOS release packaging step produces a versioned .dmg containing the gludd
binary and stages it for release. They do not invoke `hdiutil` (a macOS-only
tool) — they pin the workflow contract so a future edit cannot silently drop
the .dmg step, rename the output, omit the binary, or skip the artifact upload.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

BUILD_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build.yml"


def _load_workflow() -> dict:
    """Parse build.yml into a dict; fail loudly if the file is missing/invalid."""
    assert BUILD_YML.is_file(), f"build.yml not found at {BUILD_YML}"
    text = BUILD_YML.read_text(encoding="utf-8")
    # GitHub Actions uses ${{ ... }} and $VAR which are NOT yaml-safe by default.
    # PyYAML handles them fine as plain strings (no custom tags), so direct load.
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), f"build.yml top-level is not a mapping: {type(parsed)}"
    return parsed


def _macos_job() -> dict:
    wf = _load_workflow()
    jobs = wf.get("jobs") or {}
    macos = jobs.get("macos")
    assert isinstance(macos, dict), "'macos' job missing from build.yml jobs"
    return macos


def _macos_steps() -> list[dict]:
    macos = _macos_job()
    steps = macos.get("steps") or []
    assert isinstance(steps, list), f"macos.steps is not a list: {type(steps)}"
    return steps


def _step_text(step: dict) -> str:
    """Flatten a step's run/with fields into a single searchable string."""
    parts: list[str] = []
    run = step.get("run")
    if isinstance(run, str):
        parts.append(run)
    with_ = step.get("with")
    if isinstance(with_, dict):
        for v in with_.values():
            if isinstance(v, str):
                parts.append(v)
    name = step.get("name")
    if isinstance(name, str):
        parts.append(name)
    return "\n".join(parts)


def _all_macos_text() -> str:
    """Concatenate every macos step's text + the raw yaml block for robust grep."""
    wf_text = BUILD_YML.read_text(encoding="utf-8")
    step_blob = "\n---\n".join(_step_text(s) for s in _macos_steps())
    return wf_text + "\n---STEP BLOB---\n" + step_blob


# --- Tests -----------------------------------------------------------------


def test_macos_job_exists() -> None:
    """The workflow must declare a top-level `macos` job."""
    wf = _load_workflow()
    jobs = wf.get("jobs") or {}
    assert "macos" in jobs, (
        "build.yml has no 'macos' job. Found jobs: " + ", ".join(sorted(jobs))
    )
    macos = jobs["macos"]
    assert macos.get("runs-on") == "macos-latest", (
        f"macos job runs-on={macos.get('runs-on')!r}, expected 'macos-latest'"
    )


def test_macos_job_has_dmg_creation_step() -> None:
    """A step must create a .dmg via `hdiutil create` or `create-dmg`."""
    steps = _macos_steps()
    found = False
    for step in steps:
        blob = _step_text(step)
        if "hdiutil create" in blob or "create-dmg" in blob:
            found = True
            break
    assert found, (
        "No step in the macos job invokes `hdiutil create` or `create-dmg`. "
        "A .dmg packaging step is required for the macOS release artifact. "
        "Steps: " + ", ".join(repr(s.get("name") or s.get("id") or "?") for s in steps)
    )


def test_dmg_output_name_references_version() -> None:
    """The .dmg filename must embed the version (env.VERSION or VERSION_PLACEHOLDER)."""
    blob = _all_macos_text()
    # Look for a .dmg path that includes a version reference. Accept either the
    # GitHub Actions env substitution ($VERSION or ${{ env.VERSION }}) or a
    # literal VERSION_PLACEHOLDER token used by templating.
    version_ref_patterns = [
        r'\$VERSION[^A-Za-z_].*\.dmg',
        r'\$\{\{\s*env\.VERSION\s*\}\}[^"]*\.dmg',
        r'VERSION_PLACEHOLDER[^"]*\.dmg',
        r'gludd-\$\{VERSION\}-macos[^" ]*\.dmg',
    ]
    matched = any(re.search(p, blob) for p in version_ref_patterns)
    assert matched, (
        r"No .dmg output name embeds a version reference ($VERSION, "
        r"${{ env.VERSION }}, or VERSION_PLACEHOLDER). The DMG must be "
        "version-stamped so releases are distinguishable."
    )


def test_dmg_includes_gludd_binary() -> None:
    """The .dmg source folder must contain the built gludd binary."""
    steps = _macos_steps()
    # Find the dmg-producing step (the one with hdiutil/create-dmg), then verify
    # the same step (or its staging) copies `gludd` into the source folder.
    dmg_step_idx = None
    for i, step in enumerate(steps):
        blob = _step_text(step)
        if "hdiutil create" in blob or "create-dmg" in blob:
            dmg_step_idx = i
            break
    assert dmg_step_idx is not None, "No .dmg creation step found (prior test should have failed)"
    # The binary copy and the hdiutil call are typically in the SAME step's
    # `run:` block, but some workflows split staging into a prior step. Search
    # the dmg step + the step immediately before it for the `cp ... gludd`.
    search_blob = _step_text(steps[dmg_step_idx])
    if dmg_step_idx > 0:
        search_blob = _step_text(steps[dmg_step_idx - 1]) + "\n" + search_blob
    # Match `cp <src> .../gludd` or `cp dist/gludd <staging>/gludd` — the
    # binary must be placed into the dmg source folder.
    has_binary_copy = bool(
        re.search(r'cp\s+\S*gludd\S*\s+\S*dmg-staging\S*', search_blob)
        or re.search(r'cp\s+dist/gludd\s+\S+', search_blob)
    )
    assert has_binary_copy, (
        "The .dmg creation step (or its staging step) does not copy the gludd "
        "binary into the dmg source folder. The DMG must contain the built binary."
    )


def test_dmg_uploaded_as_artifact() -> None:
    """The .dmg must be uploaded via actions/upload-artifact or staged for release."""
    blob = _all_macos_text()
    # The macos job uploads artifacts via actions/upload-artifact. Confirm the
    # .dmg appears in the upload path list AND the upload action is present.
    has_upload = "actions/upload-artifact" in blob
    has_dmg_in_path = bool(re.search(r'dist/.*\.dmg(?:\.sha256)?', blob))
    # The release job downloads gludd-* artifacts and copies them into
    # release-assets — so a .dmg in the macos upload path transitively stages
    # it for release. Verify both halves of that contract.
    assert has_upload, (
        "macos job does not use actions/upload-artifact. The .dmg must be "
        "uploaded as a CI artifact so the release job can stage it."
    )
    assert has_dmg_in_path, (
        "No .dmg file appears in the macos job's upload-artifact path list. "
        "The DMG must be included in the uploaded artifacts."
    )


def test_dmg_sha256_generated() -> None:
    """A .dmg.sha256 checksum must be generated alongside the .dmg."""
    blob = _all_macos_text()
    # shasum -a 256 ... > ....dmg.sha256  (macos) or sha256sum (linux pattern)
    has_checksum = bool(
        re.search(r'shasum\s+-a\s+256.*\.dmg\.sha256', blob)
        or re.search(r'sha256sum.*\.dmg\.sha256', blob)
    )
    assert has_checksum, (
        "No sha256 checksum generation for the .dmg was found. A .dmg.sha256 "
        "sidecar is required for release integrity verification."
    )


def test_macos_job_needs_version() -> None:
    """The macos job must depend on the `version` job for its VERSION env var."""
    macos = _macos_job()
    needs = macos.get("needs")
    if isinstance(needs, str):
        needs_list = [needs]
    elif isinstance(needs, list):
        needs_list = needs
    else:
        needs_list = []
    assert "version" in needs_list, (
        f"macos job needs={needs!r} does not include 'version'. "
        "The VERSION env var (used in the .dmg name) comes from the version job."
    )


def test_macos_env_has_version() -> None:
    """The macos job must export a VERSION env var for the .dmg filename."""
    macos = _macos_job()
    env = macos.get("env") or {}
    assert isinstance(env, dict), f"macos.env is not a dict: {type(env)}"
    assert "VERSION" in env, (
        "macos job env does not define VERSION. The .dmg filename depends on it."
    )


@pytest.mark.parametrize("missing_asset", [
    "tarball",
    "tarball.sha256",
    "dmg",
    "dmg.sha256",
])
def test_macos_artifact_path_includes_each_asset(missing_asset: str) -> None:
    """The upload-artifact path must list the tarball, its checksum, the dmg, and its checksum."""
    steps = _macos_steps()
    upload_step = None
    for step in steps:
        uses = step.get("uses") or ""
        if isinstance(uses, str) and "actions/upload-artifact" in uses:
            upload_step = step
            break
    assert upload_step is not None, "macos job has no actions/upload-artifact step"
    with_ = upload_step.get("with") or {}
    path_field = with_.get("path")
    path_blob = "\n".join(str(p) for p in path_field) if isinstance(path_field, list) else str(path_field or "")
    # Each asset is matched by a token that should appear in the path list.
    tokens = {
        "tarball": "macos-arm64.tar.gz",
        "tarball.sha256": "macos-arm64.tar.gz.sha256",
        "dmg": "macos-arm64.dmg",
        "dmg.sha256": "macos-arm64.dmg.sha256",
    }
    token = tokens[missing_asset]
    assert token in path_blob, (
        f"upload-artifact path list missing '{token}' asset. "
        f"path was:\n{path_blob}"
    )
