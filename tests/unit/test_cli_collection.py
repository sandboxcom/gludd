"""Unit tests for ``gludd collection versions`` and ``gludd collection activate`` CLI."""

from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

import pytest

from general_ludd.cli_collection import (
    _cmd_collection_activate,
    _cmd_collection_versions,
    add_collection_subparser,
)


def _make_versioned_collection(base: Path, namespace: str, collection: str, version: str) -> Path:
    coll_root = base / "ansible_collections" / f"{namespace}@{version}" / collection
    (coll_root / "roles" / "test_role" / "tasks").mkdir(parents=True)
    (coll_root / "plugins" / "modules").mkdir(parents=True)
    (coll_root / "roles" / "test_role" / "tasks" / "main.yml").write_text(
        f"- name: version {version}\n"
    )
    return coll_root


class TestCollectionVersions:
    def test_lists_versions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        coll_root = tmp_path / "collections"
        coll_root.mkdir()
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.2.0")
        _make_versioned_collection(coll_root, "general_ludd", "agent", "latest")

        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: coll_root,
        )
        args = argparse.Namespace(
            namespace="general_ludd", collection="agent", project_dir=None
        )
        captured = StringIO()
        sys.stdout = captured
        _cmd_collection_versions(args)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "0.2.0" in output
        assert "0.1.0" in output
        assert "latest" in output

    def test_no_versions_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        coll_root = tmp_path / "collections"
        coll_root.mkdir()
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: coll_root,
        )
        args = argparse.Namespace(
            namespace="nope", collection=None, project_dir=None
        )
        captured = StringIO()
        sys.stdout = captured
        _cmd_collection_versions(args)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "No versions found for nope" in output

    def test_versions_without_collection_filter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        coll_root = tmp_path / "collections"
        coll_root.mkdir()
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(coll_root, "general_ludd", "slurm", "1.0.0")
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: coll_root,
        )
        args = argparse.Namespace(
            namespace="general_ludd", collection=None, project_dir=None
        )
        captured = StringIO()
        sys.stdout = captured
        _cmd_collection_versions(args)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "0.1.0" in output
        assert "1.0.0" in output


class TestCollectionActivate:
    def test_activate_specific_version(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        coll_root = tmp_path / "collections"
        coll_root.mkdir()
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.2.0")
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: coll_root,
        )
        args = argparse.Namespace(
            spec="general_ludd.agent@0.1.0", project_dir=None
        )
        captured = StringIO()
        sys.stdout = captured
        _cmd_collection_activate(args)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "Activated general_ludd.agent@0.1.0" in output
        assert "activation root:" in output
        assert "resolved:" in output

    def test_activate_invalid_spec_exits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        coll_root = tmp_path / "collections"
        coll_root.mkdir()
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: coll_root,
        )
        args = argparse.Namespace(spec="invalid", project_dir=None)
        with pytest.raises(SystemExit):
            _cmd_collection_activate(args)


class TestCollectionSubparser:
    def test_subparser_registered(self):
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection"])
        assert ns.command == "collection"

    def test_versions_subcommand(self):
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection", "versions", "general_ludd"])
        assert ns.collection_command == "versions"
        assert ns.namespace == "general_ludd"
        assert ns.collection is None

    def test_versions_with_collection(self):
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection", "versions", "general_ludd", "agent"])
        assert ns.collection == "agent"

    def test_activate_subcommand(self):
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection", "activate", "general_ludd.agent@0.1.0"])
        assert ns.collection_command == "activate"
        assert ns.spec == "general_ludd.agent@0.1.0"
