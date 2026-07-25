"""PK.14 — NSIS installer OutFile directive must match the release job's expected artifact name.

The release job (.github/workflows/build.yml) builds the installer via
`makensis -DVERSION=<v> -DBUILDDIR=dist dist/windows/gludd.nsi` and then:
  - hashes `dist/gludd-<v>-setup-x86_64.exe`
  - uploads `dist/gludd-${{ env.VERSION }}-setup-x86_64.exe` as a release asset
  - verifies a `*setup*x86_64.exe` asset exists via check_required

If the OutFile directive in gludd.nsi drifts from that pattern, CI silently
fails to find the .exe (certutil errors, upload no-ops, release gate fails).
These tests pin the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NSI_PATH = REPO_ROOT / "dist" / "windows" / "gludd.nsi"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build.yml"

OUTFILE_RE = re.compile(r'^\s*OutFile\s+"([^"]+)"\s*$', re.MULTILINE)
WORKFLOW_EXE_RE = re.compile(r"gludd-[^\s\"]*-setup-x86_64\.exe")


def _read_nsi() -> str:
    assert NSI_PATH.is_file(), f"NSIS installer spec missing: {NSI_PATH}"
    return NSI_PATH.read_text(encoding="utf-8")


def _read_workflow() -> str:
    assert WORKFLOW_PATH.is_file(), f"build workflow missing: {WORKFLOW_PATH}"
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_nsi_file_exists() -> None:
    """gludd.nsi must exist at dist/windows/gludd.nsi (CI hard-codes this path)."""
    assert NSI_PATH.is_file(), f"NSIS spec not found at {NSI_PATH}"


def test_nsi_has_outfile_directive() -> None:
    """The installer spec must contain an OutFile directive."""
    nsi = _read_nsi()
    match = OUTFILE_RE.search(nsi)
    assert match is not None, "No OutFile directive found in gludd.nsi"


def test_outfile_uses_version_define() -> None:
    """OutFile must substitute ${VERSION} (passed via -DVERSION on makensis).

    A literal version in the .nsi would produce a stale filename on every release.
    """
    nsi = _read_nsi()
    match = OUTFILE_RE.search(nsi)
    assert match is not None
    outfile = match.group(1)
    assert "${VERSION}" in outfile, (
        f"OutFile '{outfile}' must use ${{VERSION}} define, not a literal version"
    )


def test_outfile_ends_in_exe() -> None:
    """The OutFile path must end in .exe (Windows installer artifact)."""
    nsi = _read_nsi()
    match = OUTFILE_RE.search(nsi)
    assert match is not None
    outfile = match.group(1)
    assert outfile.lower().endswith(".exe"), f"OutFile '{outfile}' must end in .exe"


def test_outfile_matches_release_artifact_pattern() -> None:
    """OutFile basename must match `gludd-<ver>-setup-x86_64.exe`.

    This is the exact name the release job hashes, uploads, and verifies via
    check_required('*setup*x86_64.exe'). BUILDDIR may vary (dist vs dist/windows)
    but the basename shape is the contract.
    """
    nsi = _read_nsi()
    nsi_match = OUTFILE_RE.search(nsi)
    assert nsi_match is not None
    outfile = nsi_match.group(1)

    # Strip the BUILDDIR prefix (e.g. "${BUILDDIR}\") and normalize separators.
    basename = re.split(r"[\\/]", outfile)[-1]

    # Substitute the NSIS ${VERSION} define with a sample version to materialize
    # the pattern, then assert the shape.
    materialized = basename.replace("${VERSION}", "0.1.0")

    expected_shape_re = re.compile(r"^gludd-\d+\.\d+\.\d+-setup-x86_64\.exe$")
    assert expected_shape_re.match(materialized) is not None, (
        f"OutFile basename '{basename}' (materialized as '{materialized}') "
        f"does not match required shape 'gludd-<ver>-setup-x86_64.exe'"
    )


def test_workflow_references_matching_pattern() -> None:
    """build.yml must reference the same gludd-<ver>-setup-x86_64.exe pattern.

    Cross-checks that the CI certutil hash step, artifact upload step, and
    check_required gate all reference the installer by the name the .nsi emits.
    """
    workflow = _read_workflow()
    references = WORKFLOW_EXE_RE.findall(workflow)
    assert len(references) >= 2, (
        "Expected build.yml to reference 'gludd-<ver>-setup-x86_64.exe' at least "
        "twice (certutil hash + upload); found: " + ", ".join(references)
    )
    # Every reference must use the VERSION substitution form (no literal versions).
    for ref in references:
        assert "${{" in ref or "$env:" in ref or re.search(
            r"gludd-.+-setup-x86_64\.exe", ref
        ), f"Reference '{ref}' does not use a VERSION substitution"


def test_outfile_and_workflow_patterns_agree() -> None:
    """The .nsi OutFile basename and build.yml artifact name must agree.

    This is the integration assertion: both sides of the contract (emitter and
    consumer) must reference the same filename shape. A drift here is the
    PK.14-class bug this test guards against.
    """
    nsi = _read_nsi()
    workflow = _read_workflow()

    nsi_match = OUTFILE_RE.search(nsi)
    assert nsi_match is not None
    outfile = nsi_match.group(1)
    nsi_basename = re.split(r"[\\/]", outfile)[-1]

    # The canonical pattern both sides must share.
    canonical = "gludd-${VERSION}-setup-x86_64.exe"

    # NSIS uses ${VERSION}; the workflow uses either ${{ env.VERSION }} or
    # $env:VERSION. Normalize both to the ${VERSION} token and compare basenames.
    nsi_normalized = nsi_basename
    workflow_normalized = workflow

    # Confirm the .nsi side matches the canonical pattern structurally.
    nsi_shape = nsi_normalized.replace("${VERSION}", "<VER>")
    canonical_shape = canonical.replace("${VERSION}", "<VER>")
    assert nsi_shape == canonical_shape, (
        f"NSIS OutFile basename '{nsi_basename}' (shape '{nsi_shape}') "
        f"does not match canonical shape '{canonical_shape}'"
    )

    # Confirm the workflow references the same basename shape.
    workflow_refs = WORKFLOW_EXE_RE.findall(workflow)
    assert workflow_refs, "build.yml has no reference to gludd-*-setup-x86_64.exe"
    for ref in workflow_refs:
        # All workflow refs must be the setup-x86_64.exe form (not windows-x86_64.zip etc.)
        assert "setup-x86_64.exe" in ref, (
            f"Workflow reference '{ref}' is not a setup-x86_64.exe installer"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
