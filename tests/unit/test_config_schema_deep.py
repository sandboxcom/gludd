"""Deep config schema validation tests — 20+ tests covering all config/*.yml files.

Tests: all example configs parse, schema validates them, edge case values,
enum constraints, required field enforcement, model profiles, permissions,
memory bank templates, prompt profiles, binary paths, pricing, and more.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig
from general_ludd.config.model_routing import ModelRoutingConfig, load_model_routing
from general_ludd.config.user_config import (
    CompactionConfigBlock,
    HumanInTheLoopConfig,
    IssuesConfig,
    NetworkConfig,
    NotificationsConfig,
    ObservabilityConfig,
    OrchestrationGuardConfig,
    PipelineConfigBlock,
    RelationshipRoutingConfig,
    RemediationSettings,
    TerraformConfig,
    UserConfig,
    VmSandboxConfig,
)
from general_ludd.models.gateway import ModelProfile

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


# ── YAML parse tests for all example configs ──────────────────────────────────


class TestExampleConfigsParse:
    """All config/examples/*.yml files parse as valid YAML."""

    @pytest.mark.parametrize(
        "example_file",
        sorted(p.name for p in (CONFIG_DIR / "examples").glob("*.yml")),
    )
    def test_example_config_parses(self, example_file: str) -> None:
        path = CONFIG_DIR / "examples" / example_file
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data is not None, f"{example_file} parsed to None"
        assert isinstance(data, dict), f"{example_file} is not a dict"


class TestAllConfigYmlParse:
    """Every .yml file under config/ parses as valid YAML (excluding formats/ and unsupported dirs)."""

    @pytest.mark.parametrize(
        "yml_path",
        sorted(str(p.relative_to(CONFIG_DIR)) for p in CONFIG_DIR.rglob("*.yml") if "formats/" not in str(p)),
    )
    def test_config_file_parses(self, yml_path: str) -> None:
        path = CONFIG_DIR / yml_path
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            assert path.stat().st_size > 0, f"{yml_path} parsed to None but file is not empty"
        else:
            assert isinstance(data, (dict, list)), f"{yml_path} parsed to non-dict/list"


# ── ModelProfile schema validation ────────────────────────────────────────────


class TestModelProfileSchema:
    """All model_profile YAML files validate against ModelProfile pydantic model."""

    @pytest.mark.parametrize(
        "profile_file",
        sorted(p.name for p in (CONFIG_DIR / "model_profiles").glob("*.yml")),
    )
    def test_model_profile_validates(self, profile_file: str) -> None:
        path = CONFIG_DIR / "model_profiles" / profile_file
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        profile = ModelProfile(**data)
        assert profile.model_profile_id, f"{profile_file}: model_profile_id must not be empty"
        assert isinstance(profile.model_profile_id, str)

    def test_model_profile_id_required(self) -> None:
        with pytest.raises(ValidationError):
            ModelProfile(**{"provider": "openai"})

    def test_model_profile_id_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            ModelProfile(**{"model_profile_id": ""})

    def test_model_profile_id_stripped(self) -> None:
        profile = ModelProfile(**{"model_profile_id": "  foo  "})
        assert profile.model_profile_id == "foo"

    def test_positive_int_validator_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            ModelProfile(**{"model_profile_id": "test", "context_window": 0})

    def test_positive_int_validator_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            ModelProfile(**{"model_profile_id": "test", "max_input_tokens": -1})

    def test_non_negative_float_validator_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            ModelProfile(**{"model_profile_id": "test", "cost_per_input_token": -0.01})

    def test_non_negative_float_validator_rejects_nan(self) -> None:
        with pytest.raises(ValidationError):
            ModelProfile(**{"model_profile_id": "test", "cost_per_output_token": float("nan")})

    def test_fallback_profiles_defaults_to_empty(self) -> None:
        profile = ModelProfile(**{"model_profile_id": "test"})
        assert profile.fallback_profiles == []

    def test_enabled_defaults_to_false(self) -> None:
        profile = ModelProfile(**{"model_profile_id": "test"})
        assert profile.enabled is False

    def test_resource_profile_accepts_valid_string(self) -> None:
        profile = ModelProfile(**{"model_profile_id": "test", "resource_profile": "cpu_light"})
        assert profile.resource_profile == "cpu_light"

    def test_fallback_max_concurrency_default(self) -> None:
        profile = ModelProfile(**{"model_profile_id": "test"})
        assert profile.fallback_max_concurrency == 2

    def test_stream_provider_max_concurrency_default(self) -> None:
        profile = ModelProfile(**{"model_profile_id": "test"})
        assert profile.stream_provider_max_concurrency == 1


# ── NetworkConfig schema validation ───────────────────────────────────────────


class TestNetworkConfigSchema:
    """NetworkConfig pydantic model validation."""

    def test_defaults_loopback(self) -> None:
        cfg = NetworkConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8000
        assert cfg.allowed_cidr == []

    def test_loopback_requires_no_cidr(self) -> None:
        cfg = NetworkConfig(host="127.0.0.1", allowed_cidr=[])
        assert cfg.host == "127.0.0.1"

    def test_world_open_bind_requires_cidr(self) -> None:
        with pytest.raises(ValidationError, match="allowed_cidr"):
            NetworkConfig(host="0.0.0.0", allowed_cidr=[])

    def test_world_open_ipv6_bind_requires_cidr(self) -> None:
        with pytest.raises(ValidationError, match="allowed_cidr"):
            NetworkConfig(host="::", allowed_cidr=[])

    def test_world_open_with_cidr_passes(self) -> None:
        cfg = NetworkConfig(host="0.0.0.0", allowed_cidr=["10.0.0.0/8"])
        assert cfg.allowed_cidr == ["10.0.0.0/8"]

    def test_localhost_is_not_external(self) -> None:
        cfg = NetworkConfig(host="localhost")
        assert cfg.is_external_bind is False

    def test_external_ip_is_external(self) -> None:
        cfg = NetworkConfig(host="10.0.0.1")
        assert cfg.is_external_bind is True

    def test_unspecified_bind_detected(self) -> None:
        cfg = NetworkConfig(host="0.0.0.0", allowed_cidr=["10.0.0.0/8"])
        assert cfg.is_unspecified_bind is True


# ── ModelRoutingConfig schema validation ──────────────────────────────────────


class TestModelRoutingConfigSchema:
    """ModelRoutingConfig validation."""

    def test_defaults_all_empty(self) -> None:
        cfg = ModelRoutingConfig()
        assert cfg.default_profile is None
        assert cfg.weak_model_profile is None
        assert cfg.role_routing == {}
        assert cfg.quality_routing == {}
        assert cfg.latency_routing == {}
        assert cfg.pattern_routing == {}
        assert cfg.fallback_chain == []

    def test_load_from_yaml(self) -> None:
        path = CONFIG_DIR / "model_routing.yml"
        cfg = load_model_routing(path)
        assert cfg.default_profile is not None
        assert isinstance(cfg.role_routing, dict)
        assert len(cfg.role_routing) >= 2

    def test_full_config_roundtrip(self) -> None:
        data = {
            "default_profile": "foo",
            "weak_model_profile": "bar",
            "role_routing": {"coder": "foo", "reviewer": "bar"},
            "quality_routing": {"high": "foo"},
            "latency_routing": {"fast": "foo"},
            "pattern_routing": {"code_generation": "coder"},
            "fallback_chain": ["a", "b"],
        }
        cfg = ModelRoutingConfig(**data)
        assert cfg.default_profile == "foo"
        assert cfg.fallback_chain == ["a", "b"]


# ── UserConfig validation ─────────────────────────────────────────────────────


class TestUserConfigSchema:
    """UserConfig top-level schema and sub-models."""

    def test_userconfig_defaults(self) -> None:
        cfg = UserConfig()
        assert isinstance(cfg.network, NetworkConfig)
        assert isinstance(cfg.pipeline, PipelineConfigBlock)
        assert cfg.deletion_gate_threshold == 5

    def test_extra_fields_ignored(self) -> None:
        cfg = UserConfig.model_validate({"deletion_gate_threshold": 7, "bogus": True})
        assert cfg.deletion_gate_threshold == 7

    def test_pipeline_config_default_off(self) -> None:
        cfg = PipelineConfigBlock()
        assert cfg.enabled is False
        assert cfg.floor == 1
        assert cfg.target == 3

    def test_compaction_config_default_off(self) -> None:
        cfg = CompactionConfigBlock()
        assert cfg.enabled is False
        assert cfg.level == 1

    def test_vm_sandbox_config_profile_literal(self) -> None:
        cfg = VmSandboxConfig(profile="locked")
        assert cfg.profile == "locked"

    def test_vm_sandbox_config_profile_invalid(self) -> None:
        with pytest.raises(ValidationError):
            VmSandboxConfig(profile="invalid")

    def test_human_in_the_loop_defaults(self) -> None:
        cfg = HumanInTheLoopConfig()
        assert cfg.enabled is False
        assert cfg.confidence_threshold == 0.7

    def test_orchestration_guard_defaults(self) -> None:
        cfg = OrchestrationGuardConfig()
        assert cfg.max_nesting_depth == 3
        assert cfg.max_redispatch_count == 5
        assert cfg.enforce_capability_escalation is True

    def test_remediation_settings_defaults(self) -> None:
        cfg = RemediationSettings()
        assert cfg.check_interval_ticks == 30
        assert cfg.human_input_block_hours == 24
        assert cfg.max_requeues_before_chronic == 3

    def test_issues_config_defaults(self) -> None:
        cfg = IssuesConfig()
        assert cfg.polling_enabled is False
        assert cfg.github_label == "gludd"

    def test_notifications_config_defaults(self) -> None:
        cfg = NotificationsConfig()
        assert cfg.enabled is False
        assert cfg.backends == {"stdout": {}}

    def test_observability_config_defaults(self) -> None:
        cfg = ObservabilityConfig()
        assert cfg.otel_endpoint is None
        assert cfg.service_name == "general-ludd"


# ── TerraformConfig validation ────────────────────────────────────────────────


class TestTerraformConfigSchema:
    def test_terraform_config_defaults(self) -> None:
        cfg = TerraformConfig()
        assert cfg.provider == "aws"
        assert cfg.gpu_type == "t4"
        assert cfg.engine == "vllm"
        assert cfg.gpu_count == 1
        assert cfg.disk_size_gb == 100
        assert cfg.enable_structured_outputs is True

    def test_terraform_config_edge_values(self) -> None:
        cfg = TerraformConfig(
            provider="gcp",
            gpu_type="a100_80",
            engine="llamacpp",
            gpu_count=8,
            disk_size_gb=2000,
        )
        assert cfg.provider == "gcp"
        assert cfg.gpu_count == 8
        assert cfg.disk_size_gb == 2000


# ── Permission specs parse correctly ──────────────────────────────────────────


class TestPermissionSpecsParse:
    """All config/permissions/*.yml files parse and have required fields."""

    @pytest.mark.parametrize(
        "perm_file",
        sorted(p.name for p in (CONFIG_DIR / "permissions").glob("*.yml")),
    )
    def test_permission_spec_parses(self, perm_file: str) -> None:
        path = CONFIG_DIR / "permissions" / perm_file
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert isinstance(data, dict)
        assert "version" in data, f"{perm_file}: missing 'version'"
        assert "agent_type" in data, f"{perm_file}: missing 'agent_type'"
        assert "capabilities" in data, f"{perm_file}: missing 'capabilities'"
        assert isinstance(data["capabilities"], list)
        if data["capabilities"]:
            cap = data["capabilities"][0]
            assert "resource" in cap, f"{perm_file}: capability missing 'resource'"
            assert "actions" in cap, f"{perm_file}: capability missing 'actions'"

    def test_primary_permission_spec_shape(self) -> None:
        with open(CONFIG_DIR / "permissions" / "primary.yml") as f:
            data = yaml.safe_load(f) or {}
        assert data["agent_type"] == "primary"
        assert data["max_subagent_permissions"] == "same_or_fewer"
        assert any(c["resource"] == "file:repo" for c in data["capabilities"])

    def test_subagent_permission_has_denied(self) -> None:
        with open(CONFIG_DIR / "permissions" / "subagent.yml") as f:
            data = yaml.safe_load(f) or {}
        assert len(data["denied"]) >= 1
        assert any(d["resource"] == "secret:openbao" for d in data["denied"])


# ── Deployment optimization config ────────────────────────────────────────────


class TestDeploymentOptimizationConfig:
    def test_parses_from_yaml(self) -> None:
        path = CONFIG_DIR / "infra" / "deployment_optimization.yml"
        cfg = DeploymentOptimizationConfig.from_yaml(path)
        assert isinstance(cfg.vllm_defaults, dict)
        assert "gpu_memory_utilization" in cfg.vllm_defaults
        assert isinstance(cfg.hardware_presets, dict)
        assert "h100" in cfg.hardware_presets
        assert isinstance(cfg.quantization_recommendations, list)

    def test_get_preset_known_gpu(self) -> None:
        path = CONFIG_DIR / "infra" / "deployment_optimization.yml"
        cfg = DeploymentOptimizationConfig.from_yaml(path)
        preset = cfg.get_preset("vllm", "h100")
        assert isinstance(preset, dict)
        assert "tensor_parallel_size" in preset

    def test_get_preset_unknown_engine_raises(self) -> None:
        path = CONFIG_DIR / "infra" / "deployment_optimization.yml"
        cfg = DeploymentOptimizationConfig.from_yaml(path)
        with pytest.raises(ValueError, match="unknown engine"):
            cfg.get_preset("bogus", "h100")

    def test_get_preset_unknown_gpu_raises(self) -> None:
        path = CONFIG_DIR / "infra" / "deployment_optimization.yml"
        cfg = DeploymentOptimizationConfig.from_yaml(path)
        with pytest.raises(ValueError, match="unknown gpu_type"):
            cfg.get_preset("vllm", "nonexistent_gpu")


# ── Make target contract JSON ─────────────────────────────────────────────────


class TestMakeTargetContract:
    def test_contract_json_valid(self) -> None:
        path = CONFIG_DIR / "make_target_contract.json"
        with open(path) as f:
            data = json.load(f)
        assert data["version"] == 1
        assert isinstance(data["targets"], list)
        assert len(data["targets"]) >= 5

    def test_every_target_has_name_and_behavior(self) -> None:
        path = CONFIG_DIR / "make_target_contract.json"
        with open(path) as f:
            data = json.load(f)
        for t in data["targets"]:
            assert "name" in t, f"target missing 'name': {t}"
            assert "behavior" in t, f"target {t.get('name')} missing 'behavior'"


# ── Binary paths config ───────────────────────────────────────────────────────


class TestBinaryPathsConfig:
    def test_binary_paths_yml_parse(self) -> None:
        path = CONFIG_DIR / "binary_paths.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert "binary_paths" in data
        assert isinstance(data["binary_paths"], dict)
        assert len(data["binary_paths"]) >= 10

    def test_binary_paths_have_required_keys(self) -> None:
        path = CONFIG_DIR / "binary_paths.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        required = {"terraform", "git", "uv", "podman", "docker", "ansible_playbook"}
        keys = set(data["binary_paths"].keys())
        for key in required:
            assert key in keys, f"binary_paths config missing required key: {key}"


# ── Pricing configs ───────────────────────────────────────────────────────────


class TestPricingConfigs:
    def test_providers_yml_parse(self) -> None:
        path = CONFIG_DIR / "pricing" / "providers.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert "pricing" in data
        providers = data["pricing"]
        assert isinstance(providers, dict)
        assert len(providers) >= 5

    def test_providers_have_openai(self) -> None:
        path = CONFIG_DIR / "pricing" / "providers.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert "openai" in data["pricing"]
        openai = data["pricing"]["openai"]
        assert isinstance(openai["rates"], list)
        assert len(openai["rates"]) >= 3

    def test_compute_yml_parse(self) -> None:
        path = CONFIG_DIR / "pricing" / "compute.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert "instances" in data
        assert "aws" in data["instances"]
        assert len(data["instances"]) >= 7


# ── Agent definitions config ──────────────────────────────────────────────────


class TestAgentDefinitionsConfig:
    def test_default_agents_yml_parse(self) -> None:
        path = CONFIG_DIR / "agents" / "default_agents.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert "agents" in data
        assert isinstance(data["agents"], list)
        assert len(data["agents"]) >= 4

    def test_agent_entries_have_required_fields(self) -> None:
        path = CONFIG_DIR / "agents" / "default_agents.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        required = {"name", "description", "type", "model_profile", "max_steps", "permissions"}
        for agent in data["agents"]:
            for field in required:
                assert field in agent, f"agent {agent.get('name', '?')} missing {field}"

    def test_agent_permissions_have_correct_shape(self) -> None:
        path = CONFIG_DIR / "agents" / "default_agents.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for agent in data["agents"]:
            perms = agent["permissions"]
            assert isinstance(perms["can_edit"], bool)
            assert isinstance(perms["can_bash"], bool)
            assert isinstance(perms["can_read"], bool)
            assert isinstance(perms["can_dispatch_subagents"], bool)
            assert isinstance(perms["allowed_subagents"], list)


# ── Prompt profiles config ────────────────────────────────────────────────────


class TestPromptProfilesConfig:
    def test_default_profile_yml_parse(self) -> None:
        path = CONFIG_DIR / "prompt_profiles" / "default.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert data["profile_id"] == "default"
        assert "system_prompt_template" in data
        assert "behavior" in data
        assert "token_budget" in data

    def test_profile_behavior_flags_are_booleans(self) -> None:
        path = CONFIG_DIR / "prompt_profiles" / "default.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        behavior = data["behavior"]
        for key in ("session_persistence", "verbose_output", "auto_commit", "require_evidence"):
            assert isinstance(behavior[key], bool), f"behavior.{key} should be bool"


# ── Memory bank templates config ──────────────────────────────────────────────


class TestMemoryBankTemplates:
    def test_memory_bank_templates_parse(self) -> None:
        path = CONFIG_DIR / "memory_bank_templates.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert "templates" in data
        assert "coding-assistant" in data["templates"]
        assert "code-reviewer" in data["templates"]

    def test_template_has_disposition(self) -> None:
        path = CONFIG_DIR / "memory_bank_templates.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        template = data["templates"]["coding-assistant"]
        disp = template["disposition"]
        assert 1 <= disp["skepticism"] <= 5
        assert 1 <= disp["literalism"] <= 5
        assert 1 <= disp["empathy"] <= 5


# ── General-ludd.yml main config ──────────────────────────────────────────────


class TestGeneralLuddConfig:
    def test_main_config_parses(self) -> None:
        path = CONFIG_DIR / "general-ludd.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for section in ("network", "database", "agents", "orchestration", "process_isolation", "budget"):
            assert section in data, f"missing section: {section}"

    def test_network_section_values(self) -> None:
        path = CONFIG_DIR / "general-ludd.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        net = data["network"]
        assert isinstance(net["host"], str)
        assert isinstance(net["port"], int)
        assert 1 <= net["port"] <= 65535

    def test_budget_section_values(self) -> None:
        path = CONFIG_DIR / "general-ludd.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        budget = data["budget"]
        assert isinstance(budget["max_usd"], (int, float))
        assert budget["max_usd"] > 0
        assert 0 < budget["warn_percent"] <= 100

    def test_database_section_values(self) -> None:
        path = CONFIG_DIR / "general-ludd.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        db = data["database"]
        assert isinstance(db["name"], str)
        assert isinstance(db["user"], str)
        assert 1 <= db["port"] <= 65535

    def test_process_isolation_default_off(self) -> None:
        path = CONFIG_DIR / "general-ludd.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        iso = data["process_isolation"]
        assert iso["enabled"] is False
        assert iso["executable"] in ("podman", "docker")
        assert iso["container_image"] is None
        assert iso["test_only_in_process"] is False
        assert ProcessIsolationConfig(**iso).enabled is False


# ── AI SDLC config ────────────────────────────────────────────────────────────


class TestAiSdlcConfig:
    def test_ai_sdlc_yml_parse(self) -> None:
        path = CONFIG_DIR / "ai_sdlc.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert data["version"] == "1.0"
        assert "pipeline_stages" in data
        assert len(data["pipeline_stages"]) == 8

    def test_pipeline_stage_has_required_fields(self) -> None:
        path = CONFIG_DIR / "ai_sdlc.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        required = {"stage", "number", "description", "gludd_event_loop_phase", "roles"}
        for stage in data["pipeline_stages"]:
            for field in required:
                assert field in stage, f"stage {stage.get('stage', '?')} missing {field}"

    def test_blocking_stages_are_boolean(self) -> None:
        path = CONFIG_DIR / "ai_sdlc.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        blocking = data["blocking_stages"]
        assert isinstance(blocking["review"], bool)
        assert blocking["review"] is True
        assert isinstance(blocking["gate"], bool)

    def test_stage_timeouts_are_positive(self) -> None:
        path = CONFIG_DIR / "ai_sdlc.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        timeouts = data["stage_timeouts"]
        for name, val in timeouts.items():
            assert val > 0, f"stage_timeout {name} must be > 0"


# ── TDD allowlist config ──────────────────────────────────────────────────────


class TestTddAllowlistConfig:
    def test_tdd_allowlist_parse(self) -> None:
        path = CONFIG_DIR / "tdd_allowlist.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert "allowlist" in data
        assert isinstance(data["allowlist"], list)
        for entry in data["allowlist"]:
            assert "path" in entry, "allowlist entry missing 'path'"
            assert "reason" in entry, "allowlist entry missing 'reason'"


# ── Ratchet config ────────────────────────────────────────────────────────────


class TestRatchetConfig:
    def test_ratchet_yml_parse(self) -> None:
        path = CONFIG_DIR / "ratchet.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert isinstance(data, dict)


# ── Model routing standalone config ───────────────────────────────────────────


class TestModelRoutingStandalone:
    def test_model_routing_yml_loads(self) -> None:
        cfg = load_model_routing(CONFIG_DIR / "model_routing.yml")
        assert cfg.default_profile is not None
        assert isinstance(cfg.fallback_chain, list)
        assert len(cfg.fallback_chain) >= 1


# ── Infrastructure provider configs ───────────────────────────────────────────


class TestInfraProviderConfigs:
    def test_providers_yml_parse(self) -> None:
        path = CONFIG_DIR / "infra" / "providers.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert isinstance(data, dict)
        assert "providers" in data
        assert len(data["providers"]) >= 3

    def test_aws_iam_roles_parse(self) -> None:
        path = CONFIG_DIR / "infra" / "aws-iam-roles.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert isinstance(data, dict)

    def test_azure_iam_roles_parse(self) -> None:
        path = CONFIG_DIR / "infra" / "azure-iam-roles.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert isinstance(data, dict)


# ── Ansbile isolation config ──────────────────────────────────────────────────


class TestAnsibleConfig:
    def test_isolation_yml_parse(self) -> None:
        path = CONFIG_DIR / "ansible" / "isolation.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert isinstance(data, dict)


# ── OpenBao config ────────────────────────────────────────────────────────────


class TestOpenBaoConfig:
    def test_openbao_default_yml_parse(self) -> None:
        path = CONFIG_DIR / "openbao" / "default.yml"
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        assert isinstance(data, dict)


# ── RelationshipRoutingConfig bounds ──────────────────────────────────────────


class TestRelationshipRoutingBounds:
    def test_edge_decay_can_be_zero(self) -> None:
        cfg = RelationshipRoutingConfig(edge_decay=0.0)
        assert cfg.edge_decay == 0.0

    def test_min_borrow_weight_can_be_zero(self) -> None:
        cfg = RelationshipRoutingConfig(min_borrow_weight=0.0)
        assert cfg.min_borrow_weight == 0.0

    def test_external_penalty_accepts_any_float(self) -> None:
        cfg = RelationshipRoutingConfig(external_penalty=0.99)
        assert cfg.external_penalty == 0.99


# ── UserConfig from_yaml with example files ───────────────────────────────────


class TestUserConfigFromExampleFiles:
    def test_minimal_setup_loads(self) -> None:
        path = CONFIG_DIR / "examples" / "minimal_setup.yml"
        cfg = UserConfig.from_yaml(path)
        assert isinstance(cfg, UserConfig)
        assert cfg.model_routing is not None

    def test_high_security_setup_loads(self) -> None:
        path = CONFIG_DIR / "examples" / "high_security_setup.yml"
        cfg = UserConfig.from_yaml(path)
        assert cfg.network.host == "127.0.0.1"
        isolation = ProcessIsolationConfig(**cfg.process_isolation)
        assert isolation.enabled is True
        assert isolation.executable == "podman"
        assert isolation.container_image is not None
        assert "@sha256:" in isolation.container_image
        assert isolation.test_only_in_process is False

    def test_user_config_example_loads(self) -> None:
        path = CONFIG_DIR / "examples" / "user_config_example.yml"
        cfg = UserConfig.from_yaml(path)
        assert isinstance(cfg, UserConfig)


# ── ConfigCoverageGapsBaseline JSON ───────────────────────────────────────────


class TestCoverageGapsBaseline:
    def test_coverage_gaps_baseline_json_valid(self) -> None:
        path = CONFIG_DIR / "coverage_gaps_baseline.json"
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, (dict, list))
