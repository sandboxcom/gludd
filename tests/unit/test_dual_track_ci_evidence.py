"""Release evidence must bind local and hosted CI to one immutable SHA."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_dual_track_ci.py"
MAKEFILE = ROOT / "Makefile"
SHARDS = {
    "unit-1a1",
    "unit-1a2",
    "unit-1b",
    "unit-1d",
    "unit-2",
    "unit-3a",
    "unit-3b",
    "other",
}

RELEASE_POLICY = {
    "schema_version": 1,
    "python_version": "3.11",
    "python_implementation": "cpython",
    "pytest_args": ["-W", "error"],
    "xdist_workers": 1,
    "max_processes": 1,
    "distribution": "loadgroup",
    "max_worker_restart": 0,
    "coverage_config": ".coveragerc-greenlet",
}


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _pairing_fields(shards: list[str]) -> dict[str, object]:
    plans: dict[str, object] = {}
    for shard in shards:
        paths = [f"tests/{shard}/test_contract.py"]
        plans[shard] = {
            "paths": paths,
            "path_count": len(paths),
            "sha256": _digest(paths),
        }
    return {
        "shard_plans": plans,
        "execution_policy": RELEASE_POLICY.copy(),
        "execution_policy_sha256": _digest(RELEASE_POLICY),
    }


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_verify_dual_track_ci", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _attestation(*, sha: str, lane: str, shards: list[str]) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 3,
        "lane": lane,
        "identity": {
            "head_sha": sha,
            "expected_sha": sha,
            "branch": "development",
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
        },
        "shards": shards,
        "status": "pass",
        "returncode": 0,
        "started_at": "2026-08-25T00:00:00Z",
        "completed_at": "2026-08-25T00:01:00Z",
        "runner": "scripts/run_ci_shards_serial.py",
        "python": sys.version,
        **_pairing_fields(shards),
    }
    if lane == "hosted":
        payload["coverage"] = {
            "artifact": (
                f".coverage.{shards[0]}-"
                f"{sys.version_info.major}.{sys.version_info.minor}"
            ),
            "bytes": 1024,
            "sha256": "c" * 64,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }
    return payload


def _write(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_dual_track_evidence_accepts_complete_exact_sha_matrix(tmp_path: Path) -> None:
    module = _load_script()
    sha = "a" * 40
    local = _write(
        tmp_path / "local.json",
        _attestation(sha=sha, lane="local", shards=sorted(SHARDS)),
    )
    hosted = [
        _write(
            tmp_path / shard / "ci-shard-attestation.json",
            _attestation(sha=sha, lane="hosted", shards=[shard]),
        )
        for shard in sorted(SHARDS)
    ]

    assert module.verify_dual_track_evidence(local, hosted, sha) == []


def test_verifier_release_policy_is_independent_of_verifier_python() -> None:
    """A Python 3.14 verifier must accept evidence produced by release Python 3.11."""
    module = _load_script()

    assert module.EXPECTED_EXECUTION_POLICY["python_version"] == "3.11"
    assert module.EXPECTED_EXECUTION_POLICY["python_implementation"] == "cpython"


def test_dual_track_evidence_rejects_mismatched_paired_shard_plan(
    tmp_path: Path,
) -> None:
    module = _load_script()
    sha = "a" * 40
    local = _write(
        tmp_path / "local.json",
        _attestation(sha=sha, lane="local", shards=sorted(SHARDS)),
    )
    hosted_payloads = {
        shard: _attestation(sha=sha, lane="hosted", shards=[shard])
        for shard in SHARDS
    }
    plans = cast(dict[str, Any], hosted_payloads["other"]["shard_plans"])
    plan = cast(dict[str, Any], plans["other"])
    plan["paths"] = ["tests/other/test_different_contract.py"]
    plan["sha256"] = _digest(plan["paths"])
    hosted = [
        _write(tmp_path / shard / "ci-shard-attestation.json", payload)
        for shard, payload in hosted_payloads.items()
    ]

    errors = module.verify_dual_track_evidence(local, hosted, sha)

    assert any("paired plan mismatch for shard 'other'" in error for error in errors)


def test_dual_track_evidence_rejects_matching_but_weakened_policy(
    tmp_path: Path,
) -> None:
    module = _load_script()
    sha = "b" * 40
    local_payload = _attestation(sha=sha, lane="local", shards=sorted(SHARDS))
    hosted_payloads = {
        shard: _attestation(sha=sha, lane="hosted", shards=[shard])
        for shard in SHARDS
    }
    weakened = {**RELEASE_POLICY, "pytest_args": []}
    for payload in [local_payload, *hosted_payloads.values()]:
        payload["execution_policy"] = weakened
        payload["execution_policy_sha256"] = _digest(weakened)
    local = _write(tmp_path / "local.json", local_payload)
    hosted = [
        _write(tmp_path / shard / "ci-shard-attestation.json", payload)
        for shard, payload in hosted_payloads.items()
    ]

    errors = module.verify_dual_track_evidence(local, hosted, sha)

    assert any("release execution policy" in error for error in errors)


def test_dual_track_evidence_distinguishes_policy_from_plan_mismatch(
    tmp_path: Path,
) -> None:
    module = _load_script()
    sha = "b" * 40
    local = _write(
        tmp_path / "local.json",
        _attestation(sha=sha, lane="local", shards=sorted(SHARDS)),
    )
    hosted_payloads = {
        shard: _attestation(sha=sha, lane="hosted", shards=[shard])
        for shard in SHARDS
    }
    weakened = {**RELEASE_POLICY, "pytest_args": []}
    hosted_payloads["other"]["execution_policy"] = weakened
    hosted_payloads["other"]["execution_policy_sha256"] = _digest(weakened)
    hosted = [
        _write(tmp_path / shard / "ci-shard-attestation.json", payload)
        for shard, payload in hosted_payloads.items()
    ]

    errors = module.verify_dual_track_evidence(local, hosted, sha)

    assert any("paired policy mismatch for shard 'other'" in error for error in errors)
    assert not any("paired plan mismatch for shard 'other'" in error for error in errors)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("legacy-schema", "unsupported attestation schema"),
        ("missing-policy", "execution policy is missing or malformed"),
        ("invalid-policy-digest", "execution policy digest is invalid"),
        ("missing-plans", "shard plans are missing or malformed"),
        ("plan-key-mismatch", "shard plans do not match attested shards"),
        ("malformed-plan", "shard plan 'other' is malformed"),
        ("invalid-plan-digest", "shard plan 'other' digest is invalid"),
    ],
)
def test_attestation_pairing_contract_fails_closed(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    module = _load_script()
    payload = _attestation(sha="a" * 40, lane="hosted", shards=["other"])
    plans = cast(dict[str, Any], payload["shard_plans"])
    plan = cast(dict[str, Any], plans["other"])
    if mutation == "legacy-schema":
        payload["schema_version"] = 2
    elif mutation == "missing-policy":
        payload.pop("execution_policy")
    elif mutation == "invalid-policy-digest":
        payload["execution_policy_sha256"] = "0" * 64
    elif mutation == "missing-plans":
        payload.pop("shard_plans")
    elif mutation == "plan-key-mismatch":
        plans["unit-2"] = plans.pop("other")
    elif mutation == "malformed-plan":
        plan["paths"] = []
    else:
        plan["sha256"] = "0" * 64

    _shards, _pairings, errors = module._validate_attestation(
        tmp_path / f"{mutation}.json",
        payload,
        sha="a" * 40,
        lane="hosted",
    )

    assert any(message in error for error in errors)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-hosted", "missing hosted shard attestations"),
        ("wrong-sha", "does not match candidate SHA"),
        ("dirty", "was produced from a dirty checkout"),
        ("failed", "is not a terminal pass"),
        ("wrong-lane", "has lane 'local'; expected 'hosted'"),
    ],
)
def test_dual_track_evidence_fails_closed(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    module = _load_script()
    sha = "b" * 40
    local_payload = _attestation(sha=sha, lane="local", shards=sorted(SHARDS))
    local = _write(tmp_path / "local.json", local_payload)
    hosted_payloads = {
        shard: _attestation(sha=sha, lane="hosted", shards=[shard])
        for shard in SHARDS
    }
    if mutation == "missing-hosted":
        hosted_payloads.pop("other")
    elif mutation == "wrong-sha":
        identity = cast(dict[str, Any], hosted_payloads["other"]["identity"])
        identity["head_sha"] = "c" * 40
    elif mutation == "dirty":
        identity = cast(dict[str, Any], local_payload["identity"])
        identity["clean"] = False
        local = _write(tmp_path / "local.json", local_payload)
    elif mutation == "failed":
        hosted_payloads["other"]["status"] = "fail"
        hosted_payloads["other"]["returncode"] = 1
    else:
        hosted_payloads["other"]["lane"] = "local"
    hosted = [
        _write(tmp_path / shard / "ci-shard-attestation.json", payload)
        for shard, payload in hosted_payloads.items()
    ]

    errors = module.verify_dual_track_evidence(local, hosted, sha)

    assert any(message in error for error in errors)


def test_attestation_validation_reports_malformed_contract_fields(tmp_path: Path) -> None:
    module = _load_script()
    payload = {
        "schema_version": 1,
        "lane": "local",
        "identity": None,
        "shards": [],
        "status": "pass",
        "returncode": 0,
        "runner": "other.py",
        "started_at": "",
        "completed_at": "",
    }

    shards, pairings, errors = module._validate_attestation(
        tmp_path / "bad.json",
        payload,
        sha="a" * 40,
        lane="local",
    )

    assert shards == set()
    assert pairings == {}
    assert len(errors) == 5


def test_attestation_validation_rejects_duplicate_and_unknown_shards(
    tmp_path: Path,
) -> None:
    module = _load_script()
    payload = _attestation(
        sha="a" * 40,
        lane="hosted",
        shards=["unit-2", "unit-2", "not-a-shard"],
    )

    _shards, _pairings, errors = module._validate_attestation(
        tmp_path / "bad-shards.json",
        payload,
        sha="a" * 40,
        lane="hosted",
    )

    assert any("contains duplicates" in error for error in errors)
    assert any("unknown shards" in error for error in errors)


@pytest.mark.parametrize(
    "coverage",
    [
        None,
        {},
        {
            "artifact": "../.coverage.unit-2-3.11",
            "bytes": 0,
            "sha256": "not-a-digest",
            "python": "3.14",
        },
    ],
)
def test_hosted_attestation_requires_valid_bound_coverage(
    tmp_path: Path,
    coverage: object,
) -> None:
    module = _load_script()
    payload = _attestation(sha="a" * 40, lane="hosted", shards=["unit-2"])
    if coverage is None:
        payload.pop("coverage")
    else:
        payload["coverage"] = coverage

    _shards, _pairings, errors = module._validate_attestation(
        tmp_path / "hosted.json",
        payload,
        sha="a" * 40,
        lane="hosted",
    )

    assert any("coverage evidence" in error for error in errors)


def test_evidence_reader_rejects_missing_and_non_object_json(tmp_path: Path) -> None:
    module = _load_script()
    missing_payload, missing_errors = module._read_attestation(tmp_path / "missing")
    array_path = tmp_path / "array.json"
    array_path.write_text("[]", encoding="utf-8")
    array_payload, array_errors = module._read_attestation(array_path)

    assert missing_payload is None
    assert "unreadable attestation" in missing_errors[0]
    assert array_payload is None
    assert "root is not an object" in array_errors[0]


def test_hosted_evidence_rejects_duplicate_shard_attestations(tmp_path: Path) -> None:
    module = _load_script()
    sha = "a" * 40
    local = _write(
        tmp_path / "local.json",
        _attestation(sha=sha, lane="local", shards=sorted(SHARDS)),
    )
    hosted = [
        _write(
            tmp_path / shard / "ci-shard-attestation.json",
            _attestation(sha=sha, lane="hosted", shards=[shard]),
        )
        for shard in SHARDS
    ]
    hosted.append(
        _write(
            tmp_path / "duplicate" / "ci-shard-attestation.json",
            _attestation(sha=sha, lane="hosted", shards=["other"]),
        )
    )

    errors = module.verify_dual_track_evidence(local, hosted, sha)

    assert any("ambiguous" in error for error in errors)


def test_release_commands_require_dual_track_evidence() -> None:
    source = MAKEFILE.read_text(encoding="utf-8")

    assert "require-dual-track-green:" in source
    dry_run = source.split("_release-dry-run-guard:", 1)[1].split("\n\n", 1)[0]
    release_cut = source.split("release-cut:", 1)[1].split("\n\n", 1)[0]
    assert "require-dual-track-green" in dry_run
    assert "require-dual-track-green" in release_cut


def test_successful_run_selection_is_exact_sha_and_newest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    sha = "d" * 40
    payload = [
        {
            "headSha": sha,
            "workflowName": module.WORKFLOW_NAME,
            "status": "completed",
            "conclusion": "success",
            "databaseId": 40,
        },
        {
            "headSha": sha,
            "workflowName": module.WORKFLOW_NAME,
            "status": "completed",
            "conclusion": "success",
            "databaseId": 41,
        },
        {
            "headSha": "e" * 40,
            "workflowName": module.WORKFLOW_NAME,
            "status": "completed",
            "conclusion": "success",
            "databaseId": 99,
        },
    ]
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )

    assert module._successful_run_id(sha) == 41


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "message"),
    [
        (1, "", "network unavailable", "network unavailable"),
        (0, "{}", "", "non-list payload"),
        (0, "[]", "", "no successful hosted workflow"),
    ],
)
def test_successful_run_selection_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
    message: str,
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        module._successful_run_id("f" * 40)


def test_hosted_download_returns_only_attestations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    expected = _write(
        tmp_path / "coverage-unit-2-3.11" / "ci-shard-attestation.json",
        _attestation(sha="a" * 40, lane="hosted", shards=["unit-2"]),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )

    assert module._download_hosted_attestations(42, tmp_path) == [expected]


@pytest.mark.parametrize("returncode", [0, 1])
def test_hosted_download_fails_on_missing_or_failed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout="",
            stderr="download failed" if returncode else "",
        ),
    )

    with pytest.raises(RuntimeError):
        module._download_hosted_attestations(42, tmp_path)


def test_cli_validate_only_is_network_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("validate-only must not call subprocess"),
    )
    monkeypatch.setattr(
        module,
        "_successful_run_id",
        lambda _sha: pytest.fail("validate-only must not query GitHub"),
    )
    monkeypatch.setattr(
        module,
        "_download_hosted_attestations",
        lambda *_args: pytest.fail("validate-only must not download artifacts"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_dual_track_ci.py",
            "--sha",
            "a" * 40,
            "--validate-only",
        ],
    )

    assert module.main() == 0


def test_cli_verifies_explicit_evidence_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    sha = "b" * 40
    local = _write(
        tmp_path / "local.json",
        _attestation(sha=sha, lane="local", shards=sorted(SHARDS)),
    )
    hosted_dir = tmp_path / "hosted"
    for shard in SHARDS:
        _write(
            hosted_dir / shard / "ci-shard-attestation.json",
            _attestation(sha=sha, lane="hosted", shards=[shard]),
        )
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_dual_track_ci.py",
            "--sha",
            sha,
            "--local-attestation",
            str(local),
            "--hosted-evidence-dir",
            str(hosted_dir),
        ],
    )

    assert module.main() == 0


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [(0, "a" * 40 + "\n", "a" * 40), (1, "", None), (0, "abc\n", None)],
)
def test_git_head_requires_full_sha(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    expected: str | None,
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=""
        ),
    )

    if expected is None:
        with pytest.raises(RuntimeError, match="exact 40-character"):
            module._git_head()
    else:
        assert module._git_head() == expected


def test_cli_fetches_hosted_evidence_into_owned_temporary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    sha = "c" * 40
    local = _write(
        tmp_path / "local.json",
        _attestation(sha=sha, lane="local", shards=sorted(SHARDS)),
    )
    hosted = [
        _write(
            tmp_path / "fixtures" / shard / "ci-shard-attestation.json",
            _attestation(sha=sha, lane="hosted", shards=[shard]),
        )
        for shard in SHARDS
    ]
    monkeypatch.setattr(module, "resource_root", lambda _root: tmp_path / "resources")
    monkeypatch.setattr(module, "_successful_run_id", lambda _sha: 99)
    monkeypatch.setattr(
        module,
        "_download_hosted_attestations",
        lambda run_id, _destination: hosted if run_id == 99 else [],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_dual_track_ci.py",
            "--sha",
            sha,
            "--local-attestation",
            str(local),
        ],
    )

    assert module.main() == 0
    assert list((tmp_path / "resources" / "dual-track-downloads").iterdir()) == []


@pytest.mark.parametrize("sha", ["short", "d" * 40])
def test_cli_failures_are_nonzero_and_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sha: str,
) -> None:
    module = _load_script()
    args = ["verify_dual_track_ci.py", "--sha", sha]
    if len(sha) == 40:
        args.extend(["--hosted-evidence-dir", str(tmp_path / "missing")])
    monkeypatch.setattr("sys.argv", args)

    expected = 2 if sha == "short" else 1
    assert module.main() == expected
