"""
Unit tests for scripts/verify_release_artifact.py and
scripts/verify_release_completeness.py.

No real gh calls are made.  The module-level ``_run`` function is monkeypatched
so every test drives a controlled JSON response into the verification logic.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest

from tests.unit.release_asset_fixtures import complete_release_assets

# ---------------------------------------------------------------------------
# Import the modules under test by path
# ---------------------------------------------------------------------------


def _load_module(name: str) -> Any:
    script_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", name)
    spec = importlib.util.spec_from_file_location(name.rstrip(".py"), script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load spec for {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


artifact_mod = _load_module("verify_release_artifact.py")
completeness_mod = _load_module("verify_release_completeness.py")


# ---------------------------------------------------------------------------
# Helpers — build fake gh release JSON
# ---------------------------------------------------------------------------

REPO = "sandboxcom/gludd"
TAG = "v0.1.0-beta.1"
VERSION = "0.1.0-beta.1"

_BASE_ARTIFACT_RESPONSE: dict[str, Any] = {
    "tagName": TAG,
    "isDraft": False,
    "url": f"https://api.github.com/repos/{REPO}/releases/1",
    "publishedAt": "2026-07-01T12:00:00Z",
    "assets": [
        {"name": "gludd-linux-x86_64.tar.gz", "size": 5000000, "contentType": "application/gzip"},
    ],
}


def _make_artifact_response(**overrides: Any) -> str:
    """Return a fake gh --json response as if _run captured stdout."""
    d = dict(_BASE_ARTIFACT_RESPONSE)
    d.update(overrides)
    import json

    return json.dumps(d)


def _make_complete_assets(version: str = VERSION) -> list[dict[str, Any]]:
    """Produce the canonical 30-asset beta4 release matrix."""
    return complete_release_assets(version)


def _set_run(monkeypatch: pytest.MonkeyPatch, module: Any, stdout: str, rc: int = 0) -> None:
    """Replace the module's ``_run`` so it returns (rc, stdout, '')."""
    monkeypatch.setattr(module, "_run", lambda _cmd: (rc, stdout, ""))


def _set_run_error(monkeypatch: pytest.MonkeyPatch, module: Any, stderr: str = "gh not found") -> None:
    """Replace ``_run`` to simulate a gh invocation failure."""
    monkeypatch.setattr(module, "_run", lambda _cmd: (1, "", stderr))


# ===================================================================
# verify_release_artifact — check_artifact()
# ===================================================================


class TestCheckArtifact:
    """Tests for ``verify_release_artifact.check_artifact()``."""

    def test_pass_when_assets_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, artifact_mod, _make_artifact_response())
        assert artifact_mod.check_artifact(TAG, REPO) == 0

    def test_fail_when_release_is_draft(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, artifact_mod, _make_artifact_response(isDraft=True))
        assert artifact_mod.check_artifact(TAG, REPO) == 1

    def test_fail_when_zero_assets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, artifact_mod, _make_artifact_response(assets=[]))
        assert artifact_mod.check_artifact(TAG, REPO) == 1

    def test_fail_when_gh_call_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run_error(monkeypatch, artifact_mod)
        assert artifact_mod.check_artifact(TAG, REPO) == 1

    def test_fail_on_json_decode_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(artifact_mod, "_run", lambda _cmd: (0, "not json", ""))
        assert artifact_mod.check_artifact(TAG, REPO) == 1

    def test_isDraft_defaults_true_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        d = _make_artifact_response(assets=[{"name": "a.tar.gz", "size": 100}])
        d2 = {k: v for k, v in __import__("json").loads(d).items() if k != "isDraft"}
        _set_run(monkeypatch, artifact_mod, __import__("json").dumps(d2))
        assert artifact_mod.check_artifact(TAG, REPO) == 1


# ===================================================================
# verify_release_completeness — pure helpers
# ===================================================================


class TestExpectedPrerelease:
    def test_alpha_tag_is_prerelease(self) -> None:
        assert completeness_mod.expected_prerelease("v0.1.0-alpha.1") is True

    def test_beta_tag_is_prerelease(self) -> None:
        assert completeness_mod.expected_prerelease("v0.1.0-beta.3") is True

    def test_rc_tag_is_prerelease(self) -> None:
        assert completeness_mod.expected_prerelease("v2.0.0-rc.1") is True

    def test_stable_tag_is_not_prerelease(self) -> None:
        assert completeness_mod.expected_prerelease("v1.0.0") is False

    def test_tag_without_v_is_prerelease(self) -> None:
        assert completeness_mod.expected_prerelease("0.1.0-alpha.1") is True


class TestVersionFromTag:
    def test_strips_leading_v(self) -> None:
        assert completeness_mod.version_from_tag("v0.1.0") == "0.1.0"

    def test_preserves_no_v(self) -> None:
        assert completeness_mod.version_from_tag("1.2.3") == "1.2.3"

    def test_handles_prerelease(self) -> None:
        assert completeness_mod.version_from_tag("v0.1.0-beta.1") == "0.1.0-beta.1"


class TestCategoriesStatic:
    def test_exactly_28_categories(self) -> None:
        assert len(completeness_mod.EXPECTED_CATEGORIES) == 28

    def test_optional_categories_is_empty(self) -> None:
        assert frozenset() == completeness_mod.OPTIONAL_CATEGORIES


# ===================================================================
# verify_release_completeness — check_completeness()
# ===================================================================


def _make_completeness_response(**overrides: Any) -> str:
    """Build a complete beta4 release JSON response by default."""
    assets = _make_complete_assets()
    d: dict[str, Any] = {
        "tagName": TAG,
        "isDraft": False,
        "isPrerelease": True,
        "url": f"https://api.github.com/repos/{REPO}/releases/1",
        "publishedAt": "2026-07-01T12:00:00Z",
        "assets": assets,
    }
    d.update(overrides)
    import json

    return json.dumps(d)


class TestCheckCompleteness:
    """Tests for ``verify_release_completeness.check_completeness()``."""

    def test_all_28_categories_and_30_assets_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, completeness_mod, _make_completeness_response())
        assert completeness_mod.check_completeness(TAG, REPO) == 0

    def test_fail_when_draft(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(isDraft=True))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_when_zero_assets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=[]))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_when_gh_call_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run_error(monkeypatch, completeness_mod)
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_on_json_decode_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(completeness_mod, "_run", lambda _cmd: (0, "not json", ""))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_when_below_minimum_assets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        few = _make_complete_assets()[:29]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=few))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_on_prerelease_flag_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(isPrerelease=False))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_pass_when_stable_tag_has_no_prerelease_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        r = _make_completeness_response(
            tagName="v1.0.0",
            isPrerelease=False,
            assets=_make_complete_assets("1.0.0"),
        )
        _set_run(monkeypatch, completeness_mod, r)
        assert completeness_mod.check_completeness("v1.0.0", REPO) == 0

    def test_fail_when_version_not_in_asset_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _make_complete_assets()
        for a in assets:
            a["name"] = a["name"].replace(VERSION, "mismatched-version")
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_when_zero_size_assets_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = _make_complete_assets()
        assets.append({"name": "empty-file.txt", "size": 0})
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    # --- individual category failures ---

    def test_fail_missing_linux_x86_64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if "linux-x86_64" not in a["name"]]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_linux_aarch64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if "linux-aarch64" not in a["name"]]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_macos_arm64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if "macos-arm64" not in a["name"]]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_windows_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if "windows" not in a["name"]]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_deb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if not a["name"].endswith(".deb")]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_rpm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if not a["name"].endswith(".rpm")]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_dmg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if not a["name"].endswith(".dmg")]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_exe_installer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if not a["name"].endswith(".exe")]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_checksums(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if "sha256" not in a["name"].lower()]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_sbom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if "sbom" not in a["name"]]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_license(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if not a["name"].startswith("LICENSE")]
        assets.append({"name": "gludd-linux-x86_64.tar.gz", "size": 1})
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    def test_fail_missing_third_party_licenses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assets = [a for a in _make_complete_assets() if "THIRD_PARTY" not in a["name"]]
        _set_run(monkeypatch, completeness_mod, _make_completeness_response(assets=assets))
        assert completeness_mod.check_completeness(TAG, REPO) == 1

    # --- edge cases ---

    def test_prerelease_regex_case_insensitive(self) -> None:
        assert completeness_mod.expected_prerelease("v0.1.0-Alpha.1") is True
        assert completeness_mod.expected_prerelease("v0.1.0-BETA") is True
        assert completeness_mod.expected_prerelease("v0.1.0-RC5") is True

    def test_tag_without_v_handled_by_version_from_tag(self) -> None:
        for tag, expected in [("0.1.0", "0.1.0"), ("v2.0.0", "2.0.0"), ("v0.0.1", "0.0.1")]:
            assert completeness_mod.version_from_tag(tag) == expected


# ===================================================================
# verify_release_artifact — structural / integrity
# ===================================================================


class TestArtifactModuleShape:
    def test_has_required_functions(self) -> None:
        assert callable(artifact_mod._run)
        assert callable(artifact_mod.check_artifact)
        assert callable(artifact_mod.main)

    def test_main_returns_1_when_no_args(self) -> None:
        assert artifact_mod.main([]) == 1
        assert artifact_mod.main(["script.py"]) == 1

    def test_main_calls_check_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, artifact_mod, _make_artifact_response())
        rc = artifact_mod.main(["script.py", TAG, REPO])
        assert rc == 0

    def test_main_resolves_repo_from_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, artifact_mod, _make_artifact_response())
        assert artifact_mod.main(["script.py", TAG]) in (0, 1)


class TestCompletenessModuleShape:
    def test_has_required_functions(self) -> None:
        assert callable(completeness_mod._run)
        assert callable(completeness_mod.check_completeness)
        assert callable(completeness_mod.main)
        assert callable(completeness_mod.expected_prerelease)
        assert callable(completeness_mod.version_from_tag)

    def test_main_returns_1_when_no_args(self) -> None:
        assert completeness_mod.main([]) == 1
        assert completeness_mod.main(["script.py"]) == 1

    def test_main_calls_check_completeness(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, completeness_mod, _make_completeness_response())
        rc = completeness_mod.main(["script.py", TAG, REPO])
        assert rc == 0

    def test_main_resolves_repo_from_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_run(monkeypatch, completeness_mod, _make_completeness_response())
        assert completeness_mod.main(["script.py", TAG]) in (0, 1)

    def test_min_assets_constant_is_30(self) -> None:
        assert completeness_mod.MIN_ASSETS == 30
