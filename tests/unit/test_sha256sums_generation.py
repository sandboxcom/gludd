"""Structural test: release job generates a SHA256SUMS aggregate file.

Verifies that .github/workflows/build.yml's `release` job:
1. Has a step that runs `sha256sum ... > SHA256SUMS` (or equivalent) after
   staging release assets.
2. Uploads SHA256SUMS as a release asset (via `files: release-assets/*` or
   an explicit `gh release upload ... SHA256SUMS`).

This pins CP.19: an aggregate checksum file MUST be generated so consumers
can verify every release asset with `sha256sum -c SHA256SUMS` in one shot.
A per-file `.sha256` sidecar is NOT a substitute — the aggregate is the
ergonomic, standard release artifact (see GNU/Debian/Fedora conventions).

The test FAILS (assertion error) if the step is missing or neutered.
"""
from __future__ import annotations

import re

from tests.unit.test_release_pipeline_structure import _workflow_source


SHA256SUMS_STEP_PATTERNS = (
    # `sha256sum * > SHA256SUMS` or `sha256sum gludd-* > SHA256SUMS`
    re.compile(r"sha256sum\s+\S.*?>\s*SHA256SUMS", re.IGNORECASE),
    # `sha256sum ... | tee SHA256SUMS` variant
    re.compile(r"sha256sum\s+\S.*?\|\s*tee\s+SHA256SUMS", re.IGNORECASE),
)


def _release_job_section(src: str) -> str:
    """Return the slice of build.yml covering the `release:` job.

    The release job runs from its `  release:` header to the next top-level
    job key (a line matching `^  WORD[WORD-]*:$` at column 0 indentation)
    or end of file.
    """
    m = re.search(r"^  release:\s*$", src, re.MULTILINE)
    assert m, "release job not found in build.yml"
    start = m.start()
    rest = src[start + len(m.group(0)) :]
    # Next sibling job key: two-space indent, word char, colon, end-of-line.
    next_m = re.search(r"^  \w[\w-]*:\s*$", rest, re.MULTILINE)
    end = start + len(m.group(0)) + (next_m.start() if next_m else len(rest))
    return src[start:end]


class TestSha256sumsAggregateGeneration:
    """CP.19: the release job MUST emit a SHA256SUMS aggregate file."""

    def test_release_job_exists(self):
        src = _workflow_source()
        assert re.search(r"^  release:\s*$", src, re.MULTILINE), (
            "release job must exist in build.yml"
        )

    def test_sha256sums_generation_step_present(self):
        """A step in the release job must generate SHA256SUMS.

        Accepts either `sha256sum * > SHA256SUMS` or the `| tee SHA256SUMS`
        variant. The step MUST live inside the release job (not just in a
        build platform job), because SHA256SUMS aggregates ALL platform
        artifacts and must run after they are staged together.
        """
        section = _release_job_section(_workflow_source())
        assert any(p.search(section) for p in SHA256SUMS_STEP_PATTERNS), (
            "release job is missing a SHA256SUMS aggregate generation step.\n"
            "Expected a step running e.g. `sha256sum * > SHA256SUMS` inside "
            "the release job, after staging release-assets/. See CP.19."
        )

    def test_sha256sums_step_named(self):
        """The SHA256SUMS step should carry a human-readable name.

        A bare `run: sha256sum ...` with no `- name:` is hard to spot in the
        Actions UI when diagnosing a release failure. Pin the convention.
        """
        section = _release_job_section(_workflow_source())
        # Look for a `- name:` line whose text mentions SHA256SUMS or checksum
        # aggregate, OR a `run:` block containing the sha256sum redirect.
        has_named_step = bool(
            re.search(
                r"-\s+name:.*(?:SHA256SUMS|checksum.*aggregate|aggregate.*checksum)",
                section,
                re.IGNORECASE | re.DOTALL,
            )
        )
        has_redirect = any(p.search(section) for p in SHA256SUMS_STEP_PATTERNS)
        assert has_named_step and has_redirect, (
            "SHA256SUMS step should be a named step (e.g. "
            "'Generate SHA256SUMS aggregate') containing a sha256sum redirect. "
            "Found named_step=%s, redirect=%s." % (has_named_step, has_redirect)
        )

    def test_sha256sums_uploaded_as_release_asset(self):
        """SHA256SUMS MUST be uploaded as a release asset.

        The canonical pattern is `files: release-assets/*` (which sweeps up
        SHA256SUMS because it is generated inside release-assets/). An
        explicit `gh release upload ... SHA256SUMS` is also acceptable.
        """
        section = _release_job_section(_workflow_source())
        sweeps_release_assets = bool(
            re.search(r"files:\s*release-assets/\*", section)
        )
        explicit_upload = bool(
            re.search(r"gh\s+release\s+upload.*SHA256SUMS", section, re.IGNORECASE)
        )
        assert sweeps_release_assets or explicit_upload, (
            "SHA256SUMS must be uploaded as a release asset, either via "
            "`files: release-assets/*` (generated inside release-assets/) "
            "or an explicit `gh release upload ... SHA256SUMS`."
        )

    def test_sha256sums_generated_after_staging(self):
        """SHA256SUMS MUST be generated AFTER assets are staged.

        Generating it before `mkdir release-assets` + `cp artifacts/*` would
        produce an empty or partial aggregate. Verify the staging step appears
        before the SHA256SUMS step in the release job.
        """
        section = _release_job_section(_workflow_source())
        stage_m = re.search(r"Stage release assets", section)
        sums_m = None
        for p in SHA256SUMS_STEP_PATTERNS:
            sums_m = p.search(section)
            if sums_m:
                break
        assert stage_m and sums_m, (
            "Expected both a 'Stage release assets' step and a SHA256SUMS "
            "generation step in the release job."
        )
        assert stage_m.start() < sums_m.start(), (
            "SHA256SUMS generation step must come AFTER the 'Stage release "
            "assets' step — otherwise the aggregate is empty/partial."
        )
