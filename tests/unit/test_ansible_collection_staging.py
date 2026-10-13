"""Digest-bound private Galaxy staging and existing native owner contracts."""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
from collections.abc import Mapping
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, BinaryIO, TypedDict, Unpack, cast

import pytest

if TYPE_CHECKING:
    import ansible_runtime_artifacts as owner
    import check_molecule_yaml as guard
    import item_progress as policy
    import stream_command as stream
else:
    from scripts import ansible_runtime_artifacts as owner
    from scripts import check_molecule_yaml as guard
    from scripts import item_progress as policy
    from scripts import stream_command as stream


class _CollectionLock(TypedDict):
    schema_version: int
    collections: list[owner.CollectionArtifactEntry]


class _StageOverrides(TypedDict, total=False):
    timeout_seconds: int


class _CollectionCommandArguments(TypedDict):
    max_seconds: int
    environment: Mapping[str, str]
    cwd: Path


class _AdmissionArguments(TypedDict):
    environment: Mapping[str, str]
    quiet_seconds: float
    max_seconds: float


class _CollectionInfo(TypedDict):
    namespace: str
    name: str
    version: str
    dependencies: dict[str, str]


class _Manifest(TypedDict):
    collection_info: _CollectionInfo


class _CollectionRequirement(TypedDict):
    name: str
    version: str
    type: str


class _CollectionRequirements(TypedDict):
    collections: list[_CollectionRequirement]


class _TerminalObservedStatus(TypedDict):
    state: str
    termination_reason: str
    child_pid: int


class _AdmissionObservedStatus(TypedDict):
    progress_identity: dict[str, object]


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def package(
    root: Path, name: str, dependencies: dict[str, str] | None = None, *, requires_ansible: str | None = ">=2.19",
) -> owner.CollectionArtifactEntry:
    namespace, collection = name.split(".")
    archive = root / f"{namespace}-{collection}-1.0.0.tar.gz"
    metadata = {"collection_info": {
        "namespace": namespace, "name": collection, "version": "1.0.0",
        "dependencies": dependencies or {},
    }}
    contents = {
        "MANIFEST.json": json.dumps(metadata).encode(),
    }
    if requires_ansible is not None:
        contents["meta/runtime.yml"] = f"requires_ansible: '{requires_ansible}'\n".encode()
    with tarfile.open(archive, "w:gz") as output:
        for filename, data in contents.items():
            member = tarfile.TarInfo(filename)
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
    return {
        "name": name, "version": "1.0.0", "artifact": archive.name,
        "origin": "https://galaxy.ansible.com/", "sha256": digest(archive),
        "size": archive.stat().st_size,
    }


@pytest.fixture
def supplied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    root = tmp_path / "supplied"
    root.mkdir()
    entries = [package(root, "fixture.main", {"fixture.dependency": ">=1.0.0"}),
               package(root, "fixture.dependency")]
    lock = tmp_path / "collections.lock.json"
    lock.write_text(json.dumps({"schema_version": 1, "collections": entries}), encoding="utf-8")
    for variable in ("ANSIBLE_COLLECTIONS_PATH", "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH", "ANSIBLE_CONFIG"):
        monkeypatch.delenv(variable, raising=False)
    return lock, root, digest(lock)


def enter_stage(
    supplied: tuple[Path, Path, str], **overrides: Unpack[_StageOverrides],
) -> AbstractContextManager[Path]:
    lock, root, expected = supplied
    return owner.staged_collection_closure(
        lock, root, expected_lock_sha256=expected,
        required_versions={"fixture.main": "1.0.0"}, **overrides,
    )


def installer(monkeypatch: pytest.MonkeyPatch, *, outcome: int = 0, message: str = "") -> list[Path]:
    stages: list[Path] = []

    def run(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        assert command[:5] == [sys.executable, "-m", "ansible.cli.galaxy", "collection", "install"]
        assert "--offline" in command and "--no-cache" in command
        assert "--no-deps" not in command
        assert 1 <= arguments["max_seconds"] <= 120
        stage = Path(command[command.index("--collections-path") + 1])
        child = arguments["environment"]
        assert child["ANSIBLE_COLLECTIONS_PATH"] == str(stage)
        assert child["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "false"
        assert Path(child["ANSIBLE_HOME"]).is_relative_to(stage.parent)
        assert arguments["cwd"] == stage.parent
        stages.append(stage)
        inputs = cast(
            _CollectionRequirements, json.loads(Path(command[command.index("--requirements-file") + 1]).read_text()),
        )
        for entry in inputs["collections"]:
            assert entry["type"] == "file"
            archive = Path(entry["name"])
            assert archive.is_relative_to(stage.parent)
            with tarfile.open(archive) as source:
                member = source.extractfile("MANIFEST.json")
                assert member is not None
                metadata = cast(_Manifest, json.loads(member.read()))
            info = metadata["collection_info"]
            target = stage / "ansible_collections" / info["namespace"] / info["name"]
            target.mkdir(parents=True, exist_ok=True)
            (target / "MANIFEST.json").write_text(json.dumps(metadata), encoding="utf-8")
        log.write_text(message, encoding="utf-8")
        return outcome

    monkeypatch.setattr(owner, "_run_collection_command", run)
    return stages


def test_native_stage_is_offline_private_and_owned(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = dict(os.environ)
    stages = installer(monkeypatch)
    monkeypatch.setattr(owner, "urlopen", lambda *args, **kwargs: pytest.fail("offline used network"))
    with enter_stage(supplied) as stage:
        assert stage == stages[0]
        assert (stage / "ansible_collections/fixture/main/MANIFEST.json").is_file()
    assert not stage.parent.exists()
    assert dict(os.environ) == before


def test_consumer_exception_cleans_stage_and_restores_environment(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = installer(monkeypatch)
    before = dict(os.environ)
    with pytest.raises(RuntimeError, match="consumer failed"), enter_stage(supplied):
        raise RuntimeError("consumer failed")
    assert not stages[0].parent.exists()
    assert dict(os.environ) == before


@pytest.mark.parametrize("field,value", [
    ("sha256", "sha256:" + "0" * 64), ("size", 1), ("version", "2.0.0"),
    ("artifact", "../escape.tar.gz"), ("origin", "http://galaxy.ansible.com/"),
    ("origin", "https://secret@example.invalid/"), ("version", "*"),
])
def test_unsafe_or_tampered_supplied_inputs_fail_before_command(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    lock, root, _expected = supplied
    document = cast(_CollectionLock, json.loads(lock.read_text()))
    cast(dict[str, object], document["collections"][0])[field] = value
    lock.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(owner, "_run_collection_command", lambda *args, **kwargs: pytest.fail("unsafe install"))
    with pytest.raises(ValueError), enter_stage((lock, root, digest(lock))):
        pass


def test_changed_lock_is_not_accepted_as_reviewed_input(supplied: tuple[Path, Path, str]) -> None:
    lock, _root, _expected = supplied
    lock.write_text(lock.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="lock digest"), enter_stage(supplied):
        pass


@pytest.mark.parametrize("mutation", ["missing", "incompatible", "duplicate", "empty"])
def test_manifest_closure_is_verified_natively_before_install(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    lock, root, _expected = supplied
    document = cast(_CollectionLock, json.loads(lock.read_text()))
    if mutation == "missing":
        document["collections"].pop()
    elif mutation == "incompatible":
        document["collections"][0] = package(root, "fixture.main", {"fixture.dependency": ">=2.0.0"})
    elif mutation == "duplicate":
        document["collections"].append(document["collections"][0])
    else:
        document["collections"] = []
    lock.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(owner, "_run_collection_command", lambda *args, **kwargs: pytest.fail("incomplete install"))
    with pytest.raises(ValueError), enter_stage((lock, root, digest(lock))):
        pass


@pytest.mark.parametrize("variable,value", [
    ("ANSIBLE_COLLECTIONS_PATH", "/Users/example/.ansible/collections"),
    ("ANSIBLE_COLLECTIONS_SCAN_SYS_PATH", "true"),
    ("ANSIBLE_CONFIG", "/Users/example/.ansible.cfg"),
])
def test_ambient_resolution_is_refused(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, variable: str, value: str,
) -> None:
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(owner, "_run_collection_command", lambda *args, **kwargs: pytest.fail("ambient install"))
    with pytest.raises(ValueError, match="ambient"), enter_stage(supplied):
        pass


@pytest.mark.parametrize("outcome,message", [(124, ""), (2, "ERROR! invalid input\n"),
                                             (0, "[WARNING]: unsupported collection\n")])
def test_command_failure_warning_or_timeout_cleans_private_state(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, outcome: int, message: str,
) -> None:
    stages = installer(monkeypatch, outcome=outcome, message=message)
    with pytest.raises(ValueError), enter_stage(supplied):
        pass
    assert not stages[0].parent.exists()


@pytest.mark.parametrize("budget", [0, 121, True, 1.5])
def test_budget_is_bounded_before_execution(supplied: tuple[Path, Path, str], budget: object) -> None:
    with pytest.raises(ValueError, match="timeout"), enter_stage(supplied, timeout_seconds=cast(int, budget)):
        pass


@pytest.mark.parametrize("mode", ["refresh", "acquire"])
def test_native_acquisition_is_exact_bounded_atomic_and_owned(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str,
) -> None:
    lock, root, expected = supplied
    destination = tmp_path / "new-generation"
    output_lock = destination / "collections.lock.json" if mode == "refresh" else lock
    working: list[Path] = []

    def downloader(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        assert command[:5] == [sys.executable, "-m", "ansible.cli.galaxy", "collection", "download"]
        assert "--offline" not in command and "--no-cache" in command
        assert ("--no-deps" in command) == (mode == "acquire")
        assert arguments["max_seconds"] == 120
        assert command[command.index("--server") + 1] == "https://galaxy.ansible.com/"
        requests = cast(
            _CollectionRequirements, json.loads(Path(command[command.index("--requirements-file") + 1]).read_text()),
        )
        assert all(entry["version"] == "1.0.0" and entry["type"] == "galaxy"
                   for entry in requests["collections"])
        downloaded = Path(command[command.index("--download-path") + 1])
        working.append(downloaded.parent)
        entries = cast(_CollectionLock, json.loads(lock.read_text()))["collections"]
        for entry in entries:
            (downloaded / entry["artifact"]).write_bytes((root / entry["artifact"]).read_bytes())
        (downloaded / "requirements.yml").write_text(json.dumps({"collections": [
            {"name": entry["artifact"], "version": entry["version"]} for entry in entries
        ]}), encoding="utf-8")
        log.write_text("", encoding="utf-8")
        return 0

    monkeypatch.setattr(owner, "_run_collection_command", downloader)
    assert owner.prepare_collection_artifacts(
        mode, output_lock, destination, required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected,
    ) == 0
    published = cast(_CollectionLock, json.loads((destination / "collections.lock.json").read_text()))
    assert {entry["name"] for entry in published["collections"]} == {"fixture.main", "fixture.dependency"}
    for entry in published["collections"]:
        assert digest(destination / entry["artifact"]) == entry["sha256"]
    assert all(not state.exists() for state in working)
    assert all((root / entry["artifact"]).is_file() for entry in published["collections"])


@pytest.mark.parametrize("failure", ["timeout", "tamper", "missing-dependency"])
def test_failed_acquisition_never_publishes_or_changes_reviewed_lock(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    lock, root, expected = supplied
    previous = lock.read_bytes()
    destination = tmp_path / "rejected-generation"
    working: list[Path] = []

    def downloader(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        downloaded = Path(command[command.index("--download-path") + 1])
        working.append(downloaded.parent)
        entries = cast(_CollectionLock, json.loads(lock.read_text()))["collections"]
        for entry in entries if failure != "missing-dependency" else entries[:1]:
            (downloaded / entry["artifact"]).write_bytes((root / entry["artifact"]).read_bytes())
        if failure == "tamper":
            (downloaded / entries[0]["artifact"]).write_bytes(b"changed")
        log.write_text("", encoding="utf-8")
        return 124 if failure == "timeout" else 0

    monkeypatch.setattr(owner, "_run_collection_command", downloader)
    assert owner.prepare_collection_artifacts(
        "acquire", lock, destination, required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected,
    ) != 0
    assert not destination.exists()
    assert lock.read_bytes() == previous
    assert all(not state.exists() for state in working)


@pytest.mark.parametrize("mode", ["offline", "acquire", "refresh"])
def test_plan_never_executes_tools_or_creates_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str,
) -> None:
    destination = tmp_path / "plan-generation"
    monkeypatch.setattr(owner, "_run_collection_command", lambda *args, **kwargs: pytest.fail("plan executed"))
    assert owner.prepare_collection_artifacts(
        mode, destination / "collections.lock.json", destination,
        required_versions={"fixture.main": "1.0.0"}, origin="https://galaxy.ansible.com/", validate_only=True,
    ) == 0
    assert not destination.exists()


@pytest.mark.parametrize("message", [
    "[DEPRECATION WARNING]: native metadata warning\n", "ERROR! native validation error\n",
    "  [WARNING]: native dependency warning\n", "WARNING  native options warning\n",
])
def test_existing_warning_owner_rejects_native_galaxy_diagnostic_forms(tmp_path: Path, message: str) -> None:
    log = tmp_path / "galaxy.log"
    log.write_text(message, encoding="utf-8")
    assert guard.validate_native_lint_diagnostics(log) is False


def test_stage_cli_plan_has_explicit_inputs_and_no_acquisition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    requirements = tmp_path / "required.yml"
    requirements.write_text(json.dumps({"collections": [{"name": "fixture.main", "version": "1.0.0"}]}))
    monkeypatch.setattr(owner, "_run_collection_command", lambda *args, **kwargs: pytest.fail("plan executed"))
    assert owner.main([
        "stage-collections", "--collection-stage-mode", "offline", "--collection-lock", str(tmp_path / "lock.json"),
        "--artifact-root", str(tmp_path / "artifacts"), "--collection-requirements", str(requirements),
        "--collection-timeout-seconds", "120", "--galaxy-origin", "https://galaxy.ansible.com/", "--validate-only",
    ]) == 0
    assert "proof=none" in capsys.readouterr().out


def test_acquisition_retains_independent_lock_binding_during_native_command(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    lock, root, expected = supplied

    def downloader(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        downloaded = Path(command[command.index("--download-path") + 1])
        for entry in cast(_CollectionLock, json.loads(lock.read_text()))["collections"]:
            (downloaded / entry["artifact"]).write_bytes((root / entry["artifact"]).read_bytes())
        lock.write_text(lock.read_text() + " ")
        log.write_text("", encoding="utf-8")
        return 0

    monkeypatch.setattr(owner, "_run_collection_command", downloader)
    destination = tmp_path / "raced-generation"
    assert owner.prepare_collection_artifacts(
        "acquire", lock, destination, required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected,
    ) != 0
    assert not destination.exists()


@pytest.mark.parametrize("constraint", [None, "", ">=99.0"])
def test_missing_or_unsupported_core_metadata_fails_before_install(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, constraint: str | None,
) -> None:
    lock, root, _expected = supplied
    document = cast(_CollectionLock, json.loads(lock.read_text()))
    document["collections"][1] = package(root, "fixture.dependency", requires_ansible=constraint)
    lock.write_text(json.dumps(document))
    monkeypatch.setattr(owner, "_run_collection_command", lambda *args, **kwargs: pytest.fail("unsupported install"))
    with pytest.raises(ValueError, match="requires_ansible"), enter_stage((lock, root, digest(lock))):
        pass


def test_core_219_metadata_accessor_compatibility_preserves_real_runtime_proof(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ansible.galaxy.collection.concrete_artifact_manager import ConcreteArtifactsManager

    monkeypatch.delattr(ConcreteArtifactsManager, "get_direct_requires_ansible", raising=False)
    installer(monkeypatch)
    with enter_stage(supplied) as stage:
        assert (stage / "ansible_collections/fixture/main/MANIFEST.json").is_file()


@pytest.mark.parametrize("mode", ["offline", "acquire", "refresh"])
def test_native_command_never_changes_another_threads_environment_or_cwd(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str,
) -> None:
    lock, artifacts, expected = supplied
    parent_cwd = Path.cwd()
    parent_environment = hashlib.sha256(json.dumps(sorted(os.environ.items())).encode()).hexdigest()
    observed: list[tuple[str, Path]] = []

    def native_command(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        def other_caller() -> None:
            observed.append((hashlib.sha256(json.dumps(sorted(os.environ.items())).encode()).hexdigest(), Path.cwd()))

        thread = Thread(target=other_caller, name=f"gludd-collection-parent-observer-{mode}")
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert observed[-1] == (parent_environment, parent_cwd)
        child = arguments["environment"]
        assert child["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "false"
        assert arguments["cwd"] == log.parent
        entries = cast(_CollectionLock, json.loads(lock.read_text()))["collections"]
        if mode == "offline":
            requests = cast(
                _CollectionRequirements,
                json.loads(Path(command[command.index("--requirements-file") + 1]).read_text()),
            )
            stage = Path(command[command.index("--collections-path") + 1])
            for request in requests["collections"]:
                with tarfile.open(request["name"]) as archive:
                    member = archive.extractfile("MANIFEST.json")
                    assert member is not None
                    metadata = cast(_Manifest, json.loads(member.read()))
                info = metadata["collection_info"]
                target = stage / "ansible_collections" / info["namespace"] / info["name"]
                target.mkdir(parents=True)
                (target / "MANIFEST.json").write_text(json.dumps(metadata))
        else:
            downloaded = Path(command[command.index("--download-path") + 1])
            for entry in entries:
                (downloaded / entry["artifact"]).write_bytes((artifacts / entry["artifact"]).read_bytes())
            (downloaded / "requirements.yml").write_text(json.dumps({"collections": [
                {"name": entry["artifact"], "version": entry["version"]} for entry in entries
            ]}))
        log.write_text("")
        return 0

    monkeypatch.setattr(owner, "_run_collection_command", native_command)
    if mode == "offline":
        with enter_stage(supplied):
            pass
    else:
        destination = tmp_path / f"private-{mode}"
        candidate = destination / "collections.lock.json" if mode == "refresh" else lock
        assert owner.prepare_collection_artifacts(
            mode, candidate, destination, required_versions={"fixture.main": "1.0.0"},
            expected_lock_sha256=expected, origin="https://galaxy.ansible.com/",
        ) == 0
    assert observed


def test_existing_observer_keeps_explicit_environment_cwd_and_trace_in_the_child_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_environment = hashlib.sha256(json.dumps(sorted(os.environ.items())).encode()).hexdigest()
    initial_cwd = Path.cwd()
    child_environment = dict(os.environ, GLUDD_COLLECTION_CHILD_CHECK="gludd-public-child")
    before = dict(child_environment)
    child_cwd = tmp_path / "child-work"
    child_cwd.mkdir()
    log = tmp_path / "child.log"
    ready = Event()
    observed: list[tuple[str, Path]] = []
    original_write = stream._write_chunk

    def write_chunk(chunk: bytes, log_file: BinaryIO, *, quiet: bool) -> None:
        original_write(chunk, log_file, quiet=quiet)
        ready.set()

    def other_caller() -> None:
        if ready.wait(timeout=2):
            observed.append((hashlib.sha256(json.dumps(sorted(os.environ.items())).encode()).hexdigest(), Path.cwd()))

    monkeypatch.setattr(stream, "_write_chunk", write_chunk)
    thread = Thread(target=other_caller, name="gludd-collection-real-parent-observer")
    command = [sys.executable, "-c",
               "import os, pathlib, time; print(os.environ['GLUDD_COLLECTION_CHILD_CHECK'], flush=True); "
               "print(os.getcwd(), flush=True); "
               "pathlib.Path(os.environ['GLUDD_XDIST_TRACE_LOG']).write_text("
               "os.environ['GLUDD_XDIST_TRACE_RUN_ID']+' '+os.environ['GLUDD_XDIST_TRACE_TRUNCATE']); "
               "time.sleep(0.1)"]
    thread.start()
    try:
        assert stream.stream_command(
            command, log, environment=child_environment, cwd=child_cwd,
            observed_root=tmp_path / "observed", label="gludd-collection-lifecycle", max_seconds=2,
            run_id="gludd-private-child", pytest_trace=True,
        ) == 0
    finally:
        ready.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert observed == [(initial_environment, initial_cwd)]
    assert log.read_text().splitlines() == ["gludd-public-child", str(child_cwd)]
    assert (tmp_path / "observed/gludd-collection-lifecycle/gludd-private-child.pytest.jsonl").read_text() == (
        "gludd-private-child 1"
    )
    assert child_environment == before
    assert hashlib.sha256(json.dumps(sorted(os.environ.items())).encode()).hexdigest() == initial_environment
    assert Path.cwd() == initial_cwd


def test_late_destination_keeps_the_other_owners_native_inode(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    lock, artifacts, _expected = supplied
    entries = cast(_CollectionLock, json.loads(lock.read_text()))["collections"]
    destination = tmp_path / "late-other-owner"
    original_exists = Path.exists
    checks = 0
    created: list[tuple[int, int]] = []

    def interleaved_exists(path: Path) -> bool:
        nonlocal checks
        result = original_exists(path)
        if path == destination:
            checks += 1
            if checks == 2:
                destination.mkdir()
                stat = destination.stat()
                created.append((stat.st_dev, stat.st_ino))
                return False
        return result

    monkeypatch.setattr(Path, "exists", interleaved_exists)
    with pytest.raises(FileExistsError):
        owner._publish_collection_generation(artifacts, entries, destination, lock.read_bytes())
    assert created
    stat = destination.stat()
    assert (stat.st_dev, stat.st_ino) == created[0]
    assert list(destination.iterdir()) == []


@pytest.fixture
def focused_native_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "native-focus"
    root.mkdir()
    (root / "TASKS.md").write_text(
        "v0.12.0 milestone is the exact task set F12.1-F12.3\n"
        "- [ ] F12.1 — primary | status: pending\n"
    )
    monkeypatch.setattr(policy, "repository_state_id", lambda *_args, **_kwargs: "a" * 64)
    policy.focus_item(root, "F12.1")
    return root


def test_native_admission_hash_binds_child_cwd_without_exposing_private_environment(
    focused_native_owner: Path,
) -> None:
    root = focused_native_owner
    command = [sys.executable, "-c", "import sys; sys.exit(1)"]
    child_environment = dict(os.environ, GLUDD_COLLECTION_PRIVATE_TOKEN="private-value-not-for-status")
    first, changed = root / "first", root / "changed"
    first.mkdir()
    changed.mkdir()
    observed = root / "observed"
    arguments: _AdmissionArguments = {"environment": child_environment, "quiet_seconds": 0, "max_seconds": 2}
    with policy.command_admission(root, command, cwd=first, **arguments) as admission:
        assert admission is not None
        initial_identity = admission.identity["environment"]
        assert stream.stream_command(
            command, observed / "gludd-focused/failure.log", environment=child_environment, cwd=first,
            observed_root=observed, label="gludd-focused", run_id="failed-first", max_seconds=2,
            progress_admission=admission, quiet=True,
        ) == 1
    with pytest.raises(ValueError, match="unchanged failed inputs"), policy.command_admission(
        root, command, cwd=first, **arguments,
    ):
        pass
    with policy.command_admission(root, command, cwd=changed, **arguments) as admission:
        assert admission is not None
        assert admission.identity["environment"] != initial_identity
        assert stream.stream_command(
            command, observed / "gludd-focused/changed.log", environment=child_environment, cwd=changed,
            observed_root=observed, label="gludd-focused", run_id="changed-input", max_seconds=2,
            progress_admission=admission, quiet=True,
        ) == 1
    status_text = (observed / "gludd-focused/current.json").read_text()
    assert "private-value-not-for-status" not in status_text
    assert "GLUDD_COLLECTION_PRIVATE_TOKEN" not in status_text
    assert (
        cast(_AdmissionObservedStatus, json.loads(status_text))["progress_identity"]["environment"]
        == admission.identity["environment"]
    )


@pytest.mark.parametrize("changed", ["environment", "cwd"])
def test_observer_refuses_admission_for_a_different_child_context_before_spawn(
    focused_native_owner: Path, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    root = focused_native_owner
    command = [sys.executable, "-c", "raise SystemExit(0)"]
    child_environment = dict(os.environ, GLUDD_COLLECTION_PRIVATE_TOKEN="reviewed")
    child_cwd = root / "child"
    child_cwd.mkdir()
    with policy.command_admission(
        root, command, environment=child_environment, cwd=child_cwd, quiet_seconds=0, max_seconds=2,
    ) as admission:
        assert admission is not None
        if changed == "environment":
            child_environment["GLUDD_COLLECTION_PRIVATE_TOKEN"] = "different"
        else:
            child_cwd = root
        monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("unadmitted spawn"))
        with pytest.raises(ValueError, match="execution context"):
            stream.stream_command(
                command, root / "unadmitted.log", environment=child_environment, cwd=child_cwd,
                observed_root=root / "observed", label="gludd-unadmitted", max_seconds=2,
                progress_admission=admission,
            )
        assert not (root / "observed/gludd-unadmitted/current.json").exists()


@pytest.mark.parametrize("leader_exits_first", [False, True])
def test_explicit_child_context_keeps_native_timeout_and_owned_descendant_cleanup(
    tmp_path: Path, leader_exits_first: bool,
) -> None:
    import psutil

    ready = tmp_path / "gludd-owned-descendant"
    grandchild = (
        "import os, signal, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    leader = (
        "import os, subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}, sys.argv[1]]); "
        "\nwhile not Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        + ("os._exit(0)\n" if leader_exits_first else "time.sleep(30)\n")
    )
    try:
        result = stream.stream_command(
            [sys.executable, "-c", leader, str(ready)], tmp_path / "bounded.log",
            environment=dict(os.environ), cwd=tmp_path,
            observed_root=tmp_path / "observed", label="gludd-owned-timeout", run_id="owned-group",
            max_seconds=0.4, termination_grace_seconds=0.1, quiet=True,
        )
        assert result == stream.TIMEOUT_FAILURE
        status = cast(
            _TerminalObservedStatus,
            json.loads((tmp_path / "observed/gludd-owned-timeout/owned-group.json").read_text()),
        )
        assert status["state"] == "timed_out" and status["termination_reason"] == "max-runtime-timeout"
        assert not psutil.pid_exists(status["child_pid"])
        assert not psutil.pid_exists(int(ready.read_text()))
    finally:
        if ready.exists():
            with suppress(ProcessLookupError):
                os.kill(int(ready.read_text()), signal.SIGKILL)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_publication_refuses_a_missing_native_no_clobber_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    source, destination = tmp_path / "owned-source", tmp_path / "never-clobbered"
    source.mkdir()
    original = source.stat()
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: object())
    with pytest.raises(OSError, match="exclusive directory rename is unavailable"):
        owner._rename_collection_generation_no_replace(source, destination)
    assert source.stat().st_ino == original.st_ino
    assert not destination.exists()


def test_actual_native_galaxy_build_and_offline_install_complete_a_digest_locked_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real valid tarballs/FILES metadata, actual child install, and denied networking."""
    for variable in ("ANSIBLE_COLLECTIONS_PATH", "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH", "ANSIBLE_CONFIG"):
        monkeypatch.delenv(variable, raising=False)
    guard_directory = tmp_path / "gludd-network-denial"
    guard_directory.mkdir()
    (guard_directory / "sitecustomize.py").write_text(
        "import ipaddress, sys\n"
        "def refuse_network(event, arguments):\n"
        "    if event in {'socket.connect', 'socket.sendto', 'socket.gethostbyname', "
        "'socket.gethostbyaddr', 'socket.getnameinfo'}:\n"
        "        raise RuntimeError('gludd fixture refuses network')\n"
        "    if event == 'socket.getaddrinfo':\n"
        "        try:\n"
        "            ipaddress.ip_address(arguments[0])\n"
        "        except (ValueError, TypeError):\n"
        "            raise RuntimeError('gludd fixture refuses network') from None\n"
        "sys.addaudithook(refuse_network)\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(guard_directory))
    state, artifacts = tmp_path / "gludd-native-build", tmp_path / "gludd-native-artifacts"
    state.mkdir()
    artifacts.mkdir()
    private = owner._collection_environment(state)
    refused = owner._run_collection_command(
        [sys.executable, "-c", "import socket; socket.create_connection(('example.invalid', 443))"],
        state / "network-refusal.log", max_seconds=3, environment=private, cwd=state,
    )
    assert refused != 0 and "gludd fixture refuses network" in (state / "network-refusal.log").read_text()
    entries: list[owner.CollectionArtifactEntry] = []
    for name in ("dependency", "main"):
        source = tmp_path / f"gludd-native-source-{name}"
        (source / "meta").mkdir(parents=True)
        (source / "plugins/modules").mkdir(parents=True)
        (source / "README.md").write_text("Owned offline Galaxy fixture.\n")
        (source / "meta/runtime.yml").write_text("requires_ansible: '>=2.19'\n")
        (source / "plugins/modules/probe.py").write_text("\"\"\"Owned metadata-only fixture.\"\"\"\n")
        dependencies = "  fixture.dependency: '>=1.0.0'\n" if name == "main" else "  {}\n"
        (source / "galaxy.yml").write_text(
            f"namespace: fixture\nname: {name}\nversion: 1.0.0\nreadme: README.md\n"
            "authors: ['Gludd offline fixture']\ndescription: Owned native closure fixture\n"
            "license: [MIT]\ndependencies:\n" + dependencies
        )
        owner._checked_collection_command(
            [sys.executable, "-m", "ansible.cli.galaxy", "collection", "build", str(source),
             "--output-path", str(artifacts)], state, 5, environment=private, cwd=state,
        )
        archive = artifacts / f"fixture-{name}-1.0.0.tar.gz"
        entries.append({"name": f"fixture.{name}", "version": "1.0.0", "artifact": archive.name,
                        "origin": "https://galaxy.ansible.com/", "sha256": digest(archive),
                        "size": archive.stat().st_size})
    lock = tmp_path / "native-collections.lock.json"
    lock.write_text(json.dumps({"schema_version": 1, "collections": entries}))
    with owner.staged_collection_closure(
        lock, artifacts, expected_lock_sha256=digest(lock),
        required_versions={"fixture.main": "1.0.0"}, timeout_seconds=5,
    ) as stage:
        for name in ("dependency", "main"):
            installed = stage / "ansible_collections/fixture" / name
            assert (installed / "FILES.json").is_file()
            assert (installed / "plugins/modules/probe.py").is_file()
            assert (installed / "MANIFEST.json").is_file()
        assert (stage.parent / "galaxy.log").is_file()
    assert not stage.parent.exists(), "the actual installed offline stage must be owned and torn down"
