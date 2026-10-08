"""Structural pin for the no-home-directory-access user mandate.

The user issued a HARD mandate: "NEVER ask for access to my full home
directory ever again." Access is limited to these path prefixes:

    1. /Users/shawnwilson/gludd/**                    (the workspace)
    2. /tmp/**                                        (all of /tmp)
    3. /private/tmp/**                                (macOS /tmp symlink target)
    4. /private/var/folders/**                         (macOS temp directory)
    5. /Users/shawnwilson/.config/opencode/**         (opencode config)
    6. /Users/shawnwilson/.local/share/opencode/**    (opencode data/tool-output)
    7. /Users/shawnwilson/.cache/**                   (pre-commit/uv/tool caches)

Every other path under /Users/shawnwilson/ is FORBIDDEN — no ~/.ssh,
~/.aws, ~/.gnupg, ~/Documents, ~/Desktop, ~/Library, etc.

This guardrail is codified at three layers (see AGENTS.md
"CRITICAL: No External File Access"):

    Layer 1 — AGENTS.md prompt section (proactive instruction)
    Layer 2 — opencode.json permission block (hard gate at the harness level)
    Layer 3 — this structural test (regression prevention)

This test pins Layer 2 using OpenCode's current permission model. File tools
remain enabled for the active worktree, while one ``external_directory`` block
allows EXACTLY the reviewed external prefixes, starts with a ``*: deny``
catch-all, and rejects representative home-directory paths outside the allowed
set. The active worktree is internal and therefore must not be duplicated in
the external allowlist. If a future edit widens access (for example by adding
``/Users/shawnwilson/**: allow``) or drops the catch-all, this test fails.
"""

import json
import re
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = ROOT / "opencode.json"
AGENTS_MD = ROOT / "AGENTS.md"
FEATURE_DOC = ROOT / "docs/features/EXTERNAL_DIRECTORY_PERMISSION_CONTRACT.md"

FILE_PERMISSION_KEYS = (
    "read",
    "edit",
    "glob",
    "grep",
    "external_directory",
)

# The exhaustive set of allowed *external* path prefixes. The active worktree
# is internal under current OpenCode semantics and needs no external grant.
ALLOWED_EXTERNAL_PREFIXES = (
    "/Users/shawnwilson/.config/opencode/**",
    "/Users/shawnwilson/.local/share/opencode/**",
    "/Users/shawnwilson/.cache/**",
    "/tmp/**",
    "/private/tmp/**",
    "/private/var/folders/**",
)

WORKSPACE_PREFIX = "/Users/shawnwilson/gludd/**"

READ_PERMISSION = {
    "*": "allow",
    "*.env": "deny",
    "*.env.*": "deny",
    "*.env.example": "allow",
}

# Representative forbidden home-directory paths. None of these should ever
# appear as an `allow` rule in a file-tool permission block. The mandate is
# explicit about ssh/aws/gnupg/Documents/Desktop/Library — we sample those
# plus a few other plausible regression vectors.
FORBIDDEN_HOME_PATHS = (
    "/Users/shawnwilson/**",
    "/Users/shawnwilson/.ssh/**",
    "/Users/shawnwilson/.ssh",
    "/Users/shawnwilson/.aws/**",
    "/Users/shawnwilson/.aws",
    "/Users/shawnwilson/.gnupg/**",
    "/Users/shawnwilson/.gnupg",
    "/Users/shawnwilson/Documents/**",
    "/Users/shawnwilson/Documents",
    "/Users/shawnwilson/Desktop/**",
    "/Users/shawnwilson/Desktop",
    "/Users/shawnwilson/Library/**",
    "/Users/shawnwilson/Library",
    "/Users/shawnwilson/.config/**",
    "/Users/shawnwilson/.config",
    "/Users/shawnwilson/Downloads/**",
    "/Users/shawnwilson/Pictures/**",
    "/Users/shawnwilson/Movies/**",
    "/Users/shawnwilson/Music/**",
    "/Users/shawnwilson/.kube/**",
    "/Users/shawnwilson/.docker/**",
    "/Users/shawnwilson/.gitconfig",
    "/Users/shawnwilson/.bashrc",
    "/Users/shawnwilson/.zshrc",
)


def _load_config() -> dict[str, object]:
    """Load the tracked OpenCode configuration as a typed object."""
    assert OPENCODE_JSON.exists(), "opencode.json must exist at repo root"
    data: object = json.loads(OPENCODE_JSON.read_text())
    assert isinstance(data, dict), "opencode.json must contain an object"
    assert all(isinstance(key, str) for key in data), (
        "opencode.json top-level keys must be strings"
    )
    return cast("dict[str, object]", data)


def _load_permission() -> dict[str, object]:
    """Return the global ``permission`` block."""
    data = _load_config()
    assert "permission" in data, "opencode.json must have a `permission` block"
    perm = data["permission"]
    assert isinstance(perm, dict), "permission block must be an object"
    return cast("dict[str, object]", perm)


def _load_build_permission() -> dict[str, object]:
    """Return the build agent's permission block."""
    data = _load_config()
    agent = data.get("agent")
    assert isinstance(agent, dict), "opencode.json must have an `agent` object"
    build = agent.get("build")
    assert isinstance(build, dict), "agent.build must be an object"
    permission = build.get("permission")
    assert isinstance(permission, dict), "agent.build.permission must be an object"
    return cast("dict[str, object]", permission)


def _allowed_keys(block: dict[str, str]) -> list[str]:
    """Return the keys in a permission block that resolve to `allow`."""
    return [k for k, v in block.items() if v == "allow"]


def _external_directory_block() -> dict[str, str]:
    """Return the centralized external-directory permission map."""
    perm = _load_permission()
    assert "external_directory" in perm, (
        "opencode.json must define the external_directory permission"
    )
    block = perm["external_directory"]
    assert isinstance(block, dict), "external_directory permission must be an object"
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in block.items()), (
        "external_directory keys and actions must be strings"
    )
    return cast("dict[str, str]", block)


def test_file_tools_use_current_opencode_permission_semantics() -> None:
    """Keep workspace tool access separate from external path authorization.

    OpenCode routes write/edit/apply-patch through ``edit`` and applies the
    external-directory decision separately when a target is outside the active
    worktree. Per-tool absolute path maps are legacy and break this model.
    """
    perm = _load_permission()
    assert perm["read"] == READ_PERMISSION
    assert perm["edit"] == "allow"
    assert perm["glob"] == "allow"
    assert perm["grep"] == "allow"
    assert "write" not in perm, (
        "OpenCode routes write/edit/apply-patch through the edit permission"
    )


def test_build_scope_matches_global_file_permission_contract() -> None:
    """The build agent must inherit the same centralized file boundary."""
    global_permission = _load_permission()
    build_permission = _load_build_permission()

    assert "write" not in build_permission, (
        "agent.build must route write/edit/apply-patch through edit"
    )
    expected = {key: global_permission.get(key) for key in FILE_PERMISSION_KEYS}
    actual = {key: build_permission.get(key) for key in FILE_PERMISSION_KEYS}
    assert actual == expected, (
        "agent.build file permissions must match the global centralized "
        f"boundary; expected {expected!r}, got {actual!r}"
    )
    build_external = build_permission.get("external_directory")
    assert isinstance(build_external, dict), (
        "agent.build.permission.external_directory must be an object"
    )
    assert next(iter(build_external.items()), None) == ("*", "deny"), (
        "agent.build.permission.external_directory must keep `*: deny` first"
    )


def test_external_directory_has_star_deny_catchall() -> None:
    """External paths must fail closed before reviewed overrides are applied."""
    block = _external_directory_block()
    assert "*" in block, "external_directory must include a `*` catch-all key"
    assert block["*"] == "deny", (
        "external_directory's `*` catch-all must be `deny`, "
        f"got {block['*']!r}"
    )


def test_external_directory_allows_exactly_expected_prefixes() -> None:
    """The centralized block must allow exactly the reviewed external paths.

    No more, no less. A future regression that adds a new allow rule
    (e.g. `/Users/shawnwilson/**`) or drops one of the mandated ones
    would violate the user mandate and fail this test.
    """
    block = _external_directory_block()
    allowed = _allowed_keys(block)
    assert sorted(allowed) == sorted(ALLOWED_EXTERNAL_PREFIXES), (
        "external_directory allow rules must match the reviewed prefixes.\n"
        f"  expected: {sorted(ALLOWED_EXTERNAL_PREFIXES)}\n"
        f"  got:      {sorted(allowed)}"
    )


def test_external_directory_deny_is_first() -> None:
    """``*: deny`` must be the first external-directory rule.

    opencode permission rules use last-match-wins semantics. The `*: deny`
    catch-all must appear FIRST so that later, more-specific allow rules
    can override it. If any allow rule appears BEFORE `*: deny`, the deny
    has no guard effect.
    """
    block = _external_directory_block()
    keys = list(block.keys())
    assert keys[0] == "*", (
        "external_directory: `*` catch-all must be the first key "
        f"(last-match-wins); got order {keys}"
    )
    assert block[keys[0]] == "deny"


@pytest.mark.parametrize("forbidden", FORBIDDEN_HOME_PATHS)
def test_no_forbidden_home_path_is_allowed(forbidden: str) -> None:
    """No representative forbidden home-directory path may be an allow rule.

    This is the heart of the user mandate: no ~/.ssh, ~/.aws, ~/.gnupg,
    ~/Documents, ~/Desktop, ~/Library, or any broad `/Users/shawnwilson/**`
    rule. Any of these appearing as ``allow`` in the centralized external block
    is a direct violation of the mandate.
    """
    block = _external_directory_block()
    allowed = _allowed_keys(block)
    assert forbidden not in allowed, (
        "external_directory allows forbidden home path "
        f"{forbidden!r} — this violates the no-home-directory-access mandate"
    )


def test_workspace_prefix_is_not_misclassified_as_external() -> None:
    """The active worktree must stay implicit rather than become a host grant."""
    block = _external_directory_block()
    assert WORKSPACE_PREFIX not in block, (
        "the active worktree is internal; adding it to external_directory "
        "couples policy to one checkout and is not a supported workspace grant"
    )


def test_no_broader_home_prefix_than_allowed() -> None:
    """No allow rule may be a broader prefix of /Users/shawnwilson/ than
    the three permitted external ones (.config/opencode/,
    .local/share/opencode/, and .cache/).

    Catches sneaky regressions like `/Users/shawnwilson/**: allow` or
    `/Users/shawnwilson/.config/**: allow` (the latter would re-expose
    ~/.config/gh, ~/.config/git, etc. — only the opencode subdir is allowed).
    """
    permitted_home_prefixes = {
        "/Users/shawnwilson/.config/opencode/**",
        "/Users/shawnwilson/.local/share/opencode/**",
        "/Users/shawnwilson/.cache/**",
    }
    block = _external_directory_block()
    for key, value in block.items():
        if value != "allow":
            continue
        if not key.startswith("/Users/shawnwilson/"):
            continue  # /tmp/** and friends are fine
        assert key in permitted_home_prefixes, (
            f"external_directory allows home path {key!r} which is broader "
            "than the three reviewed external home prefixes — violates the mandate"
        )


def test_no_tmp_subpath_restriction() -> None:
    """The mandate widened /tmp/gludd-* to /tmp/**. Guard against regression
    that re-narrows the tmp allow rule.
    """
    block = _external_directory_block()
    allowed = _allowed_keys(block)
    assert "/tmp/**" in allowed, (
        "external_directory must allow `/tmp/**` (the widened form). "
        f"Got: {allowed}"
    )
    # No narrower /tmp/... rule should be present as an allow.
    for key in allowed:
        if key.startswith("/tmp/") and key != "/tmp/**":
            pytest.fail(
                f"external_directory allows narrowed tmp path {key!r} — "
                "mandate requires the widened `/tmp/**` form"
            )


def test_opencode_config_prefix_present() -> None:
    """The .config/opencode/** prefix must be present in every file tool.

    This is the third leg of the mandate — added 2026-07 to let the agent
    read/write its own config without prompting. Dropping it is a regression.
    """
    block = _external_directory_block()
    expected = "/Users/shawnwilson/.config/opencode/**"
    allowed = _allowed_keys(block)
    assert expected in allowed, (
        f"external_directory must allow {expected!r} per the mandate; got {allowed}"
    )


def test_agents_md_documents_allowed_boundary() -> None:
    """Layer 1 (prompt) pin: AGENTS.md "No External File Access" section
    must name all three allowed prefixes and must NOT reference the old
    `/tmp/gludd-*` only form as the sole tmp rule.
    """
    assert AGENTS_MD.exists(), "AGENTS.md must exist at repo root"
    text = AGENTS_MD.read_text()
    # Locate the section.
    m = re.search(
        r"## CRITICAL: No External File Access(.*?)(?=\n## |\Z)",
        text,
        re.DOTALL,
    )
    assert m, "AGENTS.md must contain '## CRITICAL: No External File Access'"
    section = m.group(1)
    _documented_prefixes = (
        "/Users/shawnwilson/gludd/**",
        "/Users/shawnwilson/.config/opencode/**",
        "/Users/shawnwilson/.local/share/opencode/**",
        "/Users/shawnwilson/.cache/**",
        "/tmp/**",
    )
    for prefix in _documented_prefixes:
        assert prefix in section, f"AGENTS.md No External File Access section must name allowed prefix {prefix!r}"
    # The mandate language must be present.
    assert "NEVER ask for access to the user's full home directory" in text, (
        "AGENTS.md must include the hard user-mandate quote"
    )
    # The forbidden examples must be enumerated.
    for forbidden_example in ("~/.ssh", "~/.aws", "~/.gnupg"):
        assert forbidden_example in section, f"AGENTS.md section must enumerate forbidden example {forbidden_example!r}"


def test_agents_md_lists_structural_test_as_enforcement() -> None:
    """AGENTS.md must name this test file as the Layer 3 enforcement."""
    text = AGENTS_MD.read_text()
    assert "tests/unit/test_no_home_directory_access.py" in text, (
        "AGENTS.md must reference this structural test in the Enforcement subsection of No External File Access"
    )


def test_feature_evidence_names_both_executable_permission_scopes() -> None:
    """Feature evidence must identify both guarded configuration surfaces."""
    documentation = FEATURE_DOC.read_text(encoding="utf-8")

    assert "`permission.external_directory`" in documentation
    assert "`agent.build.permission.external_directory`" in documentation
    assert "tests/unit/test_no_home_directory_access.py" in documentation
