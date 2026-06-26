"""Guards for gludd's curated MCP server catalog (_KNOWN_SERVERS).

Covers the newly-onboarded codebase-memory server and the supply-chain
invariant that every npm/uvx-family launch command is version-pinned (the
launch-time gate in transport refuses unpinned specs, so an unpinned entry here
would be a fail-closed runtime break).
"""

from __future__ import annotations

from general_ludd.mcp.catalog import _KNOWN_SERVERS, MCPCatalog

_REMOTE_FETCH_LAUNCHERS = {"npx", "npm", "pnpm", "yarn", "bunx", "uvx"}


def test_codebase_memory_entry_present_and_well_formed() -> None:
    cat = MCPCatalog()
    entry = cat.get_server("codebase-memory")
    assert entry is not None
    assert entry.server_name == "codebase-memory"
    assert entry.display_name == "Codebase Memory"
    # Launches the real DeusData package, pinned.
    assert entry.command == ["npx", "-y", "codebase-memory-mcp@0.8.1"]
    # Structural analysis — no API key / secret required.
    assert entry.env_aliases_needed == []
    assert "codebase" in entry.tags


def test_codebase_memory_listed_in_known_servers() -> None:
    cat = MCPCatalog()
    names = {e.server_name for e in cat.get_known_servers()}
    assert "codebase-memory" in names


def test_all_known_server_commands_are_version_pinned() -> None:
    # Every catalogued command that uses a remote-fetch launcher must pin an
    # explicit @<version> on its package spec (no bare name, no @latest).
    for name, entry in _KNOWN_SERVERS.items():
        cmd = entry.command
        if not cmd or cmd[0] not in _REMOTE_FETCH_LAUNCHERS:
            continue
        spec = cmd[-1]
        # Strip a leading scope '@', then a remaining '@' marks the version pin.
        assert "@" in spec.lstrip("@"), f"{name}: command spec {spec!r} is not version-pinned"
        version = spec.rsplit("@", 1)[-1]
        assert version and version.lower() != "latest", f"{name}: spec {spec!r} pins {version!r}"


def test_known_server_names_match_their_keys() -> None:
    for key, entry in _KNOWN_SERVERS.items():
        assert entry.server_name == key
