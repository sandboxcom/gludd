"""
Unit tests for verify_release_completeness.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from verify_release_completeness import EXPECTED_CATEGORIES


class TestExpectedCategories:
    def test_linux_x86_64_match(self) -> None:
        fn = EXPECTED_CATEGORIES["linux-x86_64 binary"]
        assert fn({"gludd-linux-x86_64"})
        assert fn({"gludd_linux_amd64.tar.gz"})
        assert not fn({"gludd-macos-arm64"})

    def test_linux_aarch64_match(self) -> None:
        fn = EXPECTED_CATEGORIES["linux-aarch64 binary"]
        assert fn({"gludd-linux-aarch64"})
        assert fn({"gludd-linux-arm64"})
        assert not fn({"gludd-linux-x86_64"})

    def test_macos_arm64_match(self) -> None:
        fn = EXPECTED_CATEGORIES["macos-arm64 binary"]
        assert fn({"gludd-macos-arm64"})
        assert fn({"gludd-darwin-arm64"})
        assert not fn({"gludd-linux-arm64"})

    def test_windows_x86_64_match(self) -> None:
        fn = EXPECTED_CATEGORIES["windows-x86_64 binary"]
        assert fn({"gludd-windows-x86_64"})
        assert fn({"gludd-windows-amd64"})
        assert not fn({"gludd-linux-x86_64"})

    def test_checksums_match(self) -> None:
        fn = EXPECTED_CATEGORIES["checksums"]
        assert fn({"checksums.txt"})
        assert fn({"SHA256SUMS"})
        assert fn({"gludd_0.1.0_sha256.checksums.txt"})
        assert not fn({"gludd-linux-x86_64"})

    def test_sbom_match(self) -> None:
        fn = EXPECTED_CATEGORIES["SBOM"]
        assert fn({"sbom.spdx.json"})
        assert fn({"sbom.cdx.json"})
        assert fn({"gludd-sbom-cyclonedx.json"})
        assert not fn({"gludd-linux-x86_64"})

    def test_license_match(self) -> None:
        fn = EXPECTED_CATEGORIES["LICENSE"]
        assert fn({"LICENSE"})
        assert fn({"LICENSE.txt"})
        assert not fn({"THIRD_PARTY_LICENSES"})

    def test_third_party_licenses_match(self) -> None:
        fn = EXPECTED_CATEGORIES["THIRD_PARTY_LICENSES"]
        assert fn({"THIRD_PARTY_LICENSES"})
        assert fn({"third_party_licenses.txt"})
        assert not fn({"LICENSE"})

    def test_complete_release_passes_all(self) -> None:
        assets = {
            "gludd-linux-x86_64",
            "gludd-linux-aarch64",
            "gludd-macos-arm64",
            "gludd-windows-x86_64",
            "SHA256SUMS",
            "sbom.spdx.json",
            "LICENSE",
            "THIRD_PARTY_LICENSES",
        }
        for label, fn in EXPECTED_CATEGORIES.items():
            assert fn(assets), f"{label} should match in complete set"

    def test_empty_set_fails_all(self) -> None:
        assets: set[str] = set()
        for label, fn in EXPECTED_CATEGORIES.items():
            assert not fn(assets), f"{label} should not match empty set"

    def test_min_asset_threshold(self) -> None:
        from verify_release_completeness import MIN_ASSETS

        assert MIN_ASSETS == 8


class TestMockCheckCompleteness:
    def test_missing_one_category_counts_as_failure(self) -> None:
        """Without actually calling gh, verify logic: if one check fails, result is 1."""
        mock_data = {
            "tagName": "v0.1.0-test",
            "isDraft": False,
            "assets": [
                {"name": f"gludd-{plat}"}
                for plat in ("linux-x86_64", "linux-aarch64", "macos-arm64", "windows-x86_64")
            ]
            + [{"name": "checksums.txt"}, {"name": "LICENSE"}, {"name": "THIRD_PARTY_LICENSES"}],
            "url": "https://github.com/sandboxcom/gludd/releases/tag/v0.1.0-test",
            "publishedAt": "2026-01-01T00:00:00Z",
        }
        # Missing SBOM
        asset_names = {a["name"] for a in mock_data["assets"]}
        results = {label: fn(asset_names) for label, fn in EXPECTED_CATEGORIES.items()}
        assert results["checksums"] is True
        assert results["SBOM"] is False
        assert sum(1 for v in results.values() if not v) == 1
