"""Structural test: release job downloads artifacts from ALL build jobs.

Verifies that the release job's download-artifact step covers every build
job's uploaded artifact (platform builds, container metadata, and the locked
Ansible execution environment). Prevents orphan uploads — a build job producing
an artifact the release never downloads.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"

# Every build job that produces distributable release assets through
# actions/upload-artifact. Container and execution-environment images are
# represented by digest-addressed metadata plus fail-closed smoke attestations.
BUILD_JOBS: list[str] = [
    "linux",
    "macos",
    "windows",
    "termux",
    "container",
    "ansible-ee",
]


def _workflow_source() -> str:
    assert BUILD_YML.exists(), f"build.yml not found at {BUILD_YML}"
    return BUILD_YML.read_text()


def _extract_job_sections(src: str) -> dict[str, str]:
    """Return {job_name: section_text} for each job in the jobs: block."""
    sections: dict[str, str] = {}
    jobs_idx = src.find("\njobs:")
    if jobs_idx < 0:
        return sections
    body = src[jobs_idx:]
    lines = body.split("\n")
    job_starts: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^  ([A-Za-z][\w-]*):\s*$", line)
        if m:
            job_starts.append((m.group(1), i))
    for idx, (name, start) in enumerate(job_starts):
        end = job_starts[idx + 1][1] if idx + 1 < len(job_starts) else len(lines)
        sections[name] = "\n".join(lines[start:end])
    return sections


def _extract_upload_artifact_names(section: str) -> list[str]:
    """Extract artifact name: values from upload-artifact steps in a section.

    The step's display name: (e.g. "Upload coverage data") appears BEFORE
    uses:; the artifact name: appears AFTER uses: inside with:. We scan
    forward from uses: to capture only the artifact name.
    """
    names: list[str] = []
    lines = section.split("\n")
    i = 0
    while i < len(lines):
        if re.search(r"uses:\s*actions/upload-artifact", lines[i]):
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                # A new step at 6-space indent ends this step's with: block.
                if re.match(r"^      -\s", ln):
                    break
                m = re.match(r"^\s+name:\s*(.+?)\s*$", ln)
                if m:
                    names.append(m.group(1).strip())
                    break
                j += 1
            i = j
        else:
            i += 1
    return names


def _extract_release_download_config(src: str) -> dict[str, str]:
    """Extract pattern:/name: from the release job's download-artifact step(s)."""
    sections = _extract_job_sections(src)
    release = sections.get("release", "")
    config: dict[str, str] = {}
    lines = release.split("\n")
    i = 0
    while i < len(lines):
        if re.search(r"uses:\s*actions/download-artifact", lines[i]):
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                # A new step or a job-level key at 6-space indent ends the step.
                if re.match(r"^      -\s", ln) or re.match(r"^      [A-Za-z][\w-]*:", ln):
                    break
                m_pat = re.match(r"^\s+pattern:\s*(.+?)\s*$", ln)
                m_nm = re.match(r"^\s+name:\s*(.+?)\s*$", ln)
                if m_pat:
                    config["pattern"] = m_pat.group(1).strip()
                elif m_nm:
                    config["name"] = m_nm.group(1).strip()
                j += 1
            i = j
        else:
            i += 1
    return config


class TestReleaseDownloadsAllBuildArtifacts:
    """The release job MUST download artifacts from every build job."""

    def test_release_has_download_artifact_step(self) -> None:
        """Requirement 1: at least one download-artifact step exists."""
        src = _workflow_source()
        sections = _extract_job_sections(src)
        release = sections.get("release", "")
        assert re.search(r"uses:\s*actions/download-artifact", release), (
            "release job must have at least one download-artifact step"
        )

    def test_download_uses_gludd_pattern_or_explicit_names(self) -> None:
        """Requirement 2: download uses a gludd-* pattern or lists each name."""
        src = _workflow_source()
        config = _extract_release_download_config(src)
        assert config, (
            "release download-artifact step must specify a pattern: or name:"
        )
        if "pattern" in config:
            assert "gludd-" in config["pattern"], (
                f"download pattern must match gludd-* artifacts, got: "
                f"{config['pattern']}"
            )
        else:
            assert "name" in config, (
                "download-artifact must use pattern: or name:"
            )

    def test_every_build_job_artifact_is_downloaded(self) -> None:
        """Requirement 3: no orphan uploads.

        Every artifact uploaded by a release-producing build job must be
        matched by the release download pattern.
        """
        src = _workflow_source()
        sections = _extract_job_sections(src)
        config = _extract_release_download_config(src)

        uploaded: dict[str, str] = {}  # artifact_name -> job_name
        for job in BUILD_JOBS:
            section = sections.get(job, "")
            for art_name in _extract_upload_artifact_names(section):
                uploaded[art_name] = job

        assert uploaded, (
            f"Expected at least one upload-artifact across {BUILD_JOBS} — "
            f"found none"
        )

        if "pattern" in config:
            pattern = config["pattern"]
            orphans = {
                name: job
                for name, job in uploaded.items()
                if not fnmatch.fnmatch(name, pattern)
            }
            assert not orphans, (
                f"Build job artifacts NOT matched by release download pattern "
                f"'{pattern}': {orphans}. Widen the pattern or add the name."
            )
        elif "name" in config:
            explicit = config["name"]
            assert explicit in uploaded, (
                f"Release downloads '{explicit}' but no build job uploads it"
            )

    def test_expected_build_jobs_upload_gludd_artifacts(self) -> None:
        """Every release-producing job uploads a gludd-* artifact."""
        src = _workflow_source()
        sections = _extract_job_sections(src)
        missing: list[str] = []
        for job in BUILD_JOBS:
            section = sections.get(job, "")
            names = _extract_upload_artifact_names(section)
            gludd_names = [n for n in names if n.startswith("gludd-")]
            if not gludd_names:
                missing.append(job)
        assert not missing, (
            f"Build jobs with no gludd-* upload-artifact: {missing}. "
            f"Every job in {BUILD_JOBS} must upload a gludd-* artifact."
        )

    def test_container_and_ansible_ee_are_release_prerequisites(self) -> None:
        """Image metadata lanes must remain in the release fan-in."""
        src = _workflow_source()
        sections = _extract_job_sections(src)
        release = sections.get("release", "")
        for job in ("container", "ansible-ee"):
            assert "actions/upload-artifact" in sections.get(job, ""), (
                f"{job} must upload digest metadata and smoke attestations"
            )
            assert re.search(rf"\b{re.escape(job)}\b", release), (
                f"release job must require {job} before publishing"
            )
