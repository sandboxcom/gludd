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
MAKEFILE = ROOT / "Makefile"


def _make_target_block(target: str) -> str:
    """Return one top-level Make target without depending on GNU make parsing."""
    text = MAKEFILE.read_text(encoding="utf-8")
    start = text.find(f"{target}:")
    assert start >= 0, f"Makefile lost the {target} target"
    end = text.find("\n\n", start)
    assert end >= 0, f"Makefile target {target} is not blank-line terminated"
    return text[start:end]


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

    DEBIAN_REQUIRED_FIELDS: tuple[str, ...] = (
        "Package:",
        "Version:",
        "Architecture:",
        "Maintainer:",
        "Description:",
    )

    RPM_REQUIRED_SECTIONS: tuple[str, ...] = (
        "%description",
        "%prep",
        "%build",
        "%install",
        "%files",
        "%changelog",
    )

    NSIS_REQUIRED_DIRECTIVES: tuple[str, ...] = (
        'Name "',
        'OutFile "',
        "Section",
        "WriteUninstaller",
    )

    VERSION_PLACEHOLDER_TEMPLATES: tuple[str, ...] = (
        "dist/debian/control",
        "dist/rpm/gludd.spec",
        "dist/windows/gludd.nsi",
    )

    @pytest.mark.parametrize("field", DEBIAN_REQUIRED_FIELDS)
    def test_debian_control_has_required_fields(self, field: str):
        """PK.6: Debian control has all required fields."""
        fpath = ROOT / "dist" / "debian" / "control"
        assert fpath.exists(), "dist/debian/control must be committed"
        assert field in fpath.read_text(), (
            f"dist/debian/control must have {field} field"
        )

    @pytest.mark.parametrize("section", RPM_REQUIRED_SECTIONS)
    def test_rpm_spec_has_required_sections(self, section: str):
        """PK.7: RPM spec has all required sections."""
        fpath = ROOT / "dist" / "rpm" / "gludd.spec"
        assert fpath.exists(), "dist/rpm/gludd.spec must be committed"
        assert section in fpath.read_text(), (
            f"dist/rpm/gludd.spec must have {section} section"
        )

    @pytest.mark.parametrize("directive", NSIS_REQUIRED_DIRECTIVES)
    def test_nsi_has_required_directives(self, directive: str):
        """PK.8: NSIS has required directives."""
        fpath = ROOT / "dist" / "windows" / "gludd.nsi"
        assert fpath.exists(), "dist/windows/gludd.nsi must be committed"
        assert directive in fpath.read_text(), (
            f"dist/windows/gludd.nsi must have {directive}"
        )

    @pytest.mark.parametrize("rel_path", VERSION_PLACEHOLDER_TEMPLATES)
    def test_template_has_version_placeholder(self, rel_path: str):
        """PK.9: VERSION_PLACEHOLDER exists in all 3 packaging templates."""
        fpath = ROOT / rel_path
        assert fpath.exists(), f"{rel_path} must be committed"
        assert "VERSION_PLACEHOLDER" in fpath.read_text(), (
            f"{rel_path} must contain VERSION_PLACEHOLDER "
            "for sed substitution at build time"
        )

    def test_install_sh_is_executable(self):
        fpath = ROOT / "dist" / "install.sh"
        if fpath.exists():
            assert (fpath.stat().st_mode & 0o111) != 0, (
                "dist/install.sh must be executable"
            )


def test_rpm_package_creates_portable_rpmbuild_tree() -> None:
    """CI uses /bin/sh, so Bash-only brace expansion must not create the tree."""
    block = _make_target_block("rpm-package")
    assert "{BUILD,RPMS,SOURCES,SPECS,SRPMS}" not in block, (
        "rpm-package uses Bash-only brace expansion under make's POSIX /bin/sh; "
        "run 30331174104 created one literal brace-named directory and then "
        "failed copying dist/gludd into the missing SOURCES directory"
    )
    for directory in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        assert f"$(RPMBUILD_DIR)/{directory}" in block, (
            f"rpm-package must explicitly create namespaced {directory}; "
            "do not depend on shell-specific directory expansion"
        )


def test_rpm_package_build_tree_is_namespaced_to_checkout() -> None:
    """Parallel projects/releases must not share one fixed temporary RPM tree."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    block = _make_target_block("rpm-package")
    assert "RPMBUILD_DIR := $(abspath dist/rpmbuild)" in makefile
    assert "/tmp/gludd-rpmbuild" not in block, (
        "rpm-package must use its checkout-local dist/rpmbuild directory; a fixed "
        "/tmp tree lets concurrent Gludd builds delete or overwrite each other"
    )


def test_clean_preserves_tracked_distribution_templates() -> None:
    """Clean generated outputs through Git's ignore contract, not all of dist/."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    start = makefile.index("\nclean:\n") + 1
    block = makefile[start : makefile.index("\n\n", start)]

    assert "CLEAN_VALIDATE_ONLY" in block
    assert "git clean -fdX -- dist" in block
    assert "rm -rf .venv dist " not in block
    for template in TestPackagingTemplatesCommitted.REQUIRED_FILES:
        assert (ROOT / template).is_file()


def test_windows_installer_accepts_native_and_cross_packaging_binary_names() -> None:
    """The Make target must package Windows output and fail on absent input."""
    block = _make_target_block("windows-installer")
    assert "dist/gludd.exe" in block
    assert "dist/gludd" in block
    assert "else echo \"ERROR: no gludd binary" in block
    assert "-DBUILDDIR=.." in block, (
        "NSIS changes to the script directory; BUILDDIR=.. is required for the "
        "installer to land in dist/ where release upload expects it"
    )
    assert "2>/dev/null || true" not in block, (
        "windows-installer must not hide a missing source binary and continue"
    )
