"""E2E: Repository skeleton, developer workflow, and package import chain.

Covers sprint objective 1 — everything importable, app factory works,
playbook directory exists, schemas load, initial queues defined.
"""

from __future__ import annotations

import importlib


class TestRepositorySkeleton:
    def test_package_version_importable(self):
        from agentic_harness import __version__

        assert __version__
        assert __version__ == "0.1.0"

    def test_all_subpackages_importable(self):
        subpackages = [
            "agentic_harness.schemas",
            "agentic_harness.db",
            "agentic_harness.models",
            "agentic_harness.worker",
            "agentic_harness.event_loop",
            "agentic_harness.rules",
            "agentic_harness.controllers",
            "agentic_harness.prompts",
            "agentic_harness.quality",
            "agentic_harness.secrets",
            "agentic_harness.git_automation",
            "agentic_harness.ansible",
            "agentic_harness.dependency",
            "agentic_harness.runtime",
            "agentic_harness.validation",
            "agentic_harness.reload",
            "agentic_harness.review",
            "agentic_harness.dogfood",
            "agentic_harness.agents",
        ]
        errors = []
        for pkg in subpackages:
            try:
                importlib.import_module(pkg)
            except Exception as exc:
                errors.append(f"{pkg}: {exc}")
        assert not errors, f"Failed imports: {errors}"

    def test_worker_app_factory_creates_fastapi_app(self):
        from agentic_harness.worker import create_app

        app = create_app()
        assert app is not None
        assert app.title

    def test_initial_queues_defined(self):
        from agentic_harness.schemas.queue import INITIAL_QUEUES

        names = {q.queue_name for q in INITIAL_QUEUES}
        expected = {
            "intake", "core", "worker", "ansible", "model", "qa",
            "infra", "dependency", "git", "self_improve", "audit", "manual_hold",
        }
        assert expected == names

    def test_playbooks_directory_exists(self, tmp_path):
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        playbooks_dir = os.path.join(repo_root, "playbooks")
        assert os.path.isdir(playbooks_dir)
        ymls = [f for f in os.listdir(playbooks_dir) if f.endswith(".yml")]
        assert len(ymls) >= 20, f"Expected >=20 playbook stubs, found {len(ymls)}"

    def test_config_directories_exist(self):
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        for d in [
            "config/model_profiles",
            "config/agents",
            "config/openbao",
            "templates/prompts",
            "templates/prompts/partials",
            "molecule",
            "roles",
            "tools",
            "docs",
        ]:
            path = os.path.join(repo_root, d)
            assert os.path.isdir(path), f"Missing directory: {d}"

    def test_makefile_has_required_targets(self):
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        makefile_path = os.path.join(repo_root, "Makefile")
        with open(makefile_path) as f:
            content = f.read()
        required = [
            "test", "test-unit", "test-integration", "lint", "lint-fix",
            "typecheck", "healthcheck", "qa", "validate", "sync",
            "test-and-commit", "test-live-zai", "bootstrap",
        ]
        for target in required:
            assert f"{target}:" in content, f"Missing make target: {target}"
