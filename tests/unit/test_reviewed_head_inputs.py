"""Native controls for immutable reviewed-head input production."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts import gate_status_attestation as gate_owner
from scripts import reviewed_head_inputs as inputs
from scripts import run_ci_shards_serial as shard_owner
from scripts.ci_named_shard_files import SHARDS
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
KEY = bytes(range(32))


@pytest.fixture(autouse=True)
def _isolated_native_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the existing owner beneath one canonical pytest-owned root."""
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "native-resources"))
    monkeypatch.setenv("WORK_ITEM_ID", "F12.1")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], cwd=root.resolve(), check=True, capture_output=True, text=True,
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


def _gate(root: Path, tmp_path: Path, focus_completed: str) -> Path:
    started = datetime.fromisoformat(focus_completed) + timedelta(microseconds=1)
    completed = started + timedelta(microseconds=1)
    started_text = started.isoformat()
    completed_text = completed.isoformat()
    # Ignore policy uses Git's native local exclude, preserving the candidate.
    log_dir = root / ".gate-logs"
    log_dir.mkdir(exist_ok=True)
    run_id = "20261014T120000Z-native-test"
    state = {
        "schema_version": 1, "kind": "gludd_gate_background", "state": "finished",
        "run_id": run_id, "project_root": str(root.resolve()),
        "command": ["make", "--no-print-directory", "gate"],
        "started_at": started_text, "finished_at": completed_text,
        "termination_reason": "gate-exited",
    }
    (log_dir / "gate-background-state.json").write_text(json.dumps(state), encoding="utf-8")
    status = root / ".gate-status"
    status.write_text("lint PASS\ntypecheck PASS\ncollect PASS\ntest PASS\nsmoke PASS\n"
                      "=== GATE: PASSED ===\n", encoding="utf-8")
    gate_owner.sign_status(status, state_id=gate_owner.repository_state_id(root), key=KEY)
    final = _git(root, "rev-parse", "HEAD")
    attestation = {
        "schema_version": 3, "lane": "local", "status": "pass", "returncode": 0,
        "runner": "scripts/run_ci_shards_serial.py", "shards": list(SHARDS),
        "identity": {"head_sha": final, "expected_sha": final, "clean": True,
                     "exact_sha": True, "queries_ok": True},
        "started_at": started_text, "completed_at": completed_text,
        **shard_owner._attestation_pairing(list(SHARDS), pytest_args=["-W", "error"]),
    }
    path = inputs.resource_owner.resource_root(root) / "ci-shards/attestation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(attestation), encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["cherry_pick", "merge_two_parent"])
def test_derives_review_metadata_and_application_from_real_git(tmp_path: Path, mode: str) -> None:
    root, spec = _fixture(tmp_path, mode)
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    assert prepared["candidate_sha"] == _git(root, "rev-parse", "HEAD")
    assert prepared["heads"][0]["application_mode"] == mode
    review = prepared["reviews"][0]
    assert review["verdict"] == "approved" and review["reviewer"] == "trusted-source-review"
    assert review["native"]["changed_files"] == ["source.txt"]
    assert len(review["native"]["diff_sha256"]) == 64
    assert review["source_sha"] == _git(root, "rev-parse", "reviewed/one")
    assert prepared["focused_command"][0:3] == ["make", "--no-print-directory", "test-files"]


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
    spec["heads"][0][field] = value
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
    original = inputs.receipt_owner._git

    def read_only_git(repo: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        assert "--write-tree" not in arguments, "read-only admission cannot publish native Git objects"
        return original(repo, *arguments, input_bytes=input_bytes)

    monkeypatch.setattr(inputs.receipt_owner, "_git", read_only_git)
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
    spec["heads"].append({
        "source_ref": "refs/heads/reviewed/two", "reviewed_base_ref": "refs/heads/reviewed/one",
        "reviewer": "second-trusted-review", "verdict": "approved",
        "focused_test_files": ["tests/unit/test_native.py"],
    })
    prepared = inputs.prepare_inputs(spec, repo_root=root)
    assert prepared["heads"][1]["prerequisite_ancestry"] == [{
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
    damaged["intent"]["heads"][0]["reviewer"] = "substitute"
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
    focused = inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
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
    focused = inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
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
    focused = inputs.capture_focused(repo_root=root, bundle=_record(root, spec, tmp_path), key_path=_key(tmp_path))
    child = json.loads((tmp_path / "native-child-runtime.json").read_text(encoding="utf-8"))
    assert focused["runtime"]["python"] == child["python"]
    assert focused["runtime"]["executable_sha256"] == child["executable_sha256"]
    assert focused["runtime"]["loaded_plugins"] == child["loaded_plugins"]
    assert (tmp_path / "native-child-count").read_text(encoding="utf-8") == "1"


def test_native_runtime_trace_refuses_substituted_executor_facts(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    focused = inputs.capture_focused(repo_root=root, bundle=bundle, key_path=_key(tmp_path))
    status = focused["native_status"]
    recorded = json.loads((bundle / "inputs.json").read_text(encoding="utf-8"))
    original = Path(focused["trace_file"]).read_bytes()
    trace = tmp_path / "substituted-runtime.jsonl"
    for mutation, expected in (
        ("run", "another invocation"), ("executor", "actual test executor"),
        ("terminal", "completed actual executor"), ("source", "source or executing child"),
        ("adapter", "implementation changed"),
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
                    else:
                        event["runtime"]["producer_sha256"] = "f" * 64
        trace.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        with pytest.raises(ValueError, match=expected):
            inputs._native_runtimes(trace, status, recorded)
    trace.write_text("x" * 1_048_577 + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="event exceeds"):
        inputs._native_runtimes(trace, status, recorded)
    linked = tmp_path / "linked-runtime.jsonl"
    linked.symlink_to(trace)
    with pytest.raises(ValueError, match="unsafe"):
        inputs._native_runtimes(linked, status, recorded)
    assert Path(focused["trace_file"]).read_bytes() == original


def test_runtime_probe_refuses_producer_process_as_native_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    root, spec = _fixture(tmp_path)
    focused = inputs.capture_focused(repo_root=root, bundle=_record(root, spec, tmp_path), key_path=_key(tmp_path))
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


def test_final_manifest_binds_actual_terminal_gate_and_existing_builder(tmp_path: Path) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focus = inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
    attestation = _gate(root, tmp_path, focus["completed_at"])
    manifest = inputs.finalize_inputs(repo_root=root, bundle=bundle,
                                     gate_attestation=attestation, key_path=key)
    assert manifest["exact_gate_id"] == "gate:20261014T120000Z-native-test"
    assert manifest["focused_validation"]["passed"] is True
    assert manifest["focused_validation"]["run_count"] == 1
    assert (bundle / "manifest.json").exists()
    assert inputs.finalize_inputs(repo_root=root, bundle=bundle,
                                  gate_attestation=attestation, key_path=key) == manifest
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""


@pytest.mark.parametrize("mutation", [
    "running", "failed", "wrong-sha", "partial", "unsigned", "reordered", "signed-time", "path",
])
def test_gate_fact_substitution_and_partial_focus_never_grant_release_proof(
    tmp_path: Path, mutation: str,
) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    key = _key(tmp_path)
    focus = inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
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
    else:
        attestation = tmp_path / "substituted-gate-path.json"
    attestation.write_text(json.dumps(data), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        inputs.finalize_inputs(repo_root=root, bundle=bundle,
                               gate_attestation=attestation, key_path=key)
    assert not (bundle / "manifest.json").exists()


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


def test_cli_native_sequence_preserves_admission_and_gate_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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
    assert inputs.main(["--action", "finalize", "--gate-attestation", str(gate), *common]) == 0
    assert (bundle / "manifest.json").exists()


@pytest.mark.parametrize("arguments", [
    ["--action", "unsupported"], ["--validate-only", "2"],
    ["--action", "record", "--validate-only", "0"],
    ["--action", "record", "--output", "/tmp/gludd-no-intent", "--validate-only", "0"],
    ["--action", "finalize", "--output", "/tmp/gludd-no-gate", "--validate-only", "0"],
])
def test_cli_refuses_unowned_policy_or_missing_inputs_without_writing(arguments: list[str]) -> None:
    assert inputs.main(arguments) == 2


def test_missing_key_and_substituted_execution_owner_cannot_claim_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, spec = _fixture(tmp_path)
    bundle = _record(root, spec, tmp_path)
    with pytest.raises(ValueError, match="authentication"):
        inputs.capture_focused(repo_root=root, bundle=bundle, key_path=tmp_path / "missing-key")
    assert not (tmp_path / "missing-key").exists()
    monkeypatch.setattr(inputs.observed_owner, "stream_command", lambda *_args, **_kwargs: 0)
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
    original = inputs.observed_owner._atomic_write_json
    payload = {"owner": "native-review"}
    competitor = payload if equivalent else {"owner": "foreign"}
    path = tmp_path / "immutable.json"

    def competing_publication(path: Path, _payload: Mapping[str, object], *, exclusive: bool = False) -> None:
        original(path, competitor, exclusive=exclusive)
        raise FileExistsError("native exclusive claim raced")

    monkeypatch.setattr(inputs.observed_owner, "_atomic_write_json", competing_publication)
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
    focused = inputs.capture_focused(repo_root=root, bundle=bundle, key_path=key)
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
