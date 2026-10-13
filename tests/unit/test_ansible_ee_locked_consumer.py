"""Fail-closed native EE context admission; fixtures are metadata-only archives."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shlex
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from threading import Event, Thread
from types import ModuleType
from typing import IO, cast

import pytest
import yaml
from scripts import ansible_runtime_artifacts as owner


def archive(root: Path, name: str, version: str, dependencies: dict[str, str]) -> owner.CollectionArtifactEntry:
    namespace, collection = name.split(".")
    output = root / f"{namespace}-{collection}-{version}.tar.gz"
    document = {"collection_info": {"namespace": namespace, "name": collection,
                                  "version": version, "dependencies": dependencies}}
    contents = {"MANIFEST.json": json.dumps(document).encode(),
                "meta/runtime.yml": b"requires_ansible: '>=2.19'\n"}
    with tarfile.open(output, "w:gz") as package:
        for filename, data in contents.items():
            entry = tarfile.TarInfo(filename)
            entry.size = len(data)
            package.addfile(entry, io.BytesIO(data))
    return {"name": name, "version": version, "artifact": output.name,
            "origin": "https://galaxy.ansible.com/", "size": output.stat().st_size,
            "sha256": "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()}


@pytest.fixture
def supplied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Bind synthetic native metadata bytes independently, never claim install proof."""
    configuration = tmp_path / "configuration"
    configuration.mkdir()
    inputs = {name: configuration / path.name for name, path in owner.INPUTS.items()}
    for name, path in owner.INPUTS.items():
        shutil.copyfile(path, inputs[name])
    manifest = configuration / "runtime-lock.json"
    shutil.copyfile(owner.LOCK, manifest)
    root = tmp_path / "archives"
    root.mkdir()
    frozen = cast(dict[str, object], json.loads(inputs["collection_lock"].read_text()))
    entries = cast(list[owner.CollectionArtifactEntry], frozen["collections"])
    dependencies = {"community.general": {"community.library_inventory_filtering_v1": ">=1.0.0"},
                    "community.windows": {"ansible.windows": ">=3.0.0,<4.0.0"}}
    admitted = [archive(root, entry["name"], entry["version"], dependencies.get(entry["name"], {}))
                for entry in entries]
    inputs["collection_lock"].write_text(json.dumps({"schema_version": 1, "collections": admitted}))
    runtime = cast(dict[str, object], json.loads(manifest.read_text()))
    hashes = cast(dict[str, str], runtime["inputs"])
    hashes["collection_lock"] = "sha256:" + hashlib.sha256(inputs["collection_lock"].read_bytes()).hexdigest()
    manifest.write_text(json.dumps(runtime))
    gludd = tmp_path / "gludd-artifacts"
    gludd.mkdir()
    artifacts = []
    for name, version in owner._COLLECTION_VERSIONS:
        entry = archive(gludd, f"general_ludd.{name}", version, {})
        artifacts.append((tmp_path / name, gludd / entry["artifact"]))
    monkeypatch.setattr(owner, "INPUTS", inputs)
    monkeypatch.setattr(owner, "DEFINITION", inputs["definition"])
    monkeypatch.setattr(owner, "LOCK", manifest)
    monkeypatch.setattr(owner, "COLLECTION_ARTIFACTS", tuple(artifacts))
    monkeypatch.setattr(owner, "_build_collection_artifacts", lambda: 0)
    monkeypatch.setattr(owner, "validate_files", lambda: [])
    for name in ("ANSIBLE_CONFIG", "ANSIBLE_COLLECTIONS_PATH", "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"):
        monkeypatch.delenv(name, raising=False)
    return root


def test_plan_does_not_read_archives_or_create_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(owner, "validate_files", lambda: [])
    monkeypatch.setattr(owner, "find_spec", lambda _name: pytest.fail("plan checked native tools"))
    context = tmp_path / "gludd-context"
    assert owner.prepare_environment_context("docker", "gludd:test", context, tmp_path / "absent", True) == 0
    assert not context.exists()
    assert "proof=none" in capsys.readouterr().out


@pytest.mark.parametrize("runtime,filename", [("docker", "Dockerfile"), ("podman", "Containerfile")])
def test_native_create_consumes_sixteen_local_metadata_fixtures_once(
    supplied: Path, tmp_path: Path, runtime: str, filename: str,
) -> None:
    context = tmp_path / f"gludd-{runtime}-context"
    assert owner.prepare_environment_context(runtime, "gludd:test", context, supplied, False) == 0
    requirements = cast(dict[str, object], yaml.safe_load((context / "_build/requirements.yml").read_text()))
    entries = cast(list[dict[str, str]], requirements["collections"])
    original = cast(dict[str, object], yaml.safe_load(owner.INPUTS["galaxy"].read_text()))
    local = [entry for entry in cast(list[dict[str, str]], original["collections"]) if entry.get("type") == "file"]
    assert len(entries) == 16 and entries[8:] == local
    assert all(entry["type"] == "file" and not Path(entry["name"]).is_absolute() for entry in entries)
    assert not requirements.get("roles")
    instructions = (context / filename).read_text()
    assert 'ARG ANSIBLE_GALAXY_CLI_COLLECTION_OPTS="--offline --no-cache"' in instructions
    assert "COPY --from=galaxy /usr/share/ansible /usr/share/ansible" in instructions
    assert "/output/scripts/introspect.py introspect" in instructions
    assert "--no-deps" not in instructions


@pytest.mark.parametrize("defect", ["tamper", "missing", "symlink", "root-symlink", "lock", "ambient"])
def test_archive_and_binding_defects_fail_before_native_producer(
    supplied: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    path = supplied / "community-general-13.5.0.tar.gz"
    if defect == "tamper":
        path.write_bytes(path.read_bytes() + b"changed")
    elif defect == "missing":
        path.unlink()
    elif defect == "symlink":
        copied = supplied / "moved.tar.gz"
        path.rename(copied)
        path.symlink_to(copied)
    elif defect == "root-symlink":
        linked = tmp_path / "linked-artifacts"
        linked.symlink_to(supplied, target_is_directory=True)
        supplied = linked
    elif defect == "lock":
        path = owner.INPUTS["collection_lock"]
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/ambient/collections")
    monkeypatch.setattr(owner, "_build_collection_artifacts",
                        lambda: pytest.fail("producer ran after admission defect"))
    assert owner.prepare_environment_context("docker", "gludd:test", tmp_path / "gludd-context", supplied, False) == 1


@pytest.mark.parametrize("defect", ["existing", "symlink", "unowned-name", "parent-symlink"])
def test_context_defects_do_not_mutate_existing_paths(supplied: Path, tmp_path: Path, defect: str) -> None:
    context = tmp_path / "gludd-context"
    if defect == "existing":
        context.mkdir()
        (context / "sentinel").write_text("retain")
    elif defect == "symlink":
        destination = tmp_path / "retained"
        destination.mkdir()
        context.symlink_to(destination, target_is_directory=True)
    elif defect == "unowned-name":
        context = tmp_path / "ordinary-directory"
    else:
        destination = tmp_path / "retained"
        destination.mkdir()
        linked = tmp_path / "linked"
        linked.symlink_to(destination, target_is_directory=True)
        context = linked / "gludd-context"
    assert owner.prepare_environment_context("docker", "gludd:test", context, supplied, False) == 1
    if defect == "existing":
        assert (context / "sentinel").read_text() == "retain"


class Handle:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.closed = False
        self.failure = failure

    def close(self) -> None:
        self.closed = True
        if self.failure is not None:
            raise self.failure


def test_cleanup_reports_every_close_failure_and_preserves_original_exception() -> None:
    first, second, last = Handle(OSError("first close")), Handle(OSError("second close")), Handle()
    resources: list[owner._NativeHandle] = [first, second, last]
    original = ValueError("original create")
    with pytest.raises(BaseExceptionGroup) as captured:
        owner._close_native_builder_resources(resources, original)
    assert captured.value.exceptions == (original, first.failure, second.failure)
    assert all(handle.closed for handle in (first, second, last))
    assert resources == [first, second]


def test_cleanup_closes_owned_handles_and_reraises_original() -> None:
    handle = Handle()
    resources: list[owner._NativeHandle] = [handle]
    original = ValueError("original")
    with pytest.raises(ValueError) as captured:
        owner._close_native_builder_resources(resources, original)
    assert captured.value is original and handle.closed and resources == []


def test_concurrent_foreign_native_owner_is_never_closed(tmp_path: Path) -> None:
    registry = cast(list[owner._NativeHandle], import_module("ansible_builder.user_definition")._tempfiles)
    assert not registry
    ready, finish = Event(), Event()
    foreign = Handle()

    def other_owner() -> None:
        registry.append(foreign)
        ready.set()
        finish.wait(10)

    thread = Thread(target=other_owner, name="gludd-native-foreign-owner")
    thread.start()
    assert ready.wait(10)
    try:
        with pytest.raises(ValueError, match="exclusive"):
            owner._native_create_context(tmp_path / "definition.yml", tmp_path / "gludd-context", "docker",
                                         "gludd:test", tmp_path / "iid", tmp_path / "command.json")
        assert not foreign.closed
    finally:
        finish.set()
        thread.join(10)
        registry.remove(foreign)


def test_cli_requires_explicit_artifact_root_for_real_preparation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        owner.main(["prepare-context", "--context", str(tmp_path / "gludd-context")])


def test_cli_plan_uses_literal_artifact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(owner, "validate_files", lambda: [])
    path = str(tmp_path / 'archives " literal $value')
    assert owner.main(["prepare-context", "--artifact-root", path, "--validate-only"]) == 0
    assert path in capsys.readouterr().out


@pytest.mark.parametrize("defect", ["command", "requirements", "instruction", "archive", "extra-file"])
def test_worker_context_and_command_tamper_blocks_runtime(
    supplied: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    original = owner._run_collection_command
    context = tmp_path / "gludd-context"

    def corrupt(command: list[str], log: Path, *, cwd: Path, environment: Mapping[str, str],
                max_seconds: int) -> int:
        result = original(command, log, cwd=cwd, environment=environment, max_seconds=max_seconds)
        report = Path(command[command.index("--native-context-output") + 1])
        if defect == "command":
            data = cast(dict[str, object], json.loads(report.read_text()))
            data["command"] = ["docker", "build", "unreviewed"]
            report.write_text(json.dumps(data))
        else:
            paths = {"requirements": context / "_build/requirements.yml", "instruction": context / "Dockerfile",
                     "archive": context / "_build/collections/third-party/community-general-13.5.0.tar.gz",
                     "extra-file": context / "unreviewed-file"}
            target = paths[defect]
            target.write_bytes((target.read_bytes() if target.exists() else b"") + b"tamper")
        return result

    monkeypatch.setattr(owner, "_run_collection_command", corrupt)
    assert owner.prepare_environment_context("docker", "gludd:test", context, supplied, False) == 1
    assert not context.exists()


@pytest.mark.parametrize("build_status", [0, 19])
def test_build_uses_native_command_and_exact_immutable_identity(
    supplied: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_status: int,
) -> None:
    native_run = subprocess.run
    builds: list[list[str]] = []
    smoke: list[tuple[str, str]] = []
    immutable = "sha256:" + "c" * 64
    monkeypatch.setattr(owner, "check_configured_base_image", lambda: "pinned-base")
    monkeypatch.setattr(shutil, "which", lambda name: f"/available/{name}")
    def warm(runtime: str, image: str) -> int:
        smoke.append((runtime, image))
        return 0

    monkeypatch.setattr(owner, "_smoke_environment", warm)

    def run(command: list[str], *, cwd: Path, check: bool,
            env: dict[str, str] | None = None, timeout: int | None = None,
            stdout: IO[str] | None = None, stderr: int | None = None) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["docker", "build"]:
            builds.append(command)
            image_id = Path(command[command.index("--iidfile") + 1])
            image_id.write_text(immutable)
            return subprocess.CompletedProcess(command, build_status, "", "")
        return native_run(command, cwd=cwd, check=check, env=env, timeout=timeout,
                          stdout=stdout, stderr=stderr, text=True)

    monkeypatch.setattr(subprocess, "run", run)
    context = tmp_path / "gludd-context"
    assert owner.build_environment("docker", "gludd:test", context, False, supplied) == build_status
    assert len(builds) == 1
    assert builds[0][:6] == ["docker", "build", "-f", str(context / "Dockerfile"), "-t", "gludd:test"]
    assert smoke == ([] if build_status else [("docker", immutable)])
    assert not Path(builds[0][builds[0].index("--iidfile") + 1]).exists()


@pytest.mark.parametrize("target", ["build-ansible-execution-environment",
                                      "prepare-ansible-execution-environment-context"])
def test_make_transports_all_documented_values_as_literal_data(tmp_path: Path, target: str) -> None:
    capture = tmp_path / "capture.py"
    capture.write_text("import json, os, sys\nprint(json.dumps({'args': sys.argv[1:], 'docker': "
                       "[os.environ.get('DOCKER_CONFIG'), os.environ.get('DOCKER_HOST')]}))\n")
    root = Path(__file__).resolve().parents[2]
    fixture = tmp_path / "Makefile"
    fixture.write_text(f"include {root / 'make/00-foundation.mk'}\n")
    payload = 'gludd path "quoted" $value `literal` $(shell false) ; & |'
    values = {"ANSIBLE_EE_VALIDATE_ONLY": "1", "ANSIBLE_EE_RUNTIME": "docker",
              "ANSIBLE_EE_IMAGE": payload, "ANSIBLE_EE_CONTEXT": payload, "ANSIBLE_EE_ARTIFACT_ROOT": payload,
              "ANSIBLE_EE_DOCKER_CONFIG": payload, "ANSIBLE_EE_DOCKER_HOST": payload}
    command = ["make", "--no-print-directory", "-f", str(fixture), target,
               f"UV={sys.executable} {capture}", *(f"{name}={value}" for name, value in values.items())]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    data = cast(dict[str, object], json.loads(result.stdout))
    args = cast(list[str], data["args"])
    for flag in ("--image", "--context", "--artifact-root"):
        assert args[args.index(flag) + 1] == payload
    assert args[args.index("--runtime") + 1] == "docker"
    assert "--validate-only" in args
    assert data["docker"] == [payload, payload]


@pytest.mark.parametrize("value", ["0; false", "invalid", ""])
def test_make_rejects_invalid_validation_flag_without_invoking_owner(tmp_path: Path, value: str) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = tmp_path / "Makefile"
    fixture.write_text(f"include {root / 'make/00-foundation.mk'}\n")
    result = subprocess.run(["make", "--no-print-directory", "-f", str(fixture),
                             "prepare-ansible-execution-environment-context", f"ANSIBLE_EE_VALIDATE_ONLY={value}",
                             "ANSIBLE_EE_RUNTIME=docker", "ANSIBLE_EE_IMAGE=gludd:test",
                             f"ANSIBLE_EE_CONTEXT={tmp_path / 'gludd-literal-context'}",
                             "ANSIBLE_EE_ARTIFACT_ROOT=missing",
                             "ANSIBLE_EE_DOCKER_CONFIG=", "ANSIBLE_EE_DOCKER_HOST=", "UV=never-run-this-command"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode != 0
    assert "ANSIBLE_EE_VALIDATE_ONLY must be 0 or 1" in result.stdout
    assert "never-run-this-command" not in result.stderr
    assert not (tmp_path / "gludd-literal-context").exists()


def test_initial_native_cache_is_not_borrowed(tmp_path: Path) -> None:
    probe = tmp_path / "cache-owner.py"
    probe.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from scripts import ansible_runtime_artifacts as owner\n"
        "from ansible_builder import user_definition as native\n"
        "assert not native._tempfiles and native.UserDefinition.get_dep_abs_path.cache_info().currsize == 0\n"
        "definition = native.UserDefinition(filename=str(owner.DEFINITION))\n"
        "definition.get_dep_abs_path('python')\n"
        "assert native.UserDefinition.get_dep_abs_path.cache_info().currsize == 1\n"
        "try:\n"
        "    owner._native_create_context(Path('definition'), Path('gludd-context'), 'docker', 'gludd:test', "
        "Path('iid'), Path('command'))\n"
        "except ValueError as error:\n"
        "    assert 'exclusive' in str(error)\n"
        "else:\n"
        "    raise AssertionError('borrowed another cache owner')\n"
        "assert native.UserDefinition.get_dep_abs_path.cache_info().currsize == 1\n"
        "assert not native._tempfiles\n",
    )
    result = subprocess.run([sys.executable, "-W", "error", str(probe), str(owner.ROOT)],
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("defect", ["constructor", "warning", "cleanup", "registry", "thread"])
def test_native_failures_and_lost_exclusivity_never_close_foreign_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    native = cast(owner._NativeDefinitions, import_module("ansible_builder.user_definition"))
    original_registry = native._tempfiles
    assert original_registry == [] and native.UserDefinition.get_dep_abs_path.cache_info().currsize == 0
    owned = Handle(OSError("fatal close") if defect == "cleanup" else None)
    foreign = Handle()
    finished = Event()
    thread: Thread | None = None

    class Builder:
        @property
        def build_command(self) -> list[str]:
            return []

        def create(self) -> bool:
            nonlocal thread
            if defect == "warning":
                logging.getLogger("ansible_builder").warning("owned native diagnostic")
            elif defect == "registry":
                native._tempfiles = [foreign]
            elif defect == "thread":
                original_registry.append(foreign)
                thread = Thread(target=lambda: finished.wait(10), name="gludd-native-concurrent-owner")
                thread.start()
            return True

    def constructor(**_values: object) -> Builder:
        original_registry.append(owned)
        if defect == "constructor":
            raise ValueError("constructor failure")
        return Builder()

    module = ModuleType("ansible_builder.main")
    module.__dict__["AnsibleBuilder"] = constructor
    real_import = import_module
    monkeypatch.setattr(owner, "import_module",
                        lambda name: module if name == "ansible_builder.main" else real_import(name))
    try:
        with pytest.raises((ValueError, OSError)):
            owner._native_create_context(tmp_path / "definition.yml", tmp_path / "gludd-context", "docker",
                                         "gludd:test", tmp_path / "iid", tmp_path / "command.json")
        assert not foreign.closed
        assert owned.closed is (defect not in {"registry", "thread"})
        assert not (tmp_path / "command.json").exists()
    finally:
        finished.set()
        if thread is not None:
            thread.join(10)
        native._tempfiles = original_registry
        original_registry.clear()


def test_warning_from_successful_worker_is_not_acceptance(
    supplied: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = owner._run_collection_command

    def warning(command: list[str], log: Path, *, cwd: Path, environment: Mapping[str, str],
                max_seconds: int) -> int:
        result = original(command, log, cwd=cwd, environment=environment, max_seconds=max_seconds)
        with log.open("a", encoding="utf-8") as output:
            output.write("WARNING native worker diagnostic at rc=0\n")
        return result

    monkeypatch.setattr(owner, "_run_collection_command", warning)
    context = tmp_path / "gludd-context"
    assert owner.prepare_environment_context("docker", "gludd:test", context, supplied, False) == 1
    assert not context.exists()


@pytest.mark.parametrize("path", ["requirements.txt", "bindep.txt", "smoke/ee-smoke-settings.yml",
                                    "smoke/ee-smoke-limiter.toml", "Dockerfile"])
def test_rehashed_worker_output_cannot_change_canonical_bytes_or_full_steps(
    supplied: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    original = owner._run_collection_command
    context = tmp_path / "gludd-context"

    def corrupt(command: list[str], log: Path, *, cwd: Path, environment: Mapping[str, str],
                max_seconds: int) -> int:
        result = original(command, log, cwd=cwd, environment=environment, max_seconds=max_seconds)
        target = context / path if path == "Dockerfile" else context / "_build" / path
        if path == "Dockerfile":
            original_steps = cast(dict[str, object], yaml.safe_load(owner.DEFINITION.read_text()))
            blocks = cast(dict[str, list[str]], original_steps["additional_build_steps"])
            target.write_text(target.read_text().replace(blocks["append_base"][0], "RUN changed_original_step"))
        else:
            target.write_bytes(target.read_bytes() + b"changed")
        report_path = Path(command[command.index("--native-context-output") + 1])
        report = cast(dict[str, object], json.loads(report_path.read_text()))
        report["files"] = owner._context_file_hashes(context)
        report_path.write_text(json.dumps(report))
        return result

    monkeypatch.setattr(owner, "_run_collection_command", corrupt)
    assert owner.prepare_environment_context("docker", "gludd:test", context, supplied, False) == 1
    assert not context.exists()


def test_context_cleanup_refuses_a_replacement_tree(tmp_path: Path) -> None:
    context = tmp_path / "gludd-context"
    context.mkdir()
    acquired = context.stat()
    retained = tmp_path / "retained"
    context.rename(retained)
    context.mkdir()
    sentinel = context / "sentinel"
    sentinel.write_text("another owner")
    with pytest.raises(ValueError, match="ownership changed"):
        owner._remove_owned_failed_context(context, (acquired.st_dev, acquired.st_ino))
    assert sentinel.read_text() == "another owner"


def test_original_build_defaults_are_additive(supplied: Path, tmp_path: Path) -> None:
    definition = cast(dict[str, object], yaml.safe_load(owner.DEFINITION.read_text()))
    definition["build_arg_defaults"] = {"PKGMGR_PRESERVE_CACHE": "always"}
    owner.DEFINITION.write_text(yaml.safe_dump(definition, sort_keys=False))
    manifest = cast(dict[str, object], json.loads(owner.LOCK.read_text()))
    cast(dict[str, str], manifest["inputs"])["definition"] = owner._sha256(owner.DEFINITION)
    owner.LOCK.write_text(json.dumps(manifest))
    context = tmp_path / "gludd-context"
    assert owner.prepare_environment_context("docker", "gludd:test", context, supplied, False) == 0
    assert 'ARG PKGMGR_PRESERVE_CACHE="always"' in (context / "Dockerfile").read_text()


@pytest.mark.parametrize("restore_conflict", [False, True])
def test_atomic_quarantine_race_preserves_foreign_bytes_and_reports_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_conflict: bool,
) -> None:
    context = tmp_path / "gludd-context"
    context.mkdir()
    acquired = context.stat()
    original_tree = tmp_path / "original-tree"
    rename = owner._rename_collection_generation_no_replace
    first = True

    def replace_between_check_and_rename(source: Path, destination: Path) -> None:
        nonlocal first
        if first:
            first = False
            context.rename(original_tree)
            context.mkdir()
            (context / "foreign-sentinel").write_text("retain foreign bytes")
            rename(source, destination)
            if restore_conflict:
                context.mkdir()
                (context / "second-sentinel").write_text("retain replacement bytes")
        else:
            rename(source, destination)

    monkeypatch.setattr(owner, "_rename_collection_generation_no_replace", replace_between_check_and_rename)
    with pytest.raises((ValueError, BaseExceptionGroup)) as captured:
        owner._remove_owned_failed_context(context, (acquired.st_dev, acquired.st_ino))
    quarantines = list(tmp_path.glob("gludd-ee-context-cleanup-*"))
    assert original_tree.exists()
    if restore_conflict:
        assert isinstance(captured.value, BaseExceptionGroup)
        assert len(quarantines) == 1
        retained = quarantines[0] / "failed-context"
        assert (retained / "foreign-sentinel").read_text() == "retain foreign bytes"
        assert (context / "second-sentinel").read_text() == "retain replacement bytes"
        assert str(quarantines[0]) in str(captured.value.exceptions[0])
    else:
        assert not quarantines
        assert (context / "foreign-sentinel").read_text() == "retain foreign bytes"
        assert "ownership changed" in str(captured.value)


def test_quarantine_rename_failure_cleans_empty_acquisition_and_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "gludd-context"
    context.mkdir()
    acquired = context.stat()
    original = OSError("exclusive rename failed")

    def fail(_source: Path, _destination: Path) -> None:
        raise original

    monkeypatch.setattr(owner, "_rename_collection_generation_no_replace", fail)
    with pytest.raises(OSError) as captured:
        owner._remove_owned_failed_context(context, (acquired.st_dev, acquired.st_ino))
    assert captured.value is original
    assert context.exists()
    assert not list(tmp_path.glob("gludd-ee-context-cleanup-*"))


def test_failed_native_generator_output_is_visible_and_durable_after_teardown(
    supplied: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    logs: list[Path] = []

    def failure(_command: list[str], log: Path, *, cwd: Path, environment: Mapping[str, str],
                max_seconds: int) -> int:
        assert cwd == owner.ROOT and max_seconds == 120
        assert environment["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "false"
        logs.append(log)
        log.write_text("ERROR original native generator diagnostic\n")
        print("ERROR original native generator diagnostic", flush=True)
        return 17

    monkeypatch.setattr(owner, "_run_collection_command", failure)
    context = tmp_path / "gludd-context"
    assert owner.prepare_environment_context("docker", "gludd:test", context, supplied, False) == 1
    output = capsys.readouterr()
    assert "original native generator diagnostic" in output.out
    assert "ANSIBLE_EE_CONTEXT_GENERATE_START" in output.out
    assert "ANSIBLE_EE_CONTEXT_GENERATE_END rc=17" in output.out
    assert str(logs[0]) in output.err
    assert logs[0].read_text() == "ERROR original native generator diagnostic\n"
    assert not context.exists()


def test_owned_diagnostic_handler_close_failure_preserves_constructor_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, teardown = ValueError("original constructor failure"), OSError("owned diagnostic close failure")
    closed: list[logging.Handler] = []

    def close(handler: logging.Handler) -> None:
        logging.Handler.close(handler)
        closed.append(handler)
        raise teardown

    def constructor(**_values: object) -> owner._NativeBuilder:
        raise original

    module = ModuleType("ansible_builder.main")
    module.__dict__["AnsibleBuilder"] = constructor
    real_import = import_module
    monkeypatch.setattr(owner, "import_module",
                        lambda name: module if name == "ansible_builder.main" else real_import(name))
    monkeypatch.setattr(owner._NativeBuilderDiagnostics, "close", close)
    with pytest.raises(BaseExceptionGroup) as captured:
        owner._native_create_context(tmp_path / "definition.yml", tmp_path / "gludd-context", "docker",
                                     "gludd:test", tmp_path / "iid", tmp_path / "command.json")
    assert captured.value.exceptions == (original, teardown)
    assert len(closed) == 1
    assert closed[0] not in logging.getLogger("ansible_builder").handlers
    assert not (tmp_path / "command.json").exists()


@pytest.mark.parametrize("acquire_status", [0, 17])
def test_hosted_job_hands_reviewed_native_acquisition_to_literal_build_owner(
    tmp_path: Path, acquire_status: int,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = cast(dict[str, object], yaml.safe_load((root / ".github/workflows/build.yml").read_text()))
    jobs = cast(dict[str, dict[str, object]], workflow["jobs"])
    job = jobs["ansible-ee"]
    assert job["needs"] == ["version", "gate"] and job["timeout-minutes"] == 45
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    steps = cast(list[dict[str, object]], job["steps"])
    checkout = steps[0]
    assert checkout["uses"] == "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd"
    assert checkout["with"] == {"ref": "${{ github.sha }}"}
    assert steps[1]["uses"] == "astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39"
    names = [str(step.get("name", "")) for step in steps]
    acquire = names.index("Acquire reviewed native collection closure")
    build = names.index("Build Ansible execution environment")
    assert names.index("Validate canonical Ansible runtime boundary") < acquire < build
    assert build < names.index("Smoke Ansible execution environment")
    values = cast(dict[str, str], job["env"])
    canonical = "sha256:7d176b02362222535077a47b4a4f8191e6e9b4a00be0fbc6d3a1f93d4a7a9588"
    assert values["EE_COLLECTION_LOCK_SHA256"] == canonical
    assert values["EE_ARTIFACT_ROOT"] == (
        ".gludd/collection-artifacts/gludd-ee-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    literal_root = str(tmp_path / 'reviewed archives " literal $value ; & |')
    substitutions = {"EE_COLLECTION_LOCK_SHA256": canonical, "EE_ARTIFACT_ROOT": literal_root,
                     "EE_TAG": "gludd:fixture", "EE_CONTEXT": str(tmp_path / "gludd-context")}
    capture = tmp_path / "capture.py"
    capture.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
                       f"raise SystemExit({acquire_status} if 'stage-collections' in sys.argv else 0)\n")
    makefile = tmp_path / "Makefile"
    makefile.write_text(f"include {root / 'make/00-foundation.mk'}\n")
    captures: list[list[str]] = []
    for index in (acquire, build):
        run = str(steps[index]["run"]).replace("\\\n", " ")
        command = shlex.split(run[run.index("make "):])
        command[1:1] = ["--no-print-directory", "-f", str(makefile)]
        command.append(f"UV={sys.executable} {capture}")
        for name, value in substitutions.items():
            command = [argument.replace("${" + name + "}", value) for argument in command]
        result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == (2 if index == acquire and acquire_status else 0), result.stdout + result.stderr
        arguments = cast(list[str], json.loads(result.stdout))
        captures.append(arguments)
        assert arguments[arguments.index("--artifact-root") + 1] == literal_root
        if index == acquire:
            assert arguments[arguments.index("--collection-stage-mode") + 1] == "acquire"
            assert arguments[arguments.index("--collection-lock-sha256") + 1] == canonical
            assert arguments[arguments.index("--collection-timeout-seconds") + 1] == "120"
            assert "--validate-only" not in arguments
            if acquire_status:
                break
        else:
            assert arguments[arguments.index("--runtime") + 1] == "docker"
            assert arguments[arguments.index("--image") + 1] == "gludd:fixture"
            assert "build" in arguments and "--validate-only" not in arguments
    assert len(captures) == (1 if acquire_status else 2)


def test_hosted_release_packages_complete_locked_closure_and_preserves_release_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/build.yml").read_text()
    workflow = cast(dict[str, object], yaml.safe_load(text))
    jobs = cast(dict[str, dict[str, object]], workflow["jobs"])
    steps = cast(list[dict[str, object]], jobs["ansible-ee"]["steps"])
    named = {str(step["name"]): step for step in steps if "name" in step}
    original_smoke = str(named["Smoke Ansible execution environment"]["run"])
    assert '--network=none "${EE_TAG}" /usr/local/bin/tofu version' in original_smoke
    assert '--network=none --pull=never "${EE_TAG}"' in original_smoke
    assert "/opt/gludd/ansible-ee-python/bin/python3.11 -W error" in original_smoke
    for name in ("Log in to GHCR", "Push tagged execution environment"):
        assert named[name]["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert named["Log in to GHCR"]["uses"] == "docker/login-action@dbcb813823bdd20940b903addbd779551569679f"
    assert named["Upload execution-environment artifacts"]["uses"] == (
        "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"
    )
    stage = str(named["Stage execution-environment release metadata"]["run"])
    assert "cp config/ansible/runtime-lock.json dist/ee-release/ansible-ee-runtime-lock.json" in stage
    assert "cp config/ansible/collections.lock.json dist/ee-release/ansible-ee-collections.lock.json" in stage
    assert 'cp "${EE_ARTIFACT_ROOT}"/*.tar.gz dist/ee-release/' in stage
    assert "cp dist/collections/*.tar.gz dist/ee-release/" in stage
    manifest = cast(dict[str, object], json.loads(next(line for line in stage.splitlines() if '"artifacts"' in line)))
    locked = owner._collection_lock_entries(owner.INPUTS["collection_lock"], owner.bound_collection_lock_sha256(),
                                            owner.required_collection_versions())
    expected = {entry["artifact"] for entry in locked} | {path.name for _, path in owner.COLLECTION_ARTIFACTS}
    actual = cast(list[str], manifest["artifacts"])
    assert len(actual) == 16 and set(actual) == expected
    release = jobs["release"]
    assert "ansible-ee" in cast(list[str], release["needs"])
    assert "needs['ansible-ee'].result == 'success'" in str(release["if"])
    assert "Write checksum-bound rollback receipt" in text
    assert "Verify published rollback receipt" in text
    assert "verify-published-rollback" in text and "write-rollback-receipt" in text
