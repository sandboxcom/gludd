"""Schema-conformance tests for opencode.json.

opencode.json is validated against https://opencode.ai/config.json. The Config
type declares `additionalProperties: false`, which means ANY top-level key not
listed in the schema is invalid and will be silently dropped (or worse, break
plugin loading). The most common regression is an agent inventing a top-level
key like `env` or `settings` that the harness ignores.

This test catches that class of bug at gate time. The allowed top-level key
allowlist is sourced from the live schema and re-recorded here so the test runs
offline. If the schema grows new keys, append them to ALLOWED_TOP_LEVEL_KEYS.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = ROOT / "opencode.json"

# Sourced from https://opencode.ai/config.json ($defs.Config.properties).
# When the schema is refreshed, diff this list against the new `properties`.
ALLOWED_TOP_LEVEL_KEYS = {
    "$schema",
    "shell",
    "logLevel",
    "server",
    "command",
    "skills",
    "references",
    "reference",
    "watcher",
    "snapshot",
    "plugin",
    "share",
    "autoshare",
    "autoupdate",
    "disabled_providers",
    "enabled_providers",
    "model",
    "small_model",
    "default_agent",
    "username",
    "mode",
    "agent",
    "provider",
    "mcp",
    "formatter",
    "lsp",
    "instructions",
    "layout",
    "permission",
    "tools",
    "attachment",
    "enterprise",
    "tool_output",
    "compaction",
    "experimental",
}


class TestOpencodeJsonSchema:
    def test_opencode_json_parses(self) -> None:
        assert OPENCODE_JSON.exists(), "opencode.json must exist"
        json.loads(OPENCODE_JSON.read_text())

    def test_opencode_json_top_level_keys_are_schema_allowed(self) -> None:
        """Every top-level key must appear in the opencode config schema.

        Regression guard: agents have invented top-level keys like `env` (which
        opencode silently ignores, breaking any plugin relying on the value).
        The schema sets additionalProperties: false, so unknown keys are invalid.
        """
        cfg = json.loads(OPENCODE_JSON.read_text())
        actual = set(cfg.keys())
        unknown = actual - ALLOWED_TOP_LEVEL_KEYS
        assert not unknown, (
            f"opencode.json has top-level key(s) not in the opencode schema: "
            f"{sorted(unknown)}. The opencode Config type sets "
            f"additionalProperties: false — these keys are silently dropped "
            f"and any plugin relying on them is broken. See "
            f"https://opencode.ai/config.json $defs.Config.properties for the "
            f"allowed list. (If the schema grew, extend "
            f"ALLOWED_TOP_LEVEL_KEYS in this test.)"
        )

    def test_known_bad_keys_are_rejected(self) -> None:
        """Direct regression: an `env` top-level key MUST fail the check.

        This pins the specific incident that motivated the test: an agent added
        `"env": {...}` at the opencode.json top level expecting plugins to read
        it; opencode ignored the key (additionalProperties: false) and every
        plugin that needed the env value failed silently.
        """
        bad_cfg = {"env": {"CLAUDE_AGENT_FLOOR": "10"}}
        unknown = set(bad_cfg.keys()) - ALLOWED_TOP_LEVEL_KEYS
        assert "env" in unknown, (
            "`env` MUST be classified as an unknown top-level key. If opencode "
            "ever adds a top-level `env`, update ALLOWED_TOP_LEVEL_KEYS — but "
            "until then it is the canonical regression."
        )

    def test_current_config_has_no_env_top_level(self) -> None:
        """The live opencode.json must NOT have an `env` top-level key.

        This is the narrow assertion that would have caught the 2026-06-28
        breakage directly. It is redundant with the schema test above but is
        kept as a loud, named regression marker.
        """
        cfg = json.loads(OPENCODE_JSON.read_text())
        assert "env" not in cfg, (
            "opencode.json has a top-level `env` key — opencode ignores it "
            "(Config additionalProperties: false). Move the env values into "
            "the plugin(s) that need them via `env:` on the dispatch call or "
            "into the shell environment that launches opencode."
        )
