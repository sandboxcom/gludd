"""Deep YAML config file validation tests.

Covers all .yml/.yaml files under config/: parse validity, required keys,
value types, no duplicate keys (raw-text check), reference consistency.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "config"


def _collect_yaml_files() -> list[Path]:
    return sorted(p for p in CONFIG_ROOT.rglob("*") if p.suffix in (".yml", ".yaml"))


def _load_yaml(path: Path) -> Any:
    with open(path) as fh:
        return yaml.safe_load(fh)


def _raw_text(path: Path) -> str:
    return path.read_text()


def _expect_dict(path: Path) -> dict[str, Any]:
    data = _load_yaml(path)
    assert isinstance(data, dict), f"{path.name} is not a mapping: {type(data)}"
    return data


# ---------------------------------------------------------------------------
# 1. All configs parse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _collect_yaml_files(), ids=lambda p: str(p.relative_to(CONFIG_ROOT)))
def test_yaml_config_parses(path: Path) -> None:
    """Every .yml/.yaml in config/ must parse without exception.

    Comment-only files (ratchet.yml) parse to None — that is valid.
    """
    _load_yaml(path)  # no exception = pass


# ---------------------------------------------------------------------------
# 2. Required keys for known config types
# ---------------------------------------------------------------------------


def test_general_ludd_required_keys() -> None:
    data = _expect_dict(CONFIG_ROOT / "general-ludd.yml")
    for key in ("network", "model_routing", "database", "agents", "orchestration", "process_isolation", "budget"):
        assert key in data, f"general-ludd.yml missing required key: {key}"


def test_network_subkeys() -> None:
    data = _expect_dict(CONFIG_ROOT / "general-ludd.yml")
    net: dict[str, Any] = data["network"]
    assert "host" in net
    assert "port" in net
    assert isinstance(net["host"], str)
    assert isinstance(net["port"], int)


def test_database_subkeys() -> None:
    data = _expect_dict(CONFIG_ROOT / "general-ludd.yml")
    db: dict[str, Any] = data["database"]
    for k in ("host", "port", "name", "user"):
        assert k in db


def test_budget_subkeys() -> None:
    data = _expect_dict(CONFIG_ROOT / "general-ludd.yml")
    budget: dict[str, Any] = data["budget"]
    assert "max_usd" in budget
    assert "warn_percent" in budget
    assert isinstance(budget["max_usd"], (int, float))
    assert isinstance(budget["warn_percent"], (int, float))


def test_ai_sdlc_required_keys() -> None:
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    for key in (
        "version",
        "codified",
        "description",
        "frameworks",
        "pipeline_stages",
        "quality_gates",
        "evidence_bundles",
        "role_stage_mapping",
        "blocking_stages",
        "stage_timeouts",
        "stage_model_routing",
    ):
        assert key in data, f"ai_sdlc.yml missing required key: {key}"


def test_ai_sdlc_pipeline_stages_have_required_fields() -> None:
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    stages: list[Any] = data["pipeline_stages"]
    assert isinstance(stages, list)
    assert len(stages) == 8
    for stage in stages:
        assert isinstance(stage, dict)
        for field in (
            "stage",
            "number",
            "description",
            "gludd_event_loop_phase",
            "entry_gate",
            "roles",
            "exit_gate",
            "validation_token",
            "evidence",
            "blocking",
            "timeout_minutes",
        ):
            assert field in stage, f"stage {stage.get('stage', '?')} missing field: {field}"
        assert isinstance(stage["number"], int)
        assert isinstance(stage["validation_token"], str)
        assert isinstance(stage["blocking"], bool)
        assert isinstance(stage["timeout_minutes"], int)


def test_agents_default_agents_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "agents" / "default_agents.yml")
    agents: list[Any] = data["agents"]
    assert isinstance(agents, list)
    assert len(agents) == 5
    for agent in agents:
        assert isinstance(agent, dict)
        for field in (
            "name",
            "description",
            "type",
            "model_profile",
            "prompt_profile",
            "max_steps",
            "permissions",
            "max_concurrent",
            "enabled",
        ):
            assert field in agent, f"agent {agent.get('name', '?')} missing field: {field}"
        assert agent["type"] in ("primary", "subagent")
        assert isinstance(agent["max_steps"], int)
        assert isinstance(agent["max_concurrent"], int)
        assert isinstance(agent["enabled"], bool)
        perms: dict[str, Any] = agent["permissions"]
        for pf in ("can_edit", "can_bash", "can_read", "can_dispatch_subagents"):
            assert pf in perms
            assert isinstance(perms[pf], bool)


def test_memory_bank_templates_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "memory_bank_templates.yml")
    templates: dict[str, Any] = data["templates"]
    assert isinstance(templates, dict)
    for name, tmpl in templates.items():
        assert isinstance(tmpl, dict)
        for field in ("bank_id", "mission", "disposition", "directives"):
            assert field in tmpl, f"template {name} missing field: {field}"
        disp: dict[str, Any] = tmpl["disposition"]
        for dk in ("skepticism", "literalism", "empathy"):
            assert dk in disp
            assert isinstance(disp[dk], int)
            assert 1 <= disp[dk] <= 5


def test_prompt_profiles_default_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "prompt_profiles" / "default.yml")
    for key in (
        "profile_id",
        "description",
        "system_prompt_template",
        "behavior",
        "skills",
        "model_hints",
        "token_budget",
    ):
        assert key in data
    tok: dict[str, Any] = data["token_budget"]
    for tk in ("max_input_tokens", "max_output_tokens", "reserve_for_tools"):
        assert tk in tok
        assert isinstance(tok[tk], int)


def test_binary_paths_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "binary_paths.yml")
    bp: dict[str, Any] = data["binary_paths"]
    assert isinstance(bp, dict)
    required_binaries = ("terraform", "git", "uv", "ansible_playbook")
    for b in required_binaries:
        assert b in bp


def test_openbao_default_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "openbao" / "default.yml")
    for key in ("mode", "local_image", "kv_mount", "auth_method", "approle_role_name"):
        assert key in data
    for ttl_key in ("approle_secret_id_ttl_seconds", "approle_token_ttl_seconds", "approle_token_max_ttl_seconds"):
        assert ttl_key in data
        assert isinstance(data[ttl_key], int)
        assert data[ttl_key] > 0


def test_tdd_allowlist_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "tdd_allowlist.yml")
    items: list[Any] = data["allowlist"]
    assert isinstance(items, list)
    for item in items:
        assert isinstance(item, dict)
        assert "path" in item
        assert "reason" in item


def test_ratchet_is_parsable_or_empty() -> None:
    data = _load_yaml(CONFIG_ROOT / "ratchet.yml")
    assert data is not None or data is None  # comment-only files parse to None


def test_infra_providers_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "infra" / "providers.yml")
    providers: list[Any] = data["providers"]
    assert isinstance(providers, list)
    assert len(providers) > 5
    for p in providers:
        assert isinstance(p, dict)
        for field in (
            "provider",
            "display_name",
            "terraform_provider",
            "supports_spot",
            "sub_hour_billing",
            "min_gpu",
            "max_gpu",
            "pricing",
        ):
            assert field in p
        assert isinstance(p["supports_spot"], bool)
        assert isinstance(p["sub_hour_billing"], bool)
        pricing: dict[str, Any] = p["pricing"]
        assert isinstance(pricing, dict)
        assert len(pricing) >= 1


def test_permissions_all_have_version_and_capabilities() -> None:
    perm_dir = CONFIG_ROOT / "permissions"
    for path in sorted(perm_dir.glob("*.yml")):
        data: dict[str, Any] = _expect_dict(path)
        for key in ("version", "agent_type", "capabilities", "denied"):
            assert key in data, f"{path.name} missing key: {key}"
        assert isinstance(data["version"], int)
        caps: list[Any] = data["capabilities"]
        denied: list[Any] = data["denied"]
        assert isinstance(caps, list)
        assert isinstance(denied, list)
        for cap in caps:
            assert isinstance(cap, dict)
            assert "resource" in cap
            assert "actions" in cap
            assert isinstance(cap["actions"], list)


def test_ansible_isolation_structure() -> None:
    data = _expect_dict(CONFIG_ROOT / "ansible" / "isolation.yml")
    iso: dict[str, Any] = data["process_isolation"]
    for key in ("enabled", "executable", "hide_paths", "show_paths", "ro_paths", "block_local_tools"):
        assert key in iso
    assert isinstance(iso["enabled"], bool)


# ---------------------------------------------------------------------------
# 3. No duplicate keys (raw-text check)
# ---------------------------------------------------------------------------


def test_no_duplicate_top_level_keys_in_mapping_files() -> None:
    """Scan YAML files for duplicate mapping keys at top level via raw text.

    Top-level keys are lines that: contain ':', do NOT start with '#' or
    whitespace or '-', and represent YAML mapping keys (not inline comments).
    """
    for path in _collect_yaml_files():
        text = _raw_text(path)
        lines = text.splitlines()
        top_keys: list[str] = []
        for line in lines:
            # Must have ':', not be a comment or list item
            if ":" not in line or line.lstrip().startswith("#") or line.lstrip().startswith("-"):
                continue
            # Must be at column 0 (no leading whitespace) for top-level
            if line.startswith(" ") or line.startswith("\t"):
                continue
            key = line.split(":", 1)[0].strip()
            if key:
                top_keys.append(key)
        seen: dict[str, int] = {}
        for idx, k in enumerate(top_keys):
            if k in seen:
                pytest.fail(
                    f"{path.relative_to(CONFIG_ROOT)}: duplicate top-level key '{k}' "
                    f"(lines {seen[k] + 1} and {top_keys.index(k) + 1})"
                )
            seen[k] = idx


# ---------------------------------------------------------------------------
# 4. Reference consistency
# ---------------------------------------------------------------------------


def test_model_routing_refers_to_existing_profiles() -> None:
    """Profiles referenced in model_routing.yml must exist in model_profiles/."""
    routing = _expect_dict(CONFIG_ROOT / "model_routing.yml")

    profile_dir = CONFIG_ROOT / "model_profiles"
    profile_ids: set[str] = set()
    for mp in sorted(profile_dir.rglob("*.yml")):
        # Skip format files (they use format_profile_id, not model_profile_id)
        if "formats" in str(mp):
            continue
        mp_data: dict[str, Any] = _expect_dict(mp)
        if "model_profile_id" in mp_data:
            profile_ids.add(str(mp_data["model_profile_id"]))

    refs: set[str] = set()
    dp: str | None = routing.get("default_profile")
    if dp:
        refs.add(dp)
    wp: str | None = routing.get("weak_model_profile")
    if wp:
        refs.add(wp)
    fc: list[str] = routing.get("fallback_chain", [])
    if fc:
        refs.update(fc)
    rr: dict[str, str] = routing.get("role_routing", {})
    refs.update(rr.values())
    qr: dict[str, str] = routing.get("quality_routing", {})
    refs.update(qr.values())
    lr: dict[str, str] = routing.get("latency_routing", {})
    refs.update(lr.values())

    missing = refs - profile_ids
    assert not missing, f"model_routing.yml references profiles not in model_profiles/: {missing}"


def test_agents_refer_to_existing_model_profiles() -> None:
    """Agent model_profile values must exist in model_profiles/."""
    agents_data = _expect_dict(CONFIG_ROOT / "agents" / "default_agents.yml")

    profile_dir = CONFIG_ROOT / "model_profiles"
    profile_ids: set[str] = set()
    for mp in sorted(profile_dir.rglob("*.yml")):
        if "formats" in str(mp):
            continue
        mp_data: dict[str, Any] = _expect_dict(mp)
        if "model_profile_id" in mp_data:
            profile_ids.add(str(mp_data["model_profile_id"]))

    agent_list: list[Any] = agents_data["agents"]
    for agent in agent_list:
        assert isinstance(agent, dict)
        mp = agent["model_profile"]
        assert mp in profile_ids, f"Agent '{agent['name']}' references unknown model_profile '{mp}'"


def test_model_routing_pattern_refs_match_role_routing_keys() -> None:
    """Pattern routing values must be keys in role_routing or 'weak'."""
    routing = _expect_dict(CONFIG_ROOT / "model_routing.yml")
    roler: dict[str, Any] = routing.get("role_routing", {})
    role_keys: set[str] = set(roler.keys())
    special_keys = {"weak"}

    patternr: dict[str, Any] = routing.get("pattern_routing", {})
    for pattern, role in patternr.items():
        assert role in role_keys | special_keys, (
            f"pattern '{pattern}' references role '{role}' not in role_routing keys"
        )


def test_prompt_profiles_skills_are_strings() -> None:
    """Prompt profile skills entries must be strings."""
    profile = _expect_dict(CONFIG_ROOT / "prompt_profiles" / "default.yml")
    skills: list[Any] = profile.get("skills", [])
    for skill in skills:
        assert isinstance(skill, str), f"Skill entry is not a string: {skill}"


def test_model_profiles_all_have_model_profile_id() -> None:
    """Every model profile .yml (non-format) MUST have a 'model_profile_id' key."""
    profile_dir = CONFIG_ROOT / "model_profiles"
    for mp in sorted(profile_dir.rglob("*.yml")):
        if "formats" in str(mp):
            continue
        data: dict[str, Any] = _expect_dict(mp)
        assert "model_profile_id" in data, f"{mp.name} missing model_profile_id"


def test_model_profile_format_files_all_have_format_profile_id() -> None:
    """Model profile format files must have a 'format_profile_id' key."""
    fmt_dir = CONFIG_ROOT / "model_profiles" / "formats"
    for fmt_path in sorted(fmt_dir.glob("*.yml")):
        data: dict[str, Any] = _expect_dict(fmt_path)
        assert "format_profile_id" in data, f"{fmt_path.name} missing 'format_profile_id' key"


def test_ai_sdlc_quality_gates_have_all_required_check_fields() -> None:
    """Quality gate checks must have id, check, kind, severity, description, enforcement."""
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    gates: dict[str, Any] = data.get("quality_gates", {})
    for gate_name, gate in gates.items():
        assert isinstance(gate, dict)
        checks: list[Any] = gate.get("checks", [])
        for check in checks:
            assert isinstance(check, dict)
            for field in ("id", "check", "kind", "severity", "description", "enforcement"):
                assert field in check, f"Gate '{gate_name}' check '{check.get('id', '?')}' missing field: {field}"
            assert check["severity"] in ("critical", "high", "medium", "low")
            assert check["enforcement"] in ("fail", "warn")


def test_ai_sdlc_stage_timeouts_are_positive_ints() -> None:
    """All stage timeouts must be positive integers."""
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    timeouts: dict[str, Any] = data.get("stage_timeouts", {})
    for stage, minutes in timeouts.items():
        assert isinstance(minutes, int), f"stage_timeout {stage} is not int: {minutes}"
        assert minutes > 0, f"stage_timeout {stage} is not positive: {minutes}"


def test_ai_sdlc_blocking_stages_have_boolean_values() -> None:
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    blocking: dict[str, Any] = data.get("blocking_stages", {})
    for stage, val in blocking.items():
        assert isinstance(val, bool), f"blocking_stage {stage} is not bool: {val}"


def test_ai_sdlc_evidence_bundles_have_artifact_or_kind() -> None:
    """Evidence bundle artifacts must have 'artifact' and either 'path' or 'kind'."""
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    bundles: dict[str, Any] = data.get("evidence_bundles", {})
    for stage_name, bundle in bundles.items():
        assert isinstance(bundle, dict)
        required: list[Any] = bundle.get("required", [])
        for artifact in required:
            assert isinstance(artifact, dict)
            assert "artifact" in artifact, f"evidence_bundles.{stage_name} artifact missing 'artifact' key"
            has_path_or_kind = "path" in artifact or "kind" in artifact
            assert has_path_or_kind, (
                f"evidence_bundles.{stage_name} artifact '{artifact.get('artifact')}' missing both 'path' and 'kind'"
            )


def test_ai_sdlc_frameworks_are_non_empty() -> None:
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    frameworks: dict[str, Any] = data.get("frameworks", {})
    assert len(frameworks) >= 6, f"Expected >=6 frameworks in ai_sdlc.yml, got {len(frameworks)}"
    for fw_name, fw in frameworks.items():
        assert isinstance(fw, dict)
        assert "source" in fw, f"Framework '{fw_name}' missing 'source'"


def test_ai_sdlc_role_stage_mapping_has_all_stages() -> None:
    data = _expect_dict(CONFIG_ROOT / "ai_sdlc.yml")
    mapping: dict[str, Any] = data.get("role_stage_mapping", {})
    expected_stages = {"intake", "planning", "implementation", "review", "gate", "integration", "deployment", "operate"}
    assert set(mapping.keys()) == expected_stages, (
        f"role_stage_mapping stages mismatch: {set(mapping.keys()) ^ expected_stages}"
    )
