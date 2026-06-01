"""Tests for MCP server credential resolution from Vault/OpenBao.

Ensures that MCP server credentials (API keys, tokens) are resolved from
the secrets resolver at runtime, NOT stored in YAML config files.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.mcp.config import MCPServerConfig


class TestMCPSecretResolution:
    def test_env_aliases_field_exists(self):
        cfg = MCPServerConfig(
            server_id="test",
            command=["echo"],
            env_aliases={"GITHUB_TOKEN": "github_mcp_token"},
        )
        assert cfg.env_aliases == {"GITHUB_TOKEN": "github_mcp_token"}

    def test_env_aliases_defaults_empty(self):
        cfg = MCPServerConfig(server_id="test", command=["echo"])
        assert cfg.env_aliases == {}

    def test_resolve_env_aliases_from_secrets_manager(self):
        from general_ludd.mcp.secrets import resolve_mcp_env

        secrets_mgr = MagicMock()
        secrets_mgr.resolve.side_effect = lambda alias: {
            "github_mcp_token": "ghp_abc123",
            "slack_bot_token": "xoxb-xyz",
        }.get(alias)

        cfg = MCPServerConfig(
            server_id="github",
            command=["npx", "-y", "@mcp/server-github"],
            env_aliases={
                "GITHUB_PERSONAL_ACCESS_TOKEN": "github_mcp_token",
            },
        )

        resolved = resolve_mcp_env(cfg, secrets_mgr)
        assert resolved["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_abc123"

    def test_resolve_env_aliases_unresolved_skipped(self):
        from general_ludd.mcp.secrets import resolve_mcp_env

        secrets_mgr = MagicMock()
        secrets_mgr.resolve.return_value = None

        cfg = MCPServerConfig(
            server_id="test",
            command=["echo"],
            env_aliases={"API_KEY": "missing_alias"},
        )

        resolved = resolve_mcp_env(cfg, secrets_mgr)
        assert "API_KEY" not in resolved

    def test_resolve_merges_static_env_with_resolved(self):
        from general_ludd.mcp.secrets import resolve_mcp_env

        secrets_mgr = MagicMock()
        secrets_mgr.resolve.side_effect = lambda alias: {
            "secret_token": "tok_123",
        }.get(alias)

        cfg = MCPServerConfig(
            server_id="test",
            command=["echo"],
            env={"STATIC_VAR": "static_val"},
            env_aliases={"SECRET_TOKEN": "secret_token"},
        )

        resolved = resolve_mcp_env(cfg, secrets_mgr)
        assert resolved["STATIC_VAR"] == "static_val"
        assert resolved["SECRET_TOKEN"] == "tok_123"

    def test_resolve_with_no_aliases_returns_static_env(self):
        from general_ludd.mcp.secrets import resolve_mcp_env

        secrets_mgr = MagicMock()
        cfg = MCPServerConfig(
            server_id="test",
            command=["echo"],
            env={"FOO": "bar"},
        )

        resolved = resolve_mcp_env(cfg, secrets_mgr)
        assert resolved == {"FOO": "bar"}
        secrets_mgr.resolve.assert_not_called()

    def test_yaml_config_uses_env_aliases_not_plaintext(self, tmp_path):
        import yaml

        from general_ludd.mcp.loader import load_mcp_config

        config_file = tmp_path / "mcp.yml"
        config_file.write_text(yaml.dump({
            "servers": {
                "github": {
                    "command": ["npx", "-y", "@mcp/server-github"],
                    "env_aliases": {
                        "GITHUB_PERSONAL_ACCESS_TOKEN": "github_mcp_token",
                    },
                    "enabled": True,
                },
            },
        }))

        configs = load_mcp_config(str(config_file))
        cfg = configs["github"]
        assert cfg.env_aliases["GITHUB_PERSONAL_ACCESS_TOKEN"] == "github_mcp_token"
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in cfg.env


class TestMCPSecretScrubbing:
    def test_scrub_plaintext_secrets_from_mcp_config(self, tmp_path):
        from general_ludd.mcp.secrets import scrub_mcp_config

        config_file = tmp_path / "mcp.yml"
        config_file.write_text(
            'servers:\n'
            '  github:\n'
            '    command: ["npx", "-y", "@mcp/server-github"]\n'
            '    env:\n'
            '      GITHUB_PERSONAL_ACCESS_TOKEN: ghp_plaintext-secret-here\n'
            '    enabled: true\n'
        )

        scrubbed = scrub_mcp_config(config_file)
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in scrubbed

        content = config_file.read_text()
        assert "ghp_plaintext-secret-here" not in content

    def test_scrub_preserves_safe_config(self, tmp_path):
        from general_ludd.mcp.secrets import scrub_mcp_config

        config_file = tmp_path / "mcp.yml"
        config_file.write_text(
            'servers:\n'
            '  filesystem:\n'
            '    command: ["npx", "-y", "@mcp/server-filesystem"]\n'
            '    args: ["/tmp"]\n'
            '    enabled: true\n'
        )

        scrubbed = scrub_mcp_config(config_file)
        assert scrubbed == []
        assert "filesystem" in config_file.read_text()
