"""Structural tests for OpenCode's workspace and external-path permissions.

OpenCode matches read/edit/glob/grep inputs relative to the project worktree.
Access outside that worktree is gated separately by ``external_directory``.
These tests model the documented last-match-wins behavior so a syntactically
valid but unusable rule order cannot pass.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = ROOT / "opencode.json"
AGENTS_MD = ROOT / "AGENTS.md"

WORKSPACE_PREFIX = "/Users/shawnwilson/gludd/**"
ALLOWED_EXTERNAL_PREFIXES = (
    "/tmp/**",
    "/Users/shawnwilson/.config/opencode/**",
    "/Users/shawnwilson/.local/share/opencode/**",
    "/Users/shawnwilson/.cache/**",
)
FORBIDDEN_EXTERNAL_PATHS = (
    "/Users/shawnwilson/.ssh/id_ed25519",
    "/Users/shawnwilson/.aws/credentials",
    "/Users/shawnwilson/.gnupg/private-keys-v1.d/key",
    "/Users/shawnwilson/Documents/private.txt",
    "/Users/shawnwilson/Desktop/private.txt",
    "/Users/shawnwilson/Library/Keychains/login.keychain-db",
    "/Users/shawnwilson/.config/gh/hosts.yml",
)


def _load_config() -> dict:
    parsed = json.loads(OPENCODE_JSON.read_text())
    assert isinstance(parsed, dict)
    return parsed


def _resolve(block: dict[str, str], value: str) -> str | None:
    """Apply OpenCode's documented last-matching-rule-wins semantics."""
    decision = None
    for pattern, action in block.items():
        if fnmatch.fnmatchcase(value, pattern):
            decision = action
    return decision


def _permission_scopes() -> tuple[dict, dict]:
    config = _load_config()
    global_permission = config["permission"]
    build_permission = config["agent"]["build"]["permission"]
    assert isinstance(global_permission, dict)
    assert isinstance(build_permission, dict)
    return global_permission, build_permission


@pytest.mark.parametrize("scope_index", (0, 1))
def test_workspace_file_tools_are_usable(scope_index: int) -> None:
    """Workspace operations remain usable in global and build-agent scopes."""
    permission = _permission_scopes()[scope_index]
    assert _resolve(permission["read"], "pyproject.toml") == "allow"
    for tool in ("edit", "glob", "grep"):
        assert permission[tool] == "allow"


@pytest.mark.parametrize("scope_index", (0, 1))
def test_write_uses_the_documented_edit_permission(scope_index: int) -> None:
    """OpenCode gates write/apply_patch through edit; no dead write key."""
    permission = _permission_scopes()[scope_index]
    assert "write" not in permission
    assert permission["edit"] == "allow"


@pytest.mark.parametrize("scope_index", (0, 1))
def test_sensitive_env_reads_remain_denied(scope_index: int) -> None:
    permission = _permission_scopes()[scope_index]
    read = permission["read"]
    assert list(read.items()) == [
        ("*", "allow"),
        ("*.env", "deny"),
        ("*.env.*", "deny"),
        ("*.env.example", "allow"),
    ]
    assert _resolve(read, ".env") == "deny"
    assert _resolve(read, "service.env.production") == "deny"
    assert _resolve(read, ".env.example") == "allow"


@pytest.mark.parametrize("scope_index", (0, 1))
def test_external_directory_is_deny_first_allow_specific(scope_index: int) -> None:
    """The deny catch-all precedes narrower allows under last-match-wins."""
    permission = _permission_scopes()[scope_index]
    external = permission["external_directory"]
    assert next(iter(external.items())) == ("*", "deny")
    allowed = {pattern for pattern, action in external.items() if action == "allow"}
    assert allowed == set(ALLOWED_EXTERNAL_PREFIXES)


@pytest.mark.parametrize("scope_index", (0, 1))
@pytest.mark.parametrize(
    "path",
    (
        "/tmp/gludd-opencode-e2e/result.json",
        "/Users/shawnwilson/.config/opencode/opencode.json",
        "/Users/shawnwilson/.local/share/opencode/session/data.json",
        "/Users/shawnwilson/.cache/uv/archive",
    ),
)
def test_allowed_external_paths_resolve_to_allow(
    scope_index: int,
    path: str,
) -> None:
    external = _permission_scopes()[scope_index]["external_directory"]
    assert _resolve(external, path) == "allow"


@pytest.mark.parametrize("scope_index", (0, 1))
@pytest.mark.parametrize("path", FORBIDDEN_EXTERNAL_PATHS)
def test_private_home_paths_resolve_to_deny(
    scope_index: int,
    path: str,
) -> None:
    external = _permission_scopes()[scope_index]["external_directory"]
    assert _resolve(external, path) == "deny"


@pytest.mark.parametrize("scope_index", (0, 1))
def test_bash_is_make_only(scope_index: int) -> None:
    bash = _permission_scopes()[scope_index]["bash"]
    assert list(bash.items()) == [("*", "deny"), ("make *", "allow")]
    assert _resolve(bash, "make version") == "allow"
    assert _resolve(bash, "python3 -c pass") == "deny"
    assert _resolve(bash, "GLUDD_FLAG=0 make version") == "deny"


def test_agents_md_documents_workspace_and_external_boundaries() -> None:
    text = AGENTS_MD.read_text()
    for prefix in (WORKSPACE_PREFIX, *ALLOWED_EXTERNAL_PREFIXES):
        assert prefix in text
    for forbidden in ("~/.ssh", "~/.aws", "~/.gnupg"):
        assert forbidden in text
    assert "tests/unit/test_no_home_directory_access.py" in text
