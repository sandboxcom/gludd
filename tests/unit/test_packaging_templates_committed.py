"""Verify all packaging template files exist and are valid.

These are build INPUT files referenced by make deb-package, make rpm-package,
and the NSIS installer step in .github/workflows/build.yml. Missing files
cause build failures that block release artifact creation — exactly what
happened in Session 52 when dist/debian/control and dist/windows/gludd.nsi
were never committed, causing 6+ hours of failed CI runs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


class TestPackagingTemplatesCommitted:
    """Packaging template files must be committed to the repo."""

    REQUIRED_FILES: tuple[str, ...] = (
        "dist/debian/control",
        "dist/rpm/gludd.spec",
        "dist/windows/gludd.nsi",
        "dist/install.sh",
    )

    @pytest.mark.parametrize("rel_path", REQUIRED_FILES)
    def test_file_exists(self, rel_path: str):
        fpath = ROOT / rel_path
        assert fpath.exists(), (
            f"{rel_path} must be committed — required by deb-package, "
            f"rpm-package, or NSIS installer build step. Missing this file "
            f"causes the release pipeline to fail."
        )

    def test_debian_control_has_version_placeholder(self):
        fpath = ROOT / "dist" / "debian" / "control"
        if fpath.exists():
            assert "VERSION_PLACEHOLDER" in fpath.read_text(), (
                "dist/debian/control must contain VERSION_PLACEHOLDER "
                "for sed substitution at build time"
            )

    def test_rpm_spec_has_version_placeholder(self):
        fpath = ROOT / "dist" / "rpm" / "gludd.spec"
        if fpath.exists():
            assert "VERSION_PLACEHOLDER" in fpath.read_text(), (
                "dist/rpm/gludd.spec must contain VERSION_PLACEHOLDER "
                "for sed substitution at build time"
            )

    def test_debian_control_has_required_fields(self):
        fpath = ROOT / "dist" / "debian" / "control"
        if fpath.exists():
            content = fpath.read_text()
            for field in ["Package:", "Version:", "Architecture:", "Description:"]:
                assert field in content, (
                    f"dist/debian/control must have {field} field"
                )

    def test_rpm_spec_has_required_sections(self):
        fpath = ROOT / "dist" / "rpm" / "gludd.spec"
        if fpath.exists():
            content = fpath.read_text()
            for section in ["%description", "%install", "%files"]:
                assert section in content, (
                    f"dist/rpm/gludd.spec must have {section} section"
                )

    def test_nsi_has_required_directives(self):
        fpath = ROOT / "dist" / "windows" / "gludd.nsi"
        if fpath.exists():
            content = fpath.read_text()
            for directive in ['Name "', 'OutFile "', "Section"]:
                assert directive in content, (
                    f"dist/windows/gludd.nsi must have {directive}"
                )

    def test_install_sh_is_executable(self):
        fpath = ROOT / "dist" / "install.sh"
        if fpath.exists():
            assert (fpath.stat().st_mode & 0o111) != 0, (
                "dist/install.sh must be executable"
            )
