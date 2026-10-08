"""Unit tests for cli_ornith utility functions."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from general_ludd.cli_ornith import (
    _perm_spec_has_ornith,
    _psk_headers,
)


class TestPskHeaders:
    def test_has_content_type(self):
        headers = _psk_headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    def test_no_psk_no_auth(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = _psk_headers()
        assert "Authorization" not in headers

    def test_psk_sets_bearer_token(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": "secret123"}, clear=True):
            headers = _psk_headers()
        assert headers["Authorization"] == "Bearer secret123"


class TestPermSpecHasOrnith:
    def test_agent_ornith_yml_file(self, tmp_path: Path):
        (tmp_path / "agent-ornith.yml").write_text("")
        assert _perm_spec_has_ornith(tmp_path) is True

    def test_not_a_dir(self, tmp_path: Path):
        spec_path = tmp_path / "nonexistent_dir"
        assert _perm_spec_has_ornith(spec_path) is False

    def test_yaml_with_principal_field(self, tmp_path: Path):
        import yaml
        (tmp_path / "spec.yml").write_text(
            yaml.dump({"principal": "agent:ornith", "permissions": []})
        )
        assert _perm_spec_has_ornith(tmp_path) is True

    def test_yaml_with_actor_field(self, tmp_path: Path):
        import yaml
        (tmp_path / "spec.yml").write_text(
            yaml.dump({"actor": "agent:ornith", "capabilities": []})
        )
        assert _perm_spec_has_ornith(tmp_path) is True

    def test_yaml_with_nested_principal(self, tmp_path: Path):
        import yaml
        (tmp_path / "spec.yml").write_text(
            yaml.dump({"permissions": [{"principal": "agent:ornith", "allow": ["*"]}]})
        )
        assert _perm_spec_has_ornith(tmp_path) is True

    def test_yaml_without_ornith(self, tmp_path: Path):
        import yaml
        (tmp_path / "spec.yml").write_text(
            yaml.dump({"principal": "human:admin", "permissions": []})
        )
        assert _perm_spec_has_ornith(tmp_path) is False

    def test_empty_dir(self, tmp_path: Path):
        assert _perm_spec_has_ornith(tmp_path) is False
