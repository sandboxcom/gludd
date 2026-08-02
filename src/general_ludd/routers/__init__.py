"""FastAPI routers extracted from daemon.py for modularity."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_all(app: FastAPI, daemon_state: dict[str, object]) -> None:
    # Lazy to avoid circular import: routers/*.py import from daemon at module level
    from general_ludd.routers.account import register as register_account
    from general_ludd.routers.adversarial import register as register_adversarial
    from general_ludd.routers.ansible import register as register_ansible
    from general_ludd.routers.benchmark import register as register_benchmark
    from general_ludd.routers.compute import register as register_compute
    from general_ludd.routers.coordination import register as register_coordination
    from general_ludd.routers.estimation import register as register_estimation
    from general_ludd.routers.eval import register as register_eval
    from general_ludd.routers.filestore import register as register_filestore
    from general_ludd.routers.human_todos import register as register_human_todos
    from general_ludd.routers.integrity import register as register_integrity
    from general_ludd.routers.mcp import register as register_mcp
    from general_ludd.routers.memory import register as register_memory
    from general_ludd.routers.model_performance import register as register_model_performance
    from general_ludd.routers.models import register as register_models
    from general_ludd.routers.ornith import register as register_ornith
    from general_ludd.routers.projects import register as register_projects
    from general_ludd.routers.quantization import register as register_quantization
    from general_ludd.routers.reload import register as register_reload
    from general_ludd.routers.remediation import register as register_remediation
    from general_ludd.routers.render import register as register_render
    from general_ludd.routers.security import register as register_security
    from general_ludd.routers.self_improve import register as register_self_improve
    from general_ludd.routers.signing import register as register_signing
    from general_ludd.routers.skills import register as register_skills
    from general_ludd.routers.slurm import register as register_slurm
    from general_ludd.routers.stream import register as register_stream
    from general_ludd.routers.terraform_state import register as register_terraform_state
    from general_ludd.routers.todos import register as register_todos
    from general_ludd.routers.variants import register as register_variants
    from general_ludd.routers.web_search import register as register_web_search
    from general_ludd.routers.worktree import register as register_worktree

    register_account(app, daemon_state)
    register_ansible(app, daemon_state)
    register_adversarial(app, daemon_state)
    register_benchmark(app, daemon_state)
    register_compute(app, daemon_state)
    register_coordination(app, daemon_state)
    register_estimation(app, daemon_state)
    register_eval(app, daemon_state)
    register_filestore(app, daemon_state)
    register_human_todos(app, daemon_state)
    register_integrity(app, daemon_state)
    register_mcp(app, daemon_state)
    register_memory(app, daemon_state)
    register_models(app, daemon_state)
    register_ornith(app, daemon_state)
    register_projects(app, daemon_state)
    register_quantization(app, daemon_state)
    register_reload(app, daemon_state)
    register_remediation(app, daemon_state)
    register_render(app, daemon_state)
    register_signing(app, daemon_state)
    register_skills(app, daemon_state)
    register_slurm(app, daemon_state)
    register_stream(app, daemon_state)
    register_todos(app, daemon_state)
    register_variants(app, daemon_state)
    register_worktree(app, daemon_state)
    register_self_improve(app, daemon_state)
    register_model_performance(app, daemon_state)
    register_security(app, daemon_state)
    register_web_search(app, daemon_state)
    register_terraform_state(app, daemon_state)
