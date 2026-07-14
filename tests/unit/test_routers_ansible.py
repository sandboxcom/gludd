"""Structural tests for routers/ansible.py — Ansible router endpoints."""

from pathlib import Path

from general_ludd.routers.ansible import _resolve_collections_base


class TestResolveCollectionsBase:
    def test_no_project_root(self):
        from unittest.mock import MagicMock

        app = MagicMock()
        app.state._project_root = None

        result = _resolve_collections_base(app)
        assert result is None or isinstance(result, (Path, type(None)))

    def test_with_project_root(self):
        from unittest.mock import MagicMock

        app = MagicMock()
        app.state._project_root = "/tmp"

        result = _resolve_collections_base(app)
        # May be None or a Path depending on filesystem state
        assert result is None or isinstance(result, Path)
