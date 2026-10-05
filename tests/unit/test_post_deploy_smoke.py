"""Structural test: the release job in .github/workflows/build.yml MUST run a
post-deploy smoke test on the just-published binary.

CP.18: A release that publishes assets but never exercises the published
artifact is not verified — the binary could be corrupt, the wrong arch, or
missing executable bits. This test pins the existence of a post-deploy smoke
step that:

  1. Downloads the published release asset (gh release download)
  2. Makes it executable (chmod +x)
  3. Runs a smoke command on it (gludd --version or gludd --help)
  4. Fails the job on non-zero exit

If a future refactor removes or weakens this step, the test goes red and the
gate blocks the regression from landing.

Follows the conventions in tests/unit/test_release_pipeline_structure.py:
text + structural assertions against the workflow YAML.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"

SMOKE_STEP_NAME_RE = re.compile(
    r"Post-deploy smoke", re.IGNORECASE
)
SMOKE_KEYWORDS = {
    "download": re.compile(r"gh\s+release\s+download", re.IGNORECASE),
    "chmod": re.compile(r"chmod\s+\+x", re.IGNORECASE),
    "version_cmd": re.compile(r"gludd\s+(--version|version|(--help|help))", re.IGNORECASE),
}


def _workflow_source() -> str:
    assert BUILD_YML.exists(), f"build.yml not found at {BUILD_YML}"
    return BUILD_YML.read_text()


def _release_job_steps(src: str) -> list[dict[str, str]]:
    r"""Return the list of release-job steps as {name, body} dicts.

    Walks the YAML text: finds `  release:` job, then iterates lines until
    the next top-level job (`^  \w` at column 2 with no deeper indent) or EOF.
    Within the release job, collects `- name:` / `- uses:` step blocks.
    """
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        if re.match(r"^  release:\s*$", lines[i]):
            i += 1
            break
        i += 1
    else:
        return []

    steps: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    while i < len(lines):
        line = lines[i]
        if re.match(r"^  \S", line) and not line.startswith("    "):
            break
        step_match = re.match(r"^      - (?:name:\s*(.*?))?\s*$", line)
        if step_match:
            if current is not None:
                steps.append(current)
            name = (step_match.group(1) or "").strip().strip('"').strip("'")
            current = {"name": name, "body": ""}
        elif current is not None:
            current["body"] += line + "\n"
        i += 1
    if current is not None:
        steps.append(current)
    return steps


class TestPostDeploySmokeStep:
    """The release job MUST include a post-deploy smoke step."""

    def test_release_job_exists(self) -> None:
        src = _workflow_source()
        assert re.search(r"^  release:\s*$", src, re.MULTILINE), (
            "release job must exist in build.yml"
        )

    def test_smoke_step_present(self) -> None:
        """A step whose name mentions 'smoke' must exist in the release job."""
        steps = _release_job_steps(_workflow_source())
        assert steps, "release job must have at least one step"
        matches = [s for s in steps if SMOKE_STEP_NAME_RE.search(s["name"])]
        assert matches, (
            "release job MUST contain a post-deploy smoke step "
            "(a step whose name contains 'smoke'). Found step names: "
            + ", ".join(repr(s["name"]) for s in steps)
        )

    def test_smoke_step_downloads_published_asset(self) -> None:
        """The smoke step MUST download the just-published asset via gh."""
        steps = _release_job_steps(_workflow_source())
        smoke_steps = [s for s in steps if SMOKE_STEP_NAME_RE.search(s["name"])]
        assert smoke_steps, "no smoke step found — run test_smoke_step_present first"
        bodies = "\n".join(s["body"] for s in smoke_steps)
        assert SMOKE_KEYWORDS["download"].search(bodies), (
            "smoke step must use `gh release download` to fetch the published "
            "binary — testing a locally-built artifact does not verify the "
            "release asset itself."
        )

    def test_smoke_step_selects_downloaded_archive(self) -> None:
        """The smoke step MUST select and extract the downloaded archive."""
        steps = _release_job_steps(_workflow_source())
        smoke_steps = [s for s in steps if SMOKE_STEP_NAME_RE.search(s["name"])]
        assert smoke_steps
        bodies = "\n".join(s["body"] for s in smoke_steps)
        assert "ARCHIVE=" in bodies, (
            "smoke step must bind the exact downloaded Linux archive"
        )
        assert re.search(r'tar\s+-xzf\s+"\$ARCHIVE"', bodies), (
            "smoke step must extract the bound archive directly"
        )
        assert "-not -name '*.tar.gz'" not in bodies, (
            "smoke step must not exclude the archive it needs to execute"
        )

    def test_smoke_step_makes_executable(self) -> None:
        """The smoke step MUST chmod +x the downloaded binary."""
        steps = _release_job_steps(_workflow_source())
        smoke_steps = [s for s in steps if SMOKE_STEP_NAME_RE.search(s["name"])]
        assert smoke_steps
        bodies = "\n".join(s["body"] for s in smoke_steps)
        assert SMOKE_KEYWORDS["chmod"].search(bodies), (
            "smoke step must `chmod +x` the downloaded binary before running it"
        )

    def test_smoke_step_runs_version_or_help(self) -> None:
        """The smoke step MUST execute gludd --version or gludd --help."""
        steps = _release_job_steps(_workflow_source())
        smoke_steps = [s for s in steps if SMOKE_STEP_NAME_RE.search(s["name"])]
        assert smoke_steps
        bodies = "\n".join(s["body"] for s in smoke_steps)
        assert SMOKE_KEYWORDS["version_cmd"].search(bodies), (
            "smoke step must run `gludd --version` or `gludd --help` "
            "on the downloaded binary"
        )

    def test_smoke_step_fails_on_nonzero_exit(self) -> None:
        """The smoke step MUST fail the job on non-zero exit.

        Either `set -e` at the top of the run block, or an explicit
        `exit 1` in the failure branch.
        """
        steps = _release_job_steps(_workflow_source())
        smoke_steps = [s for s in steps if SMOKE_STEP_NAME_RE.search(s["name"])]
        assert smoke_steps
        bodies = "\n".join(s["body"] for s in smoke_steps)
        has_set_e = re.search(r"(^|\n)\s*set\s+-e\b", bodies)
        has_exit_1 = re.search(r"exit\s+1", bodies)
        assert has_set_e or has_exit_1, (
            "smoke step must fail the job on non-zero exit — use `set -e` "
            "or an explicit `exit 1` in the failure branch"
        )

    def test_smoke_step_runs_on_tag_only(self) -> None:
        """The release job (and thus the smoke step) only runs on tag pushes.

        This is a sanity check: the release job's `if:` must restrict to
        `refs/tags/v*` so the smoke step doesn't try to download a
        non-existent release on push/PR runs.
        """
        src = _workflow_source()
        idx = src.find("  release:")
        assert idx >= 0
        section = src[idx:idx + 2000]
        assert re.search(r"if:\s*startsWith\(github\.ref,\s*'refs/tags/v'\)", section), (
            "release job must have `if: startsWith(github.ref, 'refs/tags/v')` "
            "so the smoke step only runs against a real published release"
        )


class TestPostDeployDebValidation:
    """The release job also validates the .deb package post-deploy.

    This is a secondary post-deploy check — not strictly required by CP.18,
    but pinned here so a future refactor doesn't silently drop it.
    """

    @pytest.mark.parametrize("needle", ["dpkg-deb", "*.deb"])
    def test_deb_validation_step_present(self, needle: str) -> None:
        src = _workflow_source()
        assert needle in src, (
            f"release job must reference '{needle}' for .deb post-deploy validation"
        )
