"""Require complete offline controller warm-up before EE build acceptance."""

from __future__ import annotations

import argparse
import ast
import errno
import hashlib
import importlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import ansible_runtime_artifacts as artifacts

PINNED_IMAGE = "registry.example/gludd-ee:beta4@sha256:" + "b" * 64
CANDIDATE_IMAGE = "gludd-ee:beta4"
BUILT_IMAGE = "sha256:" + "c" * 64
OWNER_PID = 41234
CONTROLLER_IMPORTS = (
    "ansible",
    "ansible_runner",
    "azure.identity",
    "azure.monitor.query",
    "frictionless",
    "jsonpointer",
    "openapi_core",
    "searx.webapp",
    "signxml",
)


def _record_commands(
    monkeypatch: pytest.MonkeyPatch,
    return_codes: tuple[int, ...],
    *,
    runtime: str = "podman",
    iid_contents: bytes | None = (BUILT_IMAGE + "\n").encode("ascii"),
    iid_kind: str = "regular",
) -> tuple[list[list[str]], list[str]]:
    calls: list[list[str]] = []
    events: list[str] = []
    statuses = iter(return_codes)
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(artifacts, "find_spec", lambda _name: object())
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(artifacts, "check_configured_base_image", lambda: PINNED_IMAGE)
    monkeypatch.setattr(os, "getpid", lambda: OWNER_PID)

    def collections() -> int:
        events.append("collections")
        return 0

    def run(
        command: list[str],
        *,
        cwd: Path | None = None,
        check: bool = False,
    ) -> SimpleNamespace:
        assert check is False
        calls.append(command)
        returncode = next(statuses)
        if command[:4] == [sys.executable, "-m", "ansible_builder", "build"]:
            assert cwd == artifacts.ROOT
            events.append("builder")
            capture = next(
                (
                    arg.partition("=")[2]
                    for arg in command
                    if arg.startswith("--extra-build-cli-args=")
                ),
                None,
            )
            if capture is not None and returncode == 0:
                forwarded = shlex.split(capture)
                assert len(forwarded) == 2 and forwarded[0] == "--iidfile"
                image_id_path = Path(forwarded[1])
                project = hashlib.sha256(str(artifacts.ROOT).encode()).hexdigest()[:12]
                assert image_id_path.parent.name.startswith(
                    f"gludd-ansible-ee-{project}-{OWNER_PID}-"
                )
                private = image_id_path.parent.stat()
                assert private.st_uid == os.getuid()
                assert private.st_mode & 0o777 == 0o700
                if iid_kind == "directory":
                    image_id_path.mkdir()
                elif iid_kind == "fifo":
                    os.mkfifo(image_id_path)
                elif iid_contents is not None:
                    if iid_kind in {"symlink", "hardlink"}:
                        target = image_id_path.parent / "other-image-id"
                        target.write_bytes(iid_contents)
                        if iid_kind == "symlink":
                            image_id_path.symlink_to(target)
                        else:
                            os.link(target, image_id_path)
                    else:
                        assert iid_kind == "regular"
                        image_id_path.write_bytes(iid_contents)
        elif command[:3] == [runtime, "image", "inspect"]:
            assert cwd is None
            events.append("inspect")
        else:
            assert cwd is None
            events.append("opentofu" if command[-2:] == ["/usr/local/bin/tofu", "version"] else "python")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(artifacts, "_build_collection_artifacts", collections)
    monkeypatch.setattr(subprocess, "run", run)
    return calls, events


def _invoke(mode: str, context: Path) -> int:
    if mode == "build":
        return artifacts.build_environment("podman", CANDIDATE_IMAGE, context, False)
    return artifacts.verify_environment("podman", PINNED_IMAGE, False)


def _container_names(calls: list[list[str]]) -> list[str]:
    return [command[command.index("--name") + 1] for command in calls]


@pytest.mark.parametrize("mode", ["build", "verify"])
def test_acceptance_warms_all_controller_imports_with_owned_offline_containers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    calls, events = _record_commands(monkeypatch, (0, 0, 0))
    assert _invoke(mode, tmp_path / "context") == 0
    assert events == (
        ["collections", "builder", "opentofu", "python"]
        if mode == "build"
        else ["inspect", "opentofu", "python"]
    )
    assert len(calls) == 3
    image = BUILT_IMAGE if mode == "build" else PINNED_IMAGE
    project = hashlib.sha256(str(artifacts.ROOT).encode()).hexdigest()[:12]
    smokes = calls[1:]
    assert _container_names(smokes) == [
        f"gludd-ansible-ee-{project}-{OWNER_PID}-opentofu",
        f"gludd-ansible-ee-{project}-{OWNER_PID}-python",
    ]
    for command in smokes:
        assert command[:3] == ["podman", "run", "--rm"]
        assert "--network=none" in command
        assert "--pull=never" in command
        assert CANDIDATE_IMAGE not in command
        assert command.count(image) == 1
    assert smokes[0][-2:] == ["/usr/local/bin/tofu", "version"]
    assert smokes[1][-3:-1] == ["/usr/bin/python3.11", "-c"]
    smoke_tree = ast.parse(smokes[1][-1])
    controller_statements = [
        node
        for node in smoke_tree.body
        if isinstance(node, ast.Import)
        and tuple(alias.name for alias in node.names) == CONTROLLER_IMPORTS
    ]
    assert len(controller_statements) == 1
    assert isinstance(smoke_tree.body[-1], ast.Expr)
    assert ast.unparse(smoke_tree.body[-1]) == "print('ANSIBLE_EE_IMPORT_OK')"
    assert tuple(artifacts.EXPECTED_CONTROLLER_IMPORTS) == CONTROLLER_IMPORTS


@pytest.mark.parametrize("runtime", ["podman", "docker"])
def test_builder_streams_native_progress_at_supported_verbosity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime: str,
) -> None:
    calls, _events = _record_commands(monkeypatch, (0, 0, 0), runtime=runtime)
    assert artifacts.build_environment(runtime, CANDIDATE_IMAGE, tmp_path / "context", False) == 0
    builder_cli = importlib.import_module("ansible_builder.cli")
    monkeypatch.setattr(sys, "argv", ["ansible-builder", *calls[0][3:]])
    parsed: object = builder_cli.parse_args()
    assert isinstance(parsed, argparse.Namespace)
    assert parsed.verbosity == 3
    assert parsed.container_runtime == runtime


@pytest.mark.parametrize("mode", ["build", "verify"])
@pytest.mark.parametrize(
    ("return_codes", "expected_status", "expected_tail"),
    [
        ((0, 23), 23, ["opentofu"]),
        ((0, 0, 29), 29, ["opentofu", "python"]),
    ],
)
def test_each_warm_smoke_failure_rejects_acceptance_and_stops_remaining_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    return_codes: tuple[int, ...],
    expected_status: int,
    expected_tail: list[str],
) -> None:
    calls, events = _record_commands(monkeypatch, return_codes)
    assert _invoke(mode, tmp_path / "context") == expected_status
    prefix = ["collections", "builder"] if mode == "build" else ["inspect"]
    assert events == prefix + expected_tail
    assert len(calls) == 1 + len(expected_tail)


@pytest.mark.parametrize("mode", ["build", "verify"])
def test_failed_builder_or_inspect_never_starts_warm_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    calls, events = _record_commands(monkeypatch, (37,))
    assert _invoke(mode, tmp_path / "context") == 37
    assert len(calls) == 1
    assert events == (["collections", "builder"] if mode == "build" else ["inspect"])


@pytest.mark.parametrize("mode", ["build", "verify"])
def test_smoke_consumes_validator_interpreter_source_of_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    calls, _events = _record_commands(monkeypatch, (0, 0, 0))
    dependencies = dict(artifacts.EXPECTED_DEPENDENCIES)
    dependencies["python_interpreter"] = {
        "package_system": "python3.11",
        "python_path": "/validator-owned/python3.11",
    }
    monkeypatch.setattr(artifacts, "EXPECTED_DEPENDENCIES", dependencies)
    assert _invoke(mode, tmp_path / "context") == 0
    assert calls[-1][-3] == "/validator-owned/python3.11"


def test_container_namespace_is_distinct_for_each_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _events = _record_commands(monkeypatch, (0,) * 6)
    roots = (tmp_path / "first-project", tmp_path / "second-project")
    for root in roots:
        monkeypatch.setattr(artifacts, "ROOT", root)
        assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 0
    first_names = _container_names(calls[1:3])
    second_names = _container_names(calls[4:6])
    assert set(first_names).isdisjoint(second_names)
    for root, names in zip(roots, (first_names, second_names), strict=True):
        project = hashlib.sha256(str(root).encode()).hexdigest()[:12]
        assert names == [
            f"gludd-ansible-ee-{project}-{OWNER_PID}-opentofu",
            f"gludd-ansible-ee-{project}-{OWNER_PID}-python",
        ]


@pytest.mark.parametrize("runtime", ["podman", "docker"])
def test_build_warms_captured_image_after_candidate_tag_can_be_retargeted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime: str,
) -> None:
    calls, events = _record_commands(monkeypatch, (0, 0, 0), runtime=runtime)
    assert artifacts.build_environment(
        runtime, CANDIDATE_IMAGE, tmp_path / "context", False
    ) == 0
    assert events == ["collections", "builder", "opentofu", "python"]
    assert calls[0][calls[0].index("--tag") + 1] == CANDIDATE_IMAGE
    for command in calls[1:]:
        assert command[0] == runtime
        assert BUILT_IMAGE in command
        assert CANDIDATE_IMAGE not in command
        assert "--pull=never" in command


@pytest.mark.parametrize(
    "contents",
    [
        None,
        b"",
        b"sha256:short\n",
        b"sha256:" + b"C" * 64 + b"\n",
        b"sha512:" + b"c" * 128 + b"\n",
        b"\xff" * 71,
        (BUILT_IMAGE + "\n" + " " * 81).encode("ascii"),
        (BUILT_IMAGE + "\n" + BUILT_IMAGE + "\n").encode("ascii"),
    ],
    ids=["missing", "empty", "short", "uppercase", "foreign", "non-ascii",
         "oversized-padding", "multiple-identities"],
)
def test_missing_malformed_or_oversized_builder_identity_stops_before_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contents: bytes | None,
) -> None:
    calls, events = _record_commands(
        monkeypatch, (0, 0, 0), iid_contents=contents
    )
    assert artifacts.build_environment(
        "podman", CANDIDATE_IMAGE, tmp_path / "context", False
    ) == 1
    assert events == ["collections", "builder"]
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_builder_identity_requires_an_owned_regular_single_link_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    calls, events = _record_commands(monkeypatch, (0, 0, 0), iid_kind=kind)
    assert artifacts.build_environment(
        "podman", CANDIDATE_IMAGE, tmp_path / "context", False
    ) == 1
    assert events == ["collections", "builder"]
    assert len(calls) == 1


def test_builder_capture_is_private_unique_and_preserves_literal_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_root = tmp_path / "image captures"
    capture_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(capture_root))
    calls, _events = _record_commands(monkeypatch, (0,) * 6)
    for _attempt in range(2):
        assert artifacts.build_environment(
            "podman", CANDIDATE_IMAGE, tmp_path / "context", False
        ) == 0
    captures = [
        Path(shlex.split(arg.partition("=")[2])[1])
        for command in calls
        for arg in command
        if arg.startswith("--extra-build-cli-args=")
    ]
    assert len(captures) == 2
    assert captures[0].parent != captures[1].parent
    assert all(path.parent.parent == capture_root for path in captures)
    assert all(not path.parent.exists() for path in captures)


@pytest.mark.parametrize("validate_only", [False, True])
def test_input_refusal_or_plan_never_captures_an_image_or_starts_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_only: bool,
) -> None:
    calls, events = _record_commands(monkeypatch, (0, 0, 0))
    if not validate_only:
        monkeypatch.setattr(artifacts, "validate_files", lambda: ["invalid inputs"])
    context = tmp_path / "context"
    assert artifacts.build_environment(
        "podman", CANDIDATE_IMAGE, context, validate_only
    ) == (0 if validate_only else 1)
    assert calls == []
    assert events == []
    assert not context.exists()


@pytest.mark.parametrize("image", [BUILT_IMAGE, CANDIDATE_IMAGE])
def test_verify_keeps_registry_digest_guard_before_inspection(
    monkeypatch: pytest.MonkeyPatch,
    image: str,
) -> None:
    calls, events = _record_commands(monkeypatch, (0, 0, 0))
    assert artifacts.verify_environment("podman", image, False) == 2
    assert calls == []
    assert events == []


def test_image_id_descriptor_is_reclaimed_if_wrapper_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id = tmp_path / "built-image-id"
    image_id.write_text(BUILT_IMAGE + "\n", encoding="ascii")
    opened: list[int] = []
    real_open = os.open

    def capture_open(path: Path, flags: int) -> int:
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        return descriptor

    def fail_wrapper(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic wrapper construction failure")

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(os, "fdopen", fail_wrapper)
    with pytest.raises(OSError, match="synthetic wrapper construction failure"):
        artifacts._built_image_id(image_id)
    assert len(opened) == 1
    try:
        os.fstat(opened[0])
    except OSError as error:
        assert error.errno == errno.EBADF
    else:
        os.close(opened[0])
        pytest.fail("the raw image-ID descriptor leaked after wrapper failure")
