"""Unit tests for the RoleCloner (server-side stream dispatch artifact builder).

Hermetic: no network, no daemon, no ansible invocation. All filesystem ops
land under pytest's tmp_path. The cloner lives at ``general_ludd.stream``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.stream import RoleCloner


@pytest.fixture()
def collection_root(tmp_path: Path) -> Path:
    """Build a synthetic collection root containing ``roles/foo/``."""
    root = tmp_path / "collection"
    foo_tasks = root / "roles" / "foo" / "tasks"
    foo_defaults = root / "roles" / "foo" / "defaults"
    foo_tasks.mkdir(parents=True)
    foo_defaults.mkdir(parents=True)
    (foo_tasks / "main.yml").write_text("- name: noop\n  ansible.builtin.debug:\n    msg: hi\n")
    (foo_defaults / "main.yml").write_text("foo_default: bar\n")
    return root


class TestClone:
    def test_clone_copies_role_files(self, collection_root: Path, tmp_path: Path) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        clone_path = cloner.clone("foo", overrides={})
        assert clone_path.is_dir()
        assert (clone_path / "tasks" / "main.yml").is_file()
        assert (clone_path / "defaults" / "main.yml").is_file()

    def test_clone_overrides_json_written(self, collection_root: Path, tmp_path: Path) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        overrides = {"stream_id": "abc", "chunk_index": 3}
        clone_path = cloner.clone("foo", overrides=overrides)
        import json

        data = json.loads((clone_path / "clone-overrides.json").read_text())
        assert data["stream_id"] == "abc"
        assert data["chunk_index"] == 3

    def test_clone_wrapper_playbook_includes_role_with_vars(
        self, collection_root: Path, tmp_path: Path
    ) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        overrides = {"stream_id": "sid-1"}
        clone_path = cloner.clone("foo", overrides=overrides)
        wrapper = clone_path / "run-clone.yml"
        assert wrapper.is_file()
        text = wrapper.read_text()
        assert "foo" in text  # role name referenced
        assert "vars_files" in text or "vars:" in text
        assert "clone-overrides.json" in text

    def test_clone_unknown_role_raises(self, collection_root: Path, tmp_path: Path) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        with pytest.raises(FileNotFoundError):
            cloner.clone("does_not_exist", overrides={})


class TestCloneConfinement:
    """Path-traversal confinement (defense in depth behind the router's own
    identifier validation). ``RoleCloner.clone`` must never copytree from
    outside ``collection_root/roles/``, whether the escape is via a literal
    ``..`` role_name or a symlink planted under ``roles/``."""

    def test_clone_dotdot_role_name_raises_value_error(
        self, collection_root: Path, tmp_path: Path
    ) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        with pytest.raises(ValueError):
            cloner.clone("../../etc", overrides={})
        assert not work_root.exists() or not any(work_root.iterdir())

    def test_clone_dotdot_role_name_never_calls_copytree(
        self, collection_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        calls: list[tuple[object, ...]] = []

        def _fake_copytree(*a: object, **k: object) -> Path:
            calls.append(a)
            return Path(str(a[1]))

        monkeypatch.setattr(
            shutil, "copytree", _fake_copytree
        )
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        with pytest.raises(ValueError):
            cloner.clone("../../etc", overrides={})
        assert calls == []

    def test_clone_symlink_escape_raises_value_error(
        self, collection_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # roles/evil -> a directory outside collection_root/roles entirely.
        outside = tmp_path / "outside-target"
        outside.mkdir()
        (outside / "secret.txt").write_text("nope")
        evil_link = collection_root / "roles" / "evil"
        evil_link.symlink_to(outside, target_is_directory=True)

        import shutil

        calls: list[tuple[object, ...]] = []

        def _fake_copytree2(*a: object, **k: object) -> Path:
            calls.append(a)
            return Path(str(a[1]))

        monkeypatch.setattr(
            shutil, "copytree", _fake_copytree2
        )
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        with pytest.raises(ValueError):
            cloner.clone("evil", overrides={})
        assert calls == []
        assert not work_root.exists() or not any(work_root.iterdir())


class TestMaterializeProcessor:
    def test_materialize_processor_whisper(
        self, collection_root: Path, tmp_path: Path
    ) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        clone_path = cloner.clone("foo", overrides={})
        proc = {"tool": "whisper.cpp", "binary": "/usr/local/bin/whisper-cli"}
        out = cloner.materialize_processor(clone_path, processor=proc)
        assert out.is_file()
        text = out.read_text()
        assert "whisper" in text
        assert "$CHUNK_PATH" in text

    def test_materialize_processor_ffmpeg(
        self, collection_root: Path, tmp_path: Path
    ) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        clone_path = cloner.clone("foo", overrides={})
        proc = {"tool": "ffmpeg", "binary": "/usr/bin/ffmpeg"}
        out = cloner.materialize_processor(clone_path, processor=proc)
        assert out.is_file()
        text = out.read_text()
        assert "ffmpeg" in text
        assert "$CHUNK_PATH" in text

    def test_materialize_processor_agent_creates_wait_playbook(
        self, collection_root: Path, tmp_path: Path
    ) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        clone_path = cloner.clone("foo", overrides={})
        proc = {"tool": "agent"}
        out = cloner.materialize_processor(clone_path, processor=proc)
        assert out.is_file()
        assert out.suffix == ".yml"
        text = out.read_text()
        assert "wait-for-agent-result" in text or "poll" in text.lower()

    def test_materialize_processor_unknown_tool_raises(
        self, collection_root: Path, tmp_path: Path
    ) -> None:
        work_root = tmp_path / "clones"
        cloner = RoleCloner(collection_root=collection_root, work_root=work_root)
        clone_path = cloner.clone("foo", overrides={})
        with pytest.raises(ValueError):
            cloner.materialize_processor(clone_path, processor={"tool": "bogus"})
