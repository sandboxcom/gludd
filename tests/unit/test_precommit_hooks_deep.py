"""Deep pre-commit hook configuration tests.

Validates .pre-commit-config.yaml: pinned versions, valid entry points,
correct stages, no duplicate hook IDs.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import ClassVar, NotRequired, TypedDict, cast

import pytest
import yaml

_PROJECT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _PROJECT / ".pre-commit-config.yaml"

_VALID_STAGES = frozenset(
    {
        "pre-commit",
        "pre-merge-commit",
        "pre-push",
        "prepare-commit-msg",
        "commit-msg",
        "post-commit",
        "post-checkout",
        "post-merge",
        "post-rewrite",
        "manual",
    }
)


class _HookConfig(TypedDict, total=False):
    """Typed subset of one pre-commit hook used by these contracts."""

    id: str
    entry: str
    stages: list[str]


class _RepoConfig(TypedDict):
    """Typed subset of one pre-commit repository definition."""

    repo: str
    hooks: list[_HookConfig]
    rev: NotRequired[str]
    default_stages: NotRequired[list[str]]


class _PreCommitConfig(TypedDict):
    """Top-level pre-commit configuration shape."""

    repos: list[_RepoConfig]


class _HookEntry(TypedDict):
    """Repository and hook pair used by exhaustive assertions."""

    repo: _RepoConfig
    hook: _HookConfig


def _load_config() -> _PreCommitConfig:
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        return cast(_PreCommitConfig, yaml.safe_load(fh))


@pytest.fixture(scope="module")
def config() -> _PreCommitConfig:
    return _load_config()


def _all_hooks(config: _PreCommitConfig) -> list[_HookEntry]:
    hooks: list[_HookEntry] = []
    for repo in config.get("repos", []):
        for hook in repo.get("hooks", []):
            hooks.append({"repo": repo, "hook": hook})
    return hooks


def _repo_ids(config: _PreCommitConfig) -> list[str]:
    return [r["repo"] for r in config.get("repos", [])]


# ---------------------------------------------------------------------------
# pinned versions
# ---------------------------------------------------------------------------


class TestPinnedVersions:
    def test_remote_repos_have_tagged_rev(self, config: _PreCommitConfig) -> None:
        remote = [r for r in config["repos"] if r["repo"] != "local"]
        for repo in remote:
            rev = repo.get("rev", "")
            assert re.match(r"^v\d", rev), f"repo {repo['repo']!r} has unpinned rev={rev!r}"

    def test_pre_commit_hooks_rev_pinned(self, config: _PreCommitConfig) -> None:
        for r in config["repos"]:
            if "pre-commit/pre-commit-hooks" in r["repo"]:
                assert r["rev"] == "v5.0.0"

    def test_detect_secrets_rev_pinned(self, config: _PreCommitConfig) -> None:
        for r in config["repos"]:
            if "Yelp/detect-secrets" in r["repo"]:
                assert r["rev"] == "v1.5.0"

    def test_every_remote_repo_has_rev_key(self, config: _PreCommitConfig) -> None:
        for r in config["repos"]:
            if r["repo"] != "local":
                assert "rev" in r, f"repo {r['repo']!r} missing 'rev' key"
                assert r["rev"] != "", f"repo {r['repo']!r} has empty rev"


# ---------------------------------------------------------------------------
# local hook entry points
# ---------------------------------------------------------------------------


class TestLocalHookEntryPoints:
    LOCAL_ENTRIES: ClassVar[dict[str, str]] = {
        "scan-conflicts": "python scripts/scan_conflicts.py",
        "workflow-yaml": "scripts/hooks/pre-commit-workflow-yaml",
        "ruff-lint": "uv run ruff check src tests",
        "mypy": "make _precommit-mypy",
        "check-tdd-compliance": "uv run python scripts/check_tdd_compliance.py",
        "check-disk": "uv run python scripts/check_disk_usage.py",
        "collect-check": "uv run python -m pytest tests/ --co -q",
        "verify-secrets": "make verify-secrets",
    }

    _PYTHON_SCRIPT_ID_TO_PATH: ClassVar[dict[str, str]] = {
        "scan-conflicts": "scripts/scan_conflicts.py",
        "check-tdd-compliance": "scripts/check_tdd_compliance.py",
        "check-disk": "scripts/check_disk_usage.py",
    }

    def test_entry_points_match_expected(self, config: _PreCommitConfig) -> None:
        local = [r for r in config["repos"] if r["repo"] == "local"]
        assert len(local) == 1, "expected exactly one local repo block"
        hooks = local[0]["hooks"]
        for hook in hooks:
            hid = hook["id"]
            entry = hook.get("entry", "")
            expected = self.LOCAL_ENTRIES.get(hid)
            assert expected is not None, f"unknown local hook id {hid!r}"
            assert entry == expected, f"hook {hid!r}: entry={entry!r} != expected={expected!r}"

    def test_mypy_entry_uses_cross_platform_null_cache_target(self) -> None:
        makefile = (_PROJECT / "Makefile").read_text(encoding="utf-8")
        assert "MYPY_NULL_CACHE := $(if $(filter Windows_NT,$(OS)),nul,/dev/null)" in makefile
        assert "_precommit-mypy:" in makefile
        assert 'mypy --cache-dir="$(MYPY_NULL_CACHE)" -p general_ludd' in makefile

    def test_python_scripts_exist(self) -> None:
        for hid, rel in self._PYTHON_SCRIPT_ID_TO_PATH.items():
            path = _PROJECT / rel
            assert path.is_file(), f"hook {hid!r}: script missing at {path}"

    def test_python_scripts_are_importable(self) -> None:
        for hid, rel in self._PYTHON_SCRIPT_ID_TO_PATH.items():
            path = _PROJECT / rel
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location(hid, str(path))
            if spec is None:
                pytest.fail(f"hook {hid!r}: cannot load spec from {path}")
            loader = spec.loader
            if loader is None:
                pytest.fail(f"hook {hid!r}: spec has no loader for {path}")
            mod = importlib.util.module_from_spec(spec)
            try:
                loader.exec_module(mod)
            except Exception as exc:
                pytest.fail(f"hook {hid!r}: script {path} raised {exc}")

    def test_no_local_hook_has_rev(self, config: _PreCommitConfig) -> None:
        local = [r for r in config["repos"] if r["repo"] == "local"]
        for repo in local:
            assert "rev" not in repo, "local repo must not have a 'rev' key"


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


class TestHookStages:
    def test_default_stage_applies_to_hooks_without_explicit_stages(
        self,
        config: _PreCommitConfig,
    ) -> None:
        for repo in config["repos"]:
            default = repo.get("default_stages", ["pre-commit"])
            for stage in default:
                assert stage in _VALID_STAGES, f"repo {repo['repo']!r}: invalid default_stage {stage!r}"

    def test_explicit_stages_are_valid(self, config: _PreCommitConfig) -> None:
        for entry in _all_hooks(config):
            hook = entry["hook"]
            for stage in hook.get("stages", []):
                assert stage in _VALID_STAGES, f"hook {hook['id']!r}: invalid stage {stage!r}"

    def test_all_local_hooks_are_pre_commit_or_unspecified(self, config: _PreCommitConfig) -> None:
        local = [r for r in config["repos"] if r["repo"] == "local"]
        for repo in local:
            for hook in repo["hooks"]:
                stages = hook.get("stages", [])
                for s in stages:
                    assert s == "pre-commit", f"local hook {hook['id']!r} has non-pre-commit stage {s!r}"

    def test_no_merge_commit_stages_on_source_quality_hooks(self, config: _PreCommitConfig) -> None:
        quality_ids = {"ruff-lint", "mypy", "check-tdd-compliance", "collect-check"}
        for entry in _all_hooks(config):
            hook = entry["hook"]
            if hook["id"] in quality_ids:
                stages = hook.get("stages", [])
                for s in stages:
                    assert "merge" not in s, f"quality hook {hook['id']!r} has merge stage {s!r}"


# ---------------------------------------------------------------------------
# duplicate IDs
# ---------------------------------------------------------------------------


class TestNoDuplicateHookIds:
    def test_no_duplicate_hook_ids_across_all_repos(self, config: _PreCommitConfig) -> None:
        seen: dict[str, str] = {}
        for repo in config["repos"]:
            for hook in repo["hooks"]:
                hid = hook["id"]
                if hid in seen:
                    pytest.fail(f"duplicate hook id {hid!r} in repo {repo['repo']!r} and repo {seen[hid]!r}")
                seen[hid] = repo["repo"]

    def test_no_duplicate_hook_ids_within_same_repo(self, config: _PreCommitConfig) -> None:
        for repo in config["repos"]:
            ids = [h["id"] for h in repo["hooks"]]
            assert len(ids) == len(set(ids)), f"repo {repo['repo']!r} has duplicate hook ids: {ids}"


# ---------------------------------------------------------------------------
# structural completeness
# ---------------------------------------------------------------------------


class TestConfigStructure:
    def test_config_is_valid_yaml(self) -> None:
        assert _CONFIG_PATH.is_file(), ".pre-commit-config.yaml not found"
        cfg = _load_config()
        assert "repos" in cfg

    def test_every_hook_has_id(self, config: _PreCommitConfig) -> None:
        for entry in _all_hooks(config):
            assert "id" in entry["hook"], f"hook missing 'id' in repo {entry['repo']['repo']!r}"
