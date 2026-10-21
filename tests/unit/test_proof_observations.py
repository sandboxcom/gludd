"""Producer-owned operational observations cannot become release authority."""

from __future__ import annotations

import ast
import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from scripts.makefile_layout import compose_makefile

SHA = "a" * 40
IDENTITY = {"repo": "sandboxcom/gludd", "branch": "development", "sha": SHA}
ROOT = Path(__file__).resolve().parents[2]


def _module() -> ModuleType:
    return importlib.import_module("scripts.proof_observations")


def test_writer_derives_timestamp_and_never_admits_release(tmp_path: Path) -> None:
    path = tmp_path / "current.json"
    before = datetime.now(UTC)
    _module().write_snapshot(
        path, producer="pipeline-status", identity=IDENTITY,
        state="success", evidence={"hosted": {"passed": 3}},
    )
    record = json.loads(path.read_text())
    assert record["kind"] == "proof_observation"
    assert record["producer"] == "pipeline-status"
    assert record["identity"] == IDENTITY
    assert before <= datetime.fromisoformat(record["generated_at"]) <= datetime.now(UTC)
    assert record["release_ready"] is False
    assert record["gate_evidence"] is False
    assert record["evidence"]["hosted"]["passed"] == 3


@pytest.mark.parametrize("state", ["pending", "failure", "lookup_error", "timeout"])
def test_new_non_success_observation_replaces_previous_green(
    tmp_path: Path, state: str,
) -> None:
    path = tmp_path / "current.json"
    module = _module()
    module.write_snapshot(path, producer="ci-await", identity=IDENTITY,
                          state="success", evidence={"run_id": 7})
    module.write_snapshot(path, producer="ci-await", identity=IDENTITY,
                          state=state, evidence={"run": None})
    record = module.read_snapshot(path, producer="ci-await", identity=IDENTITY)
    assert record["state"] == state
    assert record["evidence"] == {"run": None}
    assert record["release_ready"] is False


@pytest.mark.parametrize("field", ["repo", "branch", "sha"])
def test_reader_rejects_different_identity(tmp_path: Path, field: str) -> None:
    path = tmp_path / "current.json"
    module = _module()
    module.write_snapshot(path, producer="ci-await", identity=IDENTITY,
                          state="pending", evidence={})
    different = {**IDENTITY, field: "b" * 40 if field == "sha" else "other"}
    with pytest.raises(ValueError, match="identity"):
        module.read_snapshot(path, producer="ci-await", identity=different)


@pytest.mark.parametrize("seconds", [-601, 61])
def test_reader_rejects_stale_or_future_record(tmp_path: Path, seconds: int) -> None:
    path = tmp_path / "current.json"
    module = _module()
    module.write_snapshot(path, producer="ci-await", identity=IDENTITY,
                          state="pending", evidence={})
    record = json.loads(path.read_text())
    record["generated_at"] = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match=r"timestamp|stale|future"):
        module.read_snapshot(path, producer="ci-await", identity=IDENTITY)


@pytest.mark.parametrize("field", ["kind", "producer", "schema_version", "release_ready", "gate_evidence"])
def test_reader_rejects_forged_schema_and_authority(tmp_path: Path, field: str) -> None:
    path = tmp_path / "current.json"
    module = _module()
    module.write_snapshot(path, producer="pipeline-status", identity=IDENTITY,
                          state="success", evidence={})
    record = json.loads(path.read_text())
    record[field] = True if field in {"release_ready", "gate_evidence"} else "wrong"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        module.read_snapshot(path, producer="pipeline-status", identity=IDENTITY)


def test_writer_refuses_to_overwrite_manual_history(tmp_path: Path) -> None:
    path = tmp_path / "handoff.json"
    original = '{"historical_source": "keep"}'
    path.write_text(original)
    with pytest.raises(ValueError, match=r"owner|producer|schema"):
        _module().write_snapshot(path, producer="pipeline-status", identity=IDENTITY,
                                 state="pending", evidence={})
    assert path.read_text() == original


def test_writer_refuses_symlink_target(tmp_path: Path) -> None:
    original = tmp_path / "historical.json"
    original.write_text("preserve")
    link = tmp_path / "current.json"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="symlink"):
        _module().write_snapshot(link, producer="ci-await", identity=IDENTITY,
                                 state="pending", evidence={})
    assert original.read_text() == "preserve"


def test_missing_file_and_invalid_json_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "current.json"
    module = _module()
    with pytest.raises((OSError, ValueError)):
        module.read_snapshot(path, producer="ci-await", identity=IDENTITY)
    path.write_text("not-json")
    with pytest.raises(ValueError):
        module.read_snapshot(path, producer="ci-await", identity=IDENTITY)


def test_make_status_and_waiter_use_automatic_producer_outputs() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    waiter = makefile.split("ci-await:", 1)[1].split("\n\n", 1)[0]
    status = makefile.split("pipeline-status:", 1)[1].split("\n\n", 1)[0]
    assert "--observation-output" in waiter
    assert "CI_AWAIT_OBSERVATION_OUTPUT" in waiter
    assert "--snapshot-output" in status
    assert "PIPELINE_STATUS_OUTPUT" in status
    assert '--failure-ledger "$(PIPELINE_STATUS_FAILURE_LEDGER)"' in status
    assert "--observe-ledger" in status


def test_heartbeat_reuses_snapshot_producer_and_does_not_handwrite_json() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    heartbeat = makefile.split("status-heartbeat:", 1)[1].split("\n\n", 1)[0]
    assert "STATUS_HEARTBEAT_VALIDATE_ONLY" in heartbeat
    assert "PIPELINE_STATUS_OUTPUT" in heartbeat
    assert "PIPELINE_STATUS_SHA" in heartbeat
    assert "printf '{" not in heartbeat
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    entry = next(item for item in contract["targets"] if item["name"] == "status-heartbeat")
    assert "STATUS_HEARTBEAT_VALIDATE_ONLY=1" in entry["behavior"]


def test_task_ledger_authority_is_explicitly_reexported_for_static_consumers() -> None:
    source = ast.parse((ROOT / "scripts/validate_task_ledger.py").read_text())
    aliases = {alias.name: alias.asname for node in ast.walk(source)
               if isinstance(node, ast.ImportFrom)
               and node.module == "general_ludd.validation.backlog_sources"
               for alias in node.names}
    for name in ("TaskRecord", "extract_tasks", "task_is_effectively_complete"):
        assert aliases[name] == name


@pytest.mark.parametrize("field,value", [
    ("identity", []), ("identity", {}),
    ("state", ""), ("state", 42), ("evidence", []),
    ("generated_at", None), ("generated_at", "invalid"),
    ("generated_at", "2026-10-12T00:00:00"),
])
def test_reader_rejects_malformed_record_fields(
    tmp_path: Path, field: str, value: object,
) -> None:
    path = tmp_path / "current.json"
    module = _module()
    module.write_snapshot(path, producer="ci-await", identity=IDENTITY,
                          state="pending", evidence={})
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        module.read_snapshot(path, producer="ci-await", identity=IDENTITY)


@pytest.mark.parametrize("field,value", [
    ("repo", ""), ("repo", None), ("branch", ""), ("branch", 42),
    ("sha", "short"), ("sha", 42), ("sha", "A" * 40),
])
def test_writer_rejects_invalid_identity_before_publication(
    tmp_path: Path, field: str, value: object,
) -> None:
    path = tmp_path / "current.json"
    with pytest.raises(ValueError, match="identity"):
        _module().write_snapshot(path, producer="ci-await",
                                 identity={**IDENTITY, field: value},
                                 state="pending", evidence={})
    assert not path.exists()


def test_unknown_sha_cannot_reuse_success_and_is_explicit_on_error(tmp_path: Path) -> None:
    path = tmp_path / "current.json"
    module = _module()
    unknown = {**IDENTITY, "sha": None}
    with pytest.raises(ValueError, match="unknown identity"):
        module.write_snapshot(path, producer="pipeline-status", identity=unknown,
                              state="success", evidence={})
    module.write_snapshot(path, producer="pipeline-status", identity=unknown,
                          state="lookup_error", evidence={})
    assert module.read_snapshot(path, producer="pipeline-status", identity=unknown)["state"] == "lookup_error"


def test_reader_rejects_nonobject_and_oversized_json(tmp_path: Path) -> None:
    path = tmp_path / "current.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="object"):
        _module().read_snapshot(path, producer="ci-await", identity=IDENTITY)
    path.write_text(" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="bound"):
        _module().read_snapshot(path, producer="ci-await", identity=IDENTITY)


def test_writer_bound_owner_and_reader_age_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "current.json"
    module = _module()
    with pytest.raises(ValueError, match="bound"):
        module.write_snapshot(path, producer="ci-await", identity=IDENTITY,
                              state="pending", evidence={"large": "x" * (2 * 1024 * 1024)})
    assert not path.exists()
    module.write_snapshot(path, producer="ci-await", identity=IDENTITY,
                          state="pending", evidence={})
    with pytest.raises(ValueError, match="owner"):
        module.write_snapshot(path, producer="ci-await", identity={**IDENTITY, "branch": "master"},
                              state="pending", evidence={})
    with pytest.raises(ValueError, match="positive"):
        module.read_snapshot(path, producer="ci-await", identity=IDENTITY, max_age_seconds=0)
    assert module.read_snapshot(path, producer="ci-await", identity=IDENTITY)["state"] == "pending"


def test_reader_rejects_symlink_without_reading_foreign_state(tmp_path: Path) -> None:
    link = tmp_path / "current.json"
    link.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        _module().read_snapshot(link, producer="ci-await", identity=IDENTITY)
