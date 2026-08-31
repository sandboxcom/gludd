"""Fail-closed contracts for the beta release-failure ledger."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

FAILED_SHA = "a" * 40
FIX_SHA = "b" * 40
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    regression = root / "tests" / "unit" / "test_regression.py"
    regression.parent.mkdir(parents=True, exist_ok=True)
    regression.write_text(
        "class TestRegression:\n"
        "    def test_case(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (root / "Makefile").write_text("test-files:\n\t@true\n", encoding="utf-8")
    return root


def _ledger() -> dict[str, Any]:
    run_id = "32904013362"
    job_id = "97985787888"
    discovery_id = f"gha-{run_id}-{job_id}"
    return {
        "schema_version": 1,
        "release_series": "v0.1.0-beta",
        "reviewed_at": "2026-08-31",
        "discoveries": [
            {
                "id": discovery_id,
                "release_tag": "v0.1.0-beta.4",
                "run_id": run_id,
                "job_id": job_id,
                "head_sha": FAILED_SHA,
                "run_conclusion": "cancelled",
                "job_conclusion": "failure",
                "trigger_event": "workflow_dispatch",
                "evidence_command": f"make ci-view RUN={run_id}",
                "run_url": f"https://github.com/sandboxcom/gludd/actions/runs/{run_id}",
                "job_url": (
                    "https://github.com/sandboxcom/gludd/actions/runs/"
                    f"{run_id}/job/{job_id}"
                ),
            }
        ],
        "incidents": [
            {
                "discovery_id": discovery_id,
                "failure_signature": "valid padding accepted after CBC tampering",
                "fix_commit": FIX_SHA,
                "regression_node": (
                    "tests/unit/test_regression.py::TestRegression::test_case"
                ),
                "earliest_preflight": {
                    "target": "test-files",
                    "argv": [
                        "make",
                        "test-files",
                        "TESTFILES=tests/unit/test_regression.py",
                        "PYTEST_ARGS=-q -W error -k test_case",
                    ],
                },
            }
        ],
    }


def _validate(payload: dict[str, Any], root: Path) -> list[str]:
    from scripts.check_release_failure_ledger import validate_ledger

    return validate_ledger(
        payload,
        repository_root=root,
        commit_exists=lambda sha: sha in {FAILED_SHA, FIX_SHA},
        is_ancestor=lambda parent, child: (parent, child) == (FAILED_SHA, FIX_SHA),
    )


def test_missing_incident_evidence_fails_closed(tmp_path: Path) -> None:
    payload = _ledger()
    del payload["incidents"][0]["failure_signature"]

    errors = _validate(payload, _repository(tmp_path))

    assert any("failure_signature is required" in error for error in errors)


def test_stale_commit_and_regression_node_fail_closed(tmp_path: Path) -> None:
    payload = _ledger()
    payload["incidents"][0]["fix_commit"] = "c" * 40
    payload["incidents"][0]["regression_node"] = (
        "tests/unit/test_regression.py::TestRegression::test_missing"
    )

    errors = _validate(payload, _repository(tmp_path))

    assert any("fix_commit is not a local commit" in error for error in errors)
    assert any("regression node does not exist" in error for error in errors)


def test_duplicate_discovery_and_mapping_fail_closed(tmp_path: Path) -> None:
    payload = _ledger()
    payload["discoveries"].append(deepcopy(payload["discoveries"][0]))
    payload["incidents"].append(deepcopy(payload["incidents"][0]))

    errors = _validate(payload, _repository(tmp_path))

    assert any("duplicate discovery id" in error for error in errors)
    assert any("duplicate run/job mapping" in error for error in errors)
    assert any("duplicate incident mapping" in error for error in errors)


def test_unmapped_failed_discovery_fails_closed(tmp_path: Path) -> None:
    payload = _ledger()
    unmapped = deepcopy(payload["discoveries"][0])
    unmapped.update(
        {
            "id": "gha-32934741442-98074766623",
            "run_id": "32934741442",
            "job_id": "98074766623",
            "run_url": "https://github.com/sandboxcom/gludd/actions/runs/32934741442",
            "job_url": (
                "https://github.com/sandboxcom/gludd/actions/runs/"
                "32934741442/job/98074766623"
            ),
        }
    )
    payload["discoveries"].append(unmapped)

    errors = _validate(payload, _repository(tmp_path))

    assert any("unmapped failed discovery" in error for error in errors)


def test_missing_discovery_evidence_command_fails_closed(tmp_path: Path) -> None:
    payload = _ledger()
    del payload["discoveries"][0]["evidence_command"]

    errors = _validate(payload, _repository(tmp_path))

    assert any("evidence_command is required" in error for error in errors)


def test_complete_immutable_mapping_passes(tmp_path: Path) -> None:
    assert _validate(_ledger(), _repository(tmp_path)) == []


def test_repository_ledger_make_contract_and_evidence_are_wired() -> None:
    from scripts.check_release_failure_ledger import (
        _git_commit_exists,
        _git_is_ancestor,
        validate_ledger,
    )

    ledger_path = REPOSITORY_ROOT / "docs/releases/beta-release-failures.json"
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert validate_ledger(
        payload,
        repository_root=REPOSITORY_ROOT,
        commit_exists=lambda sha: _git_commit_exists(REPOSITORY_ROOT, sha),
        is_ancestor=lambda parent, child: _git_is_ancestor(
            REPOSITORY_ROOT, parent, child
        ),
    ) == []

    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "check-release-failure-ledger:" in makefile
    assert "RELEASE_FAILURE_LEDGER ?=" in makefile
    contracts = json.loads(
        (REPOSITORY_ROOT / "config/make_target_contract.json").read_text(
            encoding="utf-8"
        )
    )
    contract = next(
        item for item in contracts["targets"]
        if item["name"] == "check-release-failure-ledger"
    )
    assert contract["make_variables"] == ["RELEASE_FAILURE_LEDGER"]
    assert "RELEASE_FAILURE_LEDGER=" in contract["behavior"]

    feature = (
        REPOSITORY_ROOT / "docs/features/BETA_RELEASE_FAILURE_LEDGER.md"
    ).read_text(encoding="utf-8")
    assert "https://docs.github.com/en/rest/actions/workflow-runs" in feature
    assert "https://github.com/orgs/community/discussions/25191" in feature
    assert "Zero-downtime" in feature
    assert "Rollback" in feature
    assert "Resource bounds" in feature


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda payload: payload.update(schema_version=2), "schema_version must be 1"),
        (
            lambda payload: payload.update(release_series="v0.1.0"),
            "release_series must identify a beta series",
        ),
        (
            lambda payload: payload.update(reviewed_at="2999-01-01"),
            "reviewed_at cannot be in the future",
        ),
        (
            lambda payload: payload.update(reviewed_at="not-a-date"),
            "reviewed_at must be an ISO date",
        ),
    ],
)
def test_invalid_ledger_metadata_fails_closed(
    tmp_path: Path,
    mutation: Any,
    expected: str,
) -> None:
    payload = _ledger()
    mutation(payload)

    assert expected in _validate(payload, _repository(tmp_path))


def test_invalid_discovery_and_preflight_fields_fail_closed(tmp_path: Path) -> None:
    payload = _ledger()
    discovery = payload["discoveries"][0]
    discovery.update(
        {
            "id": "mutable-label",
            "release_tag": "beta4",
            "run_id": "0",
            "job_id": "job",
            "head_sha": "short",
            "job_conclusion": "cancelled",
            "run_url": "https://example.invalid/run",
            "job_url": "https://example.invalid/job",
        }
    )
    incident = payload["incidents"][0]
    incident["failure_signature"] = "short"
    incident["earliest_preflight"] = {
        "target": "missing-target",
        "argv": ["make", "other-target"],
    }

    errors = _validate(payload, _repository(tmp_path))

    assert any("release_tag is not a beta tag" in error for error in errors)
    assert any("run_id must be a positive decimal ID" in error for error in errors)
    assert any("job_id must be a positive decimal ID" in error for error in errors)
    assert any("head_sha is not a local commit" in error for error in errors)
    assert any("job_conclusion must be failure" in error for error in errors)
    assert any("earliest_preflight target is not defined" in error for error in errors)
    assert any("earliest_preflight must treat warnings as errors" in error for error in errors)


def test_unknown_incident_and_unsafe_regression_path_fail_closed(
    tmp_path: Path,
) -> None:
    payload = _ledger()
    incident = payload["incidents"][0]
    incident["discovery_id"] = "gha-1-2"
    incident["regression_node"] = "../outside.py::test_case"

    errors = _validate(payload, _repository(tmp_path))

    assert any("incident references unknown discovery" in error for error in errors)
    assert any(
        "regression_node must be a repository pytest node" in error
        for error in errors
    )


def test_cli_accepts_repository_ledger(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.check_release_failure_ledger import main

    result = main(
        [
            "--ledger",
            str(REPOSITORY_ROOT / "docs/releases/beta-release-failures.json"),
            "--repository-root",
            str(REPOSITORY_ROOT),
        ]
    )

    assert result == 0
    assert (
        "RELEASE_FAILURE_LEDGER_PASS discoveries=2 incidents=2"
        in capsys.readouterr().out
    )


def test_cli_rejects_malformed_and_oversized_ledgers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.check_release_failure_ledger import MAX_LEDGER_BYTES, main

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    assert main(["--ledger", str(malformed)]) == 1
    assert "ledger is not readable JSON" in capsys.readouterr().out

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_LEDGER_BYTES + 1))
    assert main(["--ledger", str(oversized)]) == 1
    assert "ledger exceeds 2000000 bytes" in capsys.readouterr().out


def test_invalid_container_shapes_fail_closed(tmp_path: Path) -> None:
    from scripts.check_release_failure_ledger import validate_ledger

    assert validate_ledger(
        [],
        repository_root=_repository(tmp_path),
        commit_exists=lambda _sha: True,
        is_ancestor=lambda _parent, _child: True,
    ) == ["ledger root must be an object"]

    payload = _ledger()
    payload["discoveries"] = ["not-an-object"]
    payload["incidents"] = ["not-an-object"]

    errors = _validate(payload, _repository(tmp_path))

    assert "discoveries[0]: entry must be an object" in errors
    assert "incidents[0]: entry must be an object" in errors
