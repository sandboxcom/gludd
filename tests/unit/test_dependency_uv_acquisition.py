"""Fixed native uv acquisition: isolated paths, leases and bounded child ownership."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import active_work_status
    import clean_ci_shard_scratch
    import collection_lock
    import dependency_profiles as owner
    import resource_arbiter
    import stream_command as observer
else:
    from scripts import (
        active_work_status,
        clean_ci_shard_scratch,
        collection_lock,
        resource_arbiter,
    )
    from scripts import dependency_profiles as owner
    from scripts import stream_command as observer

ROOT = Path(__file__).resolve().parents[2]
NATIVE_PYTHON = sys.executable
PIN = "0.13.1"
TAG = "py3-none-macosx_11_0_arm64"


def write_tool(leaf: Path, bootstrap: Path, version: str = PIN) -> None:
    """Write native-shaped fixture metadata and real, harmless version children."""
    environment = leaf / "tools/uv"
    binary = environment / "bin/uv"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f"#!{NATIVE_PYTHON}\nprint('uv {version}')\n")
    binary.chmod(0o700)
    python = environment / "bin/python"
    python.symlink_to(bootstrap)
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    (leaf / "bin").mkdir()
    (leaf / "bin/uv").symlink_to(binary)
    uvx = environment / "bin/uvx"
    uvx.write_bytes(binary.read_bytes())
    uvx.chmod(0o700)
    (leaf / "bin/uvx").symlink_to(uvx)
    receipt = (
        '[tool]\nrequirements=[{name="uv",specifier="==' + version + '"}]\n'
        f"python={json.dumps(str(bootstrap))}\n"
        'entrypoints=[{name="uv",install-path=' + json.dumps(str(leaf / "bin/uv")) +
        ',from="uv"},{name="uvx",install-path=' + json.dumps(str(leaf / "bin/uvx")) +
        ',from="uv"}]\n[tool.options]\n'
        'index=[{url="https://pypi.org/simple",default=true}]\n'
    )
    (environment / "uv-receipt.toml").write_text(receipt)
    info = environment / f"lib/python3.11/site-packages/uv-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Name: uv\nVersion: {version}\n")
    (info / "WHEEL").write_text(f"Wheel-Version: 1.0\nTag: {TAG}\n")
    digest = base64.urlsafe_b64encode(hashlib.sha256(binary.read_bytes()).digest()).decode().rstrip("=")
    (info / "RECORD").write_text(f"../../../bin/uv,sha256={digest},{binary.stat().st_size}\n")


@dataclass
class InstallCall:
    argv: tuple[str, ...]
    log: Path
    environment: dict[str, str]
    options: dict[str, object]


@dataclass
class Fixture:
    root: Path
    namespace: Path
    bootstrap: Path
    installer: Path
    leaf: Path
    calls: list[InstallCall] = field(default_factory=list)
    code: int = 0
    wheel_requests: list[str] = field(default_factory=list)

    def acquire(self, *, plan: bool = False) -> dict[str, object]:
        return owner.acquire_pinned_uv(self.root, validate_only=plan)

    def install(self, argv: Sequence[str], log: Path, **options: object) -> int:
        raw = options.get("environment")
        assert isinstance(raw, Mapping)
        environment: dict[str, str] = {}
        for key, value in raw.items():
            assert isinstance(key, str) and isinstance(value, str)
            environment[key] = value
        self.calls.append(InstallCall(tuple(argv), log, environment, options))
        if self.code == 0:
            write_tool(self.leaf, self.bootstrap)
        else:
            (self.leaf / "partial").write_text("failed own install")
        return self.code

    def wheels(self, version: str) -> list[dict[str, object]]:
        self.wheel_requests.append(version)
        return [{
            "filename": f"uv-{version}-{TAG}.whl",
            "url": f"https://files.pythonhosted.org/packages/fixture/uv-{version}-{TAG}.whl",
            "digests": {"sha256": "a" * 64},
            "packagetype": "bdist_wheel", "yanked": False,
        }]


@pytest.fixture
def acquisition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    root = tmp_path / "checkout"
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/build.yml").write_text(
        "jobs:\n  gate:\n    steps:\n      - uses: astral-sh/setup-uv@fixture\n"
        "        if: matrix.python-version == '3.12'\n"
        '        with:\n          version: "0.13.1"\n          python-version: "3.12.15"\n'
    )
    namespace = tmp_path / "owned"
    bootstrap = Path(f"{namespace}-toolchain") / "ci-shards/python-3.11/bin/python"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.symlink_to(NATIVE_PYTHON)
    (bootstrap.parent.parent / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    installer = tmp_path / "existing-uv"
    installer.write_text(f"#!{NATIVE_PYTHON}\nprint('uv 0.11.17')\n")
    installer.chmod(0o700)
    fixture = Fixture(
        root, namespace, bootstrap, installer,
        Path(f"{namespace}-toolchain") / "pinned-uv" / PIN,
    )
    monkeypatch.setattr(active_work_status, "_repository_roots", lambda: (root,))
    monkeypatch.setattr(resource_arbiter, "resource_root", lambda selected: namespace)
    monkeypatch.setattr(resource_arbiter, "resource_path", lambda name, selected: namespace / f"{name}.lock")
    monkeypatch.setattr(sys, "executable", str(bootstrap))
    monkeypatch.setattr(sys, "prefix", str(bootstrap.parent.parent))
    monkeypatch.setattr("shutil.which", lambda name: str(installer) if name == "uv" else None)
    monkeypatch.setattr(observer, "stream_command", fixture.install)
    monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", lambda path: [])
    monkeypatch.setattr(owner, "_uv_wheel_metadata", fixture.wheels, raising=False)
    return fixture


def test_pinned_uv_plan_has_no_mutation_probe_or_installer(
    acquisition: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("plan must not start any native child")
    monkeypatch.setattr(subprocess, "run", unexpected_probe)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    environment = dict(os.environ)
    report = acquisition.acquire(plan=True)
    assert report["state"] == "plan" and report["version"] == PIN
    assert report["executable"] == str(acquisition.leaf / "bin/uv")
    assert report["gate_evidence"] is False and report["release_ready"] is False
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not acquisition.leaf.exists() and not acquisition.namespace.exists()
    assert not acquisition.calls and not acquisition.wheel_requests
    assert dict(os.environ) == environment


def test_native_pinned_uv_apply_is_fixed_and_private(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS", "UV_COMPILE_BYTECODE",
                "UV_INSECURE_HOST", "UV_PYTHON", "UV_CONSTRAINT", "UV_TOOL_DIR", "PIP_INDEX_URL"):
        monkeypatch.setenv(key, "untrusted")
    before = dict(os.environ)
    report = acquisition.acquire()
    assert report["state"] == "verified" and report["reused"] is False
    assert len(acquisition.calls) == 1
    call = acquisition.calls[0]
    assert call.argv == (
        str(acquisition.installer.resolve()), "--no-config", "tool", "install",
        "--python", str(acquisition.bootstrap), "--no-python-downloads", "--no-build",
        "--default-index", "https://pypi.org/simple", f"uv=={PIN}",
    )
    for key, suffix in (("UV_TOOL_DIR", "tools"), ("UV_TOOL_BIN_DIR", "bin"),
                        ("UV_CACHE_DIR", "cache"), ("UV_PYTHON_INSTALL_DIR", "python")):
        assert call.environment[key] == str(acquisition.leaf / suffix)
    assert call.environment["UV_PYTHON_DOWNLOADS"] == "never"
    assert call.environment["UV_CONCURRENT_DOWNLOADS"] == "1"
    assert call.environment["PATH"].split(os.pathsep)[0] == str(acquisition.leaf / "bin")
    assert not any(key in call.environment for key in (
        "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS", "UV_COMPILE_BYTECODE",
        "UV_INSECURE_HOST", "UV_PYTHON", "UV_CONSTRAINT", "PIP_INDEX_URL",
    ))
    assert call.options["max_seconds"] == 180 and call.options["quiet_seconds"] == 60
    assert call.options["cwd"] == acquisition.root
    assert report["wheel_sha256"] == "a" * 64 and report["artifact_hash_locked"] is False
    assert report["executable_sha256"] == hashlib.sha256(
        (acquisition.leaf / "tools/uv/bin/uv").read_bytes()
    ).hexdigest()
    assert isinstance(report["wheel_url"], str)
    assert report["wheel_url"].startswith("https://files.pythonhosted.org/")
    assert dict(os.environ) == before


def test_valid_native_reuse_never_reinstalls(acquisition: Fixture) -> None:
    write_tool(acquisition.leaf, acquisition.bootstrap)
    # Kernel/advisory lease records may change; every installation byte stays.
    contents = {p: p.read_bytes() for p in acquisition.leaf.rglob("*") if p.is_file() and p.name != ".lock"}
    report = acquisition.acquire()
    assert report["state"] == "verified" and report["reused"] is True
    assert not acquisition.calls
    assert contents == {
        p: p.read_bytes() for p in acquisition.leaf.rglob("*") if p.is_file() and p.name != ".lock"
    }


@pytest.mark.parametrize("fault", ["partial", "leaf-symlink", "parent-symlink", "foreign-root", "foreign-uid"])
def test_unknown_acquisition_state_is_never_replaced(
    acquisition: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    if fault == "partial":
        acquisition.leaf.mkdir(parents=True)
        (acquisition.leaf / "sentinel").write_text("preserve")
    elif fault == "leaf-symlink":
        outside = tmp_path / "foreign"
        outside.mkdir()
        acquisition.leaf.parent.mkdir(parents=True)
        acquisition.leaf.symlink_to(outside, target_is_directory=True)
    elif fault == "parent-symlink":
        outside = tmp_path / "foreign"
        outside.mkdir()
        acquisition.leaf.parent.parent.mkdir(parents=True, exist_ok=True)
        acquisition.leaf.parent.symlink_to(outside, target_is_directory=True)
    elif fault == "foreign-root":
        acquisition.root = tmp_path / "unregistered"
    else:
        acquisition.leaf.mkdir(parents=True)
        monkeypatch.setattr(os, "getuid", lambda: acquisition.leaf.stat().st_uid + 1)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert not acquisition.calls and before == sorted(str(p) for p in tmp_path.rglob("*"))


@pytest.mark.parametrize("fault", ["active", "inventory-unavailable", "held-acquisition-lease", "held-native-lease"])
def test_active_or_unknown_acquisition_ownership_refuses(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    write_tool(acquisition.leaf, acquisition.bootstrap)
    if fault == "active":
        monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", lambda path: [12345])
    elif fault == "inventory-unavailable":
        def unavailable(path: Path) -> list[int]:
            raise clean_ci_shard_scratch.ProcessInspectionError("fixture inventory unavailable")
        monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", unavailable)
    lease = (acquisition.namespace / f"pinned-uv-{PIN}.lock" if fault == "held-acquisition-lease"
             else acquisition.leaf / "tools/.lock")
    if fault.startswith("held-"):
        with collection_lock.collection_lock(lease, timeout=0), pytest.raises(owner.ProfileError):
            acquisition.acquire()
    else:
        with pytest.raises(owner.ProfileError):
            acquisition.acquire()
    assert not acquisition.calls and acquisition.leaf.exists()


@pytest.mark.parametrize("fault", [
    "receipt", "receipt-symlink", "requirement", "index", "export", "python", "wheel",
    "metadata", "record", "version", "configuration",
])
def test_native_reuse_refuses_tamper_without_cleanup_or_install(
    acquisition: Fixture, tmp_path: Path, fault: str,
) -> None:
    write_tool(acquisition.leaf, acquisition.bootstrap)
    environment = acquisition.leaf / "tools/uv"
    receipt = environment / "uv-receipt.toml"
    info = environment / f"lib/python3.11/site-packages/uv-{PIN}.dist-info"
    if fault == "receipt":
        receipt.write_text("invalid = [")
    elif fault == "receipt-symlink":
        outside = tmp_path / "receipt"
        outside.write_bytes(receipt.read_bytes())
        receipt.unlink()
        receipt.symlink_to(outside)
    elif fault in {"requirement", "index"}:
        receipt.write_text(receipt.read_text().replace(
            "==0.13.1" if fault == "requirement" else "https://pypi.org/simple",
            ">=0" if fault == "requirement" else "https://foreign.invalid/simple",
        ))
    elif fault == "export":
        export = acquisition.leaf / "bin/uv"
        export.unlink()
        export.symlink_to(acquisition.installer)
    elif fault == "python":
        python = environment / "bin/python"
        python.unlink()
        python.write_text("foreign interpreter")
    elif fault == "wheel":
        (info / "WHEEL").write_text("Tag: py3-none-foreign\n")
    elif fault == "metadata":
        (info / "METADATA").write_text("Name: foreign\nVersion: 0\n")
    elif fault == "record":
        (info / "RECORD").write_text("../../../bin/uv,sha256=invalid,1\n")
    elif fault == "version":
        binary = environment / "bin/uv"
        binary.write_text(f"#!{NATIVE_PYTHON}\nprint('uv 0.0.0')\n")
        digest = base64.urlsafe_b64encode(hashlib.sha256(binary.read_bytes()).digest()).decode().rstrip("=")
        (info / "RECORD").write_text(f"../../../bin/uv,sha256={digest},{binary.stat().st_size}\n")
    else:
        (environment / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert acquisition.leaf.exists() and not acquisition.calls


@pytest.mark.parametrize("code", [1, 124, 125])
def test_failed_or_timed_out_native_install_cleans_only_its_fresh_leaf(
    acquisition: Fixture, code: int,
) -> None:
    predecessor = acquisition.leaf.parent / "0.12.0"
    predecessor.mkdir(parents=True)
    (predecessor / "sentinel").write_text("prior immutable state")
    acquisition.code = code
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert len(acquisition.calls) == 1 and not acquisition.leaf.exists()
    assert (predecessor / "sentinel").read_text() == "prior immutable state"


def test_unknown_child_failure_retains_fresh_leaf(acquisition: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    def unknown(argv: Sequence[str], log: Path, **options: object) -> int:
        raise OSError("fixture unknown child drain")
    monkeypatch.setattr(observer, "stream_command", unknown)
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert acquisition.leaf.exists()


@pytest.mark.parametrize("change", ["active", "identity", "workflow"])
def test_failed_fresh_cleanup_refuses_changed_authority(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    active: list[int] = []
    monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", lambda path: active)
    def failed(argv: Sequence[str], log: Path, **options: object) -> int:
        if change == "active":
            active.append(12345)
        elif change == "identity":
            acquisition.leaf.rename(acquisition.leaf.with_name("preserved-original"))
            acquisition.leaf.mkdir()
        else:
            workflow = acquisition.root / ".github/workflows/build.yml"
            workflow.write_text(workflow.read_text().replace(PIN, "0.13.2"))
        return 1
    monkeypatch.setattr(observer, "stream_command", failed)
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    if change != "workflow":
        assert acquisition.leaf.exists()


@pytest.mark.parametrize("fault", ["missing", "ambiguous", "unbounded", "changed-pin"])
def test_workflow_is_the_only_exact_pin_authority(acquisition: Fixture, fault: str) -> None:
    workflow = acquisition.root / ".github/workflows/build.yml"
    original = workflow.read_text()
    if fault == "changed-pin":
        workflow.write_text(original.replace(PIN, "0.13.2"))
        report = acquisition.acquire(plan=True)
        assert report["version"] == "0.13.2"
        assert report["executable"] == str(acquisition.leaf.parent / "0.13.2/bin/uv")
        assert not acquisition.calls
        return
    if fault == "missing":
        workflow.write_text(original.replace("matrix.python-version == '3.12'", "false"))
    elif fault == "ambiguous":
        workflow.write_text(original + original.split("    steps:\n", 1)[1])
    else:
        workflow.write_text(original.replace(PIN, ">=0"))
    with pytest.raises(owner.ProfileError):
        acquisition.acquire(plan=True)
    assert not acquisition.calls and not acquisition.namespace.exists()


def test_acquisition_target_has_safe_literal_contract() -> None:
    makefile = (ROOT / "make/00-foundation.mk").read_text()
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    entry = next((row for row in contract["targets"] if row["name"] == "acquire-pinned-uv"), None)
    assert entry == {
        "name": "acquire-pinned-uv", "make_variables": ["PINNED_UV_VALIDATE_ONLY"],
        "behavior": "make acquire-pinned-uv PINNED_UV_VALIDATE_ONLY=1",
    }
    assert "acquire-pinned-uv:" in makefile
    assert "PINNED_UV_VALIDATE_ONLY ?= 1" in makefile
    assert "unexport PINNED_UV_VALIDATE_ONLY" in makefile
    assert "python -B -m scripts.dependency_profiles acquire-uv" in makefile


def test_acquisition_cli_refuses_arbitrary_package(acquisition: Fixture) -> None:
    with pytest.raises(SystemExit):
        owner.main(["acquire-uv", "--root", str(acquisition.root), "--package", "foreign"])
    assert not acquisition.calls


def test_real_native_install_timeout_drains_before_fresh_cleanup(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = NATIVE_STREAM_COMMAND
    # The fixture adapter shortens only its own child deadline, not production policy.
    def bounded(argv: Sequence[str], log: Path, **options: object) -> int:
        environment = options["environment"]
        assert isinstance(environment, Mapping)
        child_environment = {str(key): str(value) for key, value in environment.items()}
        return native(
            argv, log, observed_root=acquisition.root / ".gate-logs/observed",
            label="fixture-pinned-uv", heartbeat_seconds=0, max_seconds=0.15,
            quiet_seconds=0, environment=child_environment, cwd=acquisition.root,
        )
    pidfile = acquisition.root / "fixture-installer.pid"
    acquisition.installer.write_text(
        f"#!{NATIVE_PYTHON}\nimport os,sys,time\nfrom pathlib import Path\n"
        "if '--version' in sys.argv:\n    print('uv 0.11.17');sys.exit(0)\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n"
    )
    monkeypatch.setattr(observer, "stream_command", bounded)
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert not acquisition.leaf.exists()
    pid = int(pidfile.read_text())
    import psutil
    assert not psutil.pid_exists(pid)


NATIVE_STREAM_COMMAND = observer.stream_command


def test_fresh_cleanup_refuses_a_live_native_tool_lease(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import ExitStack

    with ExitStack() as held:
        def install(argv: Sequence[str], log: Path, **options: object) -> int:
            (acquisition.leaf / "tools").mkdir()
            held.enter_context(collection_lock.collection_lock(acquisition.leaf / "tools/.lock", timeout=0))
            return 1
        monkeypatch.setattr(observer, "stream_command", install)
        with pytest.raises(owner.ProfileError):
            acquisition.acquire()
        assert acquisition.leaf.exists(), "a live native mutation lease must retain the fresh leaf"


@pytest.mark.parametrize("fault", ["missing-bootstrap", "foreign-prefix", "missing-installer", "installer-error"])
def test_missing_or_unproven_bootstrap_never_starts_install(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    if fault == "missing-bootstrap":
        acquisition.bootstrap.unlink()
    elif fault == "foreign-prefix":
        monkeypatch.setattr(sys, "prefix", str(acquisition.root))
    elif fault == "missing-installer":
        monkeypatch.setattr("shutil.which", lambda name: None)
    else:
        def unavailable(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert kwargs["timeout"] == 10 and kwargs["check"] is True
            raise subprocess.TimeoutExpired("fixture-version", 10)
        monkeypatch.setattr(subprocess, "run", unavailable)
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert not acquisition.calls and not acquisition.leaf.exists()


@pytest.mark.parametrize("fault", ["valid", "status", "timeout", "invalid-json", "invalid-urls", "oversized"])
def test_official_wheel_metadata_is_fixed_bounded_and_never_downloads_artifacts(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    import httpx

    seen: list[str] = []
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert str(request.url) == f"https://pypi.org/pypi/uv/{PIN}/json"
        if fault == "timeout":
            raise httpx.ReadTimeout("fixture release metadata timeout")
        if fault == "status":
            return httpx.Response(503)
        if fault == "invalid-json":
            return httpx.Response(200, content=b"{")
        if fault == "oversized":
            return httpx.Response(200, content=b"x" * 1048577)
        return httpx.Response(200, json={
            "urls": ["not metadata"] if fault == "invalid-urls" else acquisition.wheels(PIN),
        })
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        def stream(method: str, url: str, **options: object) -> object:
            assert options == {"timeout": 20, "follow_redirects": False, "trust_env": False}
            return client.stream(method, url, timeout=20, follow_redirects=False)
        monkeypatch.setattr(httpx, "stream", stream)
        if fault == "valid":
            assert NATIVE_WHEEL_METADATA(PIN) == acquisition.wheels(PIN)
        else:
            with pytest.raises(owner.ProfileError):
                NATIVE_WHEEL_METADATA(PIN)
    assert len(seen) == 1


@pytest.mark.parametrize("fault", ["missing-wheel", "foreign-url", "malformed-hash", "sdist-plus-wheel"])
def test_published_wheel_identity_is_checked_without_installing_again(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    write_tool(acquisition.leaf, acquisition.bootstrap)
    values = acquisition.wheels(PIN)
    if fault == "missing-wheel":
        values = []
    elif fault == "foreign-url":
        values[0]["url"] = "https://foreign.invalid/package.whl"
    elif fault == "malformed-hash":
        values[0]["digests"] = {"sha256": "not a digest"}
    else:
        values.append({"filename": f"uv-{PIN}.tar.gz", "packagetype": "sdist"})
    monkeypatch.setattr(owner, "_uv_wheel_metadata", lambda version: values)
    if fault == "sdist-plus-wheel":
        assert acquisition.acquire()["state"] == "verified"
    else:
        with pytest.raises(owner.ProfileError):
            acquisition.acquire()
    assert not acquisition.calls and acquisition.leaf.exists()


def test_workflow_change_after_install_cannot_publish_a_verified_executable(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def install(argv: Sequence[str], log: Path, **options: object) -> int:
        write_tool(acquisition.leaf, acquisition.bootstrap)
        workflow = acquisition.root / ".github/workflows/build.yml"
        workflow.write_text(workflow.read_text().replace(PIN, "0.13.2"))
        return 0
    monkeypatch.setattr(observer, "stream_command", install)
    with pytest.raises(owner.ProfileError, match="workflow input changed"):
        acquisition.acquire()
    assert not acquisition.leaf.exists()


@pytest.mark.parametrize("value", ["invalid", "1 extra", "$(shell touch {tripwire})"])
def test_actual_acquisition_target_refuses_raw_literal_policy_before_lifecycle(
    acquisition: Fixture, tmp_path: Path, value: str,
) -> None:
    tripwire = tmp_path / "make-tripwire"
    value = value.replace("{tripwire}", str(tripwire))
    result = subprocess.run(
        ["make", "--no-print-directory", "-C", str(ROOT), "acquire-pinned-uv",
         f"PINNED_UV_VALIDATE_ONLY={value}"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode != 0 and "PINNED_UV_VALIDATE_ONLY must be 0 or 1" in result.stdout
    assert not tripwire.exists() and not acquisition.calls


NATIVE_WHEEL_METADATA = owner._uv_wheel_metadata


@pytest.mark.parametrize("native", [False, True], ids=["acquisition-lease", "native-tool-lease"])
def test_symlinked_lease_is_never_followed_or_written(
    acquisition: Fixture, tmp_path: Path, native: bool,
) -> None:
    write_tool(acquisition.leaf, acquisition.bootstrap)
    foreign = tmp_path / "foreign-lease"
    foreign.write_text("unchanged foreign lease\n")
    lease = (acquisition.leaf / "tools/.lock" if native
             else acquisition.namespace / f"pinned-uv-{PIN}.lock")
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.symlink_to(foreign)
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert foreign.read_text() == "unchanged foreign lease\n"
    assert acquisition.leaf.exists() and not acquisition.calls


@pytest.mark.parametrize("fault", ["receipt-link", "export-link", "binary-link", "cfg-shadow"])
def test_native_verification_rechecks_final_path_authority(
    acquisition: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    write_tool(acquisition.leaf, acquisition.bootstrap)
    native = subprocess.run
    environment = acquisition.leaf / "tools/uv"
    def mutate(
        argv: Sequence[str], *, check: bool, capture_output: bool, text: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert text is True
        result = native(argv, check=check, capture_output=capture_output, text=True, timeout=timeout)
        if list(argv) == [str(acquisition.leaf / "bin/uv"), "--version"]:
            if fault == "cfg-shadow":
                (environment / "bin/pyvenv.cfg").write_text("include-system-site-packages = true\n")
            else:
                target = (environment / "uv-receipt.toml" if fault == "receipt-link"
                          else environment / "bin/uv")
                outside = tmp_path / f"outside-{fault}"
                outside.write_bytes(target.read_bytes())
                outside.chmod(0o700)
                if fault == "export-link":
                    target = acquisition.leaf / "bin/uv"
                target.unlink()
                target.symlink_to(outside)
        return result
    monkeypatch.setattr(subprocess, "run", mutate)
    with pytest.raises(owner.ProfileError):
        acquisition.acquire()
    assert acquisition.leaf.exists() and not acquisition.calls


def test_malformed_workflow_is_a_structured_readonly_refusal(acquisition: Fixture) -> None:
    (acquisition.root / ".github/workflows/build.yml").write_text("jobs: [")
    with pytest.raises(owner.ProfileError, match="workflow"):
        acquisition.acquire(plan=True)
    assert not acquisition.namespace.exists() and not acquisition.calls


@pytest.mark.parametrize("mode", ["0", "1"])
def test_actual_acquisition_policy_carrier_preserves_valid_modes(tmp_path: Path, mode: str) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "acquire-pinned-uv", "UV=echo",
         f"PINNED_UV_VALIDATE_ONLY={mode}", "GLUDD_PINNED_UV_VALIDATE_ONLY=invalid"],
        cwd=ROOT, capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "python -B -m scripts.dependency_profiles acquire-uv" in result.stdout
    assert ("--validate-only" in result.stdout) == (mode == "1")


def test_official_metadata_has_an_overall_read_deadline(
    acquisition: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import httpx

    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"urls": acquisition.wheels(PIN)})
    )) as client:
        def stream(method: str, url: str, **options: object) -> object:
            return client.stream(method, url, timeout=20, follow_redirects=False)
        monkeypatch.setattr(httpx, "stream", stream)
        samples = iter((0.0, 21.0, 21.0, 21.0))
        with monkeypatch.context() as clock:
            clock.setattr(time, "monotonic", lambda: next(samples, 21.0))
            with pytest.raises(owner.ProfileError, match="deadline"):
                NATIVE_WHEEL_METADATA(PIN)


def test_real_package_verification_uses_native_module_boundary(acquisition: Fixture) -> None:
    write_tool(acquisition.leaf, acquisition.bootstrap)
    code = (
        "import json,sys;from pathlib import Path;"
        "sys.path.insert(0,sys.argv[1]);"
        "from scripts import dependency_profiles as owner;"
        "owner._uv_wheel_metadata=lambda version:json.loads(sys.argv[4]);"
        "print(json.dumps(owner._verify_pinned_uv(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[5])))"
    )
    result = subprocess.run(
        [NATIVE_PYTHON, "-I", "-B", "-c", code, str(ROOT), str(acquisition.leaf),
         str(acquisition.bootstrap), json.dumps(acquisition.wheels(PIN)), PIN],
        cwd=acquisition.root, check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["executable"] == str(acquisition.leaf / "bin/uv")
    assert report["wheel_identity_kind"] == "published-tag-match"
