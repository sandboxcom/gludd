"""Tests for MCP loader: config file loading with size cap (C-4)."""

from __future__ import annotations

import pytest

from general_ludd.mcp.loader import _MCP_CONFIG_MAX_BYTES, load_mcp_config


class TestLoadMcpConfigMissingFile:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = load_mcp_config(str(tmp_path / "nonexistent.yaml"))
        assert result == {}


class TestLoadMcpConfigNormalLoad:
    def test_loads_valid_config(self, tmp_path):
        config_file = tmp_path / "mcp.yaml"
        config_file.write_text(
            "servers:\n"
            "  my-server:\n"
            "    server_id: my-server\n"
            "    command: [npx, -y, some-pkg@1.0.0]\n"
        )
        result = load_mcp_config(str(config_file))
        assert "my-server" in result
        assert result["my-server"].server_id == "my-server"

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        result = load_mcp_config(str(config_file))
        assert result == {}

    def test_no_servers_key_returns_empty_dict(self, tmp_path):
        config_file = tmp_path / "no_servers.yaml"
        config_file.write_text("other_key: value\n")
        result = load_mcp_config(str(config_file))
        assert result == {}


class TestLoadMcpConfigSizeCap:
    def test_oversized_config_raises_value_error(self, tmp_path):
        config_file = tmp_path / "huge.yaml"
        # Write a file just over the cap
        config_file.write_bytes(b"x" * (_MCP_CONFIG_MAX_BYTES + 1))
        with pytest.raises(ValueError, match="too large"):
            load_mcp_config(str(config_file))

    def test_at_cap_passes(self, tmp_path):
        config_file = tmp_path / "at_cap.yaml"
        # A file exactly at the cap must not raise; content won't be valid YAML
        # for server parsing but must not hit the size guard.
        config_file.write_bytes(b" " * _MCP_CONFIG_MAX_BYTES)
        # Should not raise ValueError for size; yaml loads as None → {}
        result = load_mcp_config(str(config_file))
        assert result == {}

    def test_size_cap_constant_is_reasonable(self):
        # 512 KB sanity check
        assert _MCP_CONFIG_MAX_BYTES == 512 * 1024
