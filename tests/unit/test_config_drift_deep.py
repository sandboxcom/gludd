"""Deep configuration drift detection tests.

Detects: version inconsistency, stale references across config files,
missing plugin files, permission-path divergence, invalid YAML/JSON/TOML,
environment parity breaks, and budget logic errors.
"""

from __future__ import annotations

import json
import tomllib
import typing
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _yaml_load(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _toml_load(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _json_load(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


# ── Version Consistency ───────────────────────────────────────────────────────


class TestVersionConsistency:
    def test_pyproject_and_init_py_version_match(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        init_py = (REPO_ROOT / "src" / "general_ludd" / "__init__.py").read_text()
        pv = pyproject["project"]["version"]
        for line in init_py.splitlines():
            if line.strip().startswith("__version__"):
                iv = line.split("=")[1].strip().strip('"').strip("'")
                assert pv == iv, f"pyproject={pv} vs __init__={iv}"
                return
        pytest.fail("__version__ not found in __init__.py")

    def test_version_is_not_placeholder(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        version = pyproject["project"]["version"]
        assert version not in ("0.0.0", "0.0.1", "", "0.1.0"), f"placeholder version: {version}"
        assert any(c.isdigit() for c in version), f"version has no digits: {version}"

    def test_python_requires_matches_mypy_python_version(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        requires = pyproject["project"]["requires-python"]
        mypy_py = pyproject["tool"]["mypy"]["python_version"]
        assert mypy_py in requires.replace(">=", ""), f"mypy={mypy_py} vs requires-python={requires}"

    def test_python_requires_matches_ruff_target(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        requires = pyproject["project"]["requires-python"]
        ruff_target = pyproject["tool"]["ruff"]["target-version"]
        rt_py = ruff_target.replace("py", "").replace("3", "3.")
        assert rt_py in requires.replace(">=", ""), f"ruff={ruff_target} vs requires-python={requires}"


# ── Plugin File Existence ─────────────────────────────────────────────────────


class TestPluginFileExistence:
    def test_all_opencode_plugins_exist_on_disk(self):
        opencode_json = _json_load(REPO_ROOT / "opencode.json")
        plugins = opencode_json.get("plugin", [])
        assert len(plugins) >= 30, f"expected >=30 plugins, got {len(plugins)}"
        for p in plugins:
            full = (REPO_ROOT / p).resolve()
            assert full.exists(), f"plugin {p} not found on disk"
            assert full.is_file(), f"plugin {p} is not a file"

    def test_no_duplicate_plugins(self):
        opencode_json = _json_load(REPO_ROOT / "opencode.json")
        plugins = opencode_json.get("plugin", [])
        seen = set()
        dups = []
        for p in plugins:
            if p in seen:
                dups.append(p)
            seen.add(p)
        assert not dups, f"duplicate plugins: {dups}"

    def test_agents_md_references_plugins_match_opencode_json(self):
        agents_md = (REPO_ROOT / "AGENTS.md").read_text()
        opencode_json = _json_load(REPO_ROOT / "opencode.json")
        registered = set(opencode_json.get("plugin", []))
        for line in agents_md.splitlines():
            if line.startswith("| enforce-") and ".ts" in line:
                name = line.split("|")[1].strip()
                if name.endswith(".ts"):
                    prefix = f"./.opencode/plugin/{name}"
                    alt_prefix = f"./.opencode/plugins/{name}"
                    assert prefix in registered or alt_prefix in registered, (
                        f"AGENTS.md references {name} but not in opencode.json plugin list"
                    )


# ── Permission Path Consistency ───────────────────────────────────────────────


class TestPermissionPathConsistency:
    """All file tools must share ONE external-path authorization source.

    OpenCode's current permission model routes write/edit/patch through
    ``edit``, keeps the workspace implicit (internal), and gates every
    external path through the single ``external_directory`` block. No tool
    may carry a private absolute-path grant map — a per-tool map would let
    one tool diverge from the reviewed prefixes. This is the current-model
    equivalent of the legacy per-tool path-map pins.
    """

    _FILE_TOOLS: typing.ClassVar[list[str]] = ["read", "edit", "glob", "grep"]
    _WORKSPACE = "/Users/shawnwilson/gludd/**"

    def test_all_permission_tools_have_same_allowed_paths(self):
        opencode_json = _json_load(REPO_ROOT / "opencode.json")
        perm = opencode_json["permission"]
        # No file tool may grant absolute paths through a per-tool map: the
        # only path-authorization surface is the shared external_directory
        # block, which applies uniformly to every tool.
        for tool in self._FILE_TOOLS:
            value = perm[tool]
            if isinstance(value, dict):
                for key in value:
                    assert not key.startswith("/"), (
                        f"{tool} carries a private absolute-path rule {key!r}; "
                        "external paths must be authorized only via external_directory"
                    )
            else:
                assert value == "allow", f"{tool} must be 'allow' for the workspace"
        assert "write" not in perm, (
            "write/edit/patch route through the 'edit' permission; a separate "
            "'write' map would diverge from the shared authorization source"
        )

    def test_star_deny_is_first_rule_in_each_permission(self):
        opencode_json = _json_load(REPO_ROOT / "opencode.json")
        perm = opencode_json["permission"]
        # Every dict-valued permission must place its catch-all first
        # (last-match-wins); the fail-closed surfaces deny by default.
        for tool, value in perm.items():
            if not isinstance(value, dict) or not value:
                continue
            items = list(value.items())
            assert items[0][0] == "*", f"{tool}: '*' catch-all must be the first rule"
            if tool in ("bash", "external_directory"):
                assert items[0][1] == "deny", f"{tool}: first rule must be deny"

    def test_workspace_is_allowed_in_all_permission_tools(self):
        opencode_json = _json_load(REPO_ROOT / "opencode.json")
        perm = opencode_json["permission"]
        # The workspace is internal: every file tool stays enabled for it and
        # the workspace prefix must not be duplicated as an external grant.
        for tool in self._FILE_TOOLS:
            value = perm[tool]
            if isinstance(value, dict):
                assert value.get("*", "allow") != "deny", f"{tool} denies the workspace"
            else:
                assert value == "allow", f"{tool} disabled for the workspace"
        assert self._WORKSPACE not in perm.get("external_directory", {}), (
            "the active worktree is internal; listing it under external_directory would couple policy to one checkout"
        )


# ── Config File Parsing ───────────────────────────────────────────────────────


class TestConfigFileParsing:
    @pytest.mark.parametrize(
        "path",
        [
            "config/general-ludd.yml",
            "config/binary_paths.yml",
            "config/tdd_allowlist.yml",
            "config/ai_sdlc.yml",
            "config/model_routing.yml",
            "config/memory_bank_templates.yml",
            ".pre-commit-config.yaml",
        ],
    )
    def test_yaml_files_parse(self, path):
        data = _yaml_load(REPO_ROOT / path)
        assert data is not None, f"{path} is empty or invalid YAML"

    def test_ratchet_yml_is_valid_template(self):
        path = REPO_ROOT / "config" / "ratchet.yml"
        assert path.exists(), "ratchet.yml missing"
        content = path.read_text()
        assert "node_id: reason" in content, "ratchet.yml missing format documentation"

    def test_opencode_json_parses(self):
        data = _json_load(REPO_ROOT / "opencode.json")
        assert "$schema" in data
        assert "permission" in data
        assert "plugin" in data

    def test_pyproject_toml_parses(self):
        data = _toml_load(REPO_ROOT / "pyproject.toml")
        assert "project" in data
        assert "tool" in data

    def test_make_target_contract_parses(self):
        data = _json_load(REPO_ROOT / "config/make_target_contract.json")
        assert "version" in data
        assert "targets" in data
        names = [t["name"] for t in data["targets"]]
        assert len(names) == len(set(names)), f"duplicate target names: {set([n for n in names if names.count(n) > 1])}"


# ── Model Profile References ──────────────────────────────────────────────────


class TestModelProfileReferences:
    def _profile_ids(self) -> set[str]:
        pdir = REPO_ROOT / "config" / "model_profiles"
        ids = set()
        for f in pdir.glob("*.yml"):
            try:
                data = _yaml_load(f)
            except Exception:
                continue
            pid = data.get("model_profile_id")
            if pid:
                ids.add(str(pid))
        return ids

    def test_general_ludd_yml_model_routing_references_exist(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        routing = data.get("model_routing", {})
        profiles = self._profile_ids()
        for key in ("default_profile", "weak_model_profile"):
            pid = routing.get(key)
            if pid:
                assert pid in profiles, f"{key}={pid} not in model_profiles/"
        for section in ("role_routing", "quality_routing", "latency_routing"):
            for role, pid in routing.get(section, {}).items():
                assert pid in profiles, f"{section}.{role}={pid} not in model_profiles/"

    def test_model_routing_yml_references_exist(self):
        data = _yaml_load(REPO_ROOT / "config" / "model_routing.yml")
        profiles = self._profile_ids()
        for key in ("default_profile", "weak_model_profile"):
            pid = data.get(key)
            if pid:
                assert pid in profiles, f"{key}={pid} not in model_profiles/"
        for pid in data.get("fallback_chain", []):
            assert pid in profiles, f"fallback_chain={pid} not in model_profiles/"
        for section in ("role_routing", "quality_routing", "latency_routing"):
            for role, pid in data.get(section, {}).items():
                assert pid in profiles, f"{section}.{role}={pid} not in model_profiles/"


# ── Binary Path Validity ──────────────────────────────────────────────────────


class TestBinaryPathValidity:
    def test_all_binary_paths_non_empty(self):
        data = _yaml_load(REPO_ROOT / "config" / "binary_paths.yml")
        paths = data.get("binary_paths", {})
        assert len(paths) >= 10, f"expected >=10 binary paths, got {len(paths)}"
        for name, path in paths.items():
            assert path and str(path).strip(), f"binary_paths.{name} is empty"

    def test_binary_paths_no_duplicate_values(self):
        data = _yaml_load(REPO_ROOT / "config" / "binary_paths.yml")
        paths = data.get("binary_paths", {})
        seen = {}
        for name, path in paths.items():
            if path in seen:
                pytest.fail(f"duplicate path={path} for {name} and {seen[path]}")
            seen[path] = name


# ── Pre-commit Config ─────────────────────────────────────────────────────────


class TestPrecommitConfig:
    def test_both_remote_and_local_hooks(self):
        data = _yaml_load(REPO_ROOT / ".pre-commit-config.yaml")
        repos = data.get("repos", [])
        remote = [r for r in repos if not str(r.get("repo", "")).startswith("local")]
        local = [r for r in repos if str(r.get("repo", "")) == "local"]
        assert len(remote) >= 2, f"expected >=2 remote repos, got {len(remote)}"
        assert len(local) >= 1, f"expected >=1 local repo, got {len(local)}"

    def test_detect_secrets_has_baseline(self):
        data = _yaml_load(REPO_ROOT / ".pre-commit-config.yaml")
        for repo in data.get("repos", []):
            if "detect-secrets" in str(repo.get("repo", "")):
                for hook in repo.get("hooks", []):
                    if hook.get("id") == "detect-secrets":
                        args = hook.get("args", [])
                        assert "--baseline" in args, "detect-secrets missing --baseline arg"
                        return
        pytest.fail("detect-secrets hook not found")

    def test_no_commit_to_branch_excludes_main(self):
        data = _yaml_load(REPO_ROOT / ".pre-commit-config.yaml")
        for repo in data.get("repos", []):
            for hook in repo.get("hooks", []):
                if hook.get("id") == "no-commit-to-branch":
                    assert "--branch" in hook.get("args", []), "no-commit-to-branch missing --branch"
                    assert "main" in hook.get("args", []), "should exclude main branch"
                    return
        pytest.fail("no-commit-to-branch hook not found")


# ── Coverage Gaps Baseline ────────────────────────────────────────────────────


class TestCoverageGapsBaseline:
    def test_valid_json_structure(self):
        data = _json_load(REPO_ROOT / "config" / "coverage_gaps_baseline.json")
        assert "allowed_gaps" in data
        assert isinstance(data["allowed_gaps"], list)

    def test_all_gaps_are_in_src(self):
        data = _json_load(REPO_ROOT / "config" / "coverage_gaps_baseline.json")
        for gap in data["allowed_gaps"]:
            assert gap.startswith("src/"), f"gap not in src/: {gap}"
            assert gap.endswith(".py"), f"gap not a .py file: {gap}"

    def test_no_duplicate_gaps(self):
        data = _json_load(REPO_ROOT / "config" / "coverage_gaps_baseline.json")
        gaps = data["allowed_gaps"]
        assert len(gaps) == len(set(gaps)), f"duplicate gaps: {set([g for g in gaps if gaps.count(g) > 1])}"


# ── TDD Allowlist ─────────────────────────────────────────────────────────────


class TestTDDAllowlist:
    def test_valid_yaml_structure(self):
        data = _yaml_load(REPO_ROOT / "config" / "tdd_allowlist.yml")
        assert "allowlist" in data
        assert isinstance(data["allowlist"], list)

    def test_all_entries_have_path_and_reason(self):
        data = _yaml_load(REPO_ROOT / "config" / "tdd_allowlist.yml")
        for entry in data["allowlist"]:
            assert "path" in entry, f"entry missing path: {entry}"
            assert "reason" in entry, f"entry missing reason: {entry}"
            assert len(str(entry["reason"]).strip()) >= 20, f"reason too short for {entry['path']}"

    def test_no_placeholder_reasons(self):
        data = _yaml_load(REPO_ROOT / "config" / "tdd_allowlist.yml")
        forbidden = ("don't need tests", "no tests needed", "placeholder", "TODO", "temp")
        for entry in data["allowlist"]:
            reason_lower = str(entry["reason"]).lower()
            for bad in forbidden:
                assert bad not in reason_lower, f"{entry['path']} has forbidden reason: {bad}"


# ── Budget Config Sanity ──────────────────────────────────────────────────────


class TestBudgetConfig:
    def test_warn_below_max(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        budget = data.get("budget", {})
        max_usd = budget.get("max_usd", 0)
        warn_pct = budget.get("warn_percent", 0)
        assert 0 < warn_pct < 100, f"warn_percent out of range: {warn_pct}"
        assert max_usd > 0, f"max_usd must be positive: {max_usd}"

    def test_orchestration_max_concurrent_positive(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        orch = data.get("orchestration", {})
        mmc = orch.get("max_concurrent_model_calls", 0)
        assert mmc >= 1, f"max_concurrent_model_calls must be >=1: {mmc}"

    def test_agents_max_concurrent_positive(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        agents = data.get("agents", {})
        mc = agents.get("max_concurrent", 0)
        assert mc >= 1, f"agents.max_concurrent must be >=1: {mc}"


# ── Ruff / Mypy / Pytest Config Consistency ───────────────────────────────────


class TestLintConfigConsistency:
    def test_line_length_consistent(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        line_length = pyproject["tool"]["ruff"]["line-length"]
        assert line_length <= 120, f"line-length too high: {line_length}"
        assert line_length >= 79, f"line-length too low: {line_length}"

    def test_coverage_fail_under_reasonable(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        fail_under = pyproject["tool"]["coverage"]["report"]["fail_under"]
        assert 50 <= fail_under <= 95, f"fail_under out of range: {fail_under}"

    def test_pytest_timeout_reasonable(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        timeout = pyproject["tool"]["pytest"]["ini_options"]["timeout"]
        assert 30 <= timeout <= 600, f"pytest timeout out of range: {timeout}"


# ── Environment Parity ────────────────────────────────────────────────────────


class TestEnvironmentParity:
    def test_core_deps_not_duplicated_in_dev(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        core_names = set()
        for dep in pyproject["project"]["dependencies"]:
            name = dep.split(">=")[0].split("==")[0].split("[")[0].strip().lower()
            core_names.add(name)
        dev_deps = pyproject["project"]["optional-dependencies"].get("dev", [])
        dev_names = set()
        for dep in dev_deps:
            name = dep.split(">=")[0].split("==")[0].split("[")[0].strip().lower()
            dev_names.add(name)
        dupes = core_names & dev_names
        for dupe in dupes:
            core_ver = None
            dev_ver = None
            for d in pyproject["project"]["dependencies"]:
                if d.lower().startswith(dupe):
                    core_ver = d
                    break
            for d in dev_deps:
                if d.lower().startswith(dupe):
                    dev_ver = d
                    break
            assert core_ver == dev_ver, f"{dupe} has different version: core={core_ver} vs dev={dev_ver}"

    def test_dependency_groups_dev_matches_optional_dev(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        opt_dev = set(d.lower() for d in pyproject["project"]["optional-dependencies"]["dev"])
        dep_group_dev = set(d.lower() for d in pyproject.get("dependency-groups", {}).get("dev", []))
        missing_in_dep_group = opt_dev - dep_group_dev
        assert not missing_in_dep_group, (
            f"deps in optional-dependencies.dev but not in dependency-groups.dev: {missing_in_dep_group}"
        )

    def test_build_system_is_hatchling(self):
        pyproject = _toml_load(REPO_ROOT / "pyproject.toml")
        assert pyproject["build-system"]["build-backend"] == "hatchling.build"


# ── AISDL Pipeline Config ─────────────────────────────────────────────────────


class TestAiSdlcConfig:
    def test_has_required_sections(self):
        data = _yaml_load(REPO_ROOT / "config" / "ai_sdlc.yml")
        for key in ("version", "frameworks", "pipeline_stages", "quality_gates"):
            assert key in data, f"ai_sdlc.yml missing key: {key}"

    def test_pipeline_stages_have_unique_numbers(self):
        data = _yaml_load(REPO_ROOT / "config" / "ai_sdlc.yml")
        nums = [s["number"] for s in data["pipeline_stages"]]
        assert len(nums) == len(set(nums)), f"duplicate stage numbers: {nums}"

    def test_stage_numbers_sequential_from_zero(self):
        data = _yaml_load(REPO_ROOT / "config" / "ai_sdlc.yml")
        nums = sorted(s["number"] for s in data["pipeline_stages"])
        assert nums == list(range(len(nums))), f"non-sequential stages: {nums}"

    def test_stage_timeouts_match_pipeline_stages(self):
        data = _yaml_load(REPO_ROOT / "config" / "ai_sdlc.yml")
        stage_names = {s["stage"] for s in data["pipeline_stages"]}
        timeout_stages = set(data.get("stage_timeouts", {}).keys())
        extra_timeouts = timeout_stages - stage_names
        missing_timeouts = stage_names - timeout_stages
        assert not extra_timeouts, f"timeouts for non-existent stages: {extra_timeouts}"
        assert not missing_timeouts, f"stages missing timeouts: {missing_timeouts}"

    def test_blocking_stages_match_pipeline_stages(self):
        data = _yaml_load(REPO_ROOT / "config" / "ai_sdlc.yml")
        stage_names = {s["stage"] for s in data["pipeline_stages"]}
        blocking = set(data.get("blocking_stages", {}).keys())
        for b in blocking:
            assert b in stage_names, f"blocking_stages references unknown stage: {b}"


# ── General Ludd Config ───────────────────────────────────────────────────────


class TestGeneralLuddConfig:
    def test_network_port_in_range(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        port = data["network"]["port"]
        assert 1 <= port <= 65535, f"port out of range: {port}"

    def test_database_port_in_range(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        port = data["database"]["port"]
        assert 1 <= port <= 65535, f"db port out of range: {port}"

    def test_database_fields_present(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        db = data["database"]
        for f in ("host", "port", "name", "user"):
            assert f in db, f"database missing field: {f}"
            assert db[f], f"database.{f} is empty"

    def test_process_isolation_container_runtime_valid(self):
        data = _yaml_load(REPO_ROOT / "config" / "general-ludd.yml")
        pi = data["process_isolation"]
        assert pi["enabled"] is False
        assert pi["executable"] in ("podman", "docker")
        assert pi["container_image"] is None
        assert pi["test_only_in_process"] is False


# ── AI SDLC Framework URLs ────────────────────────────────────────────────────


class TestAiSdlcFrameworkUrls:
    def test_external_urls_are_well_formed(self):
        data = _yaml_load(REPO_ROOT / "config" / "ai_sdlc.yml")
        for fw_name, fw_data in data.get("frameworks", {}).items():
            url = fw_data.get("url")
            if url:
                assert url.startswith("https://"), f"{fw_name} URL not https: {url}"
                assert "..." not in url, f"{fw_name} URL truncated: {url}"
