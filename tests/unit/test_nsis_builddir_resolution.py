r"""Structural guard against the NSIS BUILDDIR path resolution regression.

Background
----------
The release was blocked for DAYS by a path-resolution bug between
``.github/workflows/build.yml`` and ``dist/windows/gludd.nsi``.

NSIS resolves ``OutFile`` relative to the SCRIPT file location, not the CWD.
The script lives at ``dist/windows/gludd.nsi``. The CI contract is:

  & $makensis /WX "/DVERSION=$env:VERSION" "/DBUILDDIR=.." dist/windows/gludd.nsi
  OutFile "${BUILDDIR}\gludd-${VERSION}-setup-x86_64.exe"

With ``BUILDDIR=".."``, OutFile resolves to
``dist/windows/../gludd-<ver>-setup-x86_64.exe`` = ``dist/gludd-<ver>-setup-x86_64.exe``,
which matches what the CI ``Get-FileHash`` and ``upload-artifact`` steps expect.

The bug: a previous "fix" changed BUILDDIR to ``"dist"`` thinking OutFile
resolves relative to CWD. That produced ``dist/windows/dist/gludd-...exe`` —
the checksum step failed to find the file at ``dist/gludd-...exe`` and the
Windows installer artifact never shipped.

These tests pin the CORRECT contract so the bug cannot recur.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build.yml"
NSI_PATH = REPO_ROOT / "dist" / "windows" / "gludd.nsi"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.fail(f"required file missing: {path}")
    return path.read_text(encoding="utf-8")


def _extract_makensis_command(workflow: str) -> str:
    match = re.search(r"^\s*&\s+\$makensis\s+.+$", workflow, re.MULTILINE)
    assert match, "makensis invocation not found in build.yml"
    return match.group(0)


# ---------------------------------------------------------------------------
# 1. makensis uses -DBUILDDIR=".."  (relative to the script at dist/windows/)
# ---------------------------------------------------------------------------


def test_makensis_uses_parent_dir_for_builddir() -> None:
    """BUILDDIR MUST be '..' — NOT 'dist'.

    '..' is relative to the .nsi script location (dist/windows/), so it
    resolves to dist/. Using 'dist' would put the output in
    dist/windows/dist/ and break certutil + artifact upload.
    """
    workflow = _read(WORKFLOW_PATH)
    cmd = _extract_makensis_command(workflow)

    assert '"/DBUILDDIR=.."' in cmd, (
        f"makensis must pass \"/DBUILDDIR=..\" (relative to script at "
        f"dist/windows/). Got: {cmd!r}"
    )


def test_regression_builddir_dist_is_rejected() -> None:
    """Regression: BUILDDIR='dist' is the bug that blocked the release.

    This test FAILS if someone re-introduces /DBUILDDIR=dist.
    """
    workflow = _read(WORKFLOW_PATH)
    cmd = _extract_makensis_command(workflow)

    assert '"/DBUILDDIR=dist"' not in cmd, (
        "REGRESSION: /DBUILDDIR=dist puts OutFile in dist/windows/dist/ "
        "and breaks the Windows release. Use -DBUILDDIR=\"..\" instead."
    )


# ---------------------------------------------------------------------------
# 2. OutFile in gludd.nsi references ${BUILDDIR}
# ---------------------------------------------------------------------------


def test_outfile_uses_builddir_variable() -> None:
    """OutFile must reference ${BUILDDIR} so the -D flag controls the path."""
    nsi = _read(NSI_PATH)
    match = re.search(r"^OutFile\s+(.+)$", nsi, re.MULTILINE)
    assert match, "OutFile directive not found in gludd.nsi"
    outfile_directive = match.group(1)

    assert "${BUILDDIR}" in outfile_directive, (
        f"OutFile must reference ${{BUILDDIR}}. Got: {outfile_directive!r}"
    )


def test_builddir_guarded_by_ifndef() -> None:
    """An undefined BUILDDIR must halt compilation, not silently mis-name output."""
    nsi = _read(NSI_PATH)
    assert re.search(r"!ifndef\s+BUILDDIR", nsi), (
        "gludd.nsi must guard BUILDDIR with !ifndef (prevents silent mis-naming)"
    )


# ---------------------------------------------------------------------------
# 3. Resolved output path lands in dist/, NOT dist/windows/dist/
# ---------------------------------------------------------------------------


def test_resolved_outfile_lands_in_dist_root() -> None:
    """BUILDDIR='..' + script at dist/windows/ + OutFile '${BUILDDIR}\\...'

    must resolve to dist/gludd-<ver>-setup-x86_64.exe — the file the CI's
    checksum and upload-artifact steps expect.
    """
    workflow = _read(WORKFLOW_PATH)
    cmd = _extract_makensis_command(workflow)
    builddir_match = re.search(r'"/DBUILDDIR=([^"]+)"', cmd)
    assert builddir_match, f"BUILDDIR not passed to makensis. Got: {cmd!r}"
    builddir = builddir_match.group(1)

    nsi = _read(NSI_PATH)
    outfile_match = re.search(r'^OutFile\s+"?\$\{BUILDDIR\}\\([^"]+)"?', nsi, re.MULTILINE)
    assert outfile_match, "OutFile must use ${BUILDDIR}\\<filename> form"
    outfile_tail = outfile_match.group(1)

    # Script lives at dist/windows/, so relative-to-script resolution means
    # the OutFile is at (dist/windows/) + builddir + outfile_tail
    script_dir = NSI_PATH.parent  # dist/windows/
    resolved = (script_dir / builddir / outfile_tail).resolve()
    repo_root = NSI_PATH.parents[2]

    try:
        rel = resolved.relative_to(repo_root)
    except ValueError:
        pytest.fail(f"resolved OutFile {resolved} is outside the repo")

    parts = rel.parts
    assert parts[0] == "dist", f"OutFile must land in dist/, got {rel}"
    assert "windows" not in parts, (
        f"REGRESSION: OutFile resolves under dist/windows/ — {rel}. "
        f"BUILDDIR must be '..' not 'dist'."
    )
    assert str(rel).startswith("dist/gludd-"), (
        f"OutFile must be dist/gludd-<ver>-setup-x86_64.exe, got {rel}"
    )
    assert str(rel).endswith("-setup-x86_64.exe"), (
        f"OutFile must end with -setup-x86_64.exe, got {rel}"
    )


# ---------------------------------------------------------------------------
# 4. Get-FileHash references the correct resolved path
# ---------------------------------------------------------------------------


def test_get_file_hash_references_dist_root_path() -> None:
    """PowerShell hashes the installer resolved into the dist root."""
    workflow = _read(WORKFLOW_PATH)
    assert '$installerPath = "dist/gludd-$env:VERSION-setup-x86_64.exe"' in workflow
    assert re.search(
        r"Get-FileHash\s+-LiteralPath\s+\$installerPath\s+-Algorithm\s+SHA256",
        workflow,
    ), (
        "Get-FileHash must hash the installer at the resolved dist-root path"
    )


def test_checksum_does_not_reference_windows_subdir() -> None:
    """Regression: checksum input must NOT reference dist/windows/dist/..."""
    workflow = _read(WORKFLOW_PATH)
    assert "dist/windows/dist/gludd-" not in workflow, (
        "REGRESSION: checksum references dist/windows/dist/ — the bug class "
        "that blocked the release. Output path must be dist/gludd-...exe"
    )


# ---------------------------------------------------------------------------
# 5. upload-artifact step references the correct path
# ---------------------------------------------------------------------------


def test_upload_artifact_references_dist_root_path() -> None:
    """upload-artifact must reference dist/gludd-$VERSION-setup-x86_64.exe(.sha256)."""
    workflow = _read(WORKFLOW_PATH)
    assert re.search(
        r"dist/gludd-\$\{\{?\s*env\.VERSION\s*\}?}-setup-x86_64\.exe",
        workflow,
    ), "upload-artifact must include dist/gludd-$VERSION-setup-x86_64.exe"
