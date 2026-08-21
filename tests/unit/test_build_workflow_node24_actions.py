"""Pinned Node 24 Docker-action contracts for the beta4 build workflow."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DOCKER_ACTIONS = {
    "docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
    "docker/login-action@dbcb813823bdd20940b903addbd779551569679f",
    "docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302",
    "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
}


def test_build_workflow_uses_only_pinned_node24_docker_actions() -> None:
    """GHE must not force deprecated Node 20 Docker actions onto Node 24."""
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    refs = set(re.findall(r"uses:\s+(docker/[^\s#]+)", workflow))

    assert refs == EXPECTED_DOCKER_ACTIONS
    assert all(re.fullmatch(r"docker/[a-z-]+@[0-9a-f]{40}", ref) for ref in refs)
