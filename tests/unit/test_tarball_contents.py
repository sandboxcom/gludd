"""PK.12 — verify the release tarball bundles the required runtime assets.

The Build-and-Release workflow (``.github/workflows/build.yml``) packages a
per-platform ``.tar.gz`` from a staging directory (``dist/release``).  Each
tarball MUST carry, alongside the ``gludd`` binary, the installer script and
the runtime ``config/`` / ``templates/`` / ``playbooks/`` trees — otherwise an
operator who downloads the tarball cannot actually run gludd.  These tests
parse the workflow YAML and assert that every required asset is staged before
the ``tar czf`` invocation, on every job that produces a tarball.

The check is purely static (we parse the committed workflow file, not a built
tarball) so it runs in unit-test time without a CI build.  If someone deletes a
``cp dist/install.sh ...`` line from the workflow, the corresponding test goes
red here before a release ships a broken tarball.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest

BUILD_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build.yml"


def _read_build_yml() -> str:
    """Return the full text of build.yml.

    Kept as a function (not a module-level constant) so a missing file surfaces
    as a clear, single-point assertion rather than an ImportError at collection
    time.
    """
    assert BUILD_YML.is_file(), f"build.yml not found at {BUILD_YML}"
    return BUILD_YML.read_text(encoding="utf-8")


def _tarball_steps(yml: str) -> list[str]:
    """Extract the body of every ``Package tarball`` step in build.yml.

    A step is delimited by ``- name: Package tarball`` and the next ``- name:``
    or ``- if:`` line at the same indentation.  We only care about steps whose
    body contains ``tar czf`` — the macos job also has a ``Package .dmg`` step
    that re-stages the assets but does not produce a tarball.
    """
    steps: list[str] = []
    for match in re.finditer(
        r"- name: Package tarball\n(?P<body>(?:.*\n)+?)(?=      - name:|      - if:|\Z)",
        yml,
        re.MULTILINE,
    ):
        body = match.group("body")
        if "tar czf" in body:
            steps.append(body)
    return steps


class TestBuildYmlHasTarballStep:
    """Top-level: build.yml contains at least one ``tar czf`` tarball step."""

    def test_build_yml_contains_tar_czf(self) -> None:
        """The workflow must create at least one gzipped tarball."""
        yml = _read_build_yml()
        assert re.search(r"\btar czf\b", yml), (
            "build.yml has no `tar czf` step — no tarball is being produced"
        )

    def test_at_least_one_package_tarball_step(self) -> None:
        """At least one ``- name: Package tarball`` step must exist."""
        yml = _read_build_yml()
        steps = _tarball_steps(yml)
        assert len(steps) >= 1, (
            "build.yml has no `- name: Package tarball` step containing `tar czf`"
        )


@pytest.fixture(scope="module")
def tarball_steps() -> list[str]:
    """All ``Package tarball`` step bodies (one per linux/macos/termux job)."""
    return _tarball_steps(_read_build_yml())


class TestTarballStagingContents:
    """Each tarball-producing job must stage every required asset.

    The tests are parameterized over every ``Package tarball`` step found so a
    regression on any one platform (linux-x86_64, macos-arm64, linux-aarch64)
    is reported independently.
    """

    REQUIRED_ASSETS: ClassVar[list[tuple[str, str]]] = [
        ("gludd binary", r"cp\s+dist/gludd\s+dist/release/gludd"),
        ("install.sh", r"cp\s+dist/install\.sh\s+dist/release/install\.sh"),
        ("config/", r"cp\s+-r\s+config\s+dist/release/config"),
        ("templates/", r"cp\s+-r\s+templates\s+dist/release/templates"),
        ("playbooks/", r"cp\s+-r\s+playbooks\s+dist/release/playbooks"),
    ]

    def test_tarball_steps_nonempty(self, tarball_steps: list[str]) -> None:
        """Sanity: the fixture resolved at least one tarball step."""
        assert tarball_steps, "no tarball steps parsed from build.yml"

    @pytest.mark.parametrize(
        "label,pattern",
        REQUIRED_ASSETS,
        ids=[label for label, _ in REQUIRED_ASSETS],
    )
    def test_tarball_staging_includes(
        self,
        tarball_steps: list[str],
        label: str,
        pattern: str,
    ) -> None:
        """Every ``Package tarball`` step must stage ``label`` via cp/cp -r."""
        regex = re.compile(pattern)
        missing_jobs = [i for i, body in enumerate(tarball_steps) if not regex.search(body)]
        assert not missing_jobs, (
            f"tarball step(s) {missing_jobs} do not stage required asset '{label}' "
            f"(expected a command matching /{pattern}/)"
        )


class TestInstallShExists:
    """The install.sh referenced by the tarball step must exist in the repo."""

    def test_dist_install_sh_present(self) -> None:
        """``dist/install.sh`` is the source copied into the tarball."""
        install_sh = BUILD_YML.parent.parent.parent / "dist" / "install.sh"
        assert install_sh.is_file(), (
            f"dist/install.sh not found at {install_sh} — tarball step copies a "
            "non-existent file"
        )

    def test_install_sh_nonempty(self) -> None:
        """An empty install.sh would be a broken installer."""
        install_sh = BUILD_YML.parent.parent.parent / "dist" / "install.sh"
        assert install_sh.is_file(), "dist/install.sh not present"
        assert install_sh.stat().st_size > 0, "dist/install.sh is empty"


class TestTarballCommandShape:
    """Structural checks on the ``tar czf`` invocation itself."""

    def test_tar_czf_uses_release_dir(self, tarball_steps: list[str]) -> None:
        """``tar czf`` must archive the contents of ``dist/release``.

        The steps ``cd dist/release`` before archiving; if a future refactor
        archives the wrong directory the tarball would bundle repo source
        instead of the staged runtime assets.
        """
        for i, body in enumerate(tarball_steps):
            assert "dist/release" in body, (
                f"tarball step {i} does not reference dist/release — archiving "
                "the wrong directory"
            )
