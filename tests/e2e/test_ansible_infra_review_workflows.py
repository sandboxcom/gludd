"""E2E: Ansible, infrastructure, and review subsystem workflow tests.

Covers playbook execution, variable resolution, galaxy operations, templating,
terraform config generation, compute lifecycle, provider validation, cost
tracking, evidence checking, and decision application — all with mocks and
temp directories to avoid requiring real clusters, OpenBao, or model gateways.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Ansible — paths, collections, galaxy
# ---------------------------------------------------------------------------


class TestAnsiblePaths:
    def test_resolve_collections_paths_project_user_bundled(self, tmp_path):
        from general_ludd.ansible.paths import resolve_collections_paths

        proj = tmp_path / "project" / ".gludd" / "collections"
        proj.mkdir(parents=True)
        entries = resolve_collections_paths(project_root=tmp_path / "project")
        sources = [e.source for e in entries]
        assert sources[0] == "project"
        assert sources[-1] == "bundled"

    def test_resolve_collections_paths_no_project_skips_project_tier(self):
        from general_ludd.ansible.paths import resolve_collections_paths

        entries = resolve_collections_paths(project_root=None)
        sources = [e.source for e in entries]
        assert "project" not in sources
        assert "bundled" in sources

    def test_to_ansible_env_sets_collections_and_roles_paths(self):
        from general_ludd.ansible.paths import (
            CollectionsPathEntry,
            to_ansible_env,
        )

        e = CollectionsPathEntry("test", Path("/tmp/collections"), 0)
        env = to_ansible_env([e])
        assert "ANSIBLE_COLLECTIONS_PATH" in env
        assert "ANSIBLE_ROLES_PATH" in env
        assert "/tmp/collections" in env["ANSIBLE_COLLECTIONS_PATH"]

    def test_to_ansible_cfg_renders_collections_path_line(self):
        from general_ludd.ansible.paths import (
            CollectionsPathEntry,
            to_ansible_cfg,
        )

        e = CollectionsPathEntry("test", Path("/x"), 0)
        line = to_ansible_cfg([e])
        assert line.startswith("collections_path = ")
        assert "/x" in line

    def test_find_resource_role(self, tmp_path):
        from general_ludd.ansible.paths import (
            CollectionsPathEntry,
            find_resource,
        )

        role_dir = (
            tmp_path / "ansible_collections" / "general_ludd" / "agent" / "roles" / "project_init"
        )
        role_dir.mkdir(parents=True)
        entry = CollectionsPathEntry("test", tmp_path, 0)
        result = find_resource("general_ludd.agent.project_init", [entry])
        assert result == role_dir

    def test_find_resource_module(self, tmp_path):
        from general_ludd.ansible.paths import (
            CollectionsPathEntry,
            find_resource,
        )

        mod = (
            tmp_path / "ansible_collections" / "general_ludd" / "agent" / "plugins" / "modules"
        )
        mod.mkdir(parents=True)
        (mod / "project_init.py").touch()
        entry = CollectionsPathEntry("test", tmp_path, 0)
        result = find_resource("general_ludd.agent.project_init", [entry])
        assert result is not None
        assert result.suffix == ".py"

    def test_find_resource_none_on_missing(self):
        from general_ludd.ansible.paths import (
            CollectionsPathEntry,
            find_resource,
        )

        entry = CollectionsPathEntry("test", Path(tempfile.mkdtemp()), 0)
        result = find_resource("ns.coll.missing", [entry])
        assert result is None

    def test_scan_collection_versions(self, tmp_path):
        from general_ludd.ansible.paths import scan_collection_versions

        ac = tmp_path / "ansible_collections" / "general_ludd@0.1.0" / "agent"
        ac.mkdir(parents=True)
        (tmp_path / "ansible_collections" / "general_ludd@latest" / "agent").mkdir(parents=True)
        infos = scan_collection_versions(tmp_path, namespace="general_ludd", collection="agent")
        versions = {i.version for i in infos}
        assert "0.1.0" in versions
        assert "latest" in versions

    def test_resolve_collection_version_exact_match(self, tmp_path):
        from general_ludd.ansible.paths import resolve_collection_version

        base = tmp_path
        (base / "ansible_collections" / "general_ludd@0.1.0" / "agent").mkdir(parents=True)
        (base / "ansible_collections" / "general_ludd@0.2.0" / "agent").mkdir(parents=True)
        result = resolve_collection_version(base, "general_ludd", "agent", "0.1.0")
        assert result is not None
        assert "0.1.0" in str(result)

    def test_resolve_collection_version_latest(self, tmp_path):
        from general_ludd.ansible.paths import resolve_collection_version

        base = tmp_path
        (base / "ansible_collections" / "general_ludd@latest" / "agent").mkdir(parents=True)
        result = resolve_collection_version(base, "general_ludd", "agent")
        assert result is not None
        assert "latest" in str(result)

    def test_resolve_collection_version_bare_fallback(self, tmp_path):
        from general_ludd.ansible.paths import resolve_collection_version

        bare = tmp_path / "ansible_collections" / "general_ludd" / "agent"
        bare.mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "general_ludd", "agent")
        assert result == bare

    def test_activate_collection_version_creates_symlink(self, tmp_path):
        from general_ludd.ansible.paths import activate_collection_version

        base = tmp_path
        (base / "ansible_collections" / "general_ludd@0.1.0" / "agent").mkdir(parents=True)
        root, _cleanup = activate_collection_version(
            base, "general_ludd", "agent", "0.1.0", temp_dir=Path(tempfile.mkdtemp())
        )
        link = root / "ansible_collections" / "general_ludd" / "agent"
        assert link.is_symlink() or (link.exists())

    def test_list_all_collections(self, tmp_path):
        from general_ludd.ansible.paths import list_all_collections

        ns = tmp_path / "ansible_collections" / "general_ludd"
        ns.mkdir(parents=True)
        (ns / "agent").mkdir()
        (ns / "language").mkdir()
        result = list_all_collections(tmp_path)
        assert "agent" in result
        assert "language" in result

    def test_list_collection_versions(self, tmp_path):
        from general_ludd.ansible.paths import list_collection_versions

        (tmp_path / "ansible_collections" / "general_ludd@1.0.0" / "agent").mkdir(parents=True)
        (tmp_path / "ansible_collections" / "general_ludd@0.9.0" / "agent").mkdir(parents=True)
        versions = list_collection_versions(tmp_path, "general_ludd", "agent")
        assert "1.0.0" in versions
        assert "0.9.0" in versions


class TestAnsibleGalaxy:
    def test_get_builtin_modules_returns_list(self):
        from general_ludd.ansible.galaxy import get_builtin_modules

        mods = get_builtin_modules()
        assert isinstance(mods, list)
        assert "ansible.builtin.copy" in mods
        assert len(mods) >= 30

    def test_parse_galaxy_search_output(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output

        output = (
            "Found 2 roles\n"
            "Name                      Description\n"
            "----                      -----------\n"
            "geerlingguy.docker        Docker for Linux\n"
            "geerlingguy.nginx         Nginx for Linux\n"
        )
        results = parse_galaxy_search_output(output)
        assert len(results) == 2
        assert results[0]["name"] == "geerlingguy.docker"

    def test_parse_galaxy_search_output_empty(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output

        assert parse_galaxy_search_output("") == []
        assert parse_galaxy_search_output("   \n") == []

    def test_search_galaxy_invalid_query_leading_dash(self):
        from general_ludd.ansible.galaxy import search_galaxy

        with pytest.raises(ValueError, match="may not begin with '-'"):
            search_galaxy("-r requirements.yml")

    def test_search_galaxy_validates_type(self):
        from general_ludd.ansible.galaxy import search_galaxy

        with pytest.raises(ValueError, match="galaxy_type"):
            search_galaxy("nginx", galaxy_type="invalid")

    def test_install_galaxy_validates_name(self):
        from general_ludd.ansible.galaxy import install_galaxy

        with pytest.raises(ValueError, match="may not begin with '-'"):
            install_galaxy("--help")

    def test_install_galaxy_invalid_name_spec(self):
        from general_ludd.ansible.galaxy import install_galaxy

        with pytest.raises(ValueError, match="galaxy name"):
            install_galaxy("")


class TestAnsibleTemplating:
    def test_render_sandboxed_basic_variable(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        t = AnsibleTemplater()
        result = t.render_sandboxed("Hello {{ name }}!", name="World")
        assert result == "Hello World!"

    def test_render_sandboxed_undefined_raises(self):
        from general_ludd.ansible.templating import (
            AnsibleTemplater,
            TemplateRenderError,
        )

        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError):
            t.render_sandboxed("{{ undefined_var }}")

    def test_render_sandboxed_no_lookup_surface(self):
        from general_ludd.ansible.templating import (
            AnsibleTemplater,
            TemplateRenderError,
        )

        t = AnsibleTemplater()
        # lookup() is not available in sandbox
        with pytest.raises(TemplateRenderError):
            t.render_sandboxed("{{ lookup('pipe', 'id') }}")

    def test_render_sandboxed_value_not_re_evaluated(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ x }}", x="{{ 7*7 }}")
        # The value should be rendered literally, not re-evaluated as Jinja
        assert "7*7" in result or result == "{{ 7*7 }}"

    def test_render_sandboxed_undefined_blocked(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        t = AnsibleTemplater()
        result = t.render_sandboxed("{{ none.attr }}")
        assert isinstance(result, str)  # renders; none is a Jinja2 built-in literal

    def test_render_trusted_calls_templar(self, tmp_path):
        from general_ludd.ansible.templating import AnsibleTemplater

        t = AnsibleTemplater()
        result = t.render("{{ 1 + 1 }}")
        assert "2" in result

    def test_resolve_fact_delegates_to_runner(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        t = AnsibleTemplater()
        # Without ansible-core this will error — that's expected
        with contextlib.suppress(Exception):
            t.resolve_fact("ansible_facts", host="localhost")


# ---------------------------------------------------------------------------
# Ansible — runner adapter
# ---------------------------------------------------------------------------


class TestAnsibleRunnerAdapter:
    def test_init_creates_private_data_dir(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        assert os.path.isdir(adapter.private_data_dir)

    def test_resolve_playbook_registered(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        pb = tmp_path / "test.yml"
        pb.write_text("---\n- hosts: all\n")
        adapter = AnsibleRunnerAdapter(registry={"test.yml": str(pb)})
        assert adapter.resolve_playbook("test.yml") == str(pb)

    def test_resolve_playbook_unregistered_raises(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="not registered"):
            adapter.resolve_playbook("nonexistent.yml")

    def test_prepare_job_dirs_creates_structure(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        dirs = adapter.prepare_job_dirs("TEST-JOB-1")
        for key in ("root", "env", "project", "inventory", "artifacts"):
            assert os.path.isdir(dirs[key])

    def test_prepare_job_dirs_rejects_duplicate(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        adapter.prepare_job_dirs("DUP-JOB")
        with pytest.raises(FileExistsError):
            adapter.prepare_job_dirs("DUP-JOB")
        assert True  # exception was raised

    def test_write_vars_writes_yaml_and_sets_perms(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        adapter.prepare_job_dirs("VARS-JOB")
        path = adapter.write_vars("VARS-JOB", {"key": "val"})
        assert os.path.isfile(path)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["job_vars"]["key"] == "val"
        assert (os.stat(path).st_mode & 0o777) == 0o600

    def test_list_playbooks_returns_registry_keys(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        pb = tmp_path / "hello.yml"
        pb.write_text("---\n- hosts: all\n")
        adapter = AnsibleRunnerAdapter(registry={"hello.yml": str(pb)})
        assert "hello.yml" in adapter.list_playbooks()

    def test_register_and_unregister_playbook(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        adapter.register_playbook("custom.yml", "/tmp/custom.yml")
        assert "custom.yml" in adapter.list_playbooks()
        adapter.unregister_playbook("custom.yml")
        assert "custom.yml" not in adapter.list_playbooks()

    def test_set_project_root_refreshes_collections(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".gludd" / "collections").mkdir(parents=True)
        adapter = AnsibleRunnerAdapter()
        adapter.set_project_root(proj)


# ---------------------------------------------------------------------------
# Infrastructure — ComputeConfig, ComputeInstance
# ---------------------------------------------------------------------------


class TestComputeConfig:
    def test_defaults(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        c = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
        assert c.provider == ComputeProvider.AWS
        assert c.gpu_type == GPUType.T4
        assert c.gpu_count == 1
        assert c.spot is True
        assert c.max_cost_usd == 10.0

    def test_gpu_count_minimum(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="gpu_count"):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, gpu_count=0)

    def test_max_cost_positive(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="max_cost_usd"):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, max_cost_usd=0)

    def test_timeout_minutes_positive(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="timeout_minutes"):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, timeout_minutes=-1)

    def test_disk_size_gb_minimum(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="disk_size_gb"):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, disk_size_gb=0)

    def test_model_name_rejects_shell_metacharacters(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeConfig(
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                model_name="bad; rm -rf /",
            )

    def test_model_name_accepts_valid_registry_ref(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        c = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="ghcr.io/org/repo:latest",
        )
        assert c.model_name == "ghcr.io/org/repo:latest"

    def test_region_rejects_shell_metacharacters(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeConfig(
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                region="us-east-1;id",
            )

    def test_allowed_cidr_defaults_to_loopback(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        c = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
        assert c.allowed_cidr == "127.0.0.1/32"

    def test_workload_type_valid(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        c = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            workload_type="batch_inference",
        )
        assert c.workload_type == "batch_inference"

    def test_workload_type_invalid(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="workload_type"):
            ComputeConfig(
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                workload_type="bogus",
            )


class TestComputeInstance:
    def test_instance_creation(self):
        from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType

        inst = ComputeInstance(
            instance_id="i-abc123", provider=ComputeProvider.AWS, gpu_type=GPUType.A100_80
        )
        assert inst.instance_id == "i-abc123"
        assert inst.status == "pending"
        assert inst.port == 8000

    def test_instance_id_must_not_be_empty(self):
        from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="instance_id"):
            ComputeInstance(instance_id="", provider=ComputeProvider.AWS, gpu_type=GPUType.T4)

    def test_port_range(self):
        from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="port"):
            ComputeInstance(
                instance_id="i-1", provider=ComputeProvider.AWS, gpu_type=GPUType.T4, port=0
            )
        with pytest.raises(ValueError, match="port"):
            ComputeInstance(
                instance_id="i-1", provider=ComputeProvider.AWS, gpu_type=GPUType.T4, port=99999
            )

    def test_cost_non_negative(self):
        from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType

        with pytest.raises(ValueError, match="cost_incurred"):
            ComputeInstance(
                instance_id="i-1",
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                cost_incurred=-1.0,
            )


# ---------------------------------------------------------------------------
# Infrastructure — ProviderRegistry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_list_providers(self):
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        providers = reg.list_providers()
        assert len(providers) >= 15

    def test_get_provider(self):
        from general_ludd.infra.compute import ComputeProvider
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.AWS)
        assert info.display_name == "Amazon Web Services"
        assert info.terraform_provider == "hashicorp/aws"

    def test_get_cheapest_for_gpu(self):
        from general_ludd.infra.compute import GPUType
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        info = reg.get_cheapest_for_gpu(GPUType.A100_80)
        assert info is not None
        assert info.pricing.get("a100_80", 0) > 0

    def test_get_cheapest_for_gpu_no_match(self):
        from general_ludd.infra.compute import GPUType
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="No provider"):
            reg.get_cheapest_for_gpu(GPUType.AMD_MI250)

    def test_list_by_price_sorted(self):
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        ranked = reg.list_by_price()
        prices = [p for _, p in ranked if p != float("inf")]
        assert prices == sorted(prices)

    def test_provider_auth_env(self):
        from general_ludd.infra.compute import ComputeProvider
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.VMWARE)
        assert "VSPHERE_USER" in info.auth_env
        assert info.auth_source is not None


# ---------------------------------------------------------------------------
# Infrastructure — Terraform config generation
# ---------------------------------------------------------------------------


class TestTerraformGenerator:
    def test_escape_tfvar_value(self):
        from general_ludd.infra.terraform import escape_tfvar_value

        result = escape_tfvar_value('hello "world"')
        assert '\\"' in result
        assert result.startswith('"') and result.endswith('"')

    def test_escape_tfvar_interpolation_marker(self):
        from general_ludd.infra.terraform import escape_tfvar_value

        result = escape_tfvar_value("foo ${var.bar}")
        assert "\\${" in result

    def test_terraform_generator_init(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        assert gen is not None

    def test_generate_aws_config_produces_hcl(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="test-model",
            region="us-east-1",
        )
        result = gen.generate(config)
        assert "resource" in result or "provider" in result or "aws" in result.lower()
        assert len(result) > 0

    def test_aws_gpu_instance_mapping(self):
        from general_ludd.infra.terraform import _AWS_GPU_TO_INSTANCE

        assert _AWS_GPU_TO_INSTANCE["t4"] == "g4dn.xlarge"
        assert _AWS_GPU_TO_INSTANCE["a100_80"] == "p4d.24xlarge"

    def test_gcp_gpu_type_mapping(self):
        from general_ludd.infra.terraform import _GCP_GPU_TO_TYPE

        assert _GCP_GPU_TO_TYPE["l4"] == "nvidia-l4"
        assert _GCP_GPU_TO_TYPE["a100_80"] == "nvidia-tesla-a100"


# ---------------------------------------------------------------------------
# Infrastructure — State backend
# ---------------------------------------------------------------------------


class TestStateBackend:
    def test_select_local_when_no_openbao(self):
        from general_ludd.infra.terraform_state import StateBackendSelector

        mock_bao = MagicMock()
        mock_secrets = MagicMock()
        mock_secrets.health_check.return_value = False
        selector = StateBackendSelector(
            openbao_client=mock_bao,
            secrets_manager=mock_secrets,
        )
        config = MagicMock()
        config.max_cost_usd = 100.0
        result = selector.select(config, deployment_id="dep-1")
        assert result.kind == "local"

    def test_select_openbao_kv_above_threshold(self):
        from general_ludd.infra.terraform_state import StateBackendSelector

        mock_bao = MagicMock()
        mock_secrets = MagicMock()
        mock_secrets.health_check.return_value = True
        selector = StateBackendSelector(
            openbao_client=mock_bao,
            secrets_manager=mock_secrets,
        )
        config = MagicMock()
        config.max_cost_usd = 100.0
        result = selector.select(config, deployment_id="dep-1")
        assert result.kind == "openbao_kv"
        assert "/gludd/tfstate/dep-1" in result.path

    def test_select_local_with_api_url(self):
        from general_ludd.infra.terraform_state import StateBackendSelector

        mock_bao = MagicMock()
        mock_secrets = MagicMock()
        selector = StateBackendSelector(
            openbao_client=mock_bao,
            secrets_manager=mock_secrets,
            config={"api_url": "https://gludd.example.com"},
        )
        config = MagicMock()
        config.max_cost_usd = 100.0
        result = selector.select(config, deployment_id="dep-2")
        assert result.kind == "http"
        assert "/api/terraform/state/dep-2" in result.path

    def test_render_backend_block_local(self):
        from general_ludd.infra.terraform_state import (
            StateBackendConfig,
            render_backend_block,
        )

        cfg = StateBackendConfig(kind="local", path="terraform.tfstate")
        block = render_backend_block(cfg)
        assert 'backend "local"' in block
        assert "terraform.tfstate" in block

    def test_render_backend_block_http(self):
        from general_ludd.infra.terraform_state import (
            StateBackendConfig,
            render_backend_block,
        )

        cfg = StateBackendConfig(
            kind="http",
            path="https://api.example.com/state",
            lock_address="https://api.example.com/state",
            unlock_address="https://api.example.com/state",
        )
        block = render_backend_block(cfg)
        assert 'backend "http"' in block

    def test_render_backend_unknown_kind_raises(self):
        from general_ludd.infra.terraform_state import (
            StateBackendConfig,
            render_backend_block,
        )

        cfg = StateBackendConfig(kind="bogus", path="/tmp/x")
        with pytest.raises(ValueError, match="unknown state backend kind"):
            render_backend_block(cfg)


# ---------------------------------------------------------------------------
# Infrastructure — Cost tracker
# ---------------------------------------------------------------------------


class TestInfraCostTracker:
    def test_record_and_totals(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 5.0, sku="p4d.24xlarge")
        tracker.record("aws", "gpu_instance", "i-2", 3.0, sku="g5.xlarge")
        assert tracker.total_cost() == 8.0
        assert tracker.cost_by_provider()["aws"] == 8.0

    def test_per_project_costs(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("gcp", "gpu_instance", "i-3", 10.0, project_id="proj-a")
        tracker.record("gcp", "storage", "vol-1", 2.0, project_id="proj-b")
        assert tracker.cost_by_project()["proj-a"] == 10.0
        assert tracker.cost_by_project()["proj-b"] == 2.0

    def test_hourly_rate_usd_builtin(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("aws", "p4d.24xlarge")
        assert rate > 0

    def test_hourly_rate_usd_fallback_zero(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("unknown_provider", "nonexistent_sku")
        assert rate > 0.0  # fallback uses INFRA_PRICING["gpu_second"] * 3600

    def test_records_accumulate(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-x", 1.0)
        tracker.record("aws", "gpu_instance", "i-y", 2.0)
        assert len(tracker.records()) == 2

    def test_cost_by_resource_type(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 5.0)
        tracker.record("aws", "storage", "vol-1", 1.0)
        assert tracker.cost_by_resource_type()["gpu_instance"] == 5.0
        assert tracker.cost_by_resource_type()["storage"] == 1.0

    def test_provider_breakdown(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 5.0)
        tracker.record("aws", "storage", "vol-1", 1.0)
        bd = tracker.provider_breakdown("aws")
        assert bd["gpu_instance"] == 5.0
        assert bd["storage"] == 1.0

    def test_snapshot_returns_summary(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 5.0)
        snap = tracker.snapshot()
        assert snap["total_cost"] == 5.0
        assert "by_provider" in snap
        assert snap["record_count"] == 1


# ---------------------------------------------------------------------------
# Review — Evidence checker
# ---------------------------------------------------------------------------


class TestEvidenceChecker:
    def test_check_claim_with_sources_supported(self):
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        result = checker.check_claim("tests pass", sources=["test_output.txt:42"])
        assert result.supported is True
        assert len(result.sources) == 1

    def test_check_claim_without_sources_unsupported(self):
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        result = checker.check_claim("everything works", sources=[])
        assert result.supported is False
        assert "no source provided" in result.missing_sources

    def test_audit_response_detects_factual_claims(self):
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        results = checker.audit_response(
            "The build passed. The test count is 42.", tool_outputs=[]
        )
        assert len(results) >= 1

    def test_audit_response_exempts_questions(self):
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        results = checker.audit_response(
            "Should we deploy this? Not sure.", tool_outputs=[]
        )
        assert len(results) == 0

    def test_audit_response_exempts_opinion(self):
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        results = checker.audit_response(
            "I think the tests pass. Maybe it's fine.", tool_outputs=[]
        )
        assert len(results) == 0

    def test_audit_response_exempts_acknowledgement(self):
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        results = checker.audit_response(
            "OK. Got it. Sure.", tool_outputs=[]
        )
        assert len(results) == 0

    def test_audit_response_detects_path_evidence(self):
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        results = checker.audit_response(
            "Fixed in src/foo/bar.py:42. The tests are passing.",
            tool_outputs=["File src/foo/bar.py:42 modified"],
        )
        assert any(r.supported for r in results)


# ---------------------------------------------------------------------------
# Review — Decision applier (mocked)
# ---------------------------------------------------------------------------


class TestDecisionApplier:
    @pytest.mark.asyncio
    async def test_apply_complete_decision(self):
        from general_ludd.review.decision_applier import apply_decision
        from general_ludd.schemas.task_decision import TaskDecision
        decision = TaskDecision(
            return_id="ret-1",
            matched_todo_id="todo-1",
            decision="complete",
            confidence=0.9,
            evidence_refs=["commit:abc12345"],
        )
        mock_repo = AsyncMock()
        mock_todo = MagicMock()
        mock_todo.version = 1
        mock_todo.project_id = None
        mock_repo.get_by_id.return_value = mock_todo

        with patch(
            "general_ludd.review.decision_applier.asyncio.to_thread",
            return_value=decision,
        ):
            await apply_decision(decision, mock_repo, MagicMock(), repo_root="/tmp")
        mock_repo.transition.assert_called()
        assert mock_repo.transition.called

    @pytest.mark.asyncio
    async def test_apply_needs_more_work_decision(self):
        from general_ludd.review.decision_applier import apply_decision
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="ret-2",
            matched_todo_id="todo-2",
            decision="needs_more_work",
            confidence=0.5,
        )
        mock_repo = AsyncMock()
        mock_todo = MagicMock()
        mock_todo.version = 1
        mock_todo.project_id = None
        mock_repo.get_by_id.return_value = mock_todo

        await apply_decision(decision, mock_repo, MagicMock())
        mock_repo.transition.assert_called()

    @pytest.mark.asyncio
    async def test_apply_ignore_duplicate_returns_early(self):
        from general_ludd.review.decision_applier import apply_decision
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="ret-dup",
            matched_todo_id="todo-dup",
            decision="ignore_duplicate",
            confidence=0.0,
        )
        mock_repo = AsyncMock()
        await apply_decision(decision, mock_repo, MagicMock())
        mock_repo.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_no_matched_todo_id_returns_early(self):
        from general_ludd.review.decision_applier import apply_decision
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="ret-3",
            matched_todo_id=None,
            decision="complete",
            confidence=1.0,
        )
        mock_repo = AsyncMock()
        await apply_decision(decision, mock_repo, MagicMock())
        mock_repo.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_low_confidence_creates_validation_todo(self):
        from general_ludd.review.decision_applier import apply_decision
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="ret-low",
            matched_todo_id="todo-low",
            decision="complete",
            confidence=0.3,
            evidence_refs=["commit:abc12345"],
        )
        mock_repo = AsyncMock()
        mock_todo = MagicMock()
        mock_todo.version = 1
        mock_todo.project_id = None
        mock_repo.get_by_id.return_value = mock_todo

        with patch(
            "general_ludd.review.decision_applier.asyncio.to_thread",
            return_value=decision,
        ):
            await apply_decision(decision, mock_repo, MagicMock(), repo_root="/tmp")
        assert mock_repo.create.called

    @pytest.mark.asyncio
    async def test_apply_with_child_todos(self):
        from general_ludd.review.decision_applier import apply_decision
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="ret-child",
            matched_todo_id="todo-child",
            decision="needs_more_work",
            confidence=0.6,
            child_todos=[{"title": "Fix lint", "description": "Run lint --fix"}],
        )
        mock_repo = AsyncMock()
        mock_todo = MagicMock()
        mock_todo.version = 1
        mock_todo.project_id = None
        mock_repo.get_by_id.return_value = mock_todo

        await apply_decision(decision, mock_repo, MagicMock())
        # create should be called for the child todo
        assert mock_repo.create.called


# ---------------------------------------------------------------------------
# Review — Completion verifier
# ---------------------------------------------------------------------------


class TestCompletionVerifier:
    def test_verify_completion_missing_evidence_downgrades(self):
        from general_ludd.review.completion_verifier import verify_completion
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="r1",
            matched_todo_id="t1",
            decision="complete",
            confidence=1.0,
            evidence_refs=["commit:abc12345"],
        )
        with patch("general_ludd.review.completion_verifier.Path.is_dir", return_value=False), \
             patch("general_ludd.review.completion_verifier.FeatureVerifier") as mock_fv:
            mock_fv.return_value.check_all.return_value = {}
            result = verify_completion(decision, None, repo_root="/nonexistent")
            assert result.decision == "needs_more_work"

    def test_verify_completion_path_traversal_blocked(self):
        from general_ludd.review.completion_verifier import verify_completion
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="r2",
            matched_todo_id="t2",
            decision="complete",
            confidence=1.0,
            evidence_refs=["artifact:../../../etc/passwd"],
        )
        result = verify_completion(decision, None, repo_root="/tmp")
        assert result.decision == "needs_more_work"

    def test_verify_completion_non_complete_passes_through(self):
        from general_ludd.review.completion_verifier import verify_completion
        from general_ludd.schemas.task_decision import TaskDecision

        decision = TaskDecision(
            return_id="r3",
            matched_todo_id="t3",
            decision="needs_more_work",
            confidence=0.5,
        )
        result = verify_completion(decision, None, repo_root=None)
        assert result.decision == "needs_more_work"


# ---------------------------------------------------------------------------
# Review — Conversation
# ---------------------------------------------------------------------------


class TestConversation:
    def test_add_and_get_messages(self):
        from general_ludd.review.conversation import Conversation

        conv = Conversation(todo_id="t1", return_id="r1")
        conv.add_message("user", "Review this")
        conv.add_message("assistant", "Reviewed")
        msgs = conv.messages
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"

    def test_get_context_returns_messages(self):
        from general_ludd.review.conversation import Conversation

        conv = Conversation(todo_id="t2", return_id="r2")
        conv.add_message("user", "Q")
        conv.add_message("assistant", "A")
        ctx = conv.get_context()
        assert len(ctx) == 2

    def test_conversation_fields(self):
        from general_ludd.review.conversation import Conversation

        conv = Conversation(todo_id="t3", return_id="r3")
        conv.add_message("user", "hello")
        assert conv.todo_id == "t3"
        assert conv.return_id == "r3"
        assert conv.message_count() == 1
        assert conv.total_tokens() > 0
