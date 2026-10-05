"""
Deep release artifact and asset tests.

Covers: asset naming convention, version stamping, platform coverage, size
sanity, checksum verification, GPG signature, and edge cases for all 28
required categories in the 30-asset beta4 release matrix.
"""

from __future__ import annotations

import importlib.util
import json
import os
from typing import Any

import pytest

from tests.unit.release_asset_fixtures import complete_release_assets


def _load_module(name: str) -> Any:
    script_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", name)
    spec = importlib.util.spec_from_file_location(name.rstrip(".py"), script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load spec for {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


completeness_mod = _load_module("verify_release_completeness.py")

VERSION = "0.1.0-beta.1"
VERSION_STABLE = "1.0.0"


def _full_asset_set(version: str = VERSION) -> list[dict[str, Any]]:
    return complete_release_assets(version)


def _asset_names(version: str = VERSION) -> set[str]:
    return {a["name"] for a in _full_asset_set(version)}


def _response(**overrides: Any) -> str:
    d: dict[str, Any] = {
        "tagName": "v0.1.0-beta.1",
        "isDraft": False,
        "isPrerelease": True,
        "url": "https://api.github.com/repos/sandboxcom/gludd/releases/1",
        "publishedAt": "2026-07-01T12:00:00Z",
        "assets": _full_asset_set(),
    }
    d.update(overrides)
    return json.dumps(d)


def _monkey_run(monkeypatch: pytest.MonkeyPatch, stdout: str, rc: int = 0) -> None:
    monkeypatch.setattr(completeness_mod, "_run", lambda _cmd: (rc, stdout, ""))


# ===================================================================
# Asset naming convention
# ===================================================================


class TestAssetNamingConvention:
    def test_linux_binary_follows_gludd_linux_arch_version_pattern(self) -> None:
        patterns = [
            "gludd-linux-x86_64-0.1.0.tar.gz",
            "gludd-linux-amd64-0.1.0.tar.gz",
            "gludd-linux-x86_64-1.2.3.tar.bz2",
        ]
        check = completeness_mod.EXPECTED_CATEGORIES["linux-x86_64 binary"]
        for name in patterns:
            assert check({name}) is True

    def test_linux_aarch64_accepts_aarch64_and_arm64_names(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["linux-aarch64 binary"]
        assert check({"gludd-linux-aarch64-0.1.0.tar.gz"}) is True
        assert check({"gludd-linux-arm64-0.1.0.tar.gz"}) is True

    def test_macos_accepts_darwin_and_macos_prefixes(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["macos-arm64 binary"]
        assert check({"gludd-darwin-arm64-0.1.0.tar.gz"}) is True
        assert check({"gludd-macos-arm64-0.1.0.tar.gz"}) is True

    def test_windows_accepts_gludd_windows_prefix_and_amd64_patterns(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["windows-x86_64 binary"]
        assert check({"gludd-windows-0.1.0.zip"}) is True
        assert check({"gludd-windows-x86_64-0.1.0.zip"}) is True
        assert check({"gludd-windows-amd64-0.1.0.zip"}) is True
        assert check({"gludd-windows-x86-64-0.1.0.zip"}) is True
        assert check({"bare-win64.zip"}) is False

    def test_deb_rpm_dmg_must_have_correct_extensions(self) -> None:
        for label, _ext, good, bad in [
            (".deb (amd64)", "deb", "gludd_0.1.0_amd64.deb", "gludd_0.1.0_amd64.pkg"),
            (".rpm (x86_64)", "rpm", "gludd-0.1.0.x86_64.rpm", "gludd-0.1.0.x86_64.zip"),
            (".dmg (macOS)", "dmg", "gludd-0.1.0.dmg", "gludd-0.1.0.pkg"),
        ]:
            check = completeness_mod.EXPECTED_CATEGORIES[label]
            assert check({good}) is True
            assert check({bad}) is False

    def test_exe_installer_matches_setup_install_patterns(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES[".exe installer (Windows)"]
        for name in [
            "gludd-0.1.0-installer.exe",
            "gludd-setup-0.1.0.exe",
            "setup-gludd-0.1.0.exe",
        ]:
            assert check({name}) is True

    def test_checksum_matches_sha256_and_checksums_patterns(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["checksums"]
        for name in [
            "gludd-0.1.0-checksums.sha256",
            "SHA256SUMS",
            "gludd-0.1.0.sha256",
            "gludd-0.1.0-checksums.sha256.txt",
        ]:
            assert check({name}) is True
        assert check({"gludd-0.1.0.tar.gz"}) is False

    def test_sbom_matches_cdx_and_spdx_patterns(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["SBOM"]
        for name in [
            "gludd-0.1.0.cdx.json",
            "gludd-0.1.0.spdx.json",
            "gludd-0.1.0-cyclonedx.json",
            "sbom-gludd-0.1.0.json",
        ]:
            assert check({name}) is True

    def test_license_matches_exact_license_prefix(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["LICENSE"]
        assert check({"LICENSE"}) is True
        assert check({"LICENSE.txt"}) is True
        assert check({"LICENSE.md"}) is True
        assert check({"LICENSE-MIT"}) is True

    def test_third_party_licenses_matches_case_insensitive(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["THIRD_PARTY_LICENSES"]
        assert check({"THIRD_PARTY_LICENSES.txt"}) is True
        assert check({"third_party_licenses.md"}) is True
        assert check({"Third_Party_Licenses"}) is True


# ===================================================================
# Version stamping
# ===================================================================


class TestVersionStamping:
    def test_version_from_tag_strips_v_prefix(self) -> None:
        for tag, expected in [
            ("v0.1.0", "0.1.0"),
            ("v2.3.4", "2.3.4"),
            ("v0.1.0-beta.1", "0.1.0-beta.1"),
            ("1.0.0", "1.0.0"),
        ]:
            assert completeness_mod.version_from_tag(tag) == expected

    def test_version_embedded_in_asset_names_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _monkey_run(monkeypatch, _response())
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 0

    def test_mismatched_version_in_asset_names_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _full_asset_set()
        for asset in assets:
            asset["name"] = asset["name"].replace(VERSION, "wrong-version")
        _monkey_run(
            monkeypatch,
            _response(
                assets=assets,
                tagName="v0.1.0-beta.1",
                isPrerelease=True,
            ),
        )
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1

    def test_version_stamping_works_for_stable_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        r = _response(tagName="v1.0.0", isPrerelease=False, assets=_full_asset_set("1.0.0"))
        _monkey_run(monkeypatch, r)
        assert completeness_mod.check_completeness("v1.0.0", "sandboxcom/gludd") == 0


# ===================================================================
# Platform coverage
# ===================================================================


class TestPlatformCoverage:
    def test_all_28_categories_are_exactly_defined(self) -> None:
        assert len(completeness_mod.EXPECTED_CATEGORIES) == 28

    def test_every_category_has_a_callable_check(self) -> None:
        for label, fn in completeness_mod.EXPECTED_CATEGORIES.items():
            assert callable(fn), f"{label} check is not callable"

    def test_linux_x86_64_rejects_aarch64_only(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["linux-x86_64 binary"]
        assert check({"gludd-linux-x86_64-0.1.0.tar.gz"}) is True
        assert check({"gludd-linux-aarch64-0.1.0.tar.gz"}) is False

    def test_aarch64_rejects_x86_64_only(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["linux-aarch64 binary"]
        assert check({"gludd-linux-aarch64-0.1.0.tar.gz"}) is True
        assert check({"gludd-linux-x86_64-0.1.0.tar.gz"}) is False

    def test_macos_rejects_linux_and_windows(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["macos-arm64 binary"]
        assert check({"gludd-darwin-arm64-0.1.0.tar.gz"}) is True
        assert check({"gludd-linux-x86_64-0.1.0.tar.gz"}) is False
        assert check({"gludd-windows-x86_64-0.1.0.zip"}) is False


# ===================================================================
# Size sanity
# ===================================================================


class TestSizeSanity:
    def test_zero_size_assets_cause_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _full_asset_set()
        assets.append({"name": "empty-artifact.tar.gz", "size": 0})
        _monkey_run(monkeypatch, _response(assets=assets))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1

    def test_many_zero_size_assets_all_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _full_asset_set()
        assets.append({"name": "empty-1.txt", "size": 0})
        assets.append({"name": "empty-2.txt", "size": 0})
        _monkey_run(monkeypatch, _response(assets=assets))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1

    def test_nonzero_small_assets_pass_size_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _full_asset_set()
        assets.append({"name": "tiny-but-real.txt", "size": 1})
        _monkey_run(monkeypatch, _response(assets=assets))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 0

    def test_minimum_30_assets_required(self) -> None:
        assert completeness_mod.MIN_ASSETS == 30

    def test_release_with_only_29_assets_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        few = _full_asset_set()[:29]
        _monkey_run(monkeypatch, _response(assets=few))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1


# ===================================================================
# Checksum verification
# ===================================================================


class TestChecksumVerification:
    def test_checksums_category_matches_sha256sums_uppercase(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["checksums"]
        assert check({"SHA256SUMS"}) is True
        assert check({"sha256sums"}) is True
        assert check({"gludd-0.1.0.sha256.txt"}) is True

    def test_checksums_rejects_non_checksum_files(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["checksums"]
        assert check({"gludd-0.1.0.tar.gz"}) is False
        assert check({"gludd-0.1.0.md5"}) is False
        assert check({"README.txt"}) is False
        assert check({"gludd-0.1.0.exe"}) is False
        assert check({"gludd-0.1.0.deb"}) is False

    def test_missing_checksums_asset_fails_completeness(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _full_asset_set() if "sha256" not in a["name"].lower()]
        _monkey_run(monkeypatch, _response(assets=assets))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1


# ===================================================================
# GPG signature
# ===================================================================


class TestGpgSignature:
    def test_gpg_asset_would_match_existing_checksums_plus_signature_naming(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["checksums"]
        assert check({"gludd-0.1.0-checksums.sha256"}) is True

    def test_signing_a_dedicated_asset_in_both_gpg_and_asc_forms(self) -> None:
        check = completeness_mod.EXPECTED_CATEGORIES["checksums"]
        assert check({"gludd-0.1.0.sha256.asc"}) is True
        assert check({"gludd-0.1.0-checksums.sha256.gpg"}) is True
        assert check({"gludd-0.1.0.sha256.gpg"}) is True


# ===================================================================
# Edge cases / structural
# ===================================================================


class TestStructuralIntegrity:
    def test_exactly_28_categories_assertion_lives(self) -> None:
        assert len(completeness_mod.EXPECTED_CATEGORIES) == 28

    def test_optional_categories_is_empty_frozenset(self) -> None:
        assert frozenset() == completeness_mod.OPTIONAL_CATEGORIES

    def test_empty_asset_list_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _monkey_run(monkeypatch, _response(assets=[]))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1

    def test_draft_release_fails_even_with_full_assets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _monkey_run(monkeypatch, _response(isDraft=True))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1

    def test_prerelease_flag_matches_tag_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _monkey_run(monkeypatch, _response(isPrerelease=True))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 0

    def test_stable_tag_with_prerelease_flag_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        r = _response(tagName="v1.0.0", isPrerelease=True, assets=_full_asset_set("1.0.0"))
        _monkey_run(monkeypatch, r)
        assert completeness_mod.check_completeness("v1.0.0", "sandboxcom/gludd") == 1

    def test_bad_json_from_gh_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(completeness_mod, "_run", lambda _cmd: (0, "not valid json", ""))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1

    def test_gh_call_failure_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(completeness_mod, "_run", lambda _cmd: (1, "", "command not found"))
        assert completeness_mod.check_completeness("v0.1.0-beta.1", "sandboxcom/gludd") == 1
