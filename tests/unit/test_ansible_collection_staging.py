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


class _NativeAcquireHTTP(TypedDict):
    output: Path
    metadata: dict[str, object]
    suffix: bytes
    requests: list[tuple[str, dict[str, object]]]


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


class _CollectionAcquireArguments(TypedDict):
    lock_file: str
    artifact_root: str
    required_versions: dict[str, str]
    origin: str
    expected_lock_sha256: str
    timeout_seconds: int


class _TerminalObservedStatus(TypedDict):
    state: str
    termination_reason: str
    child_pid: int


class _AdmissionObservedStatus(TypedDict):
    progress_identity: dict[str, object]


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def acquire_arguments(command: list[str]) -> _CollectionAcquireArguments:
    assert len(command) == 5 and command[:2] == [sys.executable, "-c"]
    assert command[3] == str(owner.ROOT)
    return cast(_CollectionAcquireArguments, json.loads(command[4]))


def download_path(command: list[str]) -> Path:
    if command[1] == "-c":
        return Path(acquire_arguments(command)["artifact_root"])
    return Path(command[command.index("--download-path") + 1])


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
        if mode == "acquire":
            inputs = acquire_arguments(command)
            assert inputs == {
                "lock_file": str(lock.absolute()), "artifact_root": str(download_path(command)),
                "required_versions": {"fixture.main": "1.0.0", "fixture.dependency": "1.0.0"},
                "origin": "https://galaxy.ansible.com/", "expected_lock_sha256": expected, "timeout_seconds": 120,
            }
            assert str(lock) not in command[2] and inputs["origin"] not in command[2]
        else:
            assert command[:5] == [sys.executable, "-m", "ansible.cli.galaxy", "collection", "download"]
            assert "--no-cache" in command and "--no-deps" not in command
            assert command[command.index("--server") + 1] == "https://galaxy.ansible.com/"
            requests = cast(_CollectionRequirements, json.loads(
                Path(command[command.index("--requirements-file") + 1]).read_text(),
            ))
            assert all(entry["version"] == "1.0.0" and entry["type"] == "galaxy"
                       for entry in requests["collections"])
            assert {entry["name"] for entry in requests["collections"]} == {"fixture.main"}
        assert "--offline" not in command
        assert arguments["max_seconds"] == 120
        downloaded = download_path(command)
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


@pytest.fixture
def native_acquire_http(
    supplied: tuple[Path, Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Keep real Galaxy metadata/artifact owners in their observed child; stub HTTP only."""
    lock, artifacts, _expected = supplied
    configuration = tmp_path / "gludd-galaxy-http.json"
    trace = tmp_path / "gludd-galaxy-http.jsonl"
    configuration.write_text(json.dumps({
        "entries": json.loads(lock.read_text())["collections"], "artifacts": str(artifacts),
        "trace": str(trace), "failure": "", "parent_pid": os.getpid(),
    }))
    injection = tmp_path / "gludd-galaxy-http"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "import io, json, os, ssl, sys, time\n"
        "from pathlib import Path\n"
        "from urllib.error import HTTPError\n"
        "from ansible.galaxy import api\n"
        "from ansible.galaxy.collection import concrete_artifact_manager as artifacts\n"
        "settings = json.loads(Path(os.environ['GLUDD_GALAXY_HTTP_FIXTURE']).read_text())\n"
        "def record(kind, **values):\n"
        "    with Path(settings['trace']).open('a') as output:\n"
        "        output.write(json.dumps(dict(kind=kind, pid=os.getpid(), "
        "pgid=os.getpgid(0), sid=os.getsid(0), **values)) + '\\n')\n"
        "def refuse_network(event, arguments):\n"
        "    if event.startswith('socket.'):\n"
        "        raise AssertionError('live network is forbidden in Galaxy fixture')\n"
        "sys.addaudithook(refuse_network)\n"
        "def refuse_versions(*args, **kwargs):\n"
        "    record('version-enumeration')\n"
        "    raise AssertionError('version enumeration forbidden for reviewed acquisition')\n"
        "api.GalaxyAPI.get_collection_versions = refuse_versions\n"
        "def http(url, *args, **kwargs):\n"
        "    record('http', url=url, headers=kwargs.get('headers'), "
        "validate_certs=kwargs.get('validate_certs'), timeout=kwargs.get('timeout'))\n"
        "    if settings['failure'] == 'warning': print('[WARNING]: fixture native diagnostic', file=sys.stderr)\n"
        "    if settings['failure'] == 'auth': raise HTTPError(url, 403, 'fixture auth refused', {}, None)\n"
        "    if settings['failure'] == 'tls': raise ssl.SSLError('fixture TLS certificate rejected')\n"
        "    for entry in settings['entries']:\n"
        "        namespace, name = entry['name'].split('.')\n"
        "        source = 'https://artifacts.fixture.invalid/canonical/' + entry['artifact'] + '?reviewed=1'\n"
        "        if url == source:\n"
        "            content = (Path(settings['artifacts']) / entry['artifact']).read_bytes()\n"
        "            if settings['failure'] == 'content': content += b'changed'\n"
        "            return io.BytesIO(content)\n"
        "        if url.endswith('/collections/' + namespace + '/' + name + '/versions/' + entry['version'] + '/'):\n"
        "            if settings['failure'] == 'timeout': time.sleep(30)\n"
        "            data = dict(namespace=dict(name=namespace), collection=dict(name=name), "
        "version=entry['version'], download_url=source, artifact=dict(sha256=entry['sha256'][7:]), "
        "metadata=dict(dependencies={}), href=url, signatures=[])\n"
        "            if name == 'main': data['metadata']['dependencies'] = {'fixture.dependency': '>=1.0.0'}\n"
        "            if settings['failure'] == 'identity': data['version'] = '9.0.0'\n"
        "            if settings['failure'] == 'hash': data['artifact']['sha256'] = 'f' * 64\n"
        "            if settings['failure'] == 'url': data['download_url'] = settings['url_override']\n"
        "            if settings['failure'] == 'dependency':\n"
        "                data['metadata']['dependencies'] = {'fixture.missing': '*'}\n"
        "            if settings['failure'] == 'dependency-type': data['metadata']['dependencies'] = None\n"
        "            record('metadata', name=entry['name'], version=entry['version'])\n"
        "            return io.BytesIO(json.dumps(data).encode())\n"
        "    if url in {entry['origin'] for entry in settings['entries']} or url.endswith('/api/'):\n"
        "        return io.BytesIO(json.dumps(dict(available_versions=dict(v3='v3/'))).encode())\n"
        "    raise AssertionError('unexpected native Galaxy URL: ' + url)\n"
        "api.open_url = http\n"
        "artifacts.open_url = http\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(injection))
    monkeypatch.setenv("GLUDD_GALAXY_HTTP_FIXTURE", str(configuration))
    return configuration, trace


@pytest.mark.parametrize("origin", ["https://galaxy.ansible.com/", "https://private.fixture.invalid/custom/hub/"])
def test_reviewed_acquire_uses_native_exact_metadata_without_version_enumeration(
    supplied: tuple[Path, Path, str], native_acquire_http: tuple[Path, Path],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str,
) -> None:
    """Exercise the actual pinned core (2.19/2.21) in the real bounded observed child."""
    lock, _artifacts, expected = supplied
    configuration, trace = native_acquire_http
    document = cast(_CollectionLock, json.loads(lock.read_text()))
    for entry in document["collections"]:
        entry["origin"] = origin
    lock.write_text(json.dumps(document))
    expected = digest(lock)
    settings = json.loads(configuration.read_text())
    settings["entries"] = document["collections"]
    configuration.write_text(json.dumps(settings))
    destination = tmp_path / "gludd-exact-native-generation"
    previous = lock.read_bytes()
    calls: list[tuple[list[str], int]] = []
    logs: list[str] = []
    run = owner._run_collection_command

    def observed(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        calls.append((command, arguments["max_seconds"]))
        token = Path(arguments["environment"]["ANSIBLE_HOME"]) / "galaxy_token"
        token.write_text("token: gludd-owned-fixture-token\n")
        result = run(command, log, **arguments)
        logs.append(log.read_text())
        return result

    monkeypatch.setattr(owner, "_run_collection_command", observed)
    assert owner.prepare_collection_artifacts(
        "acquire", lock, destination, required_versions={"fixture.main": "1.0.0"},
        origin=origin, expected_lock_sha256=expected,
    ) == 0, logs
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert not any(event["kind"] == "version-enumeration" for event in events)
    assert sorted(event["name"] for event in events if event["kind"] == "metadata") == [
        "fixture.dependency", "fixture.main",
    ]
    transfers = [event for event in events if event["kind"] == "http"
                 and event["url"].startswith("https://artifacts.fixture.invalid/")]
    assert len(transfers) == 2
    assert all(event["validate_certs"] is True for event in transfers)
    assert all(event["headers"] == {"Authorization": "Token gludd-owned-fixture-token"} for event in transfers)
    assert all(event["pid"] != os.getpid() and event["pid"] == event["pgid"] == event["sid"] for event in events)
    assert len(calls) == 1 and calls[0][1] == 120
    assert lock.read_bytes() == previous == (destination / "collections.lock.json").read_bytes()


@pytest.mark.parametrize("failure, message", [
    ("identity", "metadata identity differs"), ("hash", "metadata checksum differs"),
    ("dependency", "missing staged collection manifest"),
    ("dependency-type", "dependency metadata is invalid"),
    ("content", "Mismatch artifact hash with downloaded file"),
    ("size", "artifact size differs"), ("manifest-dependency", "missing staged collection manifest"),
    ("auth", "fixture auth refused"), ("tls", "fixture TLS certificate rejected"),
    ("warning", "fixture native diagnostic"),
])
def test_native_reviewed_acquire_rejects_metadata_transfer_and_manifest_failures(
    supplied: tuple[Path, Path, str], native_acquire_http: tuple[Path, Path],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, message: str,
) -> None:
    lock, artifacts, expected = supplied
    configuration, trace = native_acquire_http
    settings = json.loads(configuration.read_text())
    settings["failure"] = failure
    if failure in {"size", "manifest-dependency"}:
        document = cast(_CollectionLock, json.loads(lock.read_text()))
        if failure == "size":
            document["collections"][0]["size"] += 1
        else:
            document["collections"][0] = package(artifacts, "fixture.main", {"fixture.missing": ">=1.0.0"})
        lock.write_text(json.dumps(document))
        settings["entries"] = document["collections"]
        expected = digest(lock)
    configuration.write_text(json.dumps(settings))
    previous, destination = lock.read_bytes(), tmp_path / "gludd-failed-native-generation"
    logs: list[str] = []
    states: list[Path] = []
    run = owner._run_collection_command

    def observed(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        states.append(log.parent)
        result = run(command, log, **arguments)
        logs.append(log.read_text())
        return result

    monkeypatch.setattr(owner, "_run_collection_command", observed)
    assert owner.prepare_collection_artifacts(
        "acquire", lock, destination, required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected,
    ) != 0
    if failure != "manifest-dependency":
        assert message in "".join(logs)
    assert lock.read_bytes() == previous and not destination.exists()
    assert states and all(not state.exists() for state in states)
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert not any(event["kind"] == "version-enumeration" for event in events)
    if failure in {"identity", "hash", "dependency", "dependency-type", "auth", "tls"}:
        assert not any(event["kind"] == "http" and "artifacts.fixture.invalid" in event["url"] for event in events)


@pytest.mark.parametrize("url", [
    "http://artifacts.fixture.invalid/unsafe", "https://user:secret@artifacts.fixture.invalid/unsafe",
    "https://artifacts.fixture.invalid/unsafe#fragment", "file:///fixture.tar.gz",
])
def test_native_acquire_rejects_unsafe_canonical_artifact_urls(
    supplied: tuple[Path, Path, str], native_acquire_http: tuple[Path, Path], tmp_path: Path, url: str,
) -> None:
    lock, _artifacts, expected = supplied
    configuration, trace = native_acquire_http
    settings = json.loads(configuration.read_text())
    settings.update(failure="url", url_override=url)
    configuration.write_text(json.dumps(settings))
    destination = tmp_path / "gludd-unsafe-native-url"
    assert owner.prepare_collection_artifacts(
        "acquire", lock, destination, required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected,
    ) != 0
    assert not destination.exists()
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert not any(event["kind"] == "http" and event["url"] == url for event in events)


def test_native_acquire_network_uses_the_single_observed_deadline_and_teardown(
    supplied: tuple[Path, Path, str], native_acquire_http: tuple[Path, Path],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, _artifacts, expected = supplied
    configuration, _trace = native_acquire_http
    settings = json.loads(configuration.read_text())
    settings["failure"] = "timeout"
    configuration.write_text(json.dumps(settings))
    outcomes: list[tuple[int, int, str]] = []
    states: list[Path] = []
    run = owner._run_collection_command

    def observed(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        states.append(log.parent)
        result = run(command, log, **arguments)
        status = json.loads((log.parent / "observed/gludd-collection-stage/current.json").read_text())
        outcomes.append((result, arguments["max_seconds"], status["termination_reason"]))
        assert "ANSIBLE_COLLECTION_METADATA" in log.read_text()
        return result

    monkeypatch.setattr(owner, "_run_collection_command", observed)
    destination = tmp_path / "gludd-timed-out-native-acquire"
    assert owner.prepare_collection_artifacts(
        "acquire", lock, destination, required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected, timeout_seconds=1,
    ) != 0
    assert outcomes == [(124, 1, "max-runtime-timeout")]
    assert not destination.exists() and all(not state.exists() for state in states)


@pytest.fixture
def in_process_acquire_http(
    supplied: tuple[Path, Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> _NativeAcquireHTTP:
    """Measure the worker itself while retaining real native API, token and transfer checks."""
    import ansible.constants as constants
    from ansible.galaxy import api
    from ansible.galaxy.collection import concrete_artifact_manager

    lock, artifacts, _expected = supplied
    state = tmp_path / "gludd-in-process-native-acquire"
    output = state / "artifacts"
    output.mkdir(parents=True)
    token = state / "galaxy_token"
    token.write_text("token: gludd-owned-in-process-token\n")
    monkeypatch.setattr(constants, "GALAXY_TOKEN_PATH", str(token))
    monkeypatch.setattr(constants, "GALAXY_CACHE_DIR", str(state / "response-cache"))
    fixture: _NativeAcquireHTTP = {"output": output, "metadata": {}, "suffix": b"", "requests": []}

    def refuse_versions(*_args: object, **_options: object) -> None:
        pytest.fail("reviewed native worker enumerated versions")

    def http(url: str, **options: object) -> io.BytesIO:
        fixture["requests"].append((url, options))
        entries = cast(_CollectionLock, json.loads(lock.read_text()))["collections"]
        for entry in entries:
            namespace, name = entry["name"].split(".")
            source = f"https://artifacts.fixture.invalid/native/{entry['artifact']}?reviewed=1"
            if url == source:
                return io.BytesIO((artifacts / entry["artifact"]).read_bytes() + fixture["suffix"])
            if url.endswith(f"/collections/{namespace}/{name}/versions/{entry['version']}/"):
                dependencies = {"fixture.dependency": ">=1.0.0"} if name == "main" else {}
                metadata: dict[str, object] = {
                    "namespace": {"name": namespace}, "collection": {"name": name}, "version": entry["version"],
                    "download_url": source, "artifact": {"sha256": entry["sha256"][7:]},
                    "metadata": {"dependencies": dependencies}, "href": url, "signatures": [],
                }
                metadata.update(fixture["metadata"])
                return io.BytesIO(json.dumps(metadata).encode())
        if url in {entry["origin"] for entry in entries} or url.endswith("/api/"):
            return io.BytesIO(b'{"available_versions": {"v3": "v3/"}}')
        pytest.fail(f"unexpected native HTTP request: {url}")

    monkeypatch.setattr(api.GalaxyAPI, "get_collection_versions", refuse_versions)
    monkeypatch.setattr(api, "open_url", http)
    monkeypatch.setattr(concrete_artifact_manager, "open_url", http)
    return fixture


def test_exact_worker_in_process_retains_native_api_auth_integrity_and_cleanup(
    supplied: tuple[Path, Path, str], in_process_acquire_http: _NativeAcquireHTTP,
) -> None:
    lock, _artifacts, expected = supplied
    previous = lock.read_bytes()
    output = in_process_acquire_http["output"]
    assert owner._acquire_reviewed_collections(
        lock, output, required_versions={"fixture.main": "1.0.0"}, origin="https://galaxy.ansible.com/",
        expected_lock_sha256=expected, timeout_seconds=120,
    ) == 0
    requests = in_process_acquire_http["requests"]
    assert len([url for url, _options in requests if "/versions/1.0.0/" in url]) == 2
    transfers = [options for url, options in requests if "artifacts.fixture.invalid" in url]
    assert len(transfers) == 2
    assert all(options["validate_certs"] is True for options in transfers)
    assert all(options["headers"] == {"Authorization": "Token gludd-owned-in-process-token"} for options in transfers)
    entries = cast(_CollectionLock, json.loads(lock.read_text()))["collections"]
    assert {path.name for path in output.iterdir()} == {entry["artifact"] for entry in entries}
    assert all(digest(output / entry["artifact"]) == entry["sha256"] for entry in entries)
    assert lock.read_bytes() == previous
    assert {path.name for path in output.parent.iterdir()} == {"artifacts", "response-cache", "galaxy_token"}


@pytest.mark.parametrize("field, value, message", [
    ("namespace", {"name": "unreviewed"}, "metadata identity differs"),
    ("collection", {"name": "unreviewed"}, "metadata identity differs"),
    ("version", "9.0.0", "metadata identity differs"),
    ("artifact", {"sha256": "f" * 64}, "metadata checksum differs"),
    ("download_url", "http://artifacts.fixture.invalid/file.tar.gz", "credential-free HTTPS"),
    ("download_url", "https://user:secret@artifacts.fixture.invalid/file.tar.gz", "credential-free HTTPS"),
    ("download_url", "https://artifacts.fixture.invalid/file.tar.gz#fragment", "credential-free HTTPS"),
    ("metadata", {"dependencies": None}, "dependency metadata is invalid"),
    ("metadata", {"dependencies": {"fixture.main": 1}}, "dependency metadata is invalid"),
    ("metadata", {"dependencies": {"fixture.missing": "*"}}, "missing staged collection manifest"),
    ("metadata", {"dependencies": {"fixture.main": ">=9.0.0"}}, "staged collection dependency mismatch"),
])
def test_exact_worker_in_process_rejects_unreviewed_native_metadata_before_transfer(
    supplied: tuple[Path, Path, str], in_process_acquire_http: _NativeAcquireHTTP,
    capsys: pytest.CaptureFixture[str], field: str, value: object, message: str,
) -> None:
    lock, _artifacts, expected = supplied
    in_process_acquire_http["metadata"][field] = value
    assert owner._acquire_reviewed_collections(
        lock, in_process_acquire_http["output"], required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected, timeout_seconds=120,
    ) == 1
    assert message in capsys.readouterr().err
    assert not any("artifacts.fixture.invalid" in url for url, _options in in_process_acquire_http["requests"])
    assert not list(in_process_acquire_http["output"].iterdir())
    assert digest(lock) == expected


@pytest.mark.parametrize("failure, message", [
    ("content", "Mismatch artifact hash with downloaded file"), ("size", "artifact size differs"),
])
def test_exact_worker_in_process_retains_native_checksum_and_reviewed_size(
    supplied: tuple[Path, Path, str], in_process_acquire_http: _NativeAcquireHTTP,
    capsys: pytest.CaptureFixture[str], failure: str, message: str,
) -> None:
    lock, _artifacts, expected = supplied
    if failure == "content":
        in_process_acquire_http["suffix"] = b"changed"
    else:
        document = cast(_CollectionLock, json.loads(lock.read_text()))
        for entry in document["collections"]:
            entry["size"] += 1
        lock.write_text(json.dumps(document))
        expected = digest(lock)
    assert owner._acquire_reviewed_collections(
        lock, in_process_acquire_http["output"], required_versions={"fixture.main": "1.0.0"},
        origin="https://galaxy.ansible.com/", expected_lock_sha256=expected, timeout_seconds=120,
    ) == 1
    assert message in capsys.readouterr().err
    assert not list(in_process_acquire_http["output"].iterdir())
    assert digest(lock) == expected


@pytest.mark.parametrize("failure", ["timeout", "origin", "digest", "missing", "populated", "symlink"])
def test_exact_worker_rejects_unsafe_admission_before_any_native_http(
    supplied: tuple[Path, Path, str], in_process_acquire_http: _NativeAcquireHTTP,
    tmp_path: Path, failure: str,
) -> None:
    lock, _artifacts, expected = supplied
    output, origin, timeout = in_process_acquire_http["output"], "https://galaxy.ansible.com/", 120
    if failure == "timeout":
        timeout = 0
    elif failure == "origin":
        origin = "https://unreviewed.fixture.invalid/"
    elif failure == "digest":
        expected = "sha256:" + "0" * 64
    elif failure == "missing":
        output = tmp_path / "absent-output"
    elif failure == "populated":
        (output / "another-owner").write_text("retain")
    else:
        link = tmp_path / "gludd-output-link"
        link.symlink_to(output, target_is_directory=True)
        output = link
    assert owner._acquire_reviewed_collections(
        lock, output, required_versions={"fixture.main": "1.0.0"}, origin=origin,
        expected_lock_sha256=expected, timeout_seconds=timeout,
    ) == 1
    assert not in_process_acquire_http["requests"]
    if failure == "populated":
        assert (output / "another-owner").read_text() == "retain"


@pytest.mark.parametrize("validate_only", [False, True])
def test_native_acquire_is_not_a_public_cli_transport_escape(
    supplied: tuple[Path, Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], validate_only: bool,
) -> None:
    """Only the existing bounded stage owner may enter the private acquisition worker."""
    lock, _artifacts, expected = supplied
    previous = lock.read_bytes()
    requirements = tmp_path / "gludd-cli-reviewed-requirements.json"
    requirements.write_text(json.dumps({"collections": [{"name": "fixture.main", "version": "1.0.0"}]}))
    output = tmp_path / "gludd-plan-only-output"
    monkeypatch.setattr(owner, "_acquire_reviewed_collections",
                        lambda *_args, **_options: pytest.fail("public CLI entered private native transport"))
    monkeypatch.setattr(owner, "_run_collection_command",
                        lambda *_args, **_options: pytest.fail("rejected CLI started a child"))
    command = [
        "native-acquire-collections", "--collection-lock", str(lock), "--artifact-root", str(output),
        "--collection-lock-sha256", expected, "--collection-requirements", str(requirements),
    ]
    if validate_only:
        command.append("--validate-only")
    with pytest.raises(SystemExit) as rejected:
        owner.main(command)
    assert rejected.value.code == 2
    assert "invalid choice: 'native-acquire-collections'" in capsys.readouterr().err
    assert not output.exists() and lock.read_bytes() == previous


@pytest.mark.parametrize("failure", ["timeout", "tamper", "missing-dependency"])
def test_failed_acquisition_never_publishes_or_changes_reviewed_lock(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    lock, root, expected = supplied
    previous = lock.read_bytes()
    destination = tmp_path / "rejected-generation"
    working: list[Path] = []

    def downloader(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        downloaded = download_path(command)
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


@pytest.mark.parametrize("mode", ["offline", "acquire", "refresh"])
def test_stage_cli_plan_has_explicit_inputs_and_no_acquisition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    requirements = tmp_path / "required.yml"
    requirements.write_text(json.dumps({"collections": [{"name": "fixture.main", "version": "1.0.0"}]}))
    monkeypatch.setattr(owner, "_run_collection_command", lambda *args, **kwargs: pytest.fail("plan executed"))
    monkeypatch.setattr(owner, "_acquire_reviewed_collections",
                        lambda *_args, **_options: pytest.fail("plan entered native worker"))
    assert owner.main([
        "stage-collections", "--collection-stage-mode", mode, "--collection-lock", str(tmp_path / "lock.json"),
        "--artifact-root", str(tmp_path / "artifacts"), "--collection-requirements", str(requirements),
        "--collection-timeout-seconds", "120", "--galaxy-origin", "https://galaxy.ansible.com/", "--validate-only",
    ]) == 0
    assert "proof=none" in capsys.readouterr().out
    assert not (tmp_path / "lock.json").exists() and not (tmp_path / "artifacts").exists()


def test_acquisition_retains_independent_lock_binding_during_native_command(
    supplied: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    lock, root, expected = supplied

    def downloader(command: list[str], log: Path, **arguments: Unpack[_CollectionCommandArguments]) -> int:
        downloaded = download_path(command)
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
            downloaded = download_path(command)
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
    # The observer already owns the exclusively allocated fresh trace; the child must append.
    assert (tmp_path / "observed/gludd-collection-lifecycle/gludd-private-child.pytest.jsonl").read_text() == (
        "gludd-private-child 0"
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


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_native_collection_progress_is_retained_before_timeout_with_buffered_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str,
) -> None:
    import psutil

    for variable in ("ANSIBLE_COLLECTIONS_PATH", "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH", "ANSIBLE_CONFIG"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("PYTHONUNBUFFERED", "0")
    caller_environment = dict(os.environ)
    state = tmp_path / "gludd-native-collection-buffering"
    state.mkdir()
    environment = owner._collection_environment(state)
    ready = state / "gludd-owned-descendant.pid"
    log = state / "native-progress.log"
    receipt = state / "observed/gludd-collection-stage/current.json"
    retained_while_running: list[bytes] = []
    original_write = stream._write_chunk

    def write_chunk(chunk: bytes, log_file: BinaryIO, *, quiet: bool) -> None:
        original_write(chunk, log_file, quiet=quiet)
        status = cast(_TerminalObservedStatus, json.loads(receipt.read_text()))
        if status["state"] == "running":
            retained_while_running.append(log.read_bytes())

    monkeypatch.setattr(stream, "_write_chunk", write_chunk)
    descendant = (
        "import os, signal, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    leader = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"descendant = subprocess.Popen([sys.executable, '-c', {descendant!r}, sys.argv[1]]); "
        "\nwhile not Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        f"print('native collection progress', file=sys.{channel})\n"
        "time.sleep(30)\n"
    )
    try:
        result = owner._run_collection_command(
            [sys.executable, "-c", leader, str(ready)], log,
            max_seconds=2, environment=environment, cwd=state,
        )
        status = cast(_TerminalObservedStatus, json.loads(receipt.read_text()))
        assert result == stream.TIMEOUT_FAILURE
        assert status["state"] == "timed_out" and status["termination_reason"] == "max-runtime-timeout"
        assert not psutil.pid_exists(status["child_pid"])
        assert not psutil.pid_exists(int(ready.read_text()))
        assert b"native collection progress\n" in retained_while_running
        assert log.read_bytes() == b"native collection progress\n"
        assert dict(os.environ) == caller_environment
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
