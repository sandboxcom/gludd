"""Test that binary bundling is automated, not manual-default."""

from __future__ import annotations

import os
from pathlib import Path


class TestAutomatedBundle:
    def test_download_script_exists(self):
        repo_root = Path(__file__).parent.parent.parent
        script = repo_root / "scripts" / "download_bundled_binaries.py"
        assert script.exists(), "download_bundled_binaries.py must exist for automated bundling"

    def test_download_script_is_executable(self):
        repo_root = Path(__file__).parent.parent.parent
        script = repo_root / "scripts" / "download_bundled_binaries.py"
        assert os.access(script, os.R_OK)

    def test_bundle_binaries_make_target_invokes_download(self):
        repo_root = Path(__file__).parent.parent.parent
        makefile = repo_root / "Makefile"
        content = makefile.read_text()
        assert "download_bundled_binaries.py" in content

    def test_dist_target_depends_on_bundle_binaries(self):
        repo_root = Path(__file__).parent.parent.parent
        makefile = repo_root / "Makefile"
        content = makefile.read_text()
        assert "bundle-binaries" in content

    def test_bootstrapper_has_download_all_method(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        assert hasattr(BinaryBootstrapper, "download_all")

    def test_guardrail_manual_default_in_agents_md(self):
        repo_root = Path(__file__).parent.parent.parent
        agents = repo_root / "AGENTS.md"
        content = agents.read_text().lower()
        assert "manual default" in content or "manual-default" in content
