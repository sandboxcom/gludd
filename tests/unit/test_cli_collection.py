"""Unit tests for ``gludd collection versions`` and ``gludd collection activate`` CLI."""

from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.cli_collection import (
    _cmd_collection_activate,
    _cmd_collection_versions,
    _find_base,
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
    def test_lists_versions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_no_versions_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_versions_without_collection_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
    def test_activate_specific_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_activate_invalid_spec_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coll_root = tmp_path / "collections"
        coll_root.mkdir()
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: coll_root,
        )
        args = argparse.Namespace(spec="invalid", project_dir=None)
        with pytest.raises(SystemExit):
            _cmd_collection_activate(args)

    def test_activate_latest_uses_next_existing_tier(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        missing = tmp_path / "missing"
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        attempts: list[Path] = []

        monkeypatch.setattr(
            "general_ludd.cli_collection.resolve_collections_paths",
            lambda **_kwargs: [
                SimpleNamespace(path=missing),
                SimpleNamespace(path=first),
                SimpleNamespace(path=second),
            ],
        )

        def _activate(path: Path, **kwargs: object) -> tuple[Path, None]:
            attempts.append(path)
            assert kwargs["version"] is None
            if path == first:
                raise FileNotFoundError("not in first tier")
            return tmp_path / "active", None

        monkeypatch.setattr(
            "general_ludd.cli_collection.activate_collection_version", _activate
        )

        _cmd_collection_activate(
            argparse.Namespace(spec="general_ludd.agent", project_dir=str(tmp_path))
        )

        assert attempts == [first, second]
        assert "Activated general_ludd.agent@(latest)" in capsys.readouterr().out

    def test_activate_propagates_last_missing_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        collections = tmp_path / "collections"
        collections.mkdir()
        monkeypatch.setattr(
            "general_ludd.cli_collection.resolve_collections_paths",
            lambda **_kwargs: [SimpleNamespace(path=collections)],
        )
        monkeypatch.setattr(
            "general_ludd.cli_collection.activate_collection_version",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                FileNotFoundError("version unavailable")
            ),
        )

        with pytest.raises(FileNotFoundError, match="version unavailable"):
            _cmd_collection_activate(
                argparse.Namespace(
                    spec="general_ludd.agent@9.9.9", project_dir=None
                )
            )

    def test_activate_without_collection_tier_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.cli_collection.resolve_collections_paths",
            lambda **_kwargs: [SimpleNamespace(path=tmp_path / "missing")],
        )

        with pytest.raises(FileNotFoundError, match="No collections directory"):
            _cmd_collection_activate(
                argparse.Namespace(spec="general_ludd.agent@1.0.0", project_dir=None)
            )


class TestCollectionBaseResolution:
    def test_find_base_uses_first_existing_tier_and_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = tmp_path / "existing"
        existing.mkdir()
        observed: list[Path | None] = []

        def _paths(*, project_root: Path | None) -> list[SimpleNamespace]:
            observed.append(project_root)
            return [
                SimpleNamespace(path=tmp_path / "missing"),
                SimpleNamespace(path=existing),
            ]

        monkeypatch.setattr(
            "general_ludd.cli_collection.resolve_collections_paths", _paths
        )

        assert _find_base(str(tmp_path)) == existing
        assert observed == [tmp_path]

    def test_find_base_without_existing_tier_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.cli_collection.resolve_collections_paths",
            lambda **_kwargs: [SimpleNamespace(path=tmp_path / "missing")],
        )

        with pytest.raises(FileNotFoundError, match="No collections directory"):
            _find_base(None)


def test_versions_empty_collection_label_and_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    collections = tmp_path / "collections"
    collections.mkdir()
    observed: list[Path | None] = []

    def _paths(*, project_root: Path | None) -> list[SimpleNamespace]:
        observed.append(project_root)
        return [SimpleNamespace(path=collections)]

    monkeypatch.setattr(
        "general_ludd.cli_collection.resolve_collections_paths", _paths
    )

    _cmd_collection_versions(
        argparse.Namespace(
            namespace="general_ludd",
            collection="agent",
            project_dir=str(tmp_path),
        )
    )

    assert observed == [tmp_path]
    assert "No versions found for general_ludd.agent" in capsys.readouterr().out


class TestCollectionSubparser:
    def test_subparser_registered(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection"])
        assert ns.command == "collection"

    def test_versions_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection", "versions", "general_ludd"])
        assert ns.collection_command == "versions"
        assert ns.namespace == "general_ludd"
        assert ns.collection is None

    def test_versions_with_collection(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection", "versions", "general_ludd", "agent"])
        assert ns.collection == "agent"

    def test_activate_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        add_collection_subparser(sub)

        ns = top.parse_args(["collection", "activate", "general_ludd.agent@0.1.0"])
        assert ns.collection_command == "activate"
        assert ns.spec == "general_ludd.agent@0.1.0"
