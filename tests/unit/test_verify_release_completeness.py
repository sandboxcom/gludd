"""
Unit tests for verify_release_completeness.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
import verify_release_completeness as vrc
from verify_release_completeness import (
    EXPECTED_CATEGORIES,
    MIN_ASSETS,
    check_completeness,
    expected_prerelease,
    version_from_tag,
)


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

    def test_deb_match(self) -> None:
        fn = EXPECTED_CATEGORIES[".deb (amd64)"]
        assert fn({"gludd_0.1.0_amd64.deb"})
        assert fn({"gludd-0.1.0-1.x86_64.deb"})
        assert not fn({"gludd-0.1.0-1.x86_64.rpm"})

    def test_rpm_match(self) -> None:
        fn = EXPECTED_CATEGORIES[".rpm (x86_64)"]
        assert fn({"gludd-0.1.0-1.x86_64.rpm"})
        assert fn({"gludd_0.1.0_amd64.rpm"})
        assert not fn({"gludd_0.1.0_amd64.deb"})

    def test_dmg_match(self) -> None:
        fn = EXPECTED_CATEGORIES[".dmg (macOS)"]
        assert fn({"gludd-0.1.0-macos-arm64.dmg"})
        assert fn({"gludd-0.1.0.dmg"})
        assert not fn({"gludd-0.1.0-macos-arm64.tar.gz"})

    def test_exe_installer_match(self) -> None:
        fn = EXPECTED_CATEGORIES[".exe installer (Windows)"]
        assert fn({"gludd-0.1.0-setup-x86_64.exe"})
        assert fn({"gludd-0.1.0-installer.exe"})
        assert fn({"gludd_setup_0.1.0.exe"})
        assert not fn({"gludd-0.1.0-windows-x86_64.zip"})

    def test_complete_release_passes_all(self) -> None:
        assets = {item["name"] for item in COMPLETE_ASSETS}
        for label, fn in EXPECTED_CATEGORIES.items():
            assert fn(assets), f"{label} should match in complete set"

    def test_empty_set_fails_all(self) -> None:
        assets: set[str] = set()
        for label, fn in EXPECTED_CATEGORIES.items():
            assert not fn(assets), f"{label} should not match empty set"

    def test_min_asset_threshold(self) -> None:
        assert len(EXPECTED_CATEGORIES) == 28
        assert MIN_ASSETS == 30

    def test_beta4_distribution_and_runtime_categories(self) -> None:
        assets = {item["name"] for item in COMPLETE_ASSETS}
        for label in (
            "wheel",
            "sdist",
            "runtime collection tarballs",
            "collection manifest",
            "execution-environment definition",
            "execution-environment requirements",
            "execution-environment Python requirements",
            "execution-environment system requirements",
            "execution-environment runtime lock",
            "managed-host Python lock",
            "collection Python boundary inventory",
            "execution-environment image metadata",
            "container image metadata",
            "install script",
            "smoke attestations",
            "release manifest",
        ):
            assert EXPECTED_CATEGORIES[label](assets), label


COMPLETE_ASSETS = [
    {"name": "gludd-0.1.0-linux-x86_64.tar.gz", "size": 100},
    {"name": "gludd-0.1.0-linux-aarch64.tar.gz", "size": 100},
    {"name": "gludd-0.1.0-macos-arm64.tar.gz", "size": 100},
    {"name": "gludd-0.1.0-windows-x86_64.zip", "size": 100},
    {"name": "gludd_0.1.0_amd64.deb", "size": 100},
    {"name": "gludd-0.1.0-1.x86_64.rpm", "size": 100},
    {"name": "gludd-0.1.0-macos-arm64.dmg", "size": 100},
    {"name": "gludd-0.1.0-setup-x86_64.exe", "size": 100},
    {"name": "general_ludd_agent-0.1.0-py3-none-any.whl", "size": 100},
    {"name": "general_ludd_agent-0.1.0.tar.gz", "size": 100},
    {"name": "general_ludd-agent-0.2.0.tar.gz", "size": 100},
    {"name": "general_ludd-language-0.1.0.tar.gz", "size": 100},
    {"name": "general_ludd-networking-0.2.0.tar.gz", "size": 100},
    {"name": "gludd-collections-0.1.0.json", "size": 100},
    {"name": "ansible-ee-execution-environment.yml", "size": 100},
    {"name": "ansible-ee-requirements.yml", "size": 100},
    {"name": "ansible-ee-requirements.txt", "size": 100},
    {"name": "ansible-ee-bindep.txt", "size": 100},
    {"name": "ansible-ee-runtime-lock.json", "size": 100},
    {"name": "ansible-managed-host-python.lock.json", "size": 100},
    {"name": "ansible-collection-python-boundary-inventory.json", "size": 100},
    {"name": "gludd-ee-image-0.1.0.json", "size": 100},
    {"name": "gludd-container-0.1.0.json", "size": 100},
    {"name": "SHA256SUMS", "size": 100},
    {"name": "sbom.json", "size": 100},
    {"name": "install.sh", "size": 100},
    {"name": "LICENSE", "size": 100},
    {"name": "THIRD_PARTY_LICENSES.md", "size": 100},
    {"name": "gludd-smoke-release-0.1.0.json", "size": 100},
    {"name": "gludd-release-manifest-0.1.0.json", "size": 100},
]


def _payload(tag="v0.1.0", *, draft=False, prerelease=False, assets=None):
    return {
        "tagName": tag,
        "isDraft": draft,
        "isPrerelease": prerelease,
        "assets": COMPLETE_ASSETS if assets is None else assets,
        "url": f"https://github.com/sandboxcom/gludd/releases/tag/{tag}",
        "publishedAt": "2026-01-01T00:00:00Z",
    }


def _mock_gh(monkeypatch, payload=None, rc=0, out=None, err=""):
    body = out if out is not None else json.dumps(payload)

    def fake_run(cmd):
        return (rc, body, err)

    monkeypatch.setattr(vrc, "_run", fake_run)


class TestCheckCompleteness:
    def test_complete_release_passes(self, monkeypatch, capsys) -> None:
        _mock_gh(monkeypatch, _payload())
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 0
        assert "COMPLETENESS CHECK: PASS" in capsys.readouterr().out

    def test_gh_failure_fails_closed(self, monkeypatch, capsys) -> None:
        _mock_gh(monkeypatch, rc=1, out="", err="release not found")
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "fail-closed" in capsys.readouterr().out

    def test_bad_json_fails_closed(self, monkeypatch, capsys) -> None:
        _mock_gh(monkeypatch, out="this is not json")
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "fail-closed" in capsys.readouterr().out

    def test_draft_release_fails(self, monkeypatch, capsys) -> None:
        _mock_gh(monkeypatch, _payload(draft=True))
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "DRAFT" in capsys.readouterr().out

    def test_zero_assets_fails(self, monkeypatch, capsys) -> None:
        _mock_gh(monkeypatch, _payload(assets=[]))
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "zero assets" in capsys.readouterr().out

    def test_missing_category_fails(self, monkeypatch, capsys) -> None:
        assets = [a for a in COMPLETE_ASSETS if a["name"] != "sbom.json"]
        _mock_gh(monkeypatch, _payload(assets=assets))
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "SBOM — MISSING" in capsys.readouterr().out

    def test_single_asset_fails_min_count(self, monkeypatch, capsys) -> None:
        _mock_gh(
            monkeypatch,
            _payload(assets=[{"name": "gludd-0.1.0-macos-arm64.tar.gz", "size": 100}]),
        )
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "minimum asset count" in capsys.readouterr().out

    def test_beta_tag_without_prerelease_flag_fails(self, monkeypatch, capsys) -> None:
        tag = "v0.1.0-beta.9"
        assets = [
            {
                "name": (
                    a["name"]
                    if a["name"].startswith("general_ludd-")
                    else a["name"].replace("0.1.0", "0.1.0-beta.9")
                ),
                "size": a["size"],
            }
            for a in COMPLETE_ASSETS
        ]
        _mock_gh(monkeypatch, _payload(tag, prerelease=False, assets=assets))
        assert check_completeness(tag, "sandboxcom/gludd") == 1
        assert "prerelease" in capsys.readouterr().out

    def test_beta_tag_with_prerelease_flag_passes(self, monkeypatch, capsys) -> None:
        tag = "v0.1.0-beta.9"
        assets = [
            {
                "name": (
                    a["name"]
                    if a["name"].startswith("general_ludd-")
                    else a["name"].replace("0.1.0", "0.1.0-beta.9")
                ),
                "size": a["size"],
            }
            for a in COMPLETE_ASSETS
        ]
        _mock_gh(monkeypatch, _payload(tag, prerelease=True, assets=assets))
        assert check_completeness(tag, "sandboxcom/gludd") == 0

    def test_stable_tag_with_prerelease_flag_fails(self, monkeypatch, capsys) -> None:
        _mock_gh(monkeypatch, _payload(prerelease=True))
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "prerelease" in capsys.readouterr().out

    def test_zero_size_asset_fails(self, monkeypatch, capsys) -> None:
        assets = [dict(a) for a in COMPLETE_ASSETS]
        assets[0]["size"] = 0
        _mock_gh(monkeypatch, _payload(assets=assets))
        assert check_completeness("v0.1.0", "sandboxcom/gludd") == 1
        assert "zero-size" in capsys.readouterr().out

    def test_version_absent_from_assets_fails(self, monkeypatch, capsys) -> None:
        _mock_gh(monkeypatch, _payload("v9.9.9"))
        assert check_completeness("v9.9.9", "sandboxcom/gludd") == 1
        assert "version" in capsys.readouterr().out


class TestHelpers:
    def test_expected_prerelease(self) -> None:
        assert expected_prerelease("v0.1.0-beta.1")
        assert expected_prerelease("v1.0.0-alpha.3")
        assert expected_prerelease("v1.0.0-rc.1")
        assert not expected_prerelease("v1.0.0")
        assert not expected_prerelease("v0.2.5")

    def test_version_from_tag(self) -> None:
        assert version_from_tag("v0.1.0-beta.1") == "0.1.0-beta.1"
        assert version_from_tag("0.2.0") == "0.2.0"

    def test_resolve_repo_ssh_url(self, monkeypatch) -> None:
        _mock_gh(monkeypatch, out="git@github.com:sandboxcom/gludd.git")
        assert vrc._resolve_repo() == "sandboxcom/gludd"

    def test_resolve_repo_https_url(self, monkeypatch) -> None:
        _mock_gh(monkeypatch, out="https://github.com/sandboxcom/gludd.git")
        assert vrc._resolve_repo() == "sandboxcom/gludd"

    def test_resolve_repo_name_ending_in_git_chars(self, monkeypatch) -> None:
        # rstrip(".git") would mangle a repo name ending in g/i/t/. — guard the fix.
        _mock_gh(monkeypatch, out="git@github.com:foo/loggit.git")
        assert vrc._resolve_repo() == "foo/loggit"

    def test_resolve_repo_fallback_on_failure(self, monkeypatch) -> None:
        _mock_gh(monkeypatch, rc=1, out="", err="no remote")
        assert vrc._resolve_repo() == vrc.FALLBACK_REPO


class TestMockCheckCompleteness:
    def test_missing_one_category_counts_as_failure(self) -> None:
        """Without actually calling gh, verify logic: if one check fails, result is 1."""
        mock_data = {
            "tagName": "v0.1.0-test",
            "isDraft": False,
            "assets": [
                {"name": item["name"]}
                for item in COMPLETE_ASSETS
                if item["name"] != "sbom.json"
            ],
            "url": "https://github.com/sandboxcom/gludd/releases/tag/v0.1.0-test",
            "publishedAt": "2026-01-01T00:00:00Z",
        }
        # Missing SBOM
        asset_names = {a["name"] for a in mock_data["assets"]}
        results = {label: fn(asset_names) for label, fn in EXPECTED_CATEGORIES.items()}
        assert results["checksums"] is True
        assert results["SBOM"] is False
        assert sum(1 for v in results.values() if not v) == 1
