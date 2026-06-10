"""FastAPI routers extracted from daemon.py for modularity."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_all(app: FastAPI, daemon_state: dict) -> None:
    # Lazy to avoid circular import: routers/*.py import from daemon at module level
    from general_ludd.routers.ansible import register as register_ansible
    from general_ludd.routers.benchmark import register as register_benchmark
    from general_ludd.routers.compute import register as register_compute
    from general_ludd.routers.filestore import register as register_filestore
    from general_ludd.routers.integrity import register as register_integrity
    from general_ludd.routers.mcp import register as register_mcp
    from general_ludd.routers.models import register as register_models
    from general_ludd.routers.projects import register as register_projects
    from general_ludd.routers.quantization import register as register_quantization
    from general_ludd.routers.reload import register as register_reload
    from general_ludd.routers.self_improve import register as register_self_improve
    from general_ludd.routers.signing import register as register_signing
    from general_ludd.routers.skills import register as register_skills
    from general_ludd.routers.slurm import register as register_slurm
    from general_ludd.routers.todos import register as register_todos
    from general_ludd.routers.worktree import register as register_worktree

    register_ansible(app, daemon_state)
    register_benchmark(app, daemon_state)
    register_compute(app, daemon_state)
    register_filestore(app, daemon_state)
    register_integrity(app, daemon_state)
    register_mcp(app, daemon_state)
    register_models(app, daemon_state)
    register_projects(app, daemon_state)
    register_quantization(app, daemon_state)
    register_reload(app, daemon_state)
    register_signing(app, daemon_state)
    register_skills(app, daemon_state)
    register_slurm(app, daemon_state)
    register_todos(app, daemon_state)
    register_worktree(app, daemon_state)
    register_self_improve(app, daemon_state)
