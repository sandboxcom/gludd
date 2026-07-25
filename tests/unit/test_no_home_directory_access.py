"""Structural pin for the no-home-directory-access user mandate.

The user issued a HARD mandate: "NEVER ask for access to my full home
directory ever again." Access is limited to exactly three path prefixes:

    1. /Users/shawnwilson/gludd/**            (the workspace)
    2. /tmp/**                                (all of /tmp, not just /tmp/gludd-*)
    3. /Users/shawnwilson/.config/opencode/** (opencode config directory)

Every other path under /Users/shawnwilson/ is FORBIDDEN — no ~/.ssh,
~/.aws, ~/.gnupg, ~/Documents, ~/Desktop, ~/Library, etc.

This guardrail is codified at three layers (see AGENTS.md
"CRITICAL: No External File Access"):

    Layer 1 — AGENTS.md prompt section (proactive instruction)
    Layer 2 — opencode.json permission block (hard gate at the harness level)
    Layer 3 — this structural test (regression prevention)

This test pins Layer 2: it verifies that the opencode.json permission block
for each of read/write/edit/glob/grep allows EXACTLY the three prefixes
above, includes a `*: deny` catch-all, and rejects representative
home-directory paths outside the allowed set. If a future edit widens
access (e.g. re-adds `/Users/shawnwilson/**: allow`) or drops the catch-all,
this test fails.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = ROOT / "opencode.json"
AGENTS_MD = ROOT / "AGENTS.md"

# The exhaustive set of allowed path prefixes (the single source of truth).
# Per the user mandate, these are the ONLY three prefixes permitted.
ALLOWED_PREFIXES = (
    "/Users/shawnwilson/gludd/**",
    "/Users/shawnwilson/.config/opencode/**",
    "/tmp/**",
)

# Each of these tools must have a permission block in opencode.json.
FILE_TOOLS = ("read", "write", "edit", "glob", "grep")

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


def _load_permission() -> dict:
    """Load opencode.json and return its `permission` block as a dict."""
    assert OPENCODE_JSON.exists(), "opencode.json must exist at repo root"
    data = json.loads(OPENCODE_JSON.read_text())
    assert "permission" in data, "opencode.json must have a `permission` block"
    perm = data["permission"]
    assert isinstance(perm, dict), "permission block must be an object"
    return perm


def _allowed_keys(block: dict) -> list[str]:
    """Return the keys in a permission block that resolve to `allow`."""
    return [k for k, v in block.items() if v == "allow"]


@pytest.mark.parametrize("tool", FILE_TOOLS)
def test_file_tool_has_permission_block(tool: str) -> None:
    """Every file-touching tool must have its own permission block."""
    perm = _load_permission()
    assert tool in perm, (
        f"tool `{tool}` must have a permission block in opencode.json"
    )
    assert isinstance(perm[tool], dict), (
        f"permission block for `{tool}` must be an object"
    )


@pytest.mark.parametrize("tool", FILE_TOOLS)
def test_file_tool_has_star_deny_catchall(tool: str) -> None:
    """Each file-tool block MUST end with `*: deny` (last-match-wins).

    Without the catch-all, an allow rule earlier in the block has no effect
    because opencode would fall through to its default (which may be allow).
    The catch-all is what makes the three-prefix allowlist actually binding.
    """
    perm = _load_permission()
    block = perm[tool]
    assert "*" in block, (
        f"`{tool}` permission block must include a `*` catch-all key"
    )
    assert block["*"] == "deny", (
        f"`{tool}` permission block's `*` catch-all must be `deny`, "
        f"got {block['*']!r}"
    )


@pytest.mark.parametrize("tool", FILE_TOOLS)
def test_file_tool_allows_exactly_three_prefixes(tool: str) -> None:
    """Each file-tool block must allow EXACTLY the three mandated prefixes.

    No more, no less. A future regression that adds a fourth allow rule
    (e.g. `/Users/shawnwilson/**`) or drops one of the three would violate
    the user mandate and fail this test.
    """
    perm = _load_permission()
    block = perm[tool]
    allowed = _allowed_keys(block)
    assert sorted(allowed) == sorted(ALLOWED_PREFIXES), (
        f"`{tool}` allow rules must be exactly the three mandated prefixes.\n"
        f"  expected: {sorted(ALLOWED_PREFIXES)}\n"
        f"  got:      {sorted(allowed)}"
    )


@pytest.mark.parametrize("tool", FILE_TOOLS)
def test_file_tool_deny_is_last(tool: str) -> None:
    """`*: deny` must be the LAST key in each file-tool block.

    opencode permission rules use last-match-wins semantics. If any `allow`
    rule appears AFTER `*: deny`, the deny has no effect for that path.
    """
    perm = _load_permission()
    block = perm[tool]
    keys = list(block.keys())
    assert keys[-1] == "*", (
        f"`{tool}` block: `*` catch-all must be the last key "
        f"(last-match-wins); got order {keys}"
    )
    assert block[keys[-1]] == "deny"


@pytest.mark.parametrize("forbidden", FORBIDDEN_HOME_PATHS)
@pytest.mark.parametrize("tool", FILE_TOOLS)
def test_no_forbidden_home_path_is_allowed(forbidden: str, tool: str) -> None:
    """No representative forbidden home-directory path may be an allow rule.

    This is the heart of the user mandate: no ~/.ssh, ~/.aws, ~/.gnupg,
    ~/Documents, ~/Desktop, ~/Library, or any broad `/Users/shawnwilson/**`
    rule. Any of these appearing as `allow` in a file-tool block is a direct
    violation of the mandate.
    """
    perm = _load_permission()
    block = perm[tool]
    allowed = _allowed_keys(block)
    assert forbidden not in allowed, (
        f"`{tool}` permission block allows forbidden home path "
        f"{forbidden!r} — this violates the no-home-directory-access mandate"
    )


def test_no_broader_home_prefix_than_allowed() -> None:
    """No allow rule may be a broader prefix of /Users/shawnwilson/ than
    the two permitted ones (gludd/ and .config/opencode/).

    Catches sneaky regressions like `/Users/shawnwilson/**: allow` or
    `/Users/shawnwilson/.config/**: allow` (the latter would re-expose
    ~/.config/gh, ~/.config/git, etc. — only the opencode subdir is allowed).
    """
    # Any allow key starting with /Users/shawnwilson/ MUST be one of the two
    # permitted prefixes (gludd/** or .config/opencode/**).
    permitted_home_prefixes = {
        "/Users/shawnwilson/gludd/**",
        "/Users/shawnwilson/.config/opencode/**",
    }
    perm = _load_permission()
    for tool in FILE_TOOLS:
        block = perm[tool]
        for key, value in block.items():
            if value != "allow":
                continue
            if not key.startswith("/Users/shawnwilson/"):
                continue  # /tmp/** and friends are fine
            assert key in permitted_home_prefixes, (
                f"`{tool}` allows home path {key!r} which is broader than "
                f"the two permitted prefixes — violates the mandate"
            )


def test_no_tmp_subpath_restriction() -> None:
    """The mandate widened /tmp/gludd-* to /tmp/**. Guard against regression
    that re-narrows the tmp allow rule.
    """
    perm = _load_permission()
    for tool in FILE_TOOLS:
        block = perm[tool]
        allowed = _allowed_keys(block)
        assert "/tmp/**" in allowed, (
            f"`{tool}` must allow `/tmp/**` (the widened form). "
            f"Got: {allowed}"
        )
        # No narrower /tmp/... rule should be present as an allow.
        for key in allowed:
            if key.startswith("/tmp/") and key != "/tmp/**":
                pytest.fail(
                    f"`{tool}` allows narrowed tmp path {key!r} — "
                    f"mandate requires the widened `/tmp/**` form"
                )


def test_opencode_config_prefix_present() -> None:
    """The .config/opencode/** prefix must be present in every file tool.

    This is the third leg of the mandate — added 2026-07 to let the agent
    read/write its own config without prompting. Dropping it is a regression.
    """
    perm = _load_permission()
    expected = "/Users/shawnwilson/.config/opencode/**"
    for tool in FILE_TOOLS:
        block = perm[tool]
        allowed = _allowed_keys(block)
        assert expected in allowed, (
            f"`{tool}` must allow {expected!r} per the mandate; "
            f"got {allowed}"
        )


def test_agents_md_documents_three_prefixes() -> None:
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
    for prefix in ALLOWED_PREFIXES:
        assert prefix in section, (
            f"AGENTS.md No External File Access section must name allowed "
            f"prefix {prefix!r}"
        )
    # The mandate language must be present.
    assert "NEVER ask for access to the user's full home directory" in text, (
        "AGENTS.md must include the hard user-mandate quote"
    )
    # The forbidden examples must be enumerated.
    for forbidden_example in ("~/.ssh", "~/.aws", "~/.gnupg"):
        assert forbidden_example in section, (
            f"AGENTS.md section must enumerate forbidden example "
            f"{forbidden_example!r}"
        )


def test_agents_md_lists_structural_test_as_enforcement() -> None:
    """AGENTS.md must name this test file as the Layer 3 enforcement."""
    text = AGENTS_MD.read_text()
    assert "tests/unit/test_no_home_directory_access.py" in text, (
        "AGENTS.md must reference this structural test in the Enforcement "
        "subsection of No External File Access"
    )
