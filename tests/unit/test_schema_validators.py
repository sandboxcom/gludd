from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.agents.behavior import AgentBehavior, GuardrailConfig
from general_ludd.ansible.ara import ARAConfig
from general_ludd.ansible.core_runner import AnsibleResult
from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.dogfood.runner import DogfoodConfig
from general_ludd.infra.compute import ComputeConfig, ComputeInstance, ComputeProvider, GPUType
from general_ludd.infra.providers import ProviderInfo
from general_ludd.mcp.catalog import MCPCatalogEntry
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool
from general_ludd.models.gateway import ModelProfile
from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.repo_map import CodeSymbol
from general_ludd.review.conversation import ConversationMessage
from general_ludd.rules.engine import Rule, RuleAction
from general_ludd.runtime.pip_bundle import BundleManifest
from general_ludd.runtime.profile import DataSourceMount, RuntimeProfile
from general_ludd.schemas.benchmark import (
    BenchmarkResult,
    BenchmarkScores,
    PromptProfile,
    RoutingCandidate,
    RoutingDecision,
    TaskType,
)
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.quality_gate import (
    MoleculeQualityGate,
    PythonQualityGate,
    QualityGateConfig,
)
from general_ludd.schemas.queue import Queue
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_definition import TaskDefinition
from general_ludd.schemas.task_return import TaskReturn
from general_ludd.schemas.todo import Todo, TodoStatus
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.skills.catalog import CatalogSkillEntry
from general_ludd.skills.skill import Skill


class TestTodoValidators:
    def test_valid_todo(self):
        t = Todo(title="fix bug")
        assert t.title == "fix bug"
        assert t.status == TodoStatus.BACKLOG

    def test_title_strips_whitespace(self):
        t = Todo(title="  fix bug  ")
        assert t.title == "fix bug"

    def test_title_rejects_empty(self):
        with pytest.raises(ValidationError):
            Todo(title="")

    def test_title_rejects_whitespace_only(self):
        with pytest.raises(ValidationError):
            Todo(title="   ")

    def test_confidence_range_valid(self):
        t = Todo(title="x", confidence=0.5)
        assert t.confidence == 0.5

    def test_confidence_range_rejects_negative(self):
        with pytest.raises(ValidationError):
            Todo(title="x", confidence=-0.1)

    def test_confidence_range_rejects_above_one(self):
        with pytest.raises(ValidationError):
            Todo(title="x", confidence=1.5)

    def test_queue_strips_whitespace(self):
        t = Todo(title="x", queue="  core  ")
        assert t.queue == "core"

    def test_queue_rejects_empty(self):
        with pytest.raises(ValidationError):
            Todo(title="x", queue="")

    def test_priority_non_negative(self):
        with pytest.raises(ValidationError):
            Todo(title="x", priority=-1)

    def test_version_minimum_one(self):
        with pytest.raises(ValidationError):
            Todo(title="x", version=0)

    def test_completed_at_only_when_complete(self):
        from datetime import UTC, datetime
        with pytest.raises(ValidationError):
            Todo(
                title="x",
                status=TodoStatus.ACTIVE,
                completed_at=datetime.now(UTC),
            )


class TestPromptProfileValidators:
    def test_valid_prompt_profile(self):
        p = PromptProfile(id="p1", name="test", source="local", prompt_text="hello")
        assert p.id == "p1"

    def test_id_strips_whitespace(self):
        p = PromptProfile(id="  p1  ", name="test", source="local", prompt_text="hello")
        assert p.id == "p1"

    def test_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            PromptProfile(id="", name="test", source="local", prompt_text="hello")

    def test_name_strips_whitespace(self):
        p = PromptProfile(id="p1", name="  test  ", source="local", prompt_text="hello")
        assert p.name == "test"

    def test_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            PromptProfile(id="p1", name="", source="local", prompt_text="hello")

    def test_prompt_text_rejects_empty(self):
        with pytest.raises(ValidationError):
            PromptProfile(id="p1", name="test", source="local", prompt_text="")


class TestBenchmarkScoresValidators:
    def test_valid_scores(self):
        s = BenchmarkScores(
            completion_score=0.8,
            code_quality_score=0.7,
            instruction_adherence_score=0.9,
            token_efficiency_score=0.6,
        )
        assert s.completion_score == 0.8

    def test_rejects_negative(self):
        with pytest.raises(ValidationError):
            BenchmarkScores(
                completion_score=-0.1,
                code_quality_score=0.0,
                instruction_adherence_score=0.0,
                token_efficiency_score=0.0,
            )

    def test_rejects_above_one(self):
        with pytest.raises(ValidationError):
            BenchmarkScores(
                completion_score=1.1,
                code_quality_score=0.0,
                instruction_adherence_score=0.0,
                token_efficiency_score=0.0,
            )


def _scores():
    return BenchmarkScores(
        completion_score=0.8,
        code_quality_score=0.7,
        instruction_adherence_score=0.9,
        token_efficiency_score=0.6,
    )


class TestBenchmarkResultValidators:
    def test_valid_result(self):
        r = BenchmarkResult(
            model_profile_id="mp1",
            task_type=TaskType.BUG_FIX,
            scores=_scores(),
        )
        assert r.model_profile_id == "mp1"

    def test_time_seconds_non_negative(self):
        with pytest.raises(ValidationError):
            BenchmarkResult(
                model_profile_id="mp1",
                task_type=TaskType.BUG_FIX,
                scores=_scores(),
                time_seconds=-1.0,
            )

    def test_input_tokens_non_negative(self):
        with pytest.raises(ValidationError):
            BenchmarkResult(
                model_profile_id="mp1",
                task_type=TaskType.BUG_FIX,
                scores=_scores(),
                input_tokens=-1,
            )

    def test_cost_non_negative(self):
        with pytest.raises(ValidationError):
            BenchmarkResult(
                model_profile_id="mp1",
                task_type=TaskType.BUG_FIX,
                scores=_scores(),
                cost_usd=-0.01,
            )

    def test_model_profile_id_strips(self):
        r = BenchmarkResult(
            model_profile_id="  mp1  ",
            task_type=TaskType.BUG_FIX,
            scores=_scores(),
        )
        assert r.model_profile_id == "mp1"


class TestRoutingCandidateValidators:
    def test_valid(self):
        rc = RoutingCandidate(
            prompt_profile_id="p1",
            model_profile_id="m1",
            composite_score=0.9,
            avg_cost_usd=0.05,
            sample_count=10,
            task_type=TaskType.FEATURE,
        )
        assert rc.composite_score == 0.9

    def test_composite_score_range(self):
        with pytest.raises(ValidationError):
            RoutingCandidate(
                prompt_profile_id="p1",
                model_profile_id="m1",
                composite_score=1.5,
                avg_cost_usd=0.05,
                sample_count=10,
                task_type=TaskType.FEATURE,
            )

    def test_avg_cost_non_negative(self):
        with pytest.raises(ValidationError):
            RoutingCandidate(
                prompt_profile_id="p1",
                model_profile_id="m1",
                composite_score=0.5,
                avg_cost_usd=-0.01,
                sample_count=10,
                task_type=TaskType.FEATURE,
            )

    def test_sample_count_positive(self):
        with pytest.raises(ValidationError):
            RoutingCandidate(
                prompt_profile_id="p1",
                model_profile_id="m1",
                composite_score=0.5,
                avg_cost_usd=0.05,
                sample_count=0,
                task_type=TaskType.FEATURE,
            )


class TestRoutingDecisionValidators:
    def test_valid(self):
        rd = RoutingDecision(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.9,
            estimated_cost_usd=0.05,
            sample_count=10,
        )
        assert rd.composite_score == 0.9

    def test_composite_score_range(self):
        with pytest.raises(ValidationError):
            RoutingDecision(
                selected_prompt_profile_id="p1",
                selected_model_profile_id="m1",
                composite_score=-0.1,
                estimated_cost_usd=0.05,
                sample_count=10,
            )

    def test_estimated_cost_non_negative(self):
        with pytest.raises(ValidationError):
            RoutingDecision(
                selected_prompt_profile_id="p1",
                selected_model_profile_id="m1",
                composite_score=0.9,
                estimated_cost_usd=-0.01,
                sample_count=10,
            )

    def test_sample_count_negative(self):
        with pytest.raises(ValidationError):
            RoutingDecision(
                selected_prompt_profile_id="p1",
                selected_model_profile_id="m1",
                composite_score=0.9,
                estimated_cost_usd=0.05,
                sample_count=-1,
            )

    def test_sample_count_zero_allowed(self):
        d = RoutingDecision(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.9,
            estimated_cost_usd=0.05,
            sample_count=0,
        )
        assert d.sample_count == 0


class TestQueueValidators:
    def test_valid_queue(self):
        q = Queue(queue_name="core")
        assert q.queue_name == "core"

    def test_queue_name_strips(self):
        q = Queue(queue_name="  core  ")
        assert q.queue_name == "core"

    def test_queue_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            Queue(queue_name="")

    def test_queue_name_pattern(self):
        with pytest.raises(ValidationError):
            Queue(queue_name="bad name!")

    def test_soft_cap_leq_hard_cap(self):
        with pytest.raises(ValidationError):
            Queue(queue_name="core", soft_cap=10, hard_cap=5)

    def test_hard_cap_minimum_one(self):
        with pytest.raises(ValidationError):
            Queue(queue_name="core", hard_cap=0)

    def test_max_error_rate_range(self):
        with pytest.raises(ValidationError):
            Queue(queue_name="core", max_error_rate=1.5)

    def test_max_error_rate_non_negative(self):
        with pytest.raises(ValidationError):
            Queue(queue_name="core", max_error_rate=-0.1)


class TestJobSpecValidators:
    def test_valid(self):
        j = JobSpec(job_id="j1", playbook="noop.yml", queue="core")
        assert j.job_id == "j1"

    def test_job_id_strips(self):
        j = JobSpec(job_id="  j1  ", playbook="noop.yml", queue="core")
        assert j.job_id == "j1"

    def test_job_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            JobSpec(job_id="", playbook="noop.yml", queue="core")

    def test_playbook_rejects_empty(self):
        with pytest.raises(ValidationError):
            JobSpec(job_id="j1", playbook="", queue="core")

    def test_queue_rejects_empty(self):
        with pytest.raises(ValidationError):
            JobSpec(job_id="j1", playbook="noop.yml", queue="")


class TestTaskReturnValidators:
    def test_valid(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="noop.yml", queue="core")
        assert tr.return_id == "r1"

    def test_return_id_strips(self):
        tr = TaskReturn(return_id="  r1  ", job_id="j1", playbook="noop.yml", queue="core")
        assert tr.return_id == "r1"

    def test_return_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            TaskReturn(return_id="", job_id="j1", playbook="noop.yml", queue="core")

    def test_job_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            TaskReturn(return_id="r1", job_id="", playbook="noop.yml", queue="core")

    def test_playbook_rejects_empty(self):
        with pytest.raises(ValidationError):
            TaskReturn(return_id="r1", job_id="j1", playbook="", queue="core")

    def test_queue_rejects_empty(self):
        with pytest.raises(ValidationError):
            TaskReturn(return_id="r1", job_id="j1", playbook="noop.yml", queue="")


class TestTaskDecisionValidators:
    def test_valid(self):
        td = TaskDecision(return_id="r1", decision="complete")
        assert td.decision == "complete"

    def test_return_id_strips(self):
        td = TaskDecision(return_id="  r1  ", decision="complete")
        assert td.return_id == "r1"

    def test_return_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            TaskDecision(return_id="", decision="complete")

    def test_confidence_range(self):
        with pytest.raises(ValidationError):
            TaskDecision(return_id="r1", decision="complete", confidence=1.5)

    def test_confidence_non_negative(self):
        with pytest.raises(ValidationError):
            TaskDecision(return_id="r1", decision="complete", confidence=-0.1)

    def test_invalid_decision(self):
        with pytest.raises(ValidationError):
            TaskDecision(return_id="r1", decision="invalid_choice")


class TestTaskDefinitionValidators:
    def test_valid(self):
        td = TaskDefinition(name="fix bug")
        assert td.name == "fix bug"

    def test_name_strips(self):
        td = TaskDefinition(name="  fix bug  ")
        assert td.name == "fix bug"

    def test_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            TaskDefinition(name="")

    def test_name_rejects_whitespace_only(self):
        with pytest.raises(ValidationError):
            TaskDefinition(name="   ")


class TestQualityGateValidators:
    def test_python_coverage_range(self):
        with pytest.raises(ValidationError):
            PythonQualityGate(line_coverage_min_percent=101.0)

    def test_python_coverage_range_negative(self):
        with pytest.raises(ValidationError):
            PythonQualityGate(line_coverage_min_percent=-1.0)

    def test_python_branch_coverage_range(self):
        with pytest.raises(ValidationError):
            PythonQualityGate(branch_coverage_min_percent=150.0)

    def test_molecule_coverage_range(self):
        with pytest.raises(ValidationError):
            MoleculeQualityGate(coverage_min_percent=101.0)

    def test_molecule_coverage_range_negative(self):
        with pytest.raises(ValidationError):
            MoleculeQualityGate(coverage_min_percent=-1.0)

    def test_exemption_max_age_positive(self):
        with pytest.raises(ValidationError):
            MoleculeQualityGate(exemption_max_age_days=0)

    def test_valid_config(self):
        qg = QualityGateConfig()
        assert qg.enabled


class TestOpenBaoConfigValidators:
    def test_valid_defaults(self):
        c = OpenBaoConfig()
        assert c.mode == "auto"

    def test_kv_mount_rejects_empty(self):
        with pytest.raises(ValidationError):
            OpenBaoConfig(kv_mount="")

    def test_auth_method_rejects_empty(self):
        with pytest.raises(ValidationError):
            OpenBaoConfig(auth_method="")


class TestMCPServerConfigValidators:
    def test_valid_stdio(self):
        c = MCPServerConfig(server_id="s1", command=["npx", "something"])
        assert c.server_id == "s1"

    def test_valid_http(self):
        c = MCPServerConfig(server_id="s1", url="http://localhost:8080")
        assert c.is_http()

    def test_server_id_strips(self):
        c = MCPServerConfig(server_id="  s1  ", command=["npx"])
        assert c.server_id == "s1"

    def test_server_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            MCPServerConfig(server_id="", command=["npx"])

    def test_timeout_positive(self):
        with pytest.raises(ValidationError):
            MCPServerConfig(server_id="s1", command=["npx"], timeout_seconds=0)

    def test_needs_command_or_url(self):
        with pytest.raises(ValidationError):
            MCPServerConfig(server_id="s1")


class TestComputeConfigValidators:
    def test_valid(self):
        c = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
        assert c.provider == ComputeProvider.AWS

    def test_gpu_count_minimum(self):
        with pytest.raises(ValidationError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, gpu_count=0)

    def test_max_cost_positive(self):
        with pytest.raises(ValidationError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, max_cost_usd=0)

    def test_timeout_positive(self):
        with pytest.raises(ValidationError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, timeout_minutes=0)

    def test_disk_size_minimum(self):
        with pytest.raises(ValidationError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, disk_size_gb=0)


class TestComputeInstanceValidators:
    def test_valid(self):
        ci = ComputeInstance(
            instance_id="i1",
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
        )
        assert ci.instance_id == "i1"

    def test_instance_id_strips(self):
        ci = ComputeInstance(
            instance_id="  i1  ",
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
        )
        assert ci.instance_id == "i1"

    def test_instance_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            ComputeInstance(
                instance_id="",
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
            )

    def test_port_range(self):
        with pytest.raises(ValidationError):
            ComputeInstance(
                instance_id="i1",
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                port=0,
            )

    def test_port_too_high(self):
        with pytest.raises(ValidationError):
            ComputeInstance(
                instance_id="i1",
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                port=70000,
            )

    def test_cost_non_negative(self):
        with pytest.raises(ValidationError):
            ComputeInstance(
                instance_id="i1",
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                cost_incurred=-1.0,
            )


class TestModelProfileValidators:
    def test_valid(self):
        mp = ModelProfile(model_profile_id="mp1")
        assert mp.model_profile_id == "mp1"

    def test_model_profile_id_strips(self):
        mp = ModelProfile(model_profile_id="  mp1  ")
        assert mp.model_profile_id == "mp1"

    def test_model_profile_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="")

    def test_context_window_positive(self):
        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="mp1", context_window=0)

    def test_cost_non_negative(self):
        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="mp1", cost_per_input_token=-0.01)

    def test_budget_non_negative(self):
        with pytest.raises(ValidationError):
            ModelProfile(model_profile_id="mp1", run_budget_usd=-1.0)


class TestGuardrailConfigValidators:
    def test_valid(self):
        gc = GuardrailConfig()
        assert gc.layer_count() >= 1

    def test_all_disabled_rejects(self):
        with pytest.raises(ValidationError):
            GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False)


class TestAgentBehaviorValidators:
    def test_valid(self):
        ab = AgentBehavior()
        assert ab.tdd_enforced

    def test_max_retries_non_negative(self):
        with pytest.raises(ValidationError):
            AgentBehavior(max_retries=-1)


class TestRuleValidators:
    def test_valid(self):
        r = Rule(rule_id="r1")
        assert r.rule_id == "r1"

    def test_rule_id_strips(self):
        r = Rule(rule_id="  r1  ")
        assert r.rule_id == "r1"

    def test_rule_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            Rule(rule_id="")


class TestRuleActionValidators:
    def test_valid(self):
        ra = RuleAction(rule_id="r1", action_type="route")
        assert ra.rule_id == "r1"

    def test_rule_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            RuleAction(rule_id="", action_type="route")

    def test_action_type_rejects_empty(self):
        with pytest.raises(ValidationError):
            RuleAction(rule_id="r1", action_type="")


class TestPlanArtifactValidators:
    def test_valid(self):
        pa = PlanArtifact(todo_id="t1")
        assert pa.todo_id == "t1"

    def test_todo_id_strips(self):
        pa = PlanArtifact(todo_id="  t1  ")
        assert pa.todo_id == "t1"

    def test_todo_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            PlanArtifact(todo_id="")


class TestCodeSymbolValidators:
    def test_valid(self):
        cs = CodeSymbol(name="foo", kind="function", file_path="a.py", line_start=1, line_end=5)
        assert cs.name == "foo"

    def test_name_strips(self):
        cs = CodeSymbol(name="  foo  ", kind="function", file_path="a.py", line_start=1, line_end=5)
        assert cs.name == "foo"

    def test_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="", kind="function", file_path="a.py", line_start=1, line_end=5)

    def test_file_path_rejects_empty(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="foo", kind="function", file_path="", line_start=1, line_end=5)

    def test_line_start_non_negative(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="foo", kind="function", file_path="a.py", line_start=-1, line_end=5)

    def test_line_end_ge_line_start(self):
        with pytest.raises(ValidationError):
            CodeSymbol(name="foo", kind="function", file_path="a.py", line_start=10, line_end=5)


class TestDataSourceMountValidators:
    def test_valid(self):
        dsm = DataSourceMount(mount_id="m1")
        assert dsm.mount_id == "m1"

    def test_mount_id_strips(self):
        dsm = DataSourceMount(mount_id="  m1  ")
        assert dsm.mount_id == "m1"

    def test_mount_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            DataSourceMount(mount_id="")

    def test_container_path_absolute(self):
        with pytest.raises(ValidationError):
            DataSourceMount(mount_id="m1", container_path="relative/path")


class TestRuntimeProfileValidators:
    def test_valid(self):
        rp = RuntimeProfile(runtime_profile_id="rp1")
        assert rp.runtime_profile_id == "rp1"

    def test_runtime_profile_id_strips(self):
        rp = RuntimeProfile(runtime_profile_id="  rp1  ")
        assert rp.runtime_profile_id == "rp1"

    def test_runtime_profile_id_rejects_empty(self):
        with pytest.raises(ValidationError):
            RuntimeProfile(runtime_profile_id="")


class TestBundleManifestValidators:
    def test_valid(self):
        bm = BundleManifest(version="1.0", commit="abc", timestamp="2025-01-01", files=[], checksums={})
        assert bm.version == "1.0"

    def test_version_strips(self):
        bm = BundleManifest(version="  1.0  ", commit="abc", timestamp="2025-01-01", files=[], checksums={})
        assert bm.version == "1.0"

    def test_version_rejects_empty(self):
        with pytest.raises(ValidationError):
            BundleManifest(version="", commit="abc", timestamp="2025-01-01", files=[], checksums={})


class TestConversationMessageValidators:
    def test_valid(self):
        cm = ConversationMessage(role="user", content="hello")
        assert cm.role == "user"

    def test_role_strips(self):
        cm = ConversationMessage(role="  user  ", content="hello")
        assert cm.role == "user"

    def test_role_rejects_empty(self):
        with pytest.raises(ValidationError):
            ConversationMessage(role="", content="hello")

    def test_content_rejects_empty(self):
        with pytest.raises(ValidationError):
            ConversationMessage(role="user", content="")


class TestDogfoodConfigValidators:
    def test_valid(self):
        dc = DogfoodConfig(repo_root="/tmp", target_repo="/tmp", runtime_profile="native_uv", model_profile="mp1")
        assert dc.repo_root == "/tmp"

    def test_repo_root_rejects_empty(self):
        with pytest.raises(ValidationError):
            DogfoodConfig(repo_root="", target_repo="/tmp", runtime_profile="native_uv", model_profile="mp1")

    def test_target_repo_rejects_empty(self):
        with pytest.raises(ValidationError):
            DogfoodConfig(repo_root="/tmp", target_repo="", runtime_profile="native_uv", model_profile="mp1")


class TestProviderInfoValidators:
    def test_valid(self):
        pi = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="AWS",
            terraform_provider="hashicorp/aws",
            supports_spot=True,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
        )
        assert pi.display_name == "AWS"

    def test_display_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            ProviderInfo(
                provider=ComputeProvider.AWS,
                display_name="",
                terraform_provider="hashicorp/aws",
                supports_spot=True,
                sub_hour_billing=False,
                min_gpu=GPUType.T4,
                max_gpu=GPUType.A100_80,
            )


class TestMCPToolValidators:
    def test_valid(self):
        mt = MCPTool(name="read_file")
        assert mt.name == "read_file"

    def test_name_strips(self):
        mt = MCPTool(name="  read_file  ")
        assert mt.name == "read_file"

    def test_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            MCPTool(name="")


class TestMCPCatalogEntryValidators:
    def test_valid(self):
        mce = MCPCatalogEntry(server_name="filesystem")
        assert mce.server_name == "filesystem"

    def test_server_name_strips(self):
        mce = MCPCatalogEntry(server_name="  filesystem  ")
        assert mce.server_name == "filesystem"

    def test_server_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            MCPCatalogEntry(server_name="")


class TestCatalogSkillEntryValidators:
    def test_valid(self):
        cse = CatalogSkillEntry(name="tdd")
        assert cse.name == "tdd"

    def test_name_strips(self):
        cse = CatalogSkillEntry(name="  tdd  ")
        assert cse.name == "tdd"

    def test_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            CatalogSkillEntry(name="")


class TestSkillValidators:
    def test_valid(self):
        s = Skill(name="tdd")
        assert s.name == "tdd"

    def test_name_strips(self):
        s = Skill(name="  tdd  ")
        assert s.name == "tdd"

    def test_name_rejects_empty(self):
        with pytest.raises(ValidationError):
            Skill(name="")


class TestProcessIsolationConfigValidators:
    def test_valid(self):
        pic = ProcessIsolationConfig()
        assert pic.executable == "podman"

    def test_executable_rejects_empty(self):
        with pytest.raises(ValidationError):
            ProcessIsolationConfig(executable="")


class TestAnsibleResultValidators:
    def test_valid(self):
        ar = AnsibleResult()
        assert ar.status == "unknown"

    def test_status_strips(self):
        ar = AnsibleResult(status="  successful  ")
        assert ar.status == "successful"


class TestARAConfigValidators:
    def test_valid(self):
        ac = ARAConfig()
        assert ac.backend == "sqlite"

    def test_postgresql_connection_string(self):
        ac = ARAConfig(backend="postgresql", connection_string="myhost/db")
        assert ac.connection_string.startswith("postgresql://")


class TestExistingDataStillPasses:
    def test_todo_with_defaults(self):
        t = Todo(title="test")
        assert t.queue == "core"
        assert t.status == TodoStatus.BACKLOG
        assert t.priority == 0
        assert t.version == 1

    def test_queue_initial_data(self):
        from general_ludd.schemas.queue import INITIAL_QUEUES
        assert len(INITIAL_QUEUES) > 0
        for q in INITIAL_QUEUES:
            assert q.queue_name
            assert q.hard_cap >= q.soft_cap

    def test_benchmark_scores_defaults(self):
        s = _scores()
        assert s.composite_score > 0

    def test_routing_decision_defaults(self):
        rd = RoutingDecision(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.5,
            estimated_cost_usd=0.01,
            sample_count=1,
        )
        assert not rd.fallback

    def test_mcp_server_config_both_transports(self):
        stdio = MCPServerConfig(server_id="s1", command=["npx", "foo"])
        http = MCPServerConfig(server_id="s2", url="http://localhost:8080/mcp")
        assert stdio.is_stdio()
        assert http.is_http()
