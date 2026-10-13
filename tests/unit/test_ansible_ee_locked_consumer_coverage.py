"""Behavioral failure controls for measured native EE consumer boundaries."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, cast

import pytest
from scripts import ansible_runtime_artifacts as owner


@pytest.mark.parametrize("payload", [b"", b"exactly8", b"oversize9"])
def test_native_result_reader_preserves_size_admission_and_descriptor_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes,
) -> None:
    """Exercise one native descriptor owner for both IID and context reports."""
    path = tmp_path / "owned-native-result"
    path.write_bytes(payload)
    close = os.close
    closed: list[int] = []

    def record(descriptor: int) -> None:
        closed.append(descriptor)
        close(descriptor)

    monkeypatch.setattr(os, "close", record)
    if len(payload) == 8:
        assert owner._read_owned_native_result(path, 8, "fixture result") == payload
    else:
        with pytest.raises(ValueError, match="fixture result must be a bounded owned regular file"):
            owner._read_owned_native_result(path, 8, "fixture result")
    assert len(closed) == 1


def test_native_result_growth_after_stat_is_rejected_and_descriptor_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent size change cannot weaken either native result's bound."""
    path = tmp_path / "owned-native-result"
    path.write_bytes(b"exactly8")
    native_fdopen, native_close = os.fdopen, os.close
    closed: list[int] = []

    def grow(descriptor: int, mode: str, *, closefd: bool) -> BinaryIO:
        assert mode == "rb" and closefd is False
        path.write_bytes(b"oversize9")
        return cast(BinaryIO, native_fdopen(descriptor, mode, closefd=closefd))

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        native_close(descriptor)

    monkeypatch.setattr(os, "fdopen", grow)
    monkeypatch.setattr(os, "close", close)
    with pytest.raises(ValueError, match="fixture result grew beyond its admitted bound"):
        owner._read_owned_native_result(path, 8, "fixture result")
    assert len(closed) == 1 and path.read_bytes() == b"oversize9"


@pytest.mark.parametrize("defect", ["valid", "empty", "large", "hardlink", "fifo", "symlink", "short", "encoding"])
def test_built_image_identity_is_owned_bounded_and_closes_every_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    """Read actual filesystem controls; invalid results never become image proof."""
    path = tmp_path / "built-image-id"
    image = "sha256:" + "a" * 64
    data = {"empty": b"", "large": b"x" * 81, "short": b"sha256:" + b"a" * 63, "encoding": b"\xff"}
    if defect == "fifo":
        os.mkfifo(path)
    elif defect == "symlink":
        target = tmp_path / "foreign-image-id"
        target.write_text(image, encoding="ascii")
        path.symlink_to(target)
    else:
        path.write_bytes(data.get(defect, image.encode() + b"\n"))
        if defect == "hardlink":
            os.link(path, tmp_path / "second-owner")
    acquired = path.lstat()
    native_close = os.close
    closed: list[int] = []

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        native_close(descriptor)

    monkeypatch.setattr(os, "close", close)
    if defect == "valid":
        assert owner._built_image_id(path) == image
    else:
        with pytest.raises((OSError, ValueError)):
            owner._built_image_id(path)
    assert len(closed) == (0 if defect == "symlink" else 1)
    assert path.lstat().st_ino == acquired.st_ino


@pytest.mark.parametrize("defect", ["regular", "missing", "file-root", "root-symlink", "entry-symlink", "fifo"])
def test_context_inventory_binds_regular_bytes_and_refuses_aliases_or_devices(
    tmp_path: Path, defect: str,
) -> None:
    context = tmp_path / "gludd-context"
    if defect == "missing":
        pass
    elif defect == "file-root":
        context.write_bytes(b"not a directory")
    elif defect == "root-symlink":
        destination = tmp_path / "foreign-context"
        destination.mkdir()
        context.symlink_to(destination, target_is_directory=True)
    else:
        context.mkdir()
        if defect == "entry-symlink":
            (context / "linked-input").symlink_to(tmp_path / "absent-input")
        elif defect == "fifo":
            os.mkfifo(context / "pipe")
        else:
            (context / "_build").mkdir()
            (context / "_build/input.txt").write_bytes(b"reviewed bytes\n")
    if defect == "regular":
        assert owner._context_file_hashes(context) == {
            "_build/input.txt": "sha256:" + hashlib.sha256(b"reviewed bytes\n").hexdigest(),
        }
    else:
        with pytest.raises(ValueError, match="native context"):
            owner._context_file_hashes(context)


@pytest.mark.parametrize("defect", ["missing", "empty", "large", "hardlink", "fifo", "symlink", "json", "shape"])
def test_worker_report_must_be_bounded_owned_regular_data_before_consumption(
    tmp_path: Path, defect: str,
) -> None:
    report = tmp_path / "native-context.json"
    data = {"empty": b"", "large": b"x" * 65537, "json": b"{"}
    valid_bytes = json.dumps({"command": [], "files": {}, "extra": "unreviewed"}).encode()
    if defect == "missing":
        pass
    elif defect == "fifo":
        os.mkfifo(report)
    elif defect == "symlink":
        target = tmp_path / "foreign-report"
        target.write_bytes(valid_bytes)
        report.symlink_to(target)
    else:
        report.write_bytes(data.get(defect, valid_bytes))
        if defect == "hardlink":
            os.link(report, tmp_path / "second-owner")
    inputs: owner._EEContextInputs = {
        "definition": tmp_path / "never-read-definition",
        "requirements": {"collections": []},
        "entries": [],
        "controller_version": "2.19.14",
    }
    context = tmp_path / "never-built-context"
    with pytest.raises((OSError, ValueError)):
        owner._admit_native_context(tmp_path, context, inputs, "docker", "gludd:test", tmp_path / "image-id")
    assert not context.exists()
    assert not inputs["definition"].exists()


class _DecliningBuilder:
    @property
    def build_command(self) -> list[str]:
        return []

    def create(self) -> bool:
        return False


def test_native_false_create_fails_and_restores_diagnostic_handler_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = cast(owner._NativeDefinitions, import_module("ansible_builder.user_definition"))
    assert native._tempfiles == [] and native.UserDefinition.get_dep_abs_path.cache_info().currsize == 0
    main = ModuleType("ansible_builder.main")

    def factory(**_values: object) -> _DecliningBuilder:
        return _DecliningBuilder()

    main.__dict__["AnsibleBuilder"] = factory
    real_import = import_module
    monkeypatch.setattr(owner, "import_module",
                        lambda name: main if name == "ansible_builder.main" else real_import(name))
    logger = logging.getLogger("ansible_builder")
    handlers = tuple(logger.handlers)
    output = tmp_path / "never-admitted-command.json"
    with pytest.raises(ValueError, match="did not create"):
        owner._native_create_context(tmp_path / "definition.yml", tmp_path / "gludd-context", "docker",
                                     "gludd:test", tmp_path / "image-id", output)
    assert tuple(logger.handlers) == handlers
    assert native._tempfiles == []
    assert not output.exists()


def test_native_creation_failure_and_lost_registry_preserve_foreign_file_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = cast(owner._NativeDefinitions, import_module("ansible_builder.user_definition"))
    original = native._tempfiles
    assert original == [] and native.UserDefinition.get_dep_abs_path.cache_info().currsize == 0
    main = ModuleType("ansible_builder.main")
    foreign = (tmp_path / "foreign-owned-file").open("w", encoding="utf-8")

    def factory(**_values: object) -> _DecliningBuilder:
        native._tempfiles = [foreign]
        return _DecliningBuilder()

    main.__dict__["AnsibleBuilder"] = factory
    real_import = import_module
    monkeypatch.setattr(owner, "import_module",
                        lambda name: main if name == "ansible_builder.main" else real_import(name))
    try:
        with pytest.raises(BaseExceptionGroup) as captured:
            owner._native_create_context(tmp_path / "definition.yml", tmp_path / "gludd-context", "docker",
                                         "gludd:test", tmp_path / "image-id", tmp_path / "command.json")
        assert len(captured.value.exceptions) == 2
        assert "did not create" in str(captured.value.exceptions[0])
        assert "exclusive ownership was lost" in str(captured.value.exceptions[1])
        assert not foreign.closed and native._tempfiles == [foreign]
        assert original == []
        assert not (tmp_path / "command.json").exists()
    finally:
        native._tempfiles = original
        foreign.close()


@pytest.mark.parametrize("operation,defect,status", [
    ("prepare", "configuration", 1), ("prepare", "builder", 2),
    ("build", "configuration", 1), ("build", "builder", 2),
    ("build", "runtime", 2), ("build", "artifacts", 2), ("build", "base-image", 1),
])
def test_consumer_preconditions_fail_before_context_or_runtime_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    operation: str, defect: str, status: int,
) -> None:
    monkeypatch.setattr(owner, "validate_files", lambda: ["invalid reviewed EE configuration"]
                        if defect == "configuration" else [])
    monkeypatch.setattr(owner, "find_spec", lambda _name: None if defect == "builder" else object())
    monkeypatch.setattr(shutil, "which", lambda _name: None if defect == "runtime" else "/available/runtime")

    def base_image() -> str:
        assert defect == "base-image"
        raise ValueError("pinned base unavailable")

    monkeypatch.setattr(owner, "check_configured_base_image", base_image)
    monkeypatch.setattr(owner, "_prepared_native_context", lambda *_args: pytest.fail("context acquired after refusal"))
    context = tmp_path / "gludd-never-built"
    artifacts = tmp_path / "never-read-artifacts"
    result = (owner.prepare_environment_context("docker", "gludd:test", context, artifacts, False)
              if operation == "prepare" else owner.build_environment(
                  "docker", "gludd:test", context, False, None if defect == "artifacts" else artifacts,
              ))
    assert result == status
    assert capsys.readouterr().err
    assert not context.exists() and not artifacts.exists()


@pytest.mark.parametrize("missing", ["--native-context-definition", "--native-context-output", "--native-image-id"])
def test_native_worker_cli_requires_each_explicit_owner_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], missing: str,
) -> None:
    monkeypatch.setattr(owner, "_native_create_context", lambda *_args: pytest.fail("incomplete worker spawned"))
    arguments = ["native-create-context", "--context", str(tmp_path / "gludd-context")]
    for flag in ("--native-context-definition", "--native-context-output", "--native-image-id"):
        if flag != missing:
            arguments.extend((flag, str(tmp_path / flag.removeprefix("--"))))
    with pytest.raises(SystemExit) as captured:
        owner.main(arguments)
    assert captured.value.code == 2
    assert "requires the existing owner's explicit input/output paths" in capsys.readouterr().err
