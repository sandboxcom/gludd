"""Connect trusted deployment owners to two closed, installed native probes.

This controller adapter has no import-time collection dependency. A caller must
provide independently trusted provenance and signature capabilities. Neither a
ledger field nor a serialized receipt can configure either capability.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from scripts import ansible_runtime_artifacts as artifacts
from scripts.verify_container_push import image_digest_from_output

_NATIVE_VERIFY_ENVIRONMENT = artifacts.verify_environment
_NATIVE_SUBPROCESS_RUN = subprocess.run

if TYPE_CHECKING:
    from ansible_collections.general_ludd.git_release.plugins.module_utils.provenance import ProvenanceRecord

INSTALLED_ROOT = "/usr/share/ansible/collections/ansible_collections/general_ludd/git_release"
PROBE_ROOT = "/opt/gludd/item-probes"
INSTALLED_LOCK_ROOT = "/opt/gludd/ansible-ee"
INSTALLED_FILES = (
    "MANIFEST.json", "FILES.json", "plugins/module_utils/provenance.py",
    "plugins/module_utils/pipeline_triage.py", "plugins/action/pipeline_triage.py",
)
FIXED_INPUTS = {
    "artifact.bin": b"gludd-native-item-probe\n",
    "lock.json": b'{"packages":{"native-item-probe":"1"}}\n',
    "report.xml": b'<testsuite tests="2" failures="1"><testcase classname="native" name="ok"/>'
                  b'<testcase classname="native" name="failure"><failure/></testcase></testsuite>\n',
    "invalid.xml": b'<!DOCTYPE testsuite [<!ENTITY bad "secret">]><testsuite>&bad;</testsuite>\n',
}
_PROBES = {"S39": "git-release-artifact-v1", "S42": "git-release-triage-v1"}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

# Reviewed program, not command text from the task ledger. The existing native
# execution owner invokes it in one already-running, exact-generation canary.
NATIVE_PROGRAM = r'''
import hashlib, importlib, json, os, sys
from importlib import metadata
from pathlib import Path
root = Path("/usr/share/ansible/collections/ansible_collections/general_ludd/git_release")
probe_root = Path("/opt/gludd/item-probes")
lock_root = Path("/opt/gludd/ansible-ee")
sys.path.insert(0, str(root.parents[2]))
request = json.loads(sys.stdin.read(65537))
assert set(request) == {"identity", "probe", "installed_files"}
identity = request["identity"]
item = identity["item_id"]
assert item in {"S39", "S42"}
for parent in (root, *root.parents, probe_root, *probe_root.parents, lock_root, *lock_root.parents):
    assert not parent.is_symlink()
provenance = importlib.import_module("ansible_collections.general_ludd.git_release.plugins.module_utils.provenance")
assert Path(provenance.__file__).resolve() == (root / "plugins/module_utils/provenance.py").resolve()
def verify_installed():
    for name, expected in request["installed_files"].items():
        provenance.verify_release_artifact(root=str(root), artifact_path=name, artifact_sha256=expected,
            lock_path="FILES.json", lock_sha256=request["installed_files"]["FILES.json"])
    expected = identity["lock_sha256"]
    provenance.verify_release_artifact(root=str(lock_root), artifact_path="dependency-lock.json",
        artifact_sha256=expected, lock_path="dependency-lock.json", lock_sha256=expected)
    root_fd = provenance._open_root(str(lock_root))
    descriptor = None
    try:
        descriptor = provenance._open_beneath(root_fd, ("dependency-lock.json",), "installed lock")
        before = provenance._file_snapshot(descriptor)
        assert 0 < before.size <= 262144 and before.links == 1
        payload = os.read(descriptor, 262145)
        assert len(payload) == before.size and provenance._file_snapshot(descriptor) == before
        assert hashlib.sha256(payload).hexdigest() == expected
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(root_fd)
    lock = json.loads(payload)
    packages = lock["packages"]
    assert isinstance(packages, dict) and {"ansible-core", "defusedxml"} <= packages.keys()
    assert all(isinstance(name, str) and isinstance(version, str) and metadata.version(name) == version
               for name, version in packages.items())
verify_installed()
inputs = request["probe"]["inputs"]
for name, expected in inputs.items():
    provenance.verify_release_artifact(root=str(probe_root), artifact_path=name, artifact_sha256=expected,
        lock_path="lock.json", lock_sha256=inputs["lock.json"])
if item == "S39":
    arguments = dict(root=str(probe_root), artifact_path="artifact.bin", artifact_sha256=inputs["artifact.bin"],
                     lock_path="lock.json", lock_sha256=inputs["lock.json"])
    result = provenance.verify_release_artifact(**arguments)
    assert result["verified"] is True
    assert result["artifact"]["sha256"] == inputs["artifact.bin"]
    assert result["dependency_lock"]["sha256"] == inputs["lock.json"]
    arguments["artifact_sha256"] = "f" * 64
    try:
        provenance.verify_release_artifact(**arguments)
    except provenance.ArtifactVerificationError:
        pass
    else:
        raise AssertionError("native artifact rejection control did not reject")
    native = {"operation":"verify_release_artifact", "positive":1, "rejected":1}
else:
    action = importlib.import_module("ansible_collections.general_ludd.git_release.plugins.action.pipeline_triage")
    triage = importlib.import_module(
        "ansible_collections.general_ludd.git_release.plugins.module_utils.pipeline_triage")
    assert Path(action.__file__).resolve() == (root / "plugins/action/pipeline_triage.py").resolve()
    assert Path(triage.__file__).resolve() == (root / "plugins/module_utils/pipeline_triage.py").resolve()
    arguments = dict(root=str(probe_root), report_path="report.xml")
    result = action.execute_action(arguments)
    assert result == action.execute_action(arguments, check_mode=True)
    assert result["triage"] == triage.triage_junit_report(**arguments)
    assert result["changed"] is False
    assert result["triage"]["counts"] == {"errors":0,"failures":1,"passed":1,"skipped":0,"tests":2}
    assert result["triage"]["outcome"] == "failed"
    try:
        action.execute_action(dict(root=str(probe_root), report_path="invalid.xml"))
    except triage.PipelineTriageError:
        pass
    else:
        raise AssertionError("native XML rejection control did not reject")
    native = {"operation":"execute_action", "positive":1, "rejected":1}
verify_installed()
for name, expected in inputs.items():
    provenance.verify_release_artifact(root=str(probe_root), artifact_path=name, artifact_sha256=expected,
        lock_path="lock.json", lock_sha256=inputs["lock.json"])
print(json.dumps(dict(schema_version=1, verified=True, identity=identity, probe=request["probe"], native=native),
                 sort_keys=True, separators=(",", ":")))
'''.strip()


@dataclass(frozen=True)
class NativeItemBinding:
    """Independent expected identity selected by the deployment owner."""

    item_id: str
    candidate_sha: str
    project_namespace: str
    runtime: str
    image: str
    platform: str
    deployment_target: str
    deployment_generation: str
    invocation_id: str
    observer_label: str
    artifact_root: Path
    artifact_path: str
    artifact_sha256: str
    lock_path: str
    lock_sha256: str
    release_id: str
    builder_identity: str


RecordOwner = Callable[[NativeItemBinding], "ProvenanceRecord"]
SignatureOwner = Callable[[bytes, NativeItemBinding], str | None]
InvocationOwner = Callable[[Path, NativeItemBinding], tuple[object, ...]]


def project_namespace(root: Path) -> str:
    """Use the existing project-path namespace rather than a global process name."""
    return "gludd-" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _installed_manifest_digest(image: Mapping[str, object]) -> str:
    """Normalize only native manifest fields through the existing digest owner."""
    candidates: dict[str, object] = {}
    if "Digest" in image:
        digest = image["Digest"]
        if not isinstance(digest, str) or artifacts.DIGEST_RE.fullmatch(digest) is None:
            raise ValueError("native manifest digest is malformed")
        candidates["native"] = {"digest": digest}
    references = image.get("RepoDigests")
    if references is not None:
        if (not isinstance(references, list) or len(references) > 32
                or not all(isinstance(value, str) and artifacts.IMAGE_RE.fullmatch(value) for value in references)):
            raise ValueError("native repository manifest references are malformed")
        candidates["references"] = [{"digest": value.rsplit("@", 1)[1]} for value in references]
    descriptor = image.get("Descriptor")
    if descriptor is not None:
        manifest_types = {"application/vnd.oci.image.manifest.v1+json",
                          "application/vnd.docker.distribution.manifest.v2+json"}
        digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
        if (not isinstance(descriptor, dict) or descriptor.get("mediaType") not in manifest_types
                or not isinstance(digest, str) or artifacts.DIGEST_RE.fullmatch(digest) is None):
            raise ValueError("native descriptor is not a supported platform manifest")
        candidates["descriptor"] = {"digest": digest}
    observed = image_digest_from_output(_canonical(candidates).decode())
    if observed is None:
        raise ValueError("native platform manifest digest is absent or ambiguous")
    return observed


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate native identity field")
        result[name] = value
    return result


def probe_identity(item_id: str) -> dict[str, object]:
    """Bind each canonical item to reviewed operations and exact fixture bytes."""
    name = _PROBES[item_id]
    files = ("artifact.bin", "lock.json") if item_id == "S39" else ("report.xml", "invalid.xml", "lock.json")
    inputs = {path: hashlib.sha256(FIXED_INPUTS[path]).hexdigest() for path in files}
    return {"name": name, "definition_sha256": hashlib.sha256((name + NATIVE_PROGRAM).encode()).hexdigest(),
            "input_sha256": hashlib.sha256(_canonical(inputs)).hexdigest(), "inputs": inputs}


def manifest_identity(binding: NativeItemBinding) -> dict[str, str]:
    """Return the signed subject's complete deployment and invocation binding."""
    return {name: str(getattr(binding, name)) for name in (
        "item_id", "candidate_sha", "project_namespace", "runtime", "image", "platform",
        "deployment_target", "deployment_generation", "invocation_id", "observer_label", "lock_sha256",
    )}


def attestation_payload(record: ProvenanceRecord) -> bytes:
    """Pass actual canonical signed payload bytes to the independent signer owner."""
    if record.attestation is None:
        raise ValueError("signed native subject attestation is missing")
    return _canonical(record.attestation.statement)


def _read_payload(root: Path, relative: str, maximum: int) -> bytes:
    """Reuse the collection's descriptor confinement and stable-file snapshot."""
    from ansible_collections.general_ludd.git_release.plugins.module_utils import provenance

    root_fd = provenance._open_root(str(root))
    descriptor: int | None = None
    try:
        parts = provenance._relative_parts(relative, "native input")
        descriptor = provenance._open_beneath(root_fd, parts, "native input")
        before = provenance._file_snapshot(descriptor)
        if before.size < 1 or before.size > maximum or before.links != 1:
            raise ValueError("native subject or lock is not one bounded regular input")
        payload = os.read(descriptor, maximum + 1)
        if len(payload) != before.size or provenance._file_snapshot(descriptor) != before:
            raise ValueError("native subject or lock changed during read")
        return payload
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(root_fd)


class _ProgressOwner(Protocol):
    def progress_status(self, root: Path) -> dict[str, object] | None: ...


class _StreamOwner(Protocol):
    def _load_status(self, root: Path, label: str, *, run_id: str) -> dict[str, object]: ...

    def _pid_alive(self, value: object) -> bool: ...

    def _age_seconds(self, value: object) -> float: ...


class _SourceOwner(Protocol):
    def _run_git(self, root: Path, *arguments: str) -> bytes: ...


def _native_invocation(root: Path, binding: NativeItemBinding) -> tuple[object, ...]:
    """Read the existing producer's pointer; status remains diagnostic-only."""
    item_progress = cast(_ProgressOwner, import_module("scripts.item_progress"))
    stream_command = cast(_StreamOwner, import_module("scripts.stream_command"))
    source_owner = cast(_SourceOwner, import_module("scripts.gate_status_attestation"))
    head = source_owner._run_git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    if head != binding.candidate_sha or source_owner._run_git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("native invocation source SHA or tracked worktree differs")

    progress = item_progress.progress_status(root)
    if progress is None:
        raise ValueError("native invocation has no item-progress owner")
    observations = cast(dict[str, dict[str, str]], progress["observations"])
    pointers = [pointer for pointer in observations.values()
                if pointer["label"] == binding.observer_label and pointer["run_id"] == binding.invocation_id]
    if len(pointers) != 1:
        raise ValueError("native invocation has no unique producer pointer")
    status = stream_command._load_status(Path(pointers[0]["root"]), binding.observer_label,
                                        run_id=binding.invocation_id)
    if (status.get("state") != "running" or status.get("child_pid") != os.getpid()
            or not stream_command._pid_alive(status.get("owner_pid"))
            or stream_command._age_seconds(status.get("updated_at")) > 60):
        raise ValueError("native invocation is not the current live observed child")
    return tuple(status.get(name) for name in ("label", "run_id", "started_at", "owner_pid", "child_pid"))


class _NativeItemOwner:
    """Ephemeral admission context consumed by the existing native EE owner."""

    def __init__(self, root: Path, binding: NativeItemBinding, record_owner: RecordOwner,
                 signature_owner: SignatureOwner, invocation_owner: InvocationOwner) -> None:
        self.root = root
        self.binding = binding
        self.record_owner = record_owner
        self.signature_owner = signature_owner
        self.invocation_owner = invocation_owner
        self.invocation: tuple[object, ...] | None = None
        self.subject: dict[str, object] | None = None
        self.accepted = False
        self.stage = "binding"

    def _validate_binding(self, runtime: str, image: str) -> None:
        binding = self.binding
        if (binding.item_id not in _PROBES or binding.runtime not in {"podman", "docker"}
                or runtime != binding.runtime or image != binding.image
                or _SHA.fullmatch(binding.candidate_sha) is None
                or binding.project_namespace != project_namespace(self.root)
                or not binding.deployment_target.startswith(binding.project_namespace + "-")
                or _NAME.fullmatch(binding.deployment_target) is None
                or _HEX64.fullmatch(binding.deployment_generation) is None
                or _NAME.fullmatch(binding.invocation_id) is None
                or _NAME.fullmatch(binding.observer_label) is None
                or binding.platform not in {"linux/amd64", "linux/arm64"}
                or _HEX64.fullmatch(binding.artifact_sha256) is None
                or _HEX64.fullmatch(binding.lock_sha256) is None
                or not binding.builder_identity or not binding.release_id):
            raise ValueError("independent native item identity is missing or unsupported")
        artifacts._require_image(image)

    def _verify_subject(self) -> dict[str, object]:
        from ansible_collections.general_ludd.git_release.plugins.module_utils import provenance

        binding = self.binding
        provenance.verify_release_artifact(root=str(binding.artifact_root), artifact_path=binding.artifact_path,
            artifact_sha256=binding.artifact_sha256, lock_path=binding.lock_path, lock_sha256=binding.lock_sha256)
        artifact = _read_payload(binding.artifact_root, binding.artifact_path, 64 * 1024)
        lock = _read_payload(binding.artifact_root, binding.lock_path, 256 * 1024)
        record = self.record_owner(binding)
        verified_digest = self.signature_owner(attestation_payload(record), binding)
        result = provenance.verify_provenance(record, expected_artifact_bytes=artifact, expected_lock_bytes=lock,
            expected_subject=binding.artifact_path, expected_builder_identity=binding.builder_identity,
            expected_release_id=binding.release_id, expected_source_sha=binding.candidate_sha,
            verified_attestation_digest=verified_digest, authorization_id=f"{binding.item_id}:{binding.invocation_id}",
            authorization_source_sha=binding.candidate_sha, receipt_purpose=provenance.ReceiptPurpose.DEPLOY,
            deployment_target=f"{binding.deployment_target}@{binding.deployment_generation}")
        receipt = provenance.verify_artifact_receipt(result.receipt, expected_purpose=provenance.ReceiptPurpose.DEPLOY,
            expected_authorization_id=f"{binding.item_id}:{binding.invocation_id}",
            expected_authorization_source_sha=binding.candidate_sha,
            expected_deployment_target=f"{binding.deployment_target}@{binding.deployment_generation}",
            expected_artifact_digest=binding.artifact_sha256)
        if result.ok is not True or receipt.ok is not True:
            raise ValueError("native subject signature/provenance was not independently verified")
        subject: object = json.loads(artifact, object_pairs_hook=_strict_object)
        if (not isinstance(subject, dict) or set(subject) != {"schema_version", "identity", "probe", "installed_files"}
                or type(subject.get("schema_version")) is not int or subject.get("schema_version") != 1
                or subject.get("identity") != manifest_identity(binding)
                or subject.get("probe") != probe_identity(binding.item_id)):
            raise ValueError("signed native subject item, identity or fixed probe differs")
        files = subject.get("installed_files")
        if (not isinstance(files, dict) or set(files) != set(INSTALLED_FILES)
                or not all(isinstance(value, str) and _HEX64.fullmatch(value) for value in files.values())):
            raise ValueError("signed installed collection identities are missing")
        return cast(dict[str, object], subject)

    def _inspect(self, command: list[str]) -> dict[str, object]:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
        if result.stderr != "":
            self.stage = "installed-identity-stderr"
            raise ValueError("installed native identity inspection emitted diagnostics")
        if result.returncode != 0 or len(result.stdout.encode()) > 64 * 1024:
            raise ValueError("installed native identity inspection failed")
        values: object = json.loads(result.stdout, object_pairs_hook=_strict_object)
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise ValueError("installed native identity is absent or ambiguous")
        return cast(dict[str, object], values[0])

    def _verify_installed_identity(self) -> None:
        binding = self.binding
        image = self._inspect([binding.runtime, "image", "inspect", binding.image])
        container = self._inspect([binding.runtime, "container", "inspect", binding.deployment_target])
        config = container.get("Config")
        state = container.get("State")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if (_installed_manifest_digest(image) != binding.image.rsplit("@", 1)[1]
                or f"{image.get('Os')}/{image.get('Architecture')}" != binding.platform
                or not isinstance(image.get("Id"), str) or artifacts.DIGEST_RE.fullmatch(str(image["Id"])) is None
                or container.get("Image") != image.get("Id")
                or container.get("Id") != binding.deployment_generation
                or container.get("Name") != "/" + binding.deployment_target
                or not isinstance(state, dict) or state.get("Running") is not True
                or not isinstance(labels, dict)
                or labels.get("org.general-ludd.source-sha") != binding.candidate_sha
                or labels.get("org.general-ludd.project") != binding.project_namespace):
            raise ValueError("installed platform, source, project, target or generation differs")

    def admit(self, runtime: str, image: str) -> None:
        """Check every independent identity before the native EE executes work."""
        self._validate_binding(runtime, image)
        self.stage = "invocation"
        self.invocation = self.invocation_owner(self.root, self.binding)
        expected_invocation = (self.binding.observer_label, self.binding.invocation_id)
        if len(self.invocation) != 5 or self.invocation[:2] != expected_invocation:
            raise ValueError("native invocation owner returned another invocation")
        self.stage = "signature-provenance"
        self.subject = self._verify_subject()
        self.stage = "installed-identity"
        self._verify_installed_identity()

    def execute(self, runtime: str, image: str) -> bool:
        """Execute the fixed installed operation and recheck all admission inputs."""
        self._validate_binding(runtime, image)
        if self.subject is None or self.invocation is None:
            raise ValueError("native item was not admitted")
        self.stage = "fixed-installed-probe"
        request = {name: self.subject[name] for name in ("identity", "probe", "installed_files")}
        binding = self.binding
        command = [runtime, "exec", "--interactive", binding.deployment_generation,
                   artifacts.CONTROLLER_PYTHON_PATH, "-I", "-W", "error", "-c", NATIVE_PROGRAM]
        result = subprocess.run(command, input=_canonical(request).decode(), check=False,
                                capture_output=True, text=True, timeout=60)
        if result.stderr != "":
            self.stage = "fixed-installed-probe-stderr"
            raise ValueError("fixed installed native operation emitted diagnostics")
        if result.returncode != 0 or len(result.stdout.encode()) > 64 * 1024:
            raise ValueError("fixed installed native operation failed")
        observed: object = json.loads(result.stdout, object_pairs_hook=_strict_object)
        expected = {"schema_version": 1, "verified": True, "identity": request["identity"], "probe": request["probe"],
                    "native": {"operation": "verify_release_artifact" if binding.item_id == "S39" else "execute_action",
                               "positive": 1, "rejected": 1}}
        if (not isinstance(observed, dict) or _canonical(observed) != _canonical(expected)
                or observed.get("verified") is not True):
            raise ValueError("native item result lacks the exact fixed operation evidence")
        self.stage = "after-installed-identity"
        self._verify_installed_identity()
        self.stage = "after-signature-provenance"
        if self._verify_subject() != self.subject:
            raise ValueError("native subject changed before acceptance")
        self.stage = "after-invocation"
        if self.invocation_owner(self.root, binding) != self.invocation:
            raise ValueError("native subject or invocation changed before acceptance")
        self.accepted = True
        self.stage = "accepted"
        return True


class _EnvironmentVerifier(Protocol):
    def __call__(self, runtime: str, image: str, validate_only: bool, *, item_owner: _NativeItemOwner) -> int: ...


def make_native_deployment_validator(
    repo_root: Path,
    bindings: Sequence[NativeItemBinding],
    *,
    record_owner: RecordOwner | None = None,
    signature_owner: SignatureOwner | None = None,
    invocation_owner: InvocationOwner | None = None,
    validate_only: bool = False,
) -> Callable[[Mapping[str, object]], bool]:
    """Create a capability for checked canonical tasks, without task writes."""
    selected = {binding.item_id: binding for binding in bindings}
    duplicates = len(selected) != len(bindings)
    root = repo_root.resolve()

    def validate(task: Mapping[str, object]) -> bool:
        item = task.get("id")
        binding = selected.get(item) if isinstance(item, str) else None
        owner: _NativeItemOwner | None = None
        stage = "owner-unavailable"
        try:
            if (validate_only or duplicates or binding is None or binding.item_id not in _PROBES
                    or record_owner is None or signature_owner is None):
                raise ValueError("native probe or independently trusted owner is unavailable")
            stage = "substituted-native-owner"
            if (artifacts.verify_environment is not _NATIVE_VERIFY_ENVIRONMENT
                    or subprocess.run is not _NATIVE_SUBPROCESS_RUN):
                raise ValueError("substituted native execution is diagnostic-only")
            owner = _NativeItemOwner(root, binding, record_owner, signature_owner,
                                     invocation_owner or _native_invocation)
            verify = cast(_EnvironmentVerifier, artifacts.verify_environment)
            status = verify(binding.runtime, binding.image, False, item_owner=owner)
            if status != 0 or owner.accepted is not True:
                raise ValueError("native execution owner did not complete item acceptance")
            print(f"BACKLOG_NATIVE_ITEM item={item} state=ACCEPTED release_ready=false gate_evidence=false", flush=True)
            return True
        except Exception as exc:
            reason = owner.stage if owner is not None else stage
            print(f"BACKLOG_NATIVE_ITEM item={item} state=INCOMPLETE reason={reason}:{type(exc).__name__} "
                  "release_ready=false gate_evidence=false", flush=True)
            return False

    return validate
