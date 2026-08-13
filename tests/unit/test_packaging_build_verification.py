"""Packaging build verification tests (PK.10-PK.13).

Verifies the CI build pipeline (.github/workflows/build.yml) and gludd.spec
(PyInstaller) meet the packaging requirements. Parses YAML with yaml.safe_load.
Missing steps are documented as known gaps (no build.yml modification).
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = REPO_ROOT / ".github" / "workflows" / "build.yml"
GLUDD_SPEC = REPO_ROOT / "gludd.spec"


@pytest.fixture(scope="module")
def build_workflow() -> dict:
    """Load .github/workflows/build.yml with yaml.safe_load."""
    with BUILD_YML.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def spec_source() -> str:
    """Read gludd.spec source as text (it's Python, not YAML)."""
    return GLUDD_SPEC.read_text(encoding="utf-8")


def _job_steps(workflow: dict, job_name: str) -> list[dict]:
    """Return the steps list for a named job, or empty list if absent."""
    job = workflow.get("jobs", {}).get(job_name, {})
    return job.get("steps", []) or []


def _steps_text(workflow: dict, job_name: str) -> str:
    """Concatenate all `run` blocks for a job into one searchable string."""
    chunks: list[str] = []
    for step in _job_steps(workflow, job_name):
        run = step.get("run")
        if isinstance(run, str):
            chunks.append(run)
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# PK.10: gludd.spec (PyInstaller) — exists and references correct entry point
# ---------------------------------------------------------------------------


class TestPK10GluddSpec:
    """PK.10: gludd.spec exists and references correct entry point."""

    def test_spec_file_exists(self) -> None:
        assert GLUDD_SPEC.is_file(), f"gludd.spec missing at {GLUDD_SPEC}"

    def test_spec_has_analysis(self, spec_source: str) -> None:
        assert re.search(r"\bAnalysis\s*\(", spec_source), (
            "gludd.spec missing Analysis(...) block"
        )

    def test_spec_has_exe(self, spec_source: str) -> None:
        assert re.search(r"\bEXE\s*\(", spec_source), (
            "gludd.spec missing EXE(...) block"
        )

    def test_spec_has_complete_bundle_layout(self, spec_source: str) -> None:
        """Accept complete PyInstaller onefile and onedir bundle layouts."""
        exe_match = re.search(
            r"\bEXE\s*\((?P<body>.*?)^\)",
            spec_source,
            re.DOTALL | re.MULTILINE,
        )
        assert exe_match, "gludd.spec EXE(...) block could not be parsed"

        collect_match = re.search(
            r"\bCOLLECT\s*\((?P<body>.*?)^\)",
            spec_source,
            re.DOTALL | re.MULTILINE,
        )
        if collect_match:
            collect_body = collect_match.group("body")
            for required in ("exe", "a.binaries", "a.datas"):
                assert required in collect_body, (
                    "gludd.spec onedir COLLECT(...) block is incomplete: "
                    f"missing {required}"
                )
            return

        exe_body = exe_match.group("body")
        for required in (
            "a.scripts",
            "a.binaries",
            "a.zipfiles",
            "a.datas",
        ):
            assert required in exe_body, (
                "gludd.spec onefile EXE(...) block is incomplete: "
                f"missing {required}"
            )
        assert not re.search(r"\bexclude_binaries\s*=\s*True\b", exe_body), (
            "gludd.spec onefile EXE(...) excludes binaries without a "
            "COLLECT(...) block"
        )

    def test_spec_entry_script_is_cli(self, spec_source: str) -> None:
        match = re.search(r"Analysis\s*\(\s*\[([^\]]+)\]", spec_source)
        assert match, "gludd.spec Analysis first-arg (entry script) not found"
        entry = match.group(1).strip().strip("'\"")
        assert entry.endswith("cli.py"), (
            f"gludd.spec entry script should point at cli.py, got: {entry}"
        )
        assert "general_ludd" in entry, (
            f"gludd.spec entry script should be in general_ludd package, got: {entry}"
        )


# ---------------------------------------------------------------------------
# PK.11: each build job (linux/macos/windows) generates a .sha256 checksum
# ---------------------------------------------------------------------------


class TestPK11Sha256Checksums:
    """PK.11: linux/macos/windows build jobs each produce a .sha256 file."""

    SHA256_PATTERNS: tuple[str, ...] = (
        r"sha256sum\b",
        r"shasum\s+-a\s+256",
        r"Get-FileHash\b.*-Algorithm\s+SHA256\b",
    )

    @pytest.mark.parametrize(
        "job_name", ["linux", "macos", "windows"], ids=lambda n: f"job:{n}"
    )
    def test_build_job_has_sha256_step(
        self, build_workflow: dict, job_name: str
    ) -> None:
        steps = _job_steps(build_workflow, job_name)
        assert steps, f"build.yml job '{job_name}' missing or has no steps"

        combined = _steps_text(build_workflow, job_name)
        assert any(
            re.search(p, combined) for p in self.SHA256_PATTERNS
        ), (
            f"build.yml job '{job_name}' has no supported checksum "
            f"step producing a .sha256 checksum"
        )

    @pytest.mark.parametrize(
        "job_name", ["linux", "macos", "windows"], ids=lambda n: f"job:{n}"
    )
    def test_build_job_emits_sha256_file(
        self, build_workflow: dict, job_name: str
    ) -> None:
        combined = _steps_text(build_workflow, job_name)
        assert ".sha256" in combined, (
            f"build.yml job '{job_name}' never writes a .sha256 output file"
        )


# ---------------------------------------------------------------------------
# PK.12: tarball step includes gludd binary, install.sh, config/, templates/
# ---------------------------------------------------------------------------


class TestPK12TarballContents:
    """PK.12: tarball packages gludd binary, install.sh, config/, templates/."""

    REQUIRED_TARBALL_ENTRIES: tuple[str, ...] = ("gludd", "install.sh", "config", "templates")

    @pytest.mark.parametrize(
        "job_name", ["linux", "macos"], ids=lambda n: f"job:{n}"
    )
    def test_tarball_includes_required_entries(
        self, build_workflow: dict, job_name: str
    ) -> None:
        # Only linux/macos produce tar.gz tarballs; windows uses zip (PK.13 gap).
        combined = _steps_text(build_workflow, job_name)
        assert "tar czf" in combined or "tar -czf" in combined, (
            f"build.yml job '{job_name}' has no tarball creation step (tar czf)"
        )

        missing: list[str] = []
        for entry in self.REQUIRED_TARBALL_ENTRIES:
            # Look for the entry being copied into the release staging dir.
            # Accept both `cp dist/X dist/release/X` and `cp -r X dist/release/X`.
            patterns = [
                rf"cp\s+(?:-r\s+)?dist/{re.escape(entry)}\b",
                rf"cp\s+(?:-r\s+)?dist/{re.escape(entry)}\s+dist/release",
                rf"cp\s+(?:-r\s+)?{re.escape(entry)}\s+dist/release",
            ]
            if not any(re.search(p, combined) for p in patterns):
                missing.append(entry)

        assert not missing, (
            f"build.yml job '{job_name}' tarball missing required entries: "
            f"{missing}. Expected each of {self.REQUIRED_TARBALL_ENTRIES} "
            f"to be copied into the release staging dir."
        )


# ---------------------------------------------------------------------------
# PK.13: macos build creates a .dmg (hdiutil or create-dmg)
# ---------------------------------------------------------------------------


class TestPK13MacosDmg:
    """PK.13: macos build job produces a .dmg disk image."""

    DMG_TOOL_PATTERNS: tuple[str, ...] = (
        r"hdiutil\s+create",
        r"create-dmg\b",
    )

    def test_macos_job_has_dmg_step(self, build_workflow: dict) -> None:
        combined = _steps_text(build_workflow, "macos")
        assert combined, "build.yml job 'macos' missing or has no run steps"

        assert any(
            re.search(p, combined) for p in self.DMG_TOOL_PATTERNS
        ), (
            "build.yml macos job has no hdiutil/create-dmg step to produce a .dmg"
        )

    def test_macos_job_emits_dmg_file(self, build_workflow: dict) -> None:
        combined = _steps_text(build_workflow, "macos")
        assert ".dmg" in combined, (
            "build.yml macos job never writes a .dmg output file"
        )

    def test_macos_dmg_step_named(self, build_workflow: dict) -> None:
        steps = _job_steps(build_workflow, "macos")
        dmg_step_names = [
            (step.get("name") or "")
            for step in steps
            if "dmg" in (step.get("name") or "").lower()
            or "hdiutil" in (step.get("run") or "")
        ]
        assert dmg_step_names, (
            "build.yml macos job has no step named for .dmg creation"
        )
