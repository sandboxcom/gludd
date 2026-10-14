"""Native bridge controls; fixture success is never deployed acceptance."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ARTIFACT = Path(__file__).resolve().parents[2]
REPO = ARTIFACT if (ARTIFACT / "Makefile").is_file() else Path.cwd()
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "collections"))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load proposed files independently of the unchanged main checkout."""
    module = _load("native_item_acceptance", ARTIFACT / "scripts/backlog_native_acceptance.py")
    artifacts = _load("native_artifacts", ARTIFACT / "scripts/ansible_runtime_artifacts.py")
    monkeypatch.setattr(artifacts, "ROOT", REPO)
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(artifacts, "_smoke_environment", lambda *_args: 0)
    monkeypatch.setattr(artifacts.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(module, "artifacts", artifacts)
    return module


@pytest.fixture
def proof(native: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Use real collection operations and real signature verification in fixtures."""
    from ansible_collections.general_ludd.git_release.plugins.module_utils import provenance

    installed = tmp_path / "installed/ansible_collections/general_ludd/git_release"
    installed.mkdir(parents=True)
    for relative in native.INSTALLED_FILES:
        destination = installed / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = REPO / "collections/ansible_collections/general_ludd/git_release" / relative
        destination.write_bytes(source.read_bytes() if source.is_file() else b"{}\n")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name, content in native.FIXED_INPUTS.items():
        (inputs / name).write_bytes(content)
    lock = json.dumps({"packages": {name: metadata.version(name) for name in (
        "ansible-core", "defusedxml")}}, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "lock.json").write_bytes(lock)
    installed_lock = tmp_path / "runtime-lock/dependency-lock.json"
    installed_lock.parent.mkdir()
    installed_lock.write_bytes(lock)
    namespace = native.project_namespace(REPO)
    binding = native.NativeItemBinding(
        item_id="S39", candidate_sha="a" * 40, project_namespace=namespace,
        runtime="podman", image="example/gludd@sha256:" + "b" * 64,
        platform="linux/amd64", deployment_target=namespace + "-ee-canary",
        deployment_generation="c" * 64, invocation_id="fixture-native-invocation",
        observer_label="backlog-audit", artifact_root=tmp_path,
        artifact_path="subject.json", artifact_sha256="0" * 64,
        lock_path="lock.json", lock_sha256=hashlib.sha256(lock).hexdigest(),
        release_id="v0.1.2", builder_identity="github:sandboxcom/gludd/build",
    )
    installed_files = {name: hashlib.sha256((installed / name).read_bytes()).hexdigest()
                       for name in native.INSTALLED_FILES}
    signature_key = Ed25519PrivateKey.generate()
    state = SimpleNamespace(binding=binding, installed=installed, inputs=inputs, installed_lock=installed_lock,
                            calls=[], mode="ok", records={}, signatures={}, signer_calls=0,
                            image_fields=None, stderr_phase=None, stderr_message="")

    def seal(selected: Any) -> Any:
        subject = {"schema_version": 1, "identity": native.manifest_identity(selected),
                   "probe": native.probe_identity(selected.item_id), "installed_files": installed_files}
        payload = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
        (tmp_path / selected.artifact_path).write_bytes(payload)
        selected = replace(selected, artifact_sha256=hashlib.sha256(payload).hexdigest())
        record = provenance.build_provenance(
            artifact_name=selected.artifact_path, artifact_bytes=payload,
            dependency_lock_bytes=lock, builder_identity=selected.builder_identity,
            release_id=selected.release_id, source_sha=selected.candidate_sha,
            signature_state=provenance.SignatureState.VERIFIED,
        )
        attestation_payload = native.attestation_payload(record)
        state.records[selected.item_id] = record
        state.signatures[selected.item_id] = signature_key.sign(attestation_payload)
        return selected

    def mutate_subject(update: Callable[[dict[str, Any]], None]) -> None:
        subject = json.loads((tmp_path / state.binding.artifact_path).read_bytes())
        update(subject)
        payload = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
        (tmp_path / state.binding.artifact_path).write_bytes(payload)
        state.binding = replace(state.binding, artifact_sha256=hashlib.sha256(payload).hexdigest())
        record = provenance.build_provenance(
            artifact_name=state.binding.artifact_path, artifact_bytes=payload, dependency_lock_bytes=lock,
            builder_identity=state.binding.builder_identity, release_id=state.binding.release_id,
            source_sha=state.binding.candidate_sha, signature_state=provenance.SignatureState.VERIFIED)
        state.records[state.binding.item_id] = record
        state.signatures[state.binding.item_id] = signature_key.sign(native.attestation_payload(record))

    def signature_owner(payload: bytes, selected: Any) -> str | None:
        state.signer_calls += 1
        if state.mode == "signer-missing":
            return None
        if state.mode == "signer-false":
            return "f" * 64
        signature_key.public_key().verify(state.signatures[selected.item_id], payload)
        return hashlib.sha256(payload).hexdigest()

    def invocation_owner(_root: Path, selected: Any) -> tuple[object, ...]:
        return (selected.observer_label, selected.invocation_id, "start", 42, os.getpid())

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        state.calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            if "capture_output" not in kwargs:
                return subprocess.CompletedProcess(command, 0)
            if state.mode == "inspect-failed":
                return subprocess.CompletedProcess(command, 7, "", "")
            if state.mode == "inspect-ambiguous":
                return subprocess.CompletedProcess(command, 0, "[]", "")
            image_id = "sha256:" + "d" * 64
            image = {"Id": image_id, "Digest": "sha256:" + "b" * 64,
                     "Os": "linux", "Architecture": "amd64"}
            if state.image_fields is not None:
                image.pop("Digest")
                image.update(state.image_fields)
            if state.mode == "platform":
                image["Architecture"] = "arm64"
            stderr = state.stderr_message if state.stderr_phase == "image" else ""
            return subprocess.CompletedProcess(command, 0, json.dumps([image]), stderr)
        if command[1:3] == ["container", "inspect"]:
            labels = {"org.general-ludd.source-sha": state.binding.candidate_sha,
                      "org.general-ludd.project": state.binding.project_namespace}
            container = {"Id": state.binding.deployment_generation,
                         "Image": "sha256:" + "d" * 64,
                         "Name": "/" + state.binding.deployment_target,
                         "Config": {"Labels": labels}, "State": {"Running": True}}
            if state.mode == "wrong-sha":
                labels["org.general-ludd.source-sha"] = "e" * 40
            if state.mode == "generation":
                container["Id"] = "e" * 64
            stderr = state.stderr_message if state.stderr_phase == "container" else ""
            return subprocess.CompletedProcess(command, 0, json.dumps([container]), stderr)
        assert command[1] == "exec", command
        if state.mode == "import-only":
            return subprocess.CompletedProcess(command, 0, "ANSIBLE_EE_IMPORT_OK\n", "")
        if state.mode == "forged-result":
            return subprocess.CompletedProcess(command, 0, '{"verified":true}', "")
        program = command[-1].replace(native.INSTALLED_ROOT, str(installed)).replace(
            native.PROBE_ROOT, str(inputs))
        if hasattr(native, "INSTALLED_LOCK_ROOT"):
            program = program.replace(native.INSTALLED_LOCK_ROOT, str(installed_lock.parent))
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(installed.parents[2])
        result = real_run([sys.executable, "-W", "error", "-c", program],
                          input=kwargs["input"], text=True, capture_output=True,
                          check=False, timeout=30, env=environment)
        if state.mode == "after-generation":
            state.mode = "generation"
        elif state.mode == "after-artifact":
            (tmp_path / state.binding.artifact_path).write_bytes(b"changed")
        elif state.mode == "after-signer":
            state.mode = "signer-missing"
        if state.stderr_phase == "exec":
            return subprocess.CompletedProcess(command, result.returncode, result.stdout,
                                               result.stderr + state.stderr_message)
        return result

    real_run = subprocess.run
    state.binding = seal(binding)
    state.seal = seal
    state.mutate_subject = mutate_subject
    state.signature_owner = signature_owner
    state.invocation_owner = invocation_owner
    state.record_owner = lambda selected: state.records[selected.item_id]
    monkeypatch.setattr(native.artifacts.subprocess, "run", run)
    return state


def _validator(native: ModuleType, proof: SimpleNamespace, **kwargs: Any) -> Callable[..., object]:
    """Exercise the private probe in fixtures, without granting public completion."""
    def diagnostic(task: dict[str, object]) -> bool:
        if kwargs.get("validate_only") or task.get("id") != proof.binding.item_id:
            return False
        owner = native._NativeItemOwner(REPO, proof.binding, proof.record_owner,
                                        proof.signature_owner, proof.invocation_owner)
        status = native.artifacts.verify_environment(
            proof.binding.runtime, proof.binding.image, False, item_owner=owner)
        accepted = status == 0 and owner.accepted is True
        if not accepted:
            print("FIXTURE_NATIVE_ITEM state=INCOMPLETE release_ready=false gate_evidence=false")
        return accepted

    return diagnostic


@pytest.mark.parametrize("item", ["S39", "S42"])
def test_real_fixed_operations_and_signatures_execute_one_item_in_fixtures(
    native: ModuleType, proof: SimpleNamespace, item: str,
) -> None:
    proof.binding = proof.seal(replace(proof.binding, item_id=item))
    assert _validator(native, proof)({"id": item}) is True
    assert proof.signer_calls == 2
    exec_calls = [call for call in proof.calls if call[1] == "exec"]
    assert len(exec_calls) == 1
    assert exec_calls[0][2:4] == ["--interactive", proof.binding.deployment_generation]
    assert "--pull" not in exec_calls[0]
    production = native.make_native_deployment_validator(
        REPO, [proof.binding], record_owner=proof.record_owner,
        signature_owner=proof.signature_owner, invocation_owner=proof.invocation_owner)
    assert production({"id": item}) is False


@pytest.mark.parametrize("mode", [
    "signer-missing", "signer-false", "wrong-sha", "platform", "generation",
    "import-only", "forged-result", "after-generation", "after-artifact", "after-signer",
])
def test_missing_forged_and_changed_proofs_fail_closed(
    native: ModuleType, proof: SimpleNamespace, mode: str, capsys: pytest.CaptureFixture[str],
) -> None:
    proof.mode = mode
    assert _validator(native, proof)({"id": "S39", "deployment_ready": True}) is False
    assert "INCOMPLETE" in capsys.readouterr().out


def test_validate_only_and_unsupported_items_execute_nothing(
    native: ModuleType, proof: SimpleNamespace,
) -> None:
    assert _validator(native, proof, validate_only=True)({"id": "S39"}) is False
    assert _validator(native, proof)({"id": "S40", "deployment_ready": True}) is False
    assert not proof.calls


@pytest.mark.parametrize("field,value", [
    ("candidate_sha", "short"), ("project_namespace", "other-project"),
    ("image", "gludd:latest"), ("runtime", "shell"),
    ("deployment_target", "unrelated"), ("deployment_generation", "short"),
    ("invocation_id", "../escape"), ("platform", "linux/unknown"),
])
def test_invalid_independent_identity_never_starts_runtime(
    native: ModuleType, proof: SimpleNamespace, field: str, value: str,
) -> None:
    proof.binding = replace(proof.binding, **{field: value})
    assert _validator(native, proof)({"id": "S39"}) is False
    assert not proof.calls


def test_wrong_item_and_missing_trusted_owners_fail_closed(
    native: ModuleType, proof: SimpleNamespace,
) -> None:
    validator = native.make_native_deployment_validator(REPO, [proof.binding])
    assert validator({"id": "S39", "signature_state": "verified"}) is False
    assert _validator(native, proof)({"id": "#65"}) is False
    assert _validator(native, proof)({"id": "SEARXNG"}) is False
    assert not proof.calls


def test_mocked_native_verifier_success_does_not_authorize_completion(
    native: ModuleType, proof: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native.artifacts, "verify_environment", lambda *_args, **_kwargs: 0)
    assert _validator(native, proof)({"id": "S39"}) is False


def test_default_invocation_reuses_existing_owner_and_rejects_absent_pointer(
    native: ModuleType, proof: SimpleNamespace,
) -> None:
    validator = native.make_native_deployment_validator(
        proof.binding.artifact_root, [proof.binding], record_owner=proof.record_owner,
        signature_owner=proof.signature_owner)
    assert validator({"id": "S39"}) is False
    assert not proof.calls


def test_fixed_probe_has_rejection_controls_and_immutable_input_hashes(native: ModuleType) -> None:
    identity = native.probe_identity("S39")
    assert identity["name"] == "git-release-artifact-v1"
    assert len(identity["definition_sha256"]) == len(identity["input_sha256"]) == 64
    assert "verify_release_artifact" in native.NATIVE_PROGRAM
    assert "triage_junit_report" in native.NATIVE_PROGRAM
    assert "ArtifactVerificationError" in native.NATIVE_PROGRAM
    assert "PipelineTriageError" in native.NATIVE_PROGRAM


def test_wrong_installed_lock_is_not_authorized_by_matching_receipt(
    native: ModuleType, proof: SimpleNamespace,
) -> None:
    proof.installed_lock.write_bytes(b'{"packages":{"ansible-core":"0"}}')
    assert _validator(native, proof)({"id": "S39"}) is False


def test_production_factory_rejects_a_substituted_native_runner(
    native: ModuleType, proof: SimpleNamespace,
) -> None:
    validator = native.make_native_deployment_validator(
        REPO, [proof.binding], record_owner=proof.record_owner,
        signature_owner=proof.signature_owner, invocation_owner=proof.invocation_owner)
    assert validator({"id": "S39"}) is False


@pytest.mark.parametrize("mutation", ["item", "probe", "input", "files", "schema"])
def test_valid_signature_cannot_override_reviewed_item_and_probe(
    native: ModuleType, proof: SimpleNamespace, mutation: str,
) -> None:
    def update(subject: dict[str, Any]) -> None:
        if mutation == "item":
            subject["identity"]["item_id"] = "S42"
        elif mutation == "probe":
            subject["probe"]["definition_sha256"] = "f" * 64
        elif mutation == "input":
            subject["probe"]["input_sha256"] = "f" * 64
        elif mutation == "files":
            subject["installed_files"] = {}
        else:
            subject["schema_version"] = True

    proof.mutate_subject(update)
    assert _validator(native, proof)({"id": "S39"}) is False
    assert not proof.calls


@pytest.mark.parametrize("input_name", ["artifact.bin", "lock.json"])
def test_changed_native_probe_input_is_rejected(
    native: ModuleType, proof: SimpleNamespace, input_name: str,
) -> None:
    (proof.inputs / input_name).write_bytes(b"changed")
    assert _validator(native, proof)({"id": "S39"}) is False


def test_changed_installed_native_code_is_rejected(native: ModuleType, proof: SimpleNamespace) -> None:
    (proof.installed / "plugins/module_utils/pipeline_triage.py").write_bytes(b"changed")
    assert _validator(native, proof)({"id": "S39"}) is False


def test_successful_item_is_independent_of_an_unsupported_item(native: ModuleType, proof: SimpleNamespace) -> None:
    validator = _validator(native, proof)
    assert validator({"id": "S46"}) is False
    assert validator({"id": "S39"}) is True


@pytest.mark.parametrize("mode", [
    "ok", "missing", "pointer", "duplicate", "state", "child", "owner", "stale", "head", "dirty",
])
def test_existing_invocation_owner_requires_current_live_child(
    native: ModuleType, proof: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    stream_command = importlib.import_module("scripts.stream_command")

    pointer = {"root": str(proof.binding.artifact_root), "label": proof.binding.observer_label,
               "run_id": proof.binding.invocation_id}
    progress: dict[str, object] | None = {"observations": {"first": pointer}}
    status = {"label": proof.binding.observer_label, "run_id": proof.binding.invocation_id,
              "started_at": "start", "owner_pid": os.getpid(), "child_pid": os.getpid(), "state": "running",
              "updated_at": datetime.now(UTC).isoformat()}
    if mode == "missing":
        progress = None
    elif mode == "pointer":
        progress = {"observations": {}}
    elif mode == "duplicate":
        progress = {"observations": {"first": pointer, "second": pointer}}
    elif mode == "state":
        status["state"] = "passed"
    elif mode == "child":
        status["child_pid"] = -1
    elif mode == "owner":
        status["owner_pid"] = -1
    elif mode == "stale":
        status["updated_at"] = "2000-01-01T00:00:00+00:00"
    progress_owner = SimpleNamespace(progress_status=lambda _root: progress)
    stream_owner = SimpleNamespace(_load_status=lambda *_args, **_kwargs: status,
                                  _pid_alive=stream_command._pid_alive, _age_seconds=stream_command._age_seconds)
    def source_state(_root: Path, *arguments: str) -> bytes:
        if arguments[0] == "status":
            return b" M source.py\n" if mode == "dirty" else b""
        return ("e" * 40 if mode == "head" else proof.binding.candidate_sha).encode()

    owners = {"scripts.item_progress": progress_owner, "scripts.stream_command": stream_owner,
              "scripts.gate_status_attestation": SimpleNamespace(_run_git=source_state)}
    monkeypatch.setattr(native, "import_module", lambda name: owners[name])
    if mode == "ok":
        assert native._native_invocation(REPO, proof.binding) == (
            proof.binding.observer_label, proof.binding.invocation_id, "start", os.getpid(), os.getpid())
    else:
        with pytest.raises(ValueError):
            native._native_invocation(REPO, proof.binding)


def test_strict_json_and_unadmitted_execution_fail_closed(native: ModuleType, proof: SimpleNamespace) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        native._strict_object([("identity", 1), ("identity", 2)])
    owner = native._NativeItemOwner(REPO, proof.binding, proof.record_owner,
                                    proof.signature_owner, proof.invocation_owner)
    with pytest.raises(ValueError, match="not admitted"):
        owner.execute(proof.binding.runtime, proof.binding.image)


def test_after_invocation_change_is_rejected(native: ModuleType, proof: SimpleNamespace) -> None:
    calls = 0

    def invocation_owner(_root: Path, selected: Any) -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        return (selected.observer_label, selected.invocation_id, "start", calls, os.getpid())

    proof.invocation_owner = invocation_owner
    assert _validator(native, proof)({"id": "S39"}) is False


@pytest.mark.parametrize("mode", ["inspect-failed", "inspect-ambiguous"])
def test_native_inspection_failure_cannot_be_masked(
    native: ModuleType, proof: SimpleNamespace, mode: str,
) -> None:
    proof.mode = mode
    assert _validator(native, proof)({"id": "S39"}) is False
    assert not any(command[1] == "exec" for command in proof.calls)


def test_missing_attestation_is_not_signature_evidence(native: ModuleType, proof: SimpleNamespace) -> None:
    record = proof.records["S39"]
    with pytest.raises(ValueError, match="attestation"):
        native.attestation_payload(replace(record, attestation=None))


def test_subject_read_limits_and_absent_files_fail_closed(native: ModuleType, proof: SimpleNamespace) -> None:
    from ansible_collections.general_ludd.git_release.plugins.module_utils import provenance

    with pytest.raises(ValueError, match="bounded"):
        native._read_payload(proof.binding.artifact_root, "lock.json", 1)
    with pytest.raises((OSError, provenance.ArtifactVerificationError)):
        native._read_payload(proof.binding.artifact_root, "absent.json", 1024)


def test_subject_descriptor_change_is_rejected(
    native: ModuleType, proof: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_read = os.read

    def changed_read(descriptor: int, limit: int) -> bytes:
        result = real_read(descriptor, limit)
        (proof.binding.artifact_root / "lock.json").write_bytes(b"changed")
        return result

    monkeypatch.setattr(native.os, "read", changed_read)
    with pytest.raises(ValueError, match="changed during read"):
        native._read_payload(proof.binding.artifact_root, "lock.json", 1024)


def test_wrong_native_invocation_is_rejected_before_execution(native: ModuleType, proof: SimpleNamespace) -> None:
    proof.invocation_owner = lambda *_args: ("another", "invocation")
    assert _validator(native, proof)({"id": "S39"}) is False
    assert not proof.calls


@pytest.mark.parametrize("runtime,image_fields", [
    ("podman", {"Digest": "sha256:" + "b" * 64}),
    ("podman", {"Digest": "sha256:" + "b" * 64,
                "RepoDigests": ["example/gludd@sha256:" + "b" * 64]}),
    ("docker", {"RepoDigests": ["example/gludd@sha256:" + "b" * 64]}),
    ("docker", {"RepoDigests": ["example/gludd@sha256:" + "b" * 64,
                               "mirror/gludd@sha256:" + "b" * 64]}),
    ("docker", {"Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                               "digest": "sha256:" + "b" * 64},
                "RepoDigests": ["example/gludd@sha256:" + "b" * 64]}),
    ("docker", {"Descriptor": {"mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                               "digest": "sha256:" + "b" * 64}}),
])
def test_supported_native_inspect_schemas_execute_in_fixtures(
    native: ModuleType, proof: SimpleNamespace, runtime: str, image_fields: dict[str, object],
) -> None:
    proof.binding = proof.seal(replace(proof.binding, runtime=runtime))
    proof.image_fields = image_fields
    assert _validator(native, proof)({"id": "S39"}) is True
    production = native.make_native_deployment_validator(
        REPO, [proof.binding], record_owner=proof.record_owner,
        signature_owner=proof.signature_owner, invocation_owner=proof.invocation_owner)
    assert production({"id": "S39"}) is False


@pytest.mark.parametrize("image_fields", [
    {},
    {"RepoDigests": ["example/gludd@sha256:" + "e" * 64]},
    {"RepoDigests": ["example/gludd@sha256:" + "b" * 64, "example/gludd@sha256:" + "e" * 64]},
    {"RepoDigests": []},
    {"RepoDigests": "example/gludd@sha256:" + "b" * 64},
    {"RepoDigests": [None]},
    {"RepoDigests": ["example/gludd:latest"]},
    {"Digest": "sha256:" + "b" * 64, "RepoDigests": ["example/gludd:latest"]},
    {"Digest": "sha256:" + "e" * 64, "RepoDigests": ["example/gludd@sha256:" + "b" * 64]},
    {"Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": "sha256:" + "e" * 64},
     "RepoDigests": ["example/gludd@sha256:" + "b" * 64]},
    {"Descriptor": {"mediaType": "application/vnd.oci.image.index.v1+json", "digest": "sha256:" + "b" * 64},
     "RepoDigests": ["example/gludd@sha256:" + "b" * 64]},
    {"Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": "not-a-digest"}},
])
def test_wrong_ambiguous_and_unsupported_inspect_subjects_are_incomplete(
    native: ModuleType, proof: SimpleNamespace, image_fields: dict[str, object],
) -> None:
    proof.image_fields = image_fields
    assert _validator(native, proof)({"id": "S39"}) is False
    assert not any(command[1] == "exec" for command in proof.calls)


@pytest.mark.parametrize("phase", ["image", "container", "exec"])
@pytest.mark.parametrize("diagnostic", ["WARNING: native runtime diagnostic\n", "ERROR: native runtime diagnostic\n"])
def test_rc_zero_stderr_never_authorizes_native_acceptance(
    native: ModuleType, proof: SimpleNamespace, phase: str, diagnostic: str,
) -> None:
    proof.stderr_phase = phase
    proof.stderr_message = diagnostic
    assert _validator(native, proof)({"id": "S39"}) is False
