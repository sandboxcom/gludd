"""Native controls for immutable reviewed-head input production."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

import pytest
from scripts.makefile_layout import compose_makefile

from general_ludd.git_release.reviewed_head_integration import ReviewedHeadIntegrationReceipt

if TYPE_CHECKING:
    import build_reviewed_head_integration_receipt as receipt_owner
    import gate_status_attestation as gate_owner
    import release_readiness as readiness
    import reviewed_head_inputs as inputs
    import run_ci_shards_serial as shard_owner
    import start_gate_background as background_owner
    import stream_command as observed_owner
    from ci_named_shard_files import SHARDS
else:
    from scripts import build_reviewed_head_integration_receipt as receipt_owner
    from scripts import gate_status_attestation as gate_owner
    from scripts import release_readiness as readiness
    from scripts import reviewed_head_inputs as inputs
    from scripts import run_ci_shards_serial as shard_owner
    from scripts import start_gate_background as background_owner
    from scripts import stream_command as observed_owner
    from scripts.ci_named_shard_files import SHARDS

ROOT = Path(__file__).resolve().parents[2]
KEY = bytes(range(32))


class _Focused(TypedDict):
    candidate_sha: str
    run_count: int
    passed: bool
    native_status: Mapping[str, object]
    runtime: Mapping[str, object]
    log_file: str
    trace_file: str
    completed_at: str
    run_id: str


@pytest.fixture(autouse=True)
def _isolated_native_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the existing owner beneath one canonical pytest-owned root."""
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "native-resources"))
    monkeypatch.setenv("WORK_ITEM_ID", "F12.1")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], cwd=root.resolve(), check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()


def _commit(root: Path, name: str, content: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(root, "add", name)
    _git(root, "commit", "-m", "native fixture")
    return _git(root, "rev-parse", "HEAD")


def _fixture(
    tmp_path: Path, mode: str = "cherry_pick", *, focus_pass: bool = True,
    native_tests: bool = True, mutate_after: bool = False,
    child_plugin_autoload: bool = True, native_child_probe: bool = False,
) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "repo"
    root.mkdir()
    root = root.resolve()
    _git(root, "init", "-b", "development")
    _git(root, "config", "user.name", "Gludd native tests")
    _git(root, "config", "user.email", "native@gludd.invalid")
    (root / ".git/info/exclude").write_text(
        ".pytest_cache/\n__pycache__/\n.gate-status\n.gate-logs/\n.gludd/\n", encoding="utf-8",
    )
    test_recipe = "@\"" + sys.executable + "\" -m pytest $(TESTFILES) $(PYTEST_ARGS) -o addopts= -q"
    if not child_plugin_autoload:
        test_recipe = test_recipe.replace("@", "@PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ", 1)
    recipe = "test-files:\n\t" + (test_recipe if native_tests else "@true") + "\n"
    if mutate_after:
        recipe += "\t@touch unreviewed-native-mutation\n"
    _commit(root, "Makefile", recipe)
    _commit(root, "TASKS.md", "- [ ] F12.1 — native focused proof | status: pending\n")
    for adapter in ("reviewed_head_inputs.py", "stream_command.py"):
        _commit(root, "scripts/" + adapter, (ROOT / "scripts" / adapter).read_text(encoding="utf-8"))
    if native_child_probe:
        _commit(root, "conftest.py", (
            "import hashlib, json, sys\nfrom pathlib import Path\n"
            f"COUNTER = Path({str(tmp_path / 'native-child-count').__repr__()})\n"
            f"RUNTIME = Path({str(tmp_path / 'native-child-runtime.json').__repr__()})\n"
            "def pytest_sessionstart(session):\n"
            "    count = int(COUNTER.read_text()) if COUNTER.exists() else 0\n"
            "    COUNTER.write_text(str(count + 1))\n"
            "def pytest_sessionfinish(session, exitstatus):\n"
            "    runtime = {'python': sys.version, 'executable_sha256': "
            "hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),\n"
            "        'loaded_plugins': sorted(name for name, plugin in "
            "session.config.pluginmanager.list_name_plugin() if plugin is not None)}\n"
            "    RUNTIME.write_text(json.dumps(runtime))\n"
        ))
    assertion = "1 + 1 == 2" if focus_pass else "False"
    base = _commit(root, "tests/unit/test_native.py", "def test_native():\n    assert " + assertion + "\n")
    _git(root, "tag", "reviewed-base", base)
    _git(root, "switch", "-c", "reviewed/one")
    source = _commit(root, "source.txt", "reviewed native change\n")
    _git(root, "switch", "development")
    before = _commit(root, "integration.txt", "distinct integration base\n")
    _git(root, "tag", "integration-base", before)
    if mode == "merge_two_parent":
        _git(root, "merge", "--no-ff", source, "-m", "apply reviewed source")
    else:
        _git(root, "cherry-pick", source)
    spec: dict[str, object] = {
        "schema_version": 1,
        "integration_base_ref": "refs/tags/integration-base",
        "candidate_ref": "refs/heads/development",
        "heads": [{
            "source_ref": "refs/heads/reviewed/one",
            "reviewed_base_ref": "refs/tags/reviewed-base",
            "reviewer": "trusted-source-review",
            "verdict": "approved",
            "focused_test_files": ["tests/unit/test_native.py"],
        }],
    }
    return root, spec


def _key(tmp_path: Path) -> Path:
    path = tmp_path / "gate.key"
    path.write_text(KEY.hex() + "\n", encoding="ascii")
    path.chmod(0o600)
    return path


def _record(root: Path, spec: dict[str, object], tmp_path: Path) -> Path:
    bundle = tmp_path / "inputs"
    inputs.record_inputs(inputs.prepare_inputs(spec, repo_root=root), bundle, repo_root=root)
    return bundle


def _producer_gate_command(root: Path) -> list[str]:
    """Ask native Make and the real background producer for their actual argv."""
    make_identity = subprocess.run(
        ["make", "--no-print-directory", "-f", "-"], cwd=root, check=True,
        capture_output=True, text=True, timeout=10,
        input="gludd-reviewed-make-identity:\n\t@printf '%s\\n' '$(MAKE)'\n",
    ).stdout.strip()
    output = io.StringIO()
    with redirect_stdout(output):
        assert background_owner.main([
            "--project-root", str(root), "--make-command", make_identity, "--validate-only", "1",
        ]) == 0
    return shlex.split(output.getvalue().split("command=", 1)[1].strip())


def _gate(root: Path, tmp_path: Path, focus_completed: str) -> Path:
    started = max(
        datetime.fromisoformat(focus_completed) + timedelta(microseconds=1),
        datetime.now(UTC),
    )
    started_text = started.isoformat()
    # Ignore policy uses Git's native local exclude, preserving the candidate.
    log_dir = root / ".gate-logs"
    log_dir.mkdir(exist_ok=True)
    run_id = "20261014T120000Z-native-test"
    command = _producer_gate_command(root)
    status = root / ".gate-status"
    status.write_text("lint PASS\ntypecheck PASS\ncollect PASS\ntest PASS\nsmoke PASS\n"
                      "=== GATE: PASSED ===\n", encoding="utf-8")
    gate_owner.sign_status(status, state_id=gate_owner.repository_state_id(root), key=KEY)
    completed = max(datetime.now(UTC), started + timedelta(microseconds=1))
    completed_text = completed.isoformat()
    state = {
        "schema_version": 1, "kind": "gludd_gate_background", "state": "finished",
        "run_id": run_id, "project_root": str(root.resolve()),
        "command": command,
        "started_at": started_text, "finished_at": completed_text,
        "termination_reason": "gate-exited",
    }
    (log_dir / "gate-background-state.json").write_text(json.dumps(state), encoding="utf-8")
    final = _git(root, "rev-parse", "HEAD")
    pairing = shard_owner._attestation_pairing(list(SHARDS), pytest_args=list(shard_owner.RELEASE_PYTEST_ARGS))
    # Model the external canonical release gate, not this fixture inspector's runtime.
    policy = shard_owner.release_execution_policy()
    pairing["execution_policy"] = policy
    pairing["execution_policy_sha256"] = shard_owner.canonical_json_sha256(policy)
    attestation = {
        "schema_version": 3, "lane": "local", "status": "pass", "returncode": 0,
        "runner": "scripts/run_ci_shards_serial.py", "shards": list(SHARDS),
        "identity": {"head_sha": final, "expected_sha": final, "clean": True,
                     "exact_sha": True, "queries_ok": True},
        "started_at": started_text, "completed_at": completed_text,
        **pairing,
    }
    path = inputs.resource_owner.resource_root(root) / "ci-shards/attestation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(attestation), encoding="utf-8")
    return path


def test_gate_fixture_lifecycle_spans_actual_native_signing_after_earlier_focus(tmp_path: Path) -> None:
    root, _ = _fixture(tmp_path)
    focus_completed = datetime.now(UTC) - timedelta(seconds=10)
    attestation = _gate(root, tmp_path, focus_completed.isoformat())
    state = json.loads((root / ".gate-logs/gate-background-state.json").read_text(encoding="utf-8"))
    gate = json.loads(attestation.read_text(encoding="utf-8"))
    outer_started = datetime.fromisoformat(state["started_at"])
    outer_completed = datetime.fromisoformat(state["finished_at"])
    gate_started = datetime.fromisoformat(gate["started_at"])
    gate_completed = datetime.fromisoformat(gate["completed_at"])
    _, authenticated_fields, _ = gate_owner._split_status(
        (root / ".gate-status").read_text(encoding="utf-8"),
    )
    signed_epoch = int(authenticated_fields["attestation-epoch"])
    assert focus_completed <= outer_started <= gate_started <= gate_completed <= outer_completed
    assert gate_started.timestamp() - 1 <= signed_epoch <= outer_completed.timestamp() + 1, (
        f"actual signed epoch={signed_epoch}; gate_started={gate_started.isoformat()}; "
        f"outer_completed={outer_completed.isoformat()}"
    )


@pytest.mark.parametrize("inspector_python", ["3.11", "3.14"])
def test_gate_fixture_binds_release_policy_independent_of_inspector_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inspector_python: str,
) -> None:
    root, _ = _fixture(tmp_path)
    observed_policy = shard_owner.execution_policy(list(shard_owner.RELEASE_PYTEST_ARGS))
    observed_policy["python_version"] = inspector_python
    monkeypatch.setattr(shard_owner, "execution_policy", lambda _args: dict(observed_policy))
    attestation = _gate(root, tmp_path, datetime.now(UTC).isoformat())
    gate = json.loads(attestation.read_text(encoding="utf-8"))
    shards, _, errors = inputs.shard_owner._validate_attestation(
        attestation, gate, sha=_git(root, "rev-parse", "HEAD"), lane="local",
    )
    assert errors == [], errors
    assert shards == set(SHARDS)
    policy = shard_owner.release_execution_policy()
    assert gate["execution_policy"] == policy
    assert gate["execution_policy_sha256"] == shard_owner.canonical_json_sha256(policy)


@pytest.mark.parametrize("mode", ["cherry_pick", "merge_two_parent"])
def test_derives_review_metadata_and_application_from_real_git(tmp_path: Path, mode: str) -> None:
    root, spec = _fixture(tmp_path, mode)
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    assert prepared["candidate_sha"] == _git(root, "rev-parse", "HEAD")
    assert cast(list[dict[str, object]], prepared["heads"])[0]["application_mode"] == mode
    review = cast(list[dict[str, object]], prepared["reviews"])[0]
    assert review["verdict"] == "approved" and review["reviewer"] == "trusted-source-review"
    native = cast(Mapping[str, object], review["native"])
    assert native["changed_files"] == ["source.txt"]
    assert len(cast(str, native["diff_sha256"])) == 64
    assert review["source_sha"] == _git(root, "rev-parse", "reviewed/one")
    assert cast(list[str], prepared["focused_command"])[0:3] == ["make", "--no-print-directory", "test-files"]


@pytest.mark.parametrize("field,value", [
    ("verdict", "ACCEPT"), ("verdict", ""), ("reviewer", ""),
    ("source_sha", "f" * 40), ("passed", True),
    ("source_ref", "HEAD"), ("source_ref", "refs/heads/absent"),
    ("focused_test_files", ["tests/unit/missing.py"]),
    ("focused_test_files", ["../escape.py"]),
])
def test_refuses_inferred_approval_unowned_fields_and_absent_sources(
    tmp_path: Path, field: str, value: object,
) -> None:
    root, spec = _fixture(tmp_path)
    cast(list[dict[str, object]], spec["heads"])[0][field] = value
    with pytest.raises(ValueError):
        inputs.prepare_inputs(spec, repo_root=root)


def test_same_source_direct_history_cannot_be_relabelled(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    _git(root, "branch", "-f", "reviewed/one", "HEAD")
    with pytest.raises(ValueError, match=r"distinct|source"):
        inputs.prepare_inputs(spec, repo_root=root)


def test_rejects_unreviewed_extra_commit_dirty_source_and_wrong_patch(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    _commit(root, "later.txt", "unreviewed\n")
    with pytest.raises(ValueError, match="chain"):
        inputs.prepare_inputs(spec, repo_root=root)
    _git(root, "switch", "reviewed/one")
    _commit(root, "unrelated.txt", "different source patch\n")
    _git(root, "switch", "development")
    spec["integration_base_ref"] = "refs/heads/development"
    with pytest.raises(ValueError):
        inputs.prepare_inputs(spec, repo_root=root)
    (root / "dirty").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        inputs.prepare_inputs(spec, repo_root=root)


def test_merge_parent_claim_cannot_mask_unreviewed_resolution(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path, "merge_two_parent")
    (root / "resolution.txt").write_text("unreviewed resolution\n", encoding="utf-8")
    _git(root, "add", "resolution.txt")
    _git(root, "commit", "--amend", "--no-edit")
    with pytest.raises(ValueError, match="merge"):
        inputs.prepare_inputs(spec, repo_root=root)


def test_admission_never_requests_native_git_object_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path, "merge_two_parent")
    original = receipt_owner._git

    def read_only_git(repo: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        assert "--write-tree" not in arguments, "read-only admission cannot publish native Git objects"
        return original(repo, *arguments, input_bytes=input_bytes)

    monkeypatch.setattr(receipt_owner, "_git", read_only_git)
    inputs.prepare_inputs(spec, repo_root=root)


def test_cherry_pick_refuses_a_changed_original_patch(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    _git(root, "switch", "reviewed/one")
    (root / "source.txt").write_text("different original patch\n", encoding="utf-8")
    _git(root, "add", "source.txt")
    _git(root, "commit", "--amend", "--no-edit")
    _git(root, "switch", "development")
    with pytest.raises(ValueError, match="patch"):
        inputs.prepare_inputs(spec, repo_root=root)


def test_native_order_resolves_a_named_earlier_source_prerequisite(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    _git(root, "switch", "reviewed/one")
    _git(root, "switch", "-c", "reviewed/two")
    _commit(root, "second.txt", "second source\n")
    _git(root, "switch", "development")
    _git(root, "cherry-pick", "reviewed/two")
    cast(list[dict[str, object]], spec["heads"]).append({
        "source_ref": "refs/heads/reviewed/two", "reviewed_base_ref": "refs/heads/reviewed/one",
        "reviewer": "second-trusted-review", "verdict": "approved",
        "focused_test_files": ["tests/unit/test_native.py"],
    })
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    assert cast(list[dict[str, object]], prepared["heads"])[1]["prerequisite_ancestry"] == [{
        "ancestor_sha": _git(root, "rev-parse", "reviewed/one"), "resolution": "required_head",
    }]
    assert prepared["focused_test_files"] == ["tests/unit/test_native.py"]


def test_admission_is_read_only_and_record_is_owned_idempotent(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    bundle = tmp_path / "inputs"
    assert not bundle.exists()
    inputs.record_inputs(prepared, bundle, repo_root=root)
    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    inputs.record_inputs(prepared, bundle, repo_root=root)
    assert before == {p.name: p.read_bytes() for p in bundle.iterdir()}
    assert "manifest.json" not in before
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""
    damaged = copy.deepcopy(prepared)
    damaged_intent = cast(dict[str, object], damaged["intent"])
    cast(list[dict[str, object]], damaged_intent["heads"])[0]["reviewer"] = "substitute"
    with pytest.raises(ValueError):
        inputs.record_inputs(damaged, bundle, repo_root=root)


@pytest.mark.parametrize("state", ["repo", "symlink", "unowned"])
def test_record_refuses_unsafe_or_foreign_output(tmp_path: Path, state: str) -> None:
    root, spec = _fixture(tmp_path)
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    bundle = root / "inputs" if state == "repo" else tmp_path / "inputs"
    if state == "symlink":
        bundle.symlink_to(tmp_path / "elsewhere")
    if state == "unowned":
        bundle.mkdir()
        (bundle / "foreign.json").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError):
        inputs.record_inputs(prepared, bundle, repo_root=root)


def test_captures_one_native_focus_and_reuses_only_authenticated_binding(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focused = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    assert focused["candidate_sha"] == _git(root, "rev-parse", "HEAD")
    assert focused["run_count"] == 1 and focused["passed"] is True
    assert focused["native_status"]["state"] == "passed"
    assert focused["native_status"]["exit_code"] == 0
    first_bytes = (bundle / "focused.json").read_bytes()
    assert inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key) == focused
    assert (bundle / "focused.json").read_bytes() == first_bytes
    assert inputs.validate_focused(repo_root=root, bundle=bundle, key_path=key) == focused


def test_focus_detects_changed_native_artifact_and_unsigned_refusal(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focused = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    log = bundle / focused["log_file"]
    log.write_text("forged pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="focused"):
        inputs.validate_focused(repo_root=root, bundle=bundle, key_path=key)
    (bundle / "focused-auth.json").unlink()
    with pytest.raises(ValueError):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)


def test_failed_focus_never_publishes_passing_binding_or_retries(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path, focus_pass=False)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    with pytest.raises(ValueError, match="focused"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
    assert not (bundle / "focused.json").exists()
    with pytest.raises(ValueError, match=r"already|reserved|unchanged failed"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)


def test_failed_candidate_cannot_retry_through_a_second_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path, focus_pass=False, native_child_probe=True)
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    first, second = tmp_path / "bundle-a", tmp_path / "bundle-b"
    inputs.record_inputs(prepared, first, repo_root=root)
    inputs.record_inputs(prepared, second, repo_root=root)
    key = _key(tmp_path)
    with pytest.raises(ValueError):
        inputs.capture_focused(repo_root=root, bundle=first, key_path=key)
    failure = next(path for path in tmp_path.rglob("focus-*.json")
                   if json.loads(path.read_text(encoding="utf-8")).get("state") == "failed")
    original = failure.read_bytes()
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"
    monkeypatch.setenv("OBSERVED_ROOT", str(tmp_path / "substituted-observer"))
    monkeypatch.setenv("OBSERVED_LABEL", "substituted-label")
    monkeypatch.setenv("GLUDD_REVIEWED_HEAD_INPUTS_OUTPUT", str(second))
    with pytest.raises(ValueError):
        inputs.capture_focused(repo_root=root, bundle=second, key_path=key)
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"
    assert failure.read_bytes() == original
    assert not (second / "focused.json").exists()


def test_focus_runtime_matches_actual_child_plugin_manager(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path, child_plugin_autoload=False, native_child_probe=True)
    focused = cast(_Focused, inputs.capture_focused(
        repo_root=root, bundle=_record(root, spec, tmp_path), key_path=_key(tmp_path),
    ))
    child = json.loads((tmp_path / "native-child-runtime.json").read_text(encoding="utf-8"))
    assert focused["runtime"]["python"] == child["python"]
    assert focused["runtime"]["executable_sha256"] == child["executable_sha256"]
    assert focused["runtime"]["loaded_plugins"] == child["loaded_plugins"]
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"


def test_historical_focus_binds_candidate_adapter_blobs_not_inspecting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path, native_child_probe=True)
    bundle, key = _record(root, spec, tmp_path), _key(tmp_path)
    focused = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    retained = {path: path.read_bytes() for path in (
        bundle / "focused.json", bundle / "focused-auth.json", Path(focused["trace_file"]),
    )}
    consumer = tmp_path / "updated-consumer" / "reviewed_head_inputs.py"
    consumer.parent.mkdir()
    consumer.write_text("# Independently updated inspector, not the tested candidate.\n", encoding="utf-8")
    monkeypatch.setattr(inputs, "__file__", str(consumer))
    assert inputs.validate_focused(repo_root=root, bundle=bundle, key_path=key) == focused
    assert inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key) == focused
    assert all(path.read_bytes() == content for path, content in retained.items())
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"


def test_finalization_accepts_actual_native_background_producer_command(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle, key = _record(root, spec, tmp_path), _key(tmp_path)
    focused = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    gate = _gate(root, tmp_path, focused["completed_at"])
    state_path = root / ".gate-logs/gate-background-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["command"] == _producer_gate_command(root)
    manifest = inputs.finalize_inputs(repo_root=root, bundle=bundle, gate_attestation=gate, key_path=key)
    assert cast(Mapping[str, object], manifest["focused_validation"])["tip_sha"] == _git(root, "rev-parse", "HEAD")
    assert (bundle / "reviewed-head-receipt.json").is_file()


@pytest.mark.parametrize("adapter", ["reviewed_head_inputs.py", "stream_command.py"])
@pytest.mark.parametrize("mutation", ["missing", "symlink", "directory", "empty", "oversized"])
def test_candidate_adapter_binding_requires_real_ordinary_git_blobs(
    tmp_path: Path, adapter: str, mutation: str,
) -> None:
    root, _spec = _fixture(tmp_path)
    path = root / "scripts" / adapter
    path.unlink()
    if mutation == "symlink":
        path.symlink_to(ROOT / "scripts" / adapter)
    elif mutation == "directory":
        path.mkdir()
        (path / "member.py").write_text("# Not an ordinary adapter blob.\n", encoding="utf-8")
    elif mutation in {"empty", "oversized"}:
        path.write_bytes(b"" if mutation == "empty" else b"#" * 1_048_577)
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "invalid candidate adapter fixture")
    with pytest.raises(ValueError, match="candidate provenance adapter blob"):
        inputs._candidate_adapter_hashes(root, _git(root, "rev-parse", "HEAD"))


@pytest.mark.parametrize("sha", ["HEAD", "-h", "a" * 39, "A" * 40, "0" * 40])
def test_candidate_adapter_binding_refuses_nonexact_or_unavailable_objects(tmp_path: Path, sha: str) -> None:
    root, _spec = _fixture(tmp_path)
    with pytest.raises(ValueError, match="candidate provenance adapter blob"):
        inputs._candidate_adapter_hashes(root, sha)


_ADAPTER_FILES = ("scripts/reviewed_head_inputs.py", "scripts/stream_command.py")
_ADAPTER_BODIES = (b"\x00producer\n\xffbinary\r\n", b"observer\x00\n\x80binary\r\n")


def _adapter_repository(
    tmp_path: Path, *, shared_blob: bool = False, object_format: str = "sha1",
) -> tuple[Path, str, tuple[str, str]]:
    root = _clean_repository(tmp_path, object_format)
    for index, name in enumerate(_ADAPTER_FILES):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(_ADAPTER_BODIES[0 if shared_blob else index])
        path.chmod(0o755 if index else 0o644)
    _git(root, "add", "scripts")
    _git(root, "commit", "-m", "ordinary binary adapter fixtures")
    sha = _git(root, "rev-parse", "HEAD")
    identities = tuple(_git(root, "rev-parse", f"{sha}:{name}") for name in _ADAPTER_FILES)
    return root, sha, (identities[0], identities[1])


@pytest.mark.parametrize("shared_blob", (False, True))
def test_candidate_adapter_metadata_uses_four_real_git_launches_per_fresh_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_blob: bool,
) -> None:
    root, sha, identities = _adapter_repository(tmp_path, shared_blob=shared_blob)
    native_run = subprocess.run
    launches: list[tuple[tuple[str, ...], bytes | None]] = []

    def launched(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert command[:3] == ["git", "-C", str(root)]
        assert set(kwargs) == {"input", "capture_output", "check"}
        stdin = kwargs["input"]
        assert isinstance(stdin, bytes) or stdin is None
        launches.append((tuple(command[3:]), stdin))
        return native_run(
            command, input=stdin, capture_output=kwargs["capture_output"] is True,
            check=kwargs["check"] is True,
        )

    monkeypatch.setattr("scripts.build_reviewed_head_integration_receipt.subprocess.run", launched)
    expected = {
        "producer_sha256": hashlib.sha256(_ADAPTER_BODIES[0]).hexdigest(),
        "observer_sha256": hashlib.sha256(_ADAPTER_BODIES[0 if shared_blob else 1]).hexdigest(),
    }
    for _fresh_boundary in range(2):
        start = len(launches)
        assert inputs._candidate_adapter_hashes(root, sha) == expected
        assert launches[start:] == [
            (("ls-tree", "-z", sha, "--", *_ADAPTER_FILES), None),
            (("cat-file", "--batch-check"), ("\n".join(identities) + "\n").encode("ascii")),
            (("cat-file", "blob", identities[0]), None),
            (("cat-file", "blob", identities[1]), None),
        ]


@pytest.mark.parametrize("body_size", (1, 1_048_576))
def test_candidate_adapter_metadata_preserves_both_inclusive_size_bounds(
    tmp_path: Path, body_size: int,
) -> None:
    root, _sha, _identities = _adapter_repository(tmp_path)
    body = b"\x00" * body_size
    (root / _ADAPTER_FILES[1]).write_bytes(body)
    _git(root, "add", _ADAPTER_FILES[1])
    _git(root, "commit", "-m", "native adapter size boundary")
    sha = _git(root, "rev-parse", "HEAD")
    assert inputs._candidate_adapter_hashes(root, sha) == {
        "producer_sha256": hashlib.sha256(_ADAPTER_BODIES[0]).hexdigest(),
        "observer_sha256": hashlib.sha256(body).hexdigest(),
    }


def test_candidate_adapter_metadata_preserves_native_replacements_and_binary_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, sha, identities = _adapter_repository(tmp_path)
    replacement_path = tmp_path / "replacement.bin"
    replacement_path.write_bytes(b"\xffeffective replacement\x00\nwith a different size\n")
    replacement = _git(root, "hash-object", "-w", str(replacement_path))
    assert replacement != identities[1]
    _git(root, "replace", identities[1], replacement)
    for use_replacements, no_replace in ((True, False), (True, True), (False, False), (True, False)):
        _git(root, "config", "core.useReplaceRefs", str(use_replacements).lower())
        if no_replace:
            monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "1")
        else:
            monkeypatch.delenv("GIT_NO_REPLACE_OBJECTS", raising=False)
        metadata = receipt_owner._git(
            root, "cat-file", "--batch-check", input_bytes=(identities[1] + "\n").encode("ascii"),
        ).split()
        expected: dict[str, str] = {}
        for name, identity in zip(("producer_sha256", "observer_sha256"), identities, strict=True):
            size = int(receipt_owner._git(root, "cat-file", "-s", identity))
            assert 0 < size <= 1_048_576
            body = receipt_owner._git(root, "cat-file", "blob", identity)
            expected[name] = hashlib.sha256(body).hexdigest()
        assert metadata == [
            identities[1].encode("ascii"), b"blob",
            str(len(replacement_path.read_bytes()) if use_replacements and not no_replace
                else len(_ADAPTER_BODIES[1])).encode("ascii"),
        ]
        assert inputs._candidate_adapter_hashes(root, sha) == expected


@pytest.mark.parametrize("replacement_kind", ("empty", "oversized", "tree"))
def test_candidate_adapter_metadata_refuses_ineligible_effective_replacement_before_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement_kind: str,
) -> None:
    root, sha, identities = _adapter_repository(tmp_path)
    monkeypatch.delenv("GIT_NO_REPLACE_OBJECTS", raising=False)
    _git(root, "config", "core.useReplaceRefs", "true")
    replacement_path = tmp_path / "ineligible.bin"
    replacement_path.write_bytes(b"" if replacement_kind == "empty" else b"x" * 1_048_577)
    replacement = (_git(root, "rev-parse", f"{sha}^{{tree}}") if replacement_kind == "tree"
                   else _git(root, "hash-object", "-w", str(replacement_path)))
    _git(root, "replace", "-f", identities[1], replacement)
    native_git = receipt_owner._git
    bodies: list[str] = []

    def recorded(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        if arguments[:2] == ("cat-file", "blob"):
            bodies.append(arguments[-1])
        return native_git(repository, *arguments, input_bytes=input_bytes)

    monkeypatch.setattr(receipt_owner, "_git", recorded)
    with pytest.raises(ValueError, match="candidate provenance adapter blob"):
        inputs._candidate_adapter_hashes(root, sha)
    assert bodies == []


@pytest.mark.parametrize("batch_fault", (
    "missing", "ambiguous", "empty", "missing-row", "extra-row", "duplicate-row",
    "unexpected-oid", "reordered", "wrong-type", "invalid-size", "negative-size",
    "zero-size", "oversized", "signed-size", "spaced-size", "nul", "non-ascii",
    "unterminated", "crlf", "failure",
))
def test_candidate_adapter_metadata_refuses_invalid_complete_response_before_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, batch_fault: str,
) -> None:
    root, sha, identities = _adapter_repository(tmp_path)
    native_git = receipt_owner._git
    valid = native_git(
        root, "cat-file", "--batch-check", input_bytes=("\n".join(identities) + "\n").encode("ascii"),
    )
    first, second = valid.splitlines()
    fields = second.split(b" ")
    alternate_size = {
        "invalid-size": b"NaN", "negative-size": b"-1", "zero-size": b"0",
        "oversized": b"1048577", "signed-size": b"+17", "spaced-size": b" 17",
    }
    replacement_row = {
        "missing": fields[0] + b" missing", "ambiguous": fields[0] + b" ambiguous",
        "duplicate-row": first, "unexpected-oid": b"0" * 40 + b" blob 17",
        "wrong-type": fields[0] + b" tree " + fields[2],
        "nul": second + b"\x00", "non-ascii": second + b"\xff",
    }.get(batch_fault, b" ".join((*fields[:2], alternate_size.get(batch_fault, fields[2]))))
    response = first + b"\n" + replacement_row + b"\n"
    if batch_fault == "empty":
        response = b""
    elif batch_fault == "missing-row":
        response = first + b"\n"
    elif batch_fault == "extra-row":
        response += second + b"\n"
    elif batch_fault == "reordered":
        response = second + b"\n" + first + b"\n"
    elif batch_fault == "unterminated":
        response = response[:-1]
    elif batch_fault == "crlf":
        response = response.replace(b"\n", b"\r\n")
    bodies: list[str] = []

    def substituted(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        if arguments == ("cat-file", "--batch-check"):
            assert input_bytes == ("\n".join(identities) + "\n").encode("ascii")
            if batch_fault == "failure":
                raise OSError("native metadata unavailable")
            return response
        if arguments[:2] == ("cat-file", "blob"):
            bodies.append(arguments[-1])
        return native_git(repository, *arguments, input_bytes=input_bytes)

    monkeypatch.setattr(receipt_owner, "_git", substituted)
    with pytest.raises(ValueError, match="candidate provenance adapter blob"):
        inputs._candidate_adapter_hashes(root, sha)
    assert bodies == []


@pytest.mark.parametrize("tree_fault", ("missing", "duplicate", "unexpected", "bad-oid", "bad-mode"))
def test_candidate_adapter_metadata_validates_all_tree_entries_before_any_metadata_or_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tree_fault: str,
) -> None:
    root, sha, _identities = _adapter_repository(tmp_path)
    native_git = receipt_owner._git
    first, second, _terminal = native_git(
        root, "ls-tree", "-z", sha, "--", *_ADAPTER_FILES,
    ).split(b"\x00")
    if tree_fault == "missing":
        response = first + b"\x00"
    elif tree_fault == "duplicate":
        response = first + b"\x00" + first + b"\x00"
    elif tree_fault == "unexpected":
        response = first + b"\x00" + second.replace(b"stream_command.py", b"unreviewed.py") + b"\x00"
    elif tree_fault == "bad-oid":
        response = first + b"\x00" + second.replace(second.split()[2], b"f" * 39) + b"\x00"
    else:
        response = first + b"\x00" + second.replace(b"100755", b"120000") + b"\x00"
    reads: list[tuple[str, ...]] = []

    def substituted(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        if arguments[0] == "ls-tree":
            return response
        reads.append(arguments)
        return native_git(repository, *arguments, input_bytes=input_bytes)

    monkeypatch.setattr(receipt_owner, "_git", substituted)
    with pytest.raises(ValueError, match="candidate provenance adapter blob"):
        inputs._candidate_adapter_hashes(root, sha)
    assert reads == []


def test_candidate_adapter_metadata_keeps_exact_sha1_candidate_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, sha, _identities = _adapter_repository(tmp_path, object_format="sha256")
    assert len(sha) == 64

    def refused(_repository: Path, *_arguments: str, input_bytes: bytes | None = None) -> bytes:
        pytest.fail("ineligible candidate reached native Git")

    monkeypatch.setattr(receipt_owner, "_git", refused)
    with pytest.raises(ValueError, match="candidate provenance adapter blob"):
        inputs._candidate_adapter_hashes(root, sha)


def test_new_focus_refuses_inspector_bytes_different_from_candidate_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path, native_child_probe=True)
    bundle, key = _record(root, spec, tmp_path), _key(tmp_path)
    inspector = tmp_path / "updated-consumer" / "reviewed_head_inputs.py"
    inspector.parent.mkdir()
    inspector.write_text("# Inspector is not the exact tested source.\n", encoding="utf-8")
    monkeypatch.setattr(inputs, "__file__", str(inspector))
    with pytest.raises(ValueError, match="provenance adapter implementation changed"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
    assert not (tmp_path / "native-child-count").exists()
    assert not (bundle / "focused.json").exists()


@pytest.mark.parametrize("mutation", ["fake-make", "makefile", "extra-target", "ownership", "empty"])
def test_finalization_refuses_noncanonical_producer_command_without_publication(
    tmp_path: Path, mutation: str,
) -> None:
    root, spec = _fixture(tmp_path)
    bundle, key = _record(root, spec, tmp_path), _key(tmp_path)
    focused = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    gate = _gate(root, tmp_path, focused["completed_at"])
    state_path = root / ".gate-logs/gate-background-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if mutation == "fake-make":
        fake = tmp_path / "make"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        state["command"][0] = str(fake)
    elif mutation == "makefile":
        state["command"][2] = str(tmp_path / "Makefile")
    elif mutation == "extra-target":
        state["command"].append("gate-lite")
    elif mutation == "ownership":
        state["command"][-1] = "gludd_watchdog_owned_gate=0"
    else:
        state["command"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="native outer gate command"):
        inputs.finalize_inputs(repo_root=root, bundle=bundle, gate_attestation=gate, key_path=key)
    assert not any((bundle / name).exists() for name in (
        "manifest.json", "gate-binding.json", "reviewed-head-receipt.json",
    ))


def test_native_runtime_trace_refuses_substituted_executor_facts(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    focused = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=_key(tmp_path)))
    status = focused["native_status"]
    recorded = json.loads((bundle / "inputs.json").read_text(encoding="utf-8"))
    original = Path(focused["trace_file"]).read_bytes()
    trace = tmp_path / "substituted-runtime.jsonl"
    for mutation, expected in (
        ("run", "another invocation"), ("executor", "actual test executor"),
        ("terminal", "completed actual executor"), ("source", "source or executing child"),
        ("adapter", "implementation changed"), ("observer-adapter", "implementation changed"),
    ):
        events = [json.loads(line) for line in original.splitlines()]
        if mutation == "run":
            events[0]["run_id"] = "substituted-run"
        elif mutation == "executor":
            events = [event for event in events if event["event"] != "START"]
        elif mutation == "terminal":
            events = [event for event in events if event["event"] != "FOCUS_RUNTIME_FINISH"]
        else:
            for event in events:
                if event["event"].startswith("FOCUS_RUNTIME"):
                    if mutation == "source":
                        event["candidate_sha"] = "f" * 40
                    elif mutation == "adapter":
                        event["runtime"]["producer_sha256"] = "f" * 64
                    else:
                        event["runtime"]["observer_sha256"] = "f" * 64
        trace.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        with pytest.raises(ValueError, match=expected):
            inputs._native_runtimes(trace, status, recorded, repo_root=root)
    trace.write_text("x" * 1_048_577 + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="event exceeds"):
        inputs._native_runtimes(trace, status, recorded, repo_root=root)
    linked = tmp_path / "linked-runtime.jsonl"
    linked.symlink_to(trace)
    with pytest.raises(ValueError, match="unsafe"):
        inputs._native_runtimes(linked, status, recorded, repo_root=root)
    assert Path(focused["trace_file"]).read_bytes() == original


@pytest.mark.parametrize("hook", ["collection", "session"])
@pytest.mark.parametrize("run_id", [None, "", "legacy", "focus-invalid", " focus-" + "a" * 64])
def test_focus_runtime_requires_an_explicit_owned_run_identity_before_reading_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
    hook: str, run_id: str | None,
) -> None:
    monkeypatch.setenv("GLUDD_REVIEWED_HEAD_FOCUS_ROOT", str(tmp_path.resolve()))
    monkeypatch.chdir(tmp_path)
    if run_id is None:
        monkeypatch.delenv("GLUDD_XDIST_TRACE_RUN_ID", raising=False)
    else:
        monkeypatch.setenv("GLUDD_XDIST_TRACE_RUN_ID", run_id)
    expected = "run identity is required" if run_id is None else "run identity is invalid"
    with pytest.raises(ValueError, match=expected):
        if hook == "collection":
            inputs.pytest_collection_finish(request.session)
        else:
            inputs.pytest_sessionfinish(request.session, 0)
    assert not (tmp_path / ".gate-logs").exists()


def test_runtime_probe_refuses_producer_process_as_native_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    root, spec = _fixture(tmp_path)
    focused = cast(_Focused, inputs.capture_focused(
        repo_root=root, bundle=_record(root, spec, tmp_path), key_path=_key(tmp_path),
    ))
    runtime = inputs._focus_runtime(request.config.pluginmanager)
    assert runtime["python"] == sys.version
    assert runtime["loaded_plugins"] == sorted(
        name for name, plugin in request.config.pluginmanager.list_name_plugin() if plugin is not None
    )
    monkeypatch.delenv("GLUDD_REVIEWED_HEAD_FOCUS_ROOT", raising=False)
    inputs.pytest_collection_finish(request.session)
    inputs.pytest_sessionfinish(request.session, 0)
    monkeypatch.setenv("GLUDD_REVIEWED_HEAD_FOCUS_ROOT", str(root))
    monkeypatch.chdir(root)
    monkeypatch.setenv("GLUDD_XDIST_TRACE_RUN_ID", "substituted-run")
    with pytest.raises(ValueError, match="run identity"):
        inputs.pytest_collection_finish(request.session)
    monkeypatch.setenv("GLUDD_XDIST_TRACE_RUN_ID", focused["run_id"])
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(tmp_path / "unowned-trace"))
    with pytest.raises(ValueError, match="trace ownership"):
        inputs.pytest_collection_finish(request.session)
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", focused["trace_file"])
    with pytest.raises(ValueError, match="executing child"):
        inputs.pytest_collection_finish(request.session)
    with pytest.raises(ValueError, match="executing child"):
        inputs.pytest_sessionfinish(request.session, 0)


def test_final_manifest_binds_actual_terminal_gate_and_existing_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path, native_child_probe=True)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focus = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    attestation = _gate(root, tmp_path, focus["completed_at"])
    calls: list[str] = []
    native_builder = receipt_owner.build_receipt_from_manifest
    native_writer = receipt_owner.write_receipt

    def counted_builder(
        raw_manifest: object, *, manifest_dir: Path, gate_attestation: object, repo_root: Path,
    ) -> ReviewedHeadIntegrationReceipt:
        calls.append("build")
        return native_builder(raw_manifest, manifest_dir=manifest_dir,
                              gate_attestation=gate_attestation, repo_root=repo_root)

    def counted_writer(path: Path, receipt: ReviewedHeadIntegrationReceipt, *, exclusive: bool = False) -> None:
        calls.append("write")
        native_writer(path, receipt, exclusive=exclusive)

    monkeypatch.setattr(receipt_owner, "build_receipt_from_manifest", counted_builder)
    monkeypatch.setattr(receipt_owner, "write_receipt", counted_writer)
    manifest = inputs.finalize_inputs(repo_root=root, bundle=bundle,
                                     gate_attestation=attestation, key_path=key)
    assert calls == ["build", "write"]
    assert manifest["exact_gate_id"] == "gate:20261014T120000Z-native-test"
    assert cast(Mapping[str, object], manifest["focused_validation"])["passed"] is True
    assert cast(Mapping[str, object], manifest["focused_validation"])["run_count"] == 1
    assert (bundle / "manifest.json").exists()
    output = bundle / "reviewed-head-receipt.json"
    original = output.read_bytes()
    metadata = output.stat()
    candidate = _git(root, "rev-parse", "HEAD")
    monkeypatch.delenv("RELEASE_ALLOW_INVALID_RECEIPT", raising=False)
    valid, detail, final_sha = readiness._reviewed_head_receipt_check(output, expected_final_sha=candidate)
    assert valid, detail
    assert final_sha == candidate
    wrong, _, _ = readiness._reviewed_head_receipt_check(output, expected_final_sha="f" * 40)
    assert not wrong
    assert set(manifest) == {"schema_version", "base_sha", "heads", "exact_gate_id", "focused_validation"}
    tasks_before = (root / "TASKS.md").read_bytes()
    assert inputs.finalize_inputs(repo_root=root, bundle=bundle,
                                  gate_attestation=attestation, key_path=key) == manifest
    assert output.read_bytes() == original
    assert output.stat().st_ino == metadata.st_ino
    assert output.stat().st_mtime_ns == metadata.st_mtime_ns
    assert calls == ["build", "write", "build"]
    inputs.record_inputs(inputs.prepare_inputs(spec, repo_root=root), bundle, repo_root=root)
    assert output.read_bytes() == original
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"
    assert (root / "TASKS.md").read_bytes() == tasks_before
    state = readiness.Readiness(
        branch="development", head=candidate, ci_head_matches=True,
        ci_verdict="PENDING", version_consistent=True, ledger_valid=True,
        release_policy_compatible=True, reviewed_head_receipt_required=True,
        reviewed_head_receipt_valid=valid, reviewed_head_receipt_final_sha=final_sha,
        incomplete_release_tasks=["S98.4"],
    )
    assert not state.ready
    assert readiness._exit_code(state) == readiness.EXIT_CI
    state.ci_verdict = "GREEN"
    state.errors.append("release-critical TASKS.md items are incomplete: S98.4")
    assert not state.ready
    assert readiness._exit_code(state) == readiness.EXIT_TASKS
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""


@pytest.mark.parametrize("output_state", ["divergent", "symlink", "directory"])
def test_final_receipt_refuses_foreign_output_without_overwriting(
    tmp_path: Path, output_state: str,
) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focus = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    gate = _gate(root, tmp_path, focus["completed_at"])
    output = bundle / "reviewed-head-receipt.json"
    foreign = tmp_path / "foreign.json"
    foreign.write_bytes(b"foreign immutable bytes\n")
    if output_state == "symlink":
        output.symlink_to(foreign)
    elif output_state == "directory":
        output.mkdir()
    else:
        output.write_bytes(foreign.read_bytes())
    with pytest.raises(ValueError, match=r"artifact|output|symbolic|owner"):
        inputs.finalize_inputs(repo_root=root, bundle=bundle, gate_attestation=gate, key_path=key)
    assert foreign.read_bytes() == b"foreign immutable bytes\n"
    if output_state == "symlink":
        assert output.is_symlink()
    elif output_state == "directory":
        assert output.is_dir()
    else:
        assert output.read_bytes() == foreign.read_bytes()
    assert not (bundle / "manifest.json").exists()


def test_final_receipt_revalidates_revocation_expiry_and_partial_focus_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path, native_child_probe=True)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focus = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    gate = _gate(root, tmp_path, focus["completed_at"])
    signer_id = inputs._auth_context(key).signer.signer_id
    future = int(time.time()) + 10**9
    focused_path = bundle / "focused.json"
    original = focused_path.read_bytes()
    for state in ("revoked", "expired", "partial"):
        with monkeypatch.context() as patch:
            if state == "revoked":
                patch.setenv("GLUDD_RECEIPT_REVOKED_SIGNERS", signer_id)
            elif state == "expired":
                patch.setattr(time, "time", lambda: float(future))
            else:
                incomplete = json.loads(original)
                incomplete["command_ids"] = []
                focused_path.write_text(json.dumps(incomplete), encoding="utf-8")
            with pytest.raises(ValueError):
                inputs.finalize_inputs(repo_root=root, bundle=bundle, gate_attestation=gate, key_path=key)
        focused_path.write_bytes(original)
        assert not any((bundle / name).exists() for name in (
            "reviewed-head-receipt.json", "gate-binding.json", "manifest.json",
        ))
        assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"


@pytest.mark.parametrize("mutation", [
    "running", "failed", "wrong-sha", "partial", "unsigned", "reordered", "signed-time", "path",
    "nonrelease-runtime",
])
def test_gate_fact_substitution_and_partial_focus_never_grant_release_proof(
    tmp_path: Path, mutation: str,
) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focus = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    attestation = _gate(root, tmp_path, focus["completed_at"])
    data = json.loads(attestation.read_text(encoding="utf-8"))
    state_path = root / ".gate-logs/gate-background-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if mutation == "running":
        state["state"] = "running"
    elif mutation == "failed":
        data["status"] = "fail"
    elif mutation == "wrong-sha":
        data["identity"]["head_sha"] = "f" * 40
    elif mutation == "partial":
        data["shards"] = [next(iter(SHARDS))]
    elif mutation == "unsigned":
        (root / ".gate-status").write_text("=== GATE: PASSED ===\n", encoding="utf-8")
    elif mutation == "reordered":
        state["started_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        data["started_at"] = state["started_at"]
    elif mutation == "signed-time":
        future = datetime.now(UTC) + timedelta(days=1)
        state["started_at"] = future.isoformat()
        state["finished_at"] = (future + timedelta(seconds=1)).isoformat()
        data["started_at"] = state["started_at"]
        data["completed_at"] = (future + timedelta(microseconds=1)).isoformat()
    elif mutation == "nonrelease-runtime":
        data["execution_policy"]["python_version"] = "3.14"
        data["execution_policy_sha256"] = shard_owner.canonical_json_sha256(data["execution_policy"])
        _, _, errors = inputs.shard_owner._validate_attestation(
            attestation, data, sha=_git(root, "rev-parse", "HEAD"), lane="local",
        )
        assert len(errors) == 1 and "does not use the release execution policy" in errors[0]
    else:
        attestation = tmp_path / "substituted-gate-path.json"
    attestation.write_text(json.dumps(data), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        inputs.finalize_inputs(repo_root=root, bundle=bundle,
                               gate_attestation=attestation, key_path=key)
    assert not (bundle / "manifest.json").exists()
    assert not (bundle / "reviewed-head-receipt.json").exists()


def test_json_duplicate_keys_and_schema_rejection_are_content_free(tmp_path: Path) -> None:
    sensitive_marker = "DO_NOT_PRINT_THIS"
    spec = tmp_path / "intent.json"
    spec.write_text('{"schema_version":1,"schema_version":1,"secret":"' + sensitive_marker + '"}', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        inputs.read_intent(spec)
    assert sensitive_marker not in str(error.value)
    with pytest.raises(ValueError):
        inputs.prepare_inputs({"schema_version": 9}, repo_root=tmp_path)


def test_cli_dry_plan_does_not_read_missing_inputs_or_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "absent"
    assert inputs.main(["--action", "record", "--intent", str(tmp_path / "missing"),
                        "--output", str(output), "--validate-only", "1"]) == 0
    assert "REVIEWED-HEAD-INPUTS-PLAN" in capsys.readouterr().out
    assert not output.exists()


@pytest.mark.parametrize("message, reason", [
    ("native outer gate command differs from the canonical producer", "outer-gate-command"),
    ("focused child provenance adapter implementation changed", "focused-adapter-identity"),
    ("candidate provenance adapter blob is unavailable or ineligible", "candidate-adapter-blob"),
    ("DO_NOT_PRINT_THIS /private/token?key=hidden", "native-provenance"),
])
def test_cli_refusal_classification_is_bounded_and_content_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    message: str, reason: str,
) -> None:
    def refused(**_arguments: object) -> dict[str, object]:
        raise ValueError(message)

    monkeypatch.setattr(inputs, "finalize_inputs", refused)
    assert inputs.main([
        "--action", "finalize", "--repo-root", str(tmp_path), "--output", str(tmp_path / "bundle"),
        "--gate-attestation", str(tmp_path / "gate.json"), "--validate-only", "0",
    ]) == 2
    captured = capsys.readouterr()
    assert "reason=" + reason in captured.err
    assert "DO_NOT_PRINT_THIS" not in captured.err and "hidden" not in captured.err
    assert len(captured.err) < 240 and not captured.out
    assert not (tmp_path / "bundle").exists()


def test_cli_native_sequence_preserves_admission_and_gate_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root, spec = _fixture(tmp_path)
    intent = tmp_path / "intent.json"
    intent.write_text(json.dumps(spec), encoding="utf-8")
    bundle = tmp_path / "inputs"
    key = _key(tmp_path)
    monkeypatch.setenv("GLUDD_GATE_KEY_PATH", str(key))
    common = ["--intent", str(intent), "--output", str(bundle), "--repo-root", str(root), "--validate-only", "0"]
    assert inputs.main(["--action", "admit", *common]) == 0
    assert not bundle.exists()
    assert inputs.main(["--action", "record", *common]) == 0
    assert inputs.main(["--action", "focus", *common]) == 0
    focus = json.loads((bundle / "focused.json").read_text(encoding="utf-8"))
    gate = _gate(root, tmp_path, focus["completed_at"])
    inputs.finalize_inputs(repo_root=root, bundle=bundle, gate_attestation=gate, key_path=key, validate_only=True)
    assert not (bundle / "manifest.json").exists()
    assert not (bundle / "gate-binding.json").exists()
    assert not (bundle / "reviewed-head-receipt.json").exists()
    capsys.readouterr()
    assert inputs.main(["--action", "finalize", "--gate-attestation", str(gate), *common]) == 0
    assert (bundle / "manifest.json").exists()
    handoff_line = next(
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("REVIEWED-HEAD-INPUTS-HANDOFF ")
    )
    handoff = json.loads(handoff_line.split(" ", 1)[1])
    candidate = _git(root, "rev-parse", "HEAD")
    assert handoff["candidate_sha"] == candidate
    assert handoff["receipt_path"] == str(bundle / "reviewed-head-receipt.json")
    assert handoff["release_ready"] is False
    assert handoff["gate_evidence"] is False
    assert handoff["next_argv"] == [
        "make", "--no-print-directory", "-C", str(root), "release-readiness",
        "TAG=v0.1.2", "RELEASE_READINESS_VALIDATE_ONLY=1",
        "RELEASE_COMPLETED_STAGES=", "RELEASE_OBSERVATIONS=",
        "REVIEWED_HEAD_INTEGRATION_RECEIPT=" + handoff["receipt_path"],
        "RELEASE_CANDIDATE_SHA=" + candidate, "RELEASE_READINESS_CI_BRANCH=",
    ]


@pytest.mark.parametrize("arguments", [
    ["--action", "unsupported"], ["--validate-only", "2"],
    ["--action", "record", "--validate-only", "0"],
    ["--action", "record", "--output", "/tmp/gludd-no-intent", "--validate-only", "0"],
    ["--action", "finalize", "--output", "/tmp/gludd-no-gate", "--validate-only", "0"],
])
def test_cli_refuses_unowned_policy_or_missing_inputs_without_writing(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as missing_inputs:
        for name in ("ACTION", "INTENT", "OUTPUT", "REPO_ROOT", "GATE_ATTESTATION", "VALIDATE_ONLY"):
            missing_inputs.delenv("GLUDD_REVIEWED_HEAD_INPUTS_" + name, raising=False)
        assert inputs.main(arguments) == 2


@pytest.mark.parametrize("arguments", [
    ["--action", "record", "--validate-only", "0"],
    ["--action", "record", "--output", "/tmp/gludd-no-intent", "--validate-only", "0"],
])
def test_missing_cli_fixture_isolates_and_restores_genuine_configured_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    root, spec = _fixture(tmp_path)
    intent = tmp_path / "intent.json"
    intent.write_text(json.dumps(spec), encoding="utf-8")
    bundle = tmp_path / "configured-inputs"
    configured = {
        "GLUDD_REVIEWED_HEAD_INPUTS_ACTION": "record",
        "GLUDD_REVIEWED_HEAD_INPUTS_INTENT": str(intent),
        "GLUDD_REVIEWED_HEAD_INPUTS_OUTPUT": str(bundle),
        "GLUDD_REVIEWED_HEAD_INPUTS_REPO_ROOT": str(root),
        "GLUDD_REVIEWED_HEAD_INPUTS_GATE_ATTESTATION": str(tmp_path / "future-gate.json"),
        "GLUDD_REVIEWED_HEAD_INPUTS_VALIDATE_ONLY": "0",
    }
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    assert inputs.main(["--action", "admit"]) == 0
    assert not bundle.exists()

    def unexpected_record(*_args: object, **_kwargs: object) -> None:
        pytest.fail("missing-input fixture attempted native record publication from inherited defaults")

    with monkeypatch.context() as guarded:
        guarded.setattr(inputs, "record_inputs", unexpected_record)
        test_cli_refuses_unowned_policy_or_missing_inputs_without_writing(arguments, guarded)
    assert {name: os.environ.get(name) for name in configured} == configured
    assert not bundle.exists()
    # Restored defaults still drive a genuine native publication into this test's owned output.
    assert inputs.main([]) == 0
    recorded = json.loads((bundle / "inputs.json").read_text(encoding="utf-8"))
    assert recorded["candidate_sha"] == _git(root, "rev-parse", "HEAD")
    assert recorded["intent"] == spec


def test_missing_key_and_substituted_execution_owner_cannot_claim_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    with pytest.raises(ValueError, match="authentication"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=tmp_path / "missing-key")
    assert not (tmp_path / "missing-key").exists()
    monkeypatch.setattr(observed_owner, "stream_command", lambda *_args, **_kwargs: 0)
    with pytest.raises(ValueError, match="substituted"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=_key(tmp_path))
    assert not (bundle / "focused.json").exists()


def test_empty_native_make_success_is_not_executed_test_evidence(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path, native_tests=False)
    # The real native make owner returns zero but has no passing JUnit outcomes.
    with pytest.raises(ValueError, match="outcomes"):
        inputs.capture_focused(repo_root=root, bundle=_record(root, spec, tmp_path), key_path=_key(tmp_path))
    assert not (tmp_path / "inputs/focused.json").exists()


def test_source_mutation_during_native_focus_cannot_receive_a_binding(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path, mutate_after=True)
    bundle = _record(root, spec, tmp_path)
    with pytest.raises(ValueError, match=r"clean|changed"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=_key(tmp_path))
    assert (root / "unreviewed-native-mutation").exists()
    assert not (bundle / "focused.json").exists()


def test_observer_symlink_cannot_write_into_another_owner(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    foreign = tmp_path / "foreign-observer"
    foreign.mkdir()
    (root / ".gate-logs").mkdir()
    (root / ".gate-logs/observed").symlink_to(foreign)
    with pytest.raises(ValueError, match="owned"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=_key(tmp_path))
    assert list(foreign.iterdir()) == []


@pytest.mark.parametrize("equivalent", [True, False])
def test_native_publication_race_preserves_the_exclusive_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, equivalent: bool,
) -> None:
    original = observed_owner._atomic_write_json
    payload = {"owner": "native-review"}
    competitor = payload if equivalent else {"owner": "foreign"}
    path = tmp_path / "immutable.json"

    def competing_publication(path: Path, _payload: Mapping[str, object], *, exclusive: bool = False) -> None:
        original(path, competitor, exclusive=exclusive)
        raise FileExistsError("native exclusive claim raced")

    monkeypatch.setattr(observed_owner, "_atomic_write_json", competing_publication)
    if equivalent:
        inputs._publish(path, payload)
    else:
        with pytest.raises(ValueError, match="owner claim"):
            inputs._publish(path, payload)
    assert json.loads(path.read_text(encoding="utf-8")) == competitor
    assert not list(tmp_path.glob(".*.tmp"))


def test_divergent_review_artifact_cannot_be_reused_for_focused_execution(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    review_path = bundle / "review-01.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["verdict"] = "changes_requested"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer provenance"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=_key(tmp_path))
    assert not (bundle / "observed").exists()


def test_authenticated_focus_refuses_runtime_drift_without_reexecution(
    tmp_path: Path,
) -> None:
    root, spec = _fixture(tmp_path, native_child_probe=True)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focused = cast(_Focused, inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key))
    trace = Path(focused["trace_file"])
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    next(event for event in events if event["event"] == "FOCUS_RUNTIME")["runtime"]["python"] = "changed-runtime"
    trace.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    with pytest.raises(ValueError, match="focused"):
        inputs.validate_focused(repo_root=root, bundle=bundle, key_path=key)
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"


def test_native_signer_revocation_refuses_previously_authenticated_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
    signer_id = inputs._auth_context(key).signer.signer_id
    monkeypatch.setenv("GLUDD_RECEIPT_REVOKED_SIGNERS", signer_id)
    with pytest.raises(ValueError, match="revoked"):
        inputs.validate_focused(repo_root=root, bundle=bundle, key_path=key)


@pytest.mark.parametrize("ambient", ["empty", "poison"])
def test_public_make_behavior_binds_current_source_before_ambient_package(tmp_path: Path, ambient: str) -> None:
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    entry = next(item for item in contract["targets"] if item["name"] == "reviewed-head-inputs")
    poison = tmp_path / "ambient-package"
    package = poison / "general_ludd"
    package.mkdir(parents=True)
    package_init = package / "__init__.py"
    package_init.write_text('raise RuntimeError("ambient general_ludd must not execute")\n', encoding="utf-8")
    compile(package_init.read_text(encoding="utf-8"), str(package_init), "exec")
    output = tmp_path / "plan-must-not-publish"
    command = [argument for argument in entry["behavior"].split()
               if not argument.startswith("REVIEWED_HEAD_INPUTS_OUTPUT=")]
    command.append("REVIEWED_HEAD_INPUTS_OUTPUT=" + str(output))
    completed = subprocess.run(
        command, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(poison) if ambient == "poison" else ""},
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert "release_ready=false" in completed.stdout
    assert not output.exists()
    assert package_init.read_text(encoding="utf-8") == 'raise RuntimeError("ambient general_ludd must not execute")\n'


def test_make_contract_has_explicit_behavior_and_literal_hostile_paths(tmp_path: Path) -> None:
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    entry = next(item for item in contract["targets"] if item["name"] == "reviewed-head-inputs")
    assert "reviewed-head-inputs" in compose_makefile(ROOT / "Makefile")
    completed = subprocess.run(entry["behavior"].split(), cwd=ROOT, check=False,
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    marker = tmp_path / "shell-injection-marker"
    hostile = 'literal spaces "; touch ' + str(marker) + '; # `touch ' + str(marker) + '`'
    command = entry["behavior"].split()
    command = [arg for arg in command if not arg.startswith("REVIEWED_HEAD_INPUTS_OUTPUT=")]
    command.append("REVIEWED_HEAD_INPUTS_OUTPUT=" + hostile)
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
    assert "release_ready=false" in completed.stdout


def test_focused_signature_expiry_refuses_replay(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
    with pytest.raises(ValueError, match="focused"):
        inputs.validate_focused(repo_root=root, bundle=bundle, key_path=key,
                                verification_epoch=int(time.time()) + 10**9)



@pytest.mark.parametrize("uv_kind", ["bare", "absolute", "literal"])
def test_native_focus_make_transports_exact_uv_executable(tmp_path: Path, uv_kind: str) -> None:
    """A dry-run must retain the caller's complete executable as one token."""
    uv_executable = {
        "bare": "uv",
        "absolute": str(tmp_path / "bin" / "uv"),
        "literal": str(tmp_path / "selected uv's; literal launcher"),
    }[uv_kind]
    cases = [
        (
            "reviewed-head-inputs",
            ["REVIEWED_HEAD_INPUTS_ACTION=focus", "REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=0"],
            "scripts/reviewed_head_inputs.py",
            [uv_executable, "run", "python", "scripts/reviewed_head_inputs.py",
             "--watchdog-owned-reviewed-head-focus"],
        ),
        (
            "test-files",
            ["TESTFILES=tests/unit/test_native.py", "PYTEST_ARGS=-W error",
             "NPROC=1", "GLUDD_XDIST_WORKERS=1"],
            " -m pytest ",
            [uv_executable, "run", "python", "-m", "pytest"],
        ),
    ]
    for target, arguments, needle, expected in cases:
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", target, *arguments,
             "UV=" + shlex.quote(uv_executable)],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
        )
        recipe = next(line for line in result.stdout.splitlines() if needle in line)
        command = shlex.split(recipe)
        offset = command.index(uv_executable)
        assert command[offset:offset + len(expected)] == expected


def test_native_focus_commands_preserve_watchdog_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inspect native Make's real recipes against the unchanged legacy filter."""
    watchdog_owner = import_module("scripts.task_watchdog")
    marker = "--watchdog-owned-reviewed-head-focus"
    cache_option = (
        "--override-ini=cache_dir=.gate-logs/observed/reviewed-head-focus/"
        "watchdog-owned-reviewed-head-focus/pytest-cache"
    )

    def producer_command(action: str, validate_only: str, *, uv_argument: str | None = None) -> list[str]:
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", "reviewed-head-inputs",
             "REVIEWED_HEAD_INPUTS_ACTION=" + action,
             "REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=" + validate_only,
             *([uv_argument] if uv_argument is not None else [])],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
        )
        line = next(line for line in result.stdout.splitlines() if "scripts/reviewed_head_inputs.py" in line)
        return shlex.split(line)

    # Ask Make itself so command-line overrides retain their native precedence.
    source_binding = "PYTHONPATH=" + str(ROOT / "src") + "${PYTHONPATH:+:$PYTHONPATH}"
    selected = producer_command("focus", "0")
    assert selected[0] == source_binding
    selected = selected[1:]
    assert selected[1:] == ["run", "python", "scripts/reviewed_head_inputs.py", marker]
    located_uv = shutil.which(selected[0])
    assert located_uv is not None
    uv_executable = str(Path(located_uv).absolute())
    uv_argument = "UV=" + shlex.quote(uv_executable)
    producer = producer_command("focus", "0", uv_argument=uv_argument)
    assert producer[0] == source_binding
    assert producer[-1] == marker
    tripwire = tmp_path / "make-expansion-tripwire"
    literal_function = "$(shell touch " + shlex.quote(str(tripwire)) + ")"
    for action, validate_only in (
        ("admit", "0"), ("record", "0"), ("finalize", "0"),
        ("admit", "1"), ("record", "1"), ("focus", "1"), ("finalize", "1"),
        ("focus extra", "0"), ("focus", "0 extra"),
        ("focus" + literal_function, "0"), ("focus", "0" + literal_function),
        (literal_function, "0"), ("focus", literal_function),
    ):
        assert marker not in producer_command(action, validate_only, uv_argument=uv_argument)
    assert not tripwire.exists()

    root, spec = _fixture(tmp_path)
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    command = receipt_owner._strings(prepared["focused_command"], "focused test command")
    assert command[:3] == ("make", "--no-print-directory", "test-files")
    assert command[-2:] == ("NPROC=1", "GLUDD_XDIST_WORKERS=1")
    pytest_args = next(argument.removeprefix("PYTEST_ARGS=") for argument in command
                       if argument.startswith("PYTEST_ARGS="))
    assert shlex.split(pytest_args) == [
        "-W", "error", '--junitxml=$${OBSERVED_JUNIT_PATH}',
        "-p", "scripts.xdist_trace_plugin", "-p", "scripts.reviewed_head_inputs", cache_option,
    ]
    child = subprocess.run(
        [*command[:2], "-n", *command[2:], uv_argument], cwd=ROOT, check=True,
        capture_output=True, text=True, timeout=10,
    )
    recipe = next(line for line in child.stdout.splitlines() if " -m pytest " in line)
    recipe_argv = shlex.split(recipe)
    uv_child = recipe_argv[recipe_argv.index(uv_executable):]
    assert uv_child[:5] == [uv_executable, "run", "python", "-m", "pytest"]
    assert cache_option in uv_child
    commands = [
        shlex.join(["make", "reviewed-head-inputs", "REVIEWED_HEAD_INPUTS_ACTION=focus",
                    "REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=0"]),
        shlex.join(["/bin/sh", "-c", shlex.join(producer)]),
        shlex.join(producer), shlex.join(producer[producer.index("python"):]),
        shlex.join(command), shlex.join(["/bin/sh", "-c", recipe]),
        shlex.join(uv_child), shlex.join(uv_child[2:]),
    ]
    # Ordinary stale work, including a separate project, must remain killable.
    ordinary = [
        "uv run python scripts/reviewed_head_inputs.py",
        "make test-files TESTFILES=tests/unit/test_native.py",
        "python -m pytest tests/unit/test_native.py",
        "uv run python -m pytest /tmp/another-project/tests/test_native.py",
    ]
    rows = [
        f"{50000 + number} 1 10:00 {command_line}"
        for number, command_line in enumerate([*commands, *ordinary])
    ]
    snapshot = subprocess.CompletedProcess(
        ["ps", "-eo", "pid,ppid,etime,command"], 0,
        stdout="PID PPID ELAPSED COMMAND\n" + "\n".join(rows) + "\n", stderr="",
    )

    def native_ps(_command: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return snapshot

    with monkeypatch.context() as legacy:
        legacy.setattr(subprocess, "run", native_ps)
        candidates = watchdog_owner.find_hung_processes(
            timeout_secs=300, gate_pid_file=str(tmp_path / "missing.pid"),
            gate_run_lock_file=str(tmp_path / "missing.lock"), repository_roots=(),
        )
    assert [candidate["command"] for candidate in candidates] == ordinary


@pytest.mark.parametrize(("action", "validate_only"), [
    ("admit", "0"), ("record", "0"), ("finalize", "0"), ("focus", "1"),
])
def test_cli_watchdog_marker_refuses_ordinary_actions_and_plans(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str, validate_only: str,
) -> None:
    output = tmp_path / "must-not-be-written"
    assert inputs.main([
        "--action", action, "--validate-only", validate_only, "--output", str(output),
        "--watchdog-owned-reviewed-head-focus",
    ]) == 2
    captured = capsys.readouterr()
    assert "required native provenance is unavailable or mismatched" in captured.err
    assert not captured.out and not output.exists()


def test_owned_focus_finishes_native_authentication_after_observer_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path, native_child_probe=True)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    monkeypatch.setenv("GLUDD_GATE_KEY_PATH", str(key))
    original_publish = inputs._publish
    published: list[str] = []

    def publish_after_native_exit(path: Path, payload: Mapping[str, object]) -> None:
        statuses = list((root / ".gate-logs/observed/reviewed-head-focus").glob("focus-*.json"))
        assert len(statuses) == 1
        status = json.loads(statuses[0].read_text(encoding="utf-8"))
        assert status["state"] == "passed" and status["exit_code"] == 0
        assert (root / ".gate-logs/observed/reviewed-head-focus/"
                "watchdog-owned-reviewed-head-focus/pytest-cache").is_dir()
        assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"
        published.append(path.name)
        original_publish(path, payload)

    monkeypatch.setattr(inputs, "_publish", publish_after_native_exit)
    arguments = [
        "--action", "focus", "--validate-only", "0", "--repo-root", str(root),
        "--output", str(bundle), "--watchdog-owned-reviewed-head-focus",
    ]
    assert inputs.main(arguments) == 0
    assert published == ["focused-auth.json", "focused.json"]
    first = {name: (bundle / name).read_bytes() for name in published}
    assert inputs.main(arguments) == 0
    assert published == ["focused-auth.json", "focused.json"]
    assert {name: (bundle / name).read_bytes() for name in first} == first
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"
    assert (root / ".gate-logs/observed/reviewed-head-focus/"
            "watchdog-owned-reviewed-head-focus/pytest-cache").is_dir()
    assert not (root / ".pytest_cache").exists()


@pytest.mark.parametrize("relative", [
    "watchdog-owned-reviewed-head-focus", "watchdog-owned-reviewed-head-focus/pytest-cache",
])
def test_focus_cache_symlink_refuses_foreign_owner_before_child(
    tmp_path: Path, relative: str,
) -> None:
    root, spec = _fixture(tmp_path, native_child_probe=True)
    bundle = _record(root, spec, tmp_path)
    foreign = tmp_path / "foreign-cache"
    foreign.mkdir()
    cache = root / ".gate-logs/observed/reviewed-head-focus" / relative
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="owned"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=_key(tmp_path))
    assert not (tmp_path / "native-child-count").exists()
    assert list(foreign.iterdir()) == []
    assert not (bundle / "focused.json").exists()
    assert not (bundle / "focused-auth.json").exists()


def test_prepare_refuses_wrong_source_parent_before_any_patch_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path)
    source = _git(root, "rev-parse", "refs/heads/reviewed/one")
    _git(root, "tag", "different-reviewed-base", source + "~2")
    heads = spec["heads"]
    assert isinstance(heads, list) and len(heads) == 1
    assert isinstance(heads[0], dict)
    heads[0]["reviewed_base_ref"] = "refs/tags/different-reviewed-base"
    original = receipt_owner._git
    patches: list[tuple[str, ...]] = []

    def recorded(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        if arguments[0] == "patch-id" or (arguments[0] == "show" and "--binary" in arguments):
            patches.append(arguments)
            pytest.fail("invalid source parent reached patch identity inspection")
        return original(repository, *arguments, input_bytes=input_bytes)

    monkeypatch.setattr(receipt_owner, "_git", recorded)
    with pytest.raises(ValueError, match="native cherry-pick patch or reviewed source base differs"):
        inputs.prepare_inputs(spec, repo_root=root)
    assert patches == []


def test_prepare_uses_one_fresh_paired_native_patch_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path)
    expected = inputs.prepare_inputs(spec, repo_root=root)
    source = _git(root, "rev-parse", "refs/heads/reviewed/one")
    application = _git(root, "rev-parse", "HEAD")
    assert source != application
    assert receipt_owner._patch_id(root, source) == receipt_owner._patch_id(root, application)
    original = receipt_owner._git
    commands: list[tuple[str, ...]] = []

    def recorded(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        result = original(repository, *arguments, input_bytes=input_bytes)
        if arguments[0] == "patch-id" or (arguments[0] == "show" and "--binary" in arguments):
            commands.append(arguments)
        return result

    monkeypatch.setattr(receipt_owner, "_git", recorded)
    for _boundary in range(2):
        start = len(commands)
        assert inputs.prepare_inputs(spec, repo_root=root) == expected
        current = commands[start:]
        assert len(current) == 2, current
        assert current[0][0] == "show" and {source, application}.issubset(current[0])
        assert current[1] == ("patch-id", "--stable")


@pytest.mark.parametrize("mode", ["cherry_pick", "merge_two_parent"])
def test_prepare_uses_one_fresh_native_commit_metadata_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    root, spec = _fixture(tmp_path, mode=mode)
    expected = inputs.prepare_inputs(spec, repo_root=root)
    after = _git(root, "rev-parse", "HEAD")
    source = _git(root, "rev-parse", "refs/heads/reviewed/one")
    expected_parents = _git(root, "rev-list", "--parents", "-n", "1", after).split()[1:]
    expected_tree = _git(root, "rev-parse", f"{source}^{{tree}}")
    original = receipt_owner._git
    original_clean = inputs._clean_identity
    commands: list[tuple[str, ...]] = []
    clean_calls: list[Path] = []

    def recorded(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        result = original(repository, *arguments, input_bytes=input_bytes)
        commands.append(arguments)
        return result

    def cleaned(repository: Path) -> tuple[str, str]:
        result = original_clean(repository)
        clean_calls.append(repository)
        return result

    monkeypatch.setattr(receipt_owner, "_git", recorded)
    monkeypatch.setattr(inputs, "_clean_identity", cleaned)
    for _boundary in range(2):
        start = len(commands)
        before_clean = len(clean_calls)
        prepared = inputs.prepare_inputs(spec, repo_root=root)
        assert prepared == expected
        current = commands[start:]
        metadata = [
            arguments for arguments in current
            if (
                arguments[:4] == ("rev-list", "--parents", "-n", "1")
                and arguments[-1] in {after, source}
            ) or arguments == ("rev-parse", f"{source}^{{tree}}")
            or arguments[:2] == ("show", "--no-patch")
        ]
        assert len(metadata) == 1, metadata
        assert metadata[0][-3:] == (after, source, "--")
        assert "--no-show-signature" in metadata[0]
        assert len(clean_calls) - before_clean == 2
        assert current.count(_CLEAN_STATUS) == 2
        assert current.count(("rev-parse", "HEAD")) == 0
        heads = prepared["heads"]
        reviews = prepared["reviews"]
        assert isinstance(heads, list) and len(heads) == 1
        assert isinstance(reviews, list) and len(reviews) == 1
        assert heads[0]["parent_shas"] == expected_parents
        assert reviews[0]["native"]["source_tree"] == expected_tree
    assert len(clean_calls) == 4


_CLEAN_STATUS = (
    "status", "--porcelain=v2", "--branch", "--no-ahead-behind",
    "-z", "--untracked-files=all",
)


def _clean_repository(tmp_path: Path, object_format: str = "sha1") -> Path:
    root = tmp_path / "repo with spaces"
    root.mkdir(parents=True)
    _git(root, "init", f"--object-format={object_format}", "-b", "development")
    _git(root, "config", "user.name", "Gludd native tests")
    _git(root, "config", "user.email", "native@gludd.invalid")
    _git(root, "config", "core.filemode", "true")
    _commit(root, "tracked.txt", "original\n")
    return root


def _legacy_clean_identity(root: Path) -> tuple[str, str]:
    """Keep the original native two-command behavior as the parity reference."""
    if receipt_owner._git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("native inputs require an exact clean repository")
    sha = receipt_owner._git(root, "rev-parse", "HEAD").decode("ascii").strip()
    state = gate_owner.repository_state_id(root, source="worktree")
    if state != gate_owner.repository_state_id(root, source="index"):
        raise ValueError("native worktree and index identities disagree")
    return sha, state


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
@pytest.mark.parametrize("detached", (False, True))
def test_clean_identity_uses_one_fresh_status_and_both_native_state_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    object_format: str, detached: bool,
) -> None:
    root = _clean_repository(tmp_path, object_format)
    if detached:
        _git(root, "checkout", "--detach")
    expected = _legacy_clean_identity(root)
    assert len(expected[0]) == (40 if object_format == "sha1" else 64)
    native_git = receipt_owner._git
    native_state = gate_owner.repository_state_id
    commands: list[tuple[str, ...]] = []
    reads: list[str] = []

    def recorded(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        commands.append(arguments)
        return native_git(repository, *arguments, input_bytes=input_bytes)

    def state(repository: Path, *, source: str = "worktree") -> str:
        reads.append(source)
        return native_state(repository, source=source)

    monkeypatch.setattr(receipt_owner, "_git", recorded)
    monkeypatch.setattr(gate_owner, "repository_state_id", state)
    for _boundary in range(2):
        start, before = len(commands), len(reads)
        assert inputs._clean_identity(root) == expected
        assert commands[start:] == [_CLEAN_STATUS]
        assert reads[before:] == ["worktree", "index"]


@pytest.mark.parametrize("change", (
    "tracked", "index", "untracked-newline", "untracked-unicode", "header-looking",
    "rename", "mode", "conflict", "ignored-only", "submodule",
))
def test_clean_identity_preserves_real_git_dirty_and_ignored_refusals(
    tmp_path: Path, change: str,
) -> None:
    root = _clean_repository(tmp_path)
    tracked = root / "tracked.txt"
    if change in {"tracked", "index"}:
        tracked.write_text("changed\n", encoding="utf-8")
        if change == "index":
            _git(root, "add", "tracked.txt")
    elif change.startswith("untracked") or change == "header-looking":
        name = {
            "untracked-newline": "line\nnext",
            "untracked-unicode": "naïve-λ",
            "header-looking": "# branch.oid " + "a" * 40 + "\n# branch.head fake",
        }[change]
        (root / name).write_text("untracked\n", encoding="utf-8")
    elif change == "rename":
        _git(root, "mv", "tracked.txt", "renamed\n# branch.oid fake")
    elif change == "mode":
        tracked.chmod(0o755)
    elif change == "conflict":
        _git(root, "checkout", "-b", "side")
        _commit(root, "tracked.txt", "side\n")
        _git(root, "checkout", "development")
        _commit(root, "tracked.txt", "main\n")
        with pytest.raises(subprocess.CalledProcessError):
            _git(root, "merge", "side")
    elif change == "ignored-only":
        _commit(root, ".gitignore", "ignored/\n")
        (root / "ignored").mkdir()
        (root / "ignored" / "file").write_text("ignored\n", encoding="utf-8")
    else:
        nested = _clean_repository(tmp_path / "source")
        _git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(nested), "nested")
        _git(root, "commit", "-am", "native submodule")
        (root / "nested" / "tracked.txt").write_text("nested mutation\n", encoding="utf-8")
    if change == "ignored-only":
        assert inputs._clean_identity(root) == _legacy_clean_identity(root)
    else:
        assert receipt_owner._git(root, *_CLEAN_STATUS).split(b"\0")[-2]
        for observer in (_legacy_clean_identity, inputs._clean_identity):
            with pytest.raises(ValueError, match="native inputs require an exact clean repository"):
                observer(root)


@pytest.mark.parametrize("repository_kind", ("unborn", "invalid"))
def test_clean_identity_preserves_native_unborn_and_repository_failure(
    tmp_path: Path, repository_kind: str,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    if repository_kind == "unborn":
        _git(root, "init", "-b", "development")
    for observer in (_legacy_clean_identity, inputs._clean_identity):
        with pytest.raises(ValueError, match="repository evidence does not match the integration manifest"):
            observer(root)


@pytest.mark.parametrize("envelope", (
    "native", "reordered", "unknown-header", "duplicate-oid", "missing-oid",
    "valueless-oid", "extra-valueless-oid",
    "initial", "short-oid", "uppercase-oid", "nonhex-oid", "missing-nul",
    "empty-record", "empty", "lf-framing", "dirty", "dirty-malformed-oid",
    "rename-header-tail", "unknown-data", "native-failure",
))
def test_clean_identity_strict_native_nul_envelope_before_state_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, envelope: str,
) -> None:
    root = _clean_repository(tmp_path)
    expected = _legacy_clean_identity(root)
    native_git = receipt_owner._git
    native_state = gate_owner.repository_state_id
    raw = native_git(root, *_CLEAN_STATUS)
    oid = expected[0].encode("ascii")
    header = b"# branch.oid " + oid + b"\0"
    variants = {
        "native": raw,
        "reordered": b"# branch.head (detached)\0" + header,
        "unknown-header": (b"# future.extension opaque\0" + raw
                           + b"# branch.oid-format sha1\0# \0# branch.oidish ignored\0"),
        "duplicate-oid": raw + header,
        "missing-oid": b"# branch.head development\0",
        "valueless-oid": b"# branch.oid\0",
        "extra-valueless-oid": raw + b"# branch.oid\0",
        "initial": b"# branch.oid (initial)\0# branch.head development\0",
        "short-oid": b"# branch.oid aaaa\0",
        "uppercase-oid": b"# branch.oid " + b"A" * 40 + b"\0",
        "nonhex-oid": b"# branch.oid " + b"g" * 40 + b"\0",
        "missing-nul": raw[:-1],
        "empty-record": raw + b"\0",
        "empty": b"",
        "lf-framing": raw.replace(b"\0", b"\n"),
        "dirty": raw + b"? line\n# branch.oid " + oid + b"\0",
        "dirty-malformed-oid": b"# branch.oid invalid\0? native-dirty\0",
        "rename-header-tail": raw + b"2 R. native rename\0# branch.oid " + oid + b"\0",
        "unknown-data": raw + b"x unsupported-data\0",
    }
    commands: list[tuple[str, ...]] = []
    reads: list[str] = []

    def recorded(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        commands.append(arguments)
        if arguments == _CLEAN_STATUS:
            if envelope == "native-failure":
                return native_git(repository, *arguments, "--gludd-unsupported-status-option")
            return variants[envelope]
        return native_git(repository, *arguments, input_bytes=input_bytes)

    def state(repository: Path, *, source: str = "worktree") -> str:
        reads.append(source)
        return native_state(repository, source=source)

    monkeypatch.setattr(receipt_owner, "_git", recorded)
    monkeypatch.setattr(gate_owner, "repository_state_id", state)
    if envelope in {"native", "reordered", "unknown-header"}:
        assert inputs._clean_identity(root) == expected
        assert reads == ["worktree", "index"]
    else:
        dirty = envelope in {"dirty", "dirty-malformed-oid", "rename-header-tail", "unknown-data"}
        reason = ("native inputs require an exact clean repository" if dirty else
                  "repository evidence does not match the integration manifest")
        with pytest.raises(ValueError, match=reason):
            inputs._clean_identity(root)
        assert reads == []
    assert commands == [_CLEAN_STATUS]


def test_clean_identity_refreshes_head_tracking_and_native_configuration_each_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _clean_repository(tmp_path)
    first = inputs._clean_identity(root)
    _git(root, "branch", "upstream")
    _git(root, "config", "branch.development.remote", ".")
    _git(root, "config", "branch.development.merge", "refs/heads/upstream")
    _git(root, "config", "color.status", "always")
    _git(root, "config", "status.relativePaths", "false")
    _git(root, "config", "status.renames", "copies")
    second_sha = _commit(root, "tracked.txt", "new head\n")
    native_git = receipt_owner._git
    commands: list[tuple[str, ...]] = []

    def recorded(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        commands.append(arguments)
        return native_git(repository, *arguments, input_bytes=input_bytes)

    monkeypatch.setattr(receipt_owner, "_git", recorded)
    second = inputs._clean_identity(root)
    assert second == _legacy_clean_identity(root)
    assert second[0] == second_sha and second != first
    assert commands[0] == _CLEAN_STATUS
    assert b"# branch.upstream " in native_git(root, *_CLEAN_STATUS)
    _git(root, "checkout", "--detach", first[0])
    before = len(commands)
    third = inputs._clean_identity(root)
    assert third == _legacy_clean_identity(root)
    assert third[0] == first[0] and third != second
    assert commands[before] == _CLEAN_STATUS


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_clean_identity_preserves_raw_head_in_fresh_native_replacement_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, object_format: str,
) -> None:
    root = _clean_repository(tmp_path, object_format)
    head = _git(root, "rev-parse", "HEAD")
    replacement = _git(root, "commit-tree", f"{head}^{{tree}}", "-m", "replacement metadata")
    assert replacement != head
    _git(root, "replace", head, replacement)
    for no_replace in (False, True, False):
        if no_replace:
            monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "1")
        else:
            monkeypatch.delenv("GIT_NO_REPLACE_OBJECTS", raising=False)
        assert _git(root, "rev-parse", "HEAD") == head
        assert inputs._clean_identity(root) == _legacy_clean_identity(root)
        assert inputs._clean_identity(root)[0] == head
    (root / "tracked.txt").write_text("fresh mutation\n", encoding="utf-8")
    for observer in (_legacy_clean_identity, inputs._clean_identity):
        with pytest.raises(ValueError, match="native inputs require an exact clean repository"):
            observer(root)


def test_clean_identity_preserves_native_worktree_index_mismatch_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _clean_repository(tmp_path)
    reads: list[str] = []

    def disagree(_repository: Path, *, source: str = "worktree") -> str:
        reads.append(source)
        return source

    monkeypatch.setattr(gate_owner, "repository_state_id", disagree)
    with pytest.raises(ValueError, match="native worktree and index identities disagree"):
        inputs._clean_identity(root)
    assert reads == ["worktree", "index"]
