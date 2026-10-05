"""gludd self-hosts through its own ToolchainAdapter (Phase E / WP-E2).

The repo-root ``project.yml`` is what lets ``make dogfood`` keep passing after
the runner migrates from hardcoded make-target invocation to project-runner
detection. This test pins that the file (a) exists, (b) loads cleanly through
``load_project_profile``, and (c) every declared command resolves to an
allow-listed, metachar-free argv — the same contract any external target repo
must satisfy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.project_runner import (
    ProjectProfileError,
    load_project_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gludd_project_yml_loads():
    profile = load_project_profile(REPO_ROOT)
    assert profile.name == "gludd"


def test_gludd_profile_declares_canonical_checks():
    profile = load_project_profile(REPO_ROOT)
    for check in ("test", "lint", "typecheck", "gate", "smoke"):
        assert profile.has(check), f"project.yml missing required check: {check}"


def test_gludd_profile_commands_resolve():
    """Every command must shlex-parse to an allow-listed argv (fail-closed)."""
    profile = load_project_profile(REPO_ROOT)
    assert "make" in profile.allowed_exec
    for check, raw in profile.commands.items():
        argv = profile.resolve_argv(check)
        assert argv, f"check '{check}' resolved to empty argv (raw={raw!r})"
        assert argv[0] == "make", (
            f"gludd self-host commands must go through make (check={check!r}, "
            f"argv0={argv[0]!r}); direct-tool invocation bypasses the make gate"
        )


def test_gludd_profile_env_passthrough_excludes_secrets():
    """env_passthrough is the target-repo allowlist; it must never name a
    secret-shaped var (the runner's _build_env double-checks, but pin it here
    so a future edit to project.yml can't silently opt gludd's own secrets
    into child-command env)."""
    profile = load_project_profile(REPO_ROOT)
    secret_shaped = {
        "ZAI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "DB_PASSWORD",
        "GLUDD_AUTH_PSK",
    }
    leaked = secret_shaped.intersection(profile.env_passthrough)
    assert not leaked, f"project.yml env_passthrough leaks secret vars: {leaked}"


def test_gludd_profile_missing_file_fails_closed(monkeypatch, tmp_path):
    """Sanity: the loader still fails closed for a workspace without project.yml."""
    with pytest.raises(ProjectProfileError):
        load_project_profile(tmp_path)
