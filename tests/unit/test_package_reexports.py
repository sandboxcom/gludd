"""Tests verifying all package __init__.py files re-export their public symbols."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import pytest


def _walk_packages(
    root: ModuleType,
) -> list[str]:
    result: list[str] = []
    prefix = root.__name__ + "."
    for info in pkgutil.walk_packages(
        path=root.__path__,
        prefix=prefix,
    ):
        result.append(info.name)
    return sorted(result)


PACKAGES_WITH_REEXPORTS = [
    "general_ludd.worktree",
    "general_ludd.security",
    "general_ludd.ansible",
    "general_ludd.schemas",
    "general_ludd.scoring",
    "general_ludd.models",
    "general_ludd.dogfood",
    "general_ludd.controllers",
    "general_ludd.worker",
    "general_ludd.prompts",
    "general_ludd.rules",
    "general_ludd.event_loop",
    "general_ludd.runtime",
    "general_ludd.secrets",
    "general_ludd.git_automation",
    "general_ludd.config",
]

PACKAGES_NEEDING_REEXPORTS = [
    "general_ludd.routers",
    "general_ludd.self_improve",
    "general_ludd.tui",
    "general_ludd.integrity",
    "general_ludd.quality",
    "general_ludd.filestore",
    "general_ludd.code_intelligence",
    "general_ludd.observability",
    "general_ludd.logging",
    "general_ludd.metrics",
    "general_ludd.projects",
    "general_ludd.events",
    "general_ludd.infra",
    "general_ludd.skills",
    "general_ludd.planning",
    "general_ludd.mcp",
    "general_ludd.reload",
    "general_ludd.validation",
    "general_ludd.dependency",
    "general_ludd.agents",
    "general_ludd.review",
    "general_ludd.db",
]


class TestExistingReexports:
    @pytest.mark.parametrize("pkg", PACKAGES_WITH_REEXPORTS)
    def test_package_is_importable(self, pkg):
        mod = importlib.import_module(pkg)
        assert mod is not None

    def test_worktree_reexports(self):
        from general_ludd.worktree import WorktreeMonitor, WorktreeScanner

        assert WorktreeScanner is not None
        assert WorktreeMonitor is not None

    def test_security_reexports(self):
        from general_ludd.security import sanitize_job_id, sanitize_path

        assert callable(sanitize_path)
        assert callable(sanitize_job_id)

    def test_schemas_reexports(self):
        from general_ludd.schemas import JobSpec, Todo

        assert Todo is not None
        assert JobSpec is not None

    def test_models_reexports(self):
        from general_ludd.models import ModelGateway, ModelRegistry

        assert ModelGateway is not None
        assert ModelRegistry is not None


class TestNeededReexports:
    @pytest.mark.parametrize("pkg", PACKAGES_NEEDING_REEXPORTS)
    def test_package_is_importable(self, pkg):
        mod = importlib.import_module(pkg)
        assert mod is not None

    def test_self_improve_reexports(self):
        from general_ludd.self_improve import SelfImprovementHarness

        assert SelfImprovementHarness is not None

    def test_tui_reexports(self):
        from general_ludd.tui import TUIKeyHandler, run_tui

        assert callable(run_tui)
        assert TUIKeyHandler is not None

    def test_integrity_reexports(self):
        from general_ludd.integrity import FileIntegrityScanner

        assert FileIntegrityScanner is not None

    def test_quality_reexports(self):
        from general_ludd.quality import QualityGateChecker, run_preflight

        assert callable(run_preflight)
        assert QualityGateChecker is not None

    def test_filestore_reexports(self):
        from general_ludd.filestore import BinaryBootstrapper, FileStore

        assert FileStore is not None
        assert BinaryBootstrapper is not None

    def test_code_intelligence_reexports(self):
        from general_ludd.code_intelligence import (
            ASTBlockExtractor,
            CallGraph,
            CodeSearch,
            GitIntelligence,
        )

        assert ASTBlockExtractor is not None
        assert CallGraph is not None
        assert CodeSearch is not None
        assert GitIntelligence is not None

    def test_observability_reexports(self):
        from general_ludd.observability import (
            AutoBenchmarkRecorder,
            ExecutionSpan,
            ExecutionTrace,
            ModelComparison,
        )

        assert ExecutionSpan is not None
        assert ExecutionTrace is not None
        assert ModelComparison is not None
        assert AutoBenchmarkRecorder is not None

    def test_logging_reexports(self):
        from general_ludd.logging import ProjectLogAdapter, ProjectLogFilter

        assert ProjectLogAdapter is not None
        assert ProjectLogFilter is not None

    def test_metrics_reexports(self):
        from general_ludd.metrics import AgentMetrics, MetricsCollector

        assert MetricsCollector is not None
        assert AgentMetrics is not None

    def test_projects_reexports(self):
        from general_ludd.projects import ProjectManager, ProjectWorkspace

        assert ProjectManager is not None
        assert ProjectWorkspace is not None

    def test_events_reexports(self):
        from general_ludd.events import Event, EventBus, HookSystem

        assert EventBus is not None
        assert HookSystem is not None
        assert Event is not None

    def test_infra_reexports(self):
        from general_ludd.infra import (
            DeploymentManager,
            LocalInferenceManager,
            SlurmAdapter,
            TerraformGenerator,
            UtilizationTracker,
        )

        assert SlurmAdapter is not None
        assert LocalInferenceManager is not None
        assert DeploymentManager is not None
        assert TerraformGenerator is not None
        assert UtilizationTracker is not None

    def test_skills_reexports(self):
        from general_ludd.skills import (
            RemoteSkillFetcher,
            Skill,
            SkillCatalog,
            SkillRegistry,
        )

        assert Skill is not None
        assert SkillRegistry is not None
        assert SkillCatalog is not None
        assert RemoteSkillFetcher is not None

    def test_planning_reexports(self):
        from general_ludd.planning import PlanArtifact, RepoMap

        assert RepoMap is not None
        assert PlanArtifact is not None

    def test_mcp_reexports(self):
        from general_ludd.mcp import (
            MCPCatalog,
            MCPClient,
            MCPServerConfig,
            MCPToolRegistry,
        )

        assert MCPClient is not None
        assert MCPServerConfig is not None
        assert MCPCatalog is not None
        assert MCPToolRegistry is not None

    def test_reload_reexports(self):
        from general_ludd.reload import (
            HotReloader,
            ReloadManager,
            SelfImprovementWorkflow,
            WorkerBroadcaster,
        )

        assert HotReloader is not None
        assert ReloadManager is not None
        assert WorkerBroadcaster is not None
        assert SelfImprovementWorkflow is not None

    def test_validation_reexports(self):
        from general_ludd.validation import (
            GapAnalyzer,
            LogAuditor,
            ValidationRunner,
        )

        assert ValidationRunner is not None
        assert LogAuditor is not None
        assert GapAnalyzer is not None

    def test_dependency_reexports(self):
        from general_ludd.dependency import DependencyManager

        assert DependencyManager is not None

    def test_agents_reexports(self):
        from general_ludd.agents import (
            AgentDispatcher,
            AgentRegistry,
            BehaviorRenderer,
            GuardrailConfig,
        )

        assert AgentRegistry is not None
        assert AgentDispatcher is not None
        assert BehaviorRenderer is not None
        assert GuardrailConfig is not None

    def test_review_reexports(self):
        from general_ludd.review import ReturnReviewer, apply_decision

        assert ReturnReviewer is not None
        assert callable(apply_decision)

    def test_db_reexports_core(self):
        from general_ludd.db import (
            Base,
            ProjectRepository,
            TodoRepository,
            init_engine_from_config,
        )

        assert callable(init_engine_from_config)
        assert TodoRepository is not None
        assert ProjectRepository is not None
        assert Base is not None

    def test_routers_reexports(self):
        from general_ludd.routers import register_all

        assert callable(register_all)
