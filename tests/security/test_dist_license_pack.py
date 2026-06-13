"""W5.2 (V4.2/V4.3): LICENSE + THIRD_PARTY_LICENSES + SBOM are packed into dist.

The `make dist` tarball must ship the license texts and an SBOM so the
distributed artifact is legally complete and supply-chain auditable.

Building the full tarball requires pyinstaller + network (binary download), so
the load-bearing proof here inspects the `dist` recipe in the Makefile: it must
(a) depend on `sbom` so dist/sbom.json exists, and (b) copy LICENSE,
THIRD_PARTY_LICENSES.md, and sbom.json into the tarball directory. The source
files themselves must also exist in the repo.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MAKEFILE = ROOT / "Makefile"


class TestDistLicensePack:
    def test_license_files_exist_in_repo(self) -> None:
        assert (ROOT / "LICENSE").is_file(), "LICENSE must exist to be packed"
        assert (ROOT / "THIRD_PARTY_LICENSES.md").is_file(), (
            "THIRD_PARTY_LICENSES.md must exist to be packed"
        )

    def _dist_recipe(self) -> str:
        content = MAKEFILE.read_text()
        # Grab the dist: recipe block (from 'dist:' to the next top-level target).
        match = re.search(r"\ndist:.*?(?=\n[a-zA-Z0-9_-]+:)", content, re.DOTALL)
        assert match, "Could not locate the dist: recipe in the Makefile"
        return match.group(0)

    def test_dist_depends_on_sbom(self) -> None:
        recipe = self._dist_recipe()
        first_line = recipe.strip().splitlines()[0]
        assert "sbom" in first_line, (
            f"dist target must depend on 'sbom' so the SBOM is generated; got: {first_line}"
        )

    def test_dist_copies_license(self) -> None:
        recipe = self._dist_recipe()
        assert re.search(r"cp\s+LICENSE\s+\$\(TARBALL_DIR\)", recipe), (
            "dist recipe must copy LICENSE into the tarball directory"
        )

    def test_dist_copies_third_party_licenses(self) -> None:
        recipe = self._dist_recipe()
        assert "THIRD_PARTY_LICENSES.md" in recipe and "$(TARBALL_DIR)" in recipe, (
            "dist recipe must copy THIRD_PARTY_LICENSES.md into the tarball directory"
        )

    def test_dist_writes_sbom_into_tarball(self) -> None:
        recipe = self._dist_recipe()
        # The recipe reads dist/sbom.json and writes it into the tarball dir
        # (currently via a path-scrubbing python one-liner — W5.3). The
        # load-bearing assertion is that $(TARBALL_DIR)/sbom.json is produced
        # from dist/sbom.json.
        assert "dist/sbom.json" in recipe, "dist recipe must read dist/sbom.json"
        assert "$(TARBALL_DIR)/sbom.json" in recipe, (
            "dist recipe must write sbom.json into the tarball directory"
        )

    def test_dist_scrubs_build_paths(self) -> None:
        """W5.3: the packed SBOM must have build-machine paths scrubbed and the
        tarball dir is verified path-clean before archiving."""
        recipe = self._dist_recipe()
        assert "getcwd" in recipe or "Scrubbing" in recipe, (
            "dist recipe must scrub absolute build paths from the SBOM"
        )
        assert re.search(r"grep[^\n]*(/Users/|Mac\.localdomain)", recipe), (
            "dist recipe must verify the tarball dir has no leaked local paths"
        )
