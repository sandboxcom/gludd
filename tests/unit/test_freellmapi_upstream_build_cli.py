"""Operator contracts for ephemeral FreeLLMAPI upstream builds."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
import scripts.freellmapi_upstream_build as build_cli

from general_ludd.models.freellmapi_upstream_build import (
    FreeLLMAPIUpstreamBuildError,
    FreeLLMAPIUpstreamBuildFault,
)

_ROOT = Path(__file__).resolve().parents[2]
_COMMIT = "4191d8e7abef39fcd93fab009123467036f39750"
_ARCHIVE_DIGEST = "9f5156164cfc9b98416014b1ed1a9a49bb0005b32ae64b7a21e4afd8c467a198"


def _files() -> dict[str, bytes]:
    return {
        "LICENSE": (
            b"MIT License\nPermission is hereby granted, free of charge\n"
            b'THE SOFTWARE IS PROVIDED "AS IS"\n'
        ),
        "package-lock.json": json.dumps(
            {
                "name": "@freellmapi/monorepo",
                "lockfileVersion": 3,
                "packages": {},
            }
        ).encode(),
        "package.json": json.dumps(
            {
                "name": "@freellmapi/monorepo",
                "scripts": {
                    "test": "upstream root tests",
                    "test:migrations": "upstream migrations",
                    "lint": "upstream lint",
                    "build": "upstream build",
                },
            }
        ).encode(),
        "server/package.json": json.dumps(
            {
                "name": "@freellmapi/server",
                "scripts": {"test:coverage": "upstream server coverage"},
            }
        ).encode(),
        "server/src/services/scoring.ts": b"\n".join(
            f"export function {name}() {{ return 1; }}".encode()
            for name in (
                "reliabilityPosterior",
                "expectedReliability",
                "speedScore",
                "headroomFactor",
                "rateWindowHeadroomFactor",
                "rateLimitFactor",
            )
        ),
    }


def _archive(
    *,
    extra_member: tarfile.TarInfo | None = None,
    extra_body: bytes = b"",
) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for relative, body in _files().items():
            member = tarfile.TarInfo(f"freellmapi-release/{relative}")
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
        if extra_member is not None:
            extra_member.size = len(extra_body)
            archive.addfile(extra_member, io.BytesIO(extra_body))
    return stream.getvalue()


def _candidate(archive: bytes) -> dict[str, object]:
    value: object = json.loads(
        (_ROOT / "config/freellmapi/upstream_candidate.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    candidate = cast(dict[str, object], value)
    candidate["archive"] = {
        "sha256": build_cli.sha256_hex(archive),
        "size_bytes": len(archive),
    }
    candidate.pop("candidate_id")
    candidate["candidate_id"] = "sha256:" + build_cli.canonical_sha256(candidate)
    return candidate


def _plan(candidate: dict[str, object]) -> dict[str, object]:
    value: object = json.loads(
        (_ROOT / "config/freellmapi/upstream_build_plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    plan = cast(dict[str, object], value)
    archive = candidate["archive"]
    assert isinstance(archive, Mapping)
    plan["candidate_id"] = candidate["candidate_id"]
    plan["archive_sha256"] = archive["sha256"]
    return plan


def _write_inputs(tmp_path: Path, archive: bytes) -> tuple[Path, Path]:
    candidate = _candidate(archive)
    candidate_path = tmp_path / "candidate.json"
    plan_path = tmp_path / "plan.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    plan_path.write_text(json.dumps(_plan(candidate)), encoding="utf-8")
    return candidate_path, plan_path


class _Client:
    def __init__(self, archive: bytes) -> None:
        self.archive = archive
        self.endpoints: list[str] = []

    def get_bytes(self, endpoint: str) -> bytes:
        self.endpoints.append(endpoint)
        return self.archive


class _Executor:
    def __init__(
        self,
        *,
        fail_step: tuple[str, ...] | None = None,
        failure_code: int = 1,
        node_version: str = "v20.20.2",
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.working_directories: list[Path] = []
        self.environments: list[dict[str, str]] = []
        self.fail_step = fail_step
        self.failure_code = failure_code
        self.node_version = node_version

    def output(self, argv: tuple[str, ...], *, cwd: Path, env: dict[str, str]) -> str:
        self._record(argv, cwd, env)
        return self.node_version if argv == ("node", "--version") else "10.8.2"

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> int:
        assert timeout_seconds == 1800
        self._record(argv, cwd, env)
        return self.failure_code if argv == self.fail_step else 0

    def _record(self, argv: tuple[str, ...], cwd: Path, env: dict[str, str]) -> None:
        assert cwd.is_dir()
        assert (cwd / "package.json").is_file()
        self.calls.append(argv)
        self.working_directories.append(cwd)
        self.environments.append(dict(env))


def _argv(candidate_path: Path, plan_path: Path, report_path: Path, mode: str) -> list[str]:
    return [
        "--mode",
        mode,
        "--candidate",
        str(candidate_path),
        "--plan",
        str(plan_path),
        "--report",
        str(report_path),
        "--repository-root",
        str(_ROOT),
    ]


def test_validate_mode_is_offline_read_only_and_records_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = _ROOT / "config/freellmapi/upstream_candidate.json"
    plan = _ROOT / "config/freellmapi/upstream_build_plan.json"
    report = tmp_path / "must-not-exist.json"

    assert build_cli.main(_argv(candidate, plan, report, "validate")) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["decision"] == "validated_not_run"
    assert summary["runtime_admitted"] is False
    assert summary["archive_sha256"] == _ARCHIVE_DIGEST
    assert not report.exists()


def test_live_mode_fetches_exact_commit_runs_fixed_steps_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive()
    candidate_path, plan_path = _write_inputs(tmp_path, archive)
    report = tmp_path / "gludd-freellmapi-build-test" / "evidence.json"
    client = _Client(archive)
    executor = _Executor()
    monkeypatch.setenv("PROVIDER_SECRET", "must-not-leak")

    result = build_cli.main(
        _argv(candidate_path, plan_path, report, "live"),
        client=client,
        executor=executor,
    )

    assert result == 0
    assert client.endpoints == [
        f"repos/tashfeenahmed/freellmapi/tarball/{_COMMIT}"
    ]
    assert executor.calls == [
        ("node", "--version"),
        ("npm", "--version"),
        ("npm", "ci", "--no-audit", "--no-fund"),
        ("npm", "run", "test:migrations"),
        ("npm", "test"),
        ("npm", "run", "lint"),
        ("npm", "run", "build"),
        ("npm", "run", "test:coverage", "-w", "server"),
    ]
    assert all("PROVIDER_SECRET" not in env for env in executor.environments)
    assert all(not cwd.exists() for cwd in executor.working_directories)
    evidence = json.loads(report.read_text(encoding="utf-8"))
    assert evidence["decision"] == "upstream_build_verified"
    assert evidence["runtime_admitted"] is False


def test_failed_step_writes_rejection_evidence_and_returns_failure(
    tmp_path: Path,
) -> None:
    archive = _archive()
    candidate_path, plan_path = _write_inputs(tmp_path, archive)
    report = tmp_path / "gludd-freellmapi-build-failed" / "evidence.json"
    executor = _Executor(fail_step=("npm", "run", "lint"), failure_code=-9)

    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_cli.run_live_build(
            candidate_path=candidate_path,
            plan_path=plan_path,
            report_path=report,
            client=_Client(archive),
            executor=executor,
        )

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.STEP_FAILED
    evidence = json.loads(report.read_text(encoding="utf-8"))
    assert evidence["decision"] == "rejected_upstream_build"
    assert evidence["failed_step"] == "lint"
    assert evidence["steps"][3]["exit_code"] == 137
    assert all(not cwd.exists() for cwd in executor.working_directories)


def test_unplanned_node_is_rejected_before_install_or_upstream_scripts(
    tmp_path: Path,
) -> None:
    archive = _archive()
    candidate_path, plan_path = _write_inputs(tmp_path, archive)
    executor = _Executor(node_version="v26.0.0")

    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_cli.run_live_build(
            candidate_path=candidate_path,
            plan_path=plan_path,
            report_path=tmp_path
            / "gludd-freellmapi-build-toolchain/evidence.json",
            client=_Client(archive),
            executor=executor,
        )

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID
    assert executor.calls == [("node", "--version"), ("npm", "--version")]
    assert all(not cwd.exists() for cwd in executor.working_directories)


@pytest.mark.parametrize(
    "member",
    [
        tarfile.TarInfo("freellmapi-release/../escape"),
        tarfile.TarInfo("/absolute"),
        tarfile.TarInfo("another-root/file"),
    ],
)
def test_archive_paths_and_multiple_roots_are_rejected_before_execution(
    tmp_path: Path, member: tarfile.TarInfo
) -> None:
    archive = _archive(extra_member=member, extra_body=b"bad")
    candidate_path, plan_path = _write_inputs(tmp_path, archive)
    report = tmp_path / "gludd-freellmapi-build-invalid" / "evidence.json"
    executor = _Executor()

    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_cli.run_live_build(
            candidate_path=candidate_path,
            plan_path=plan_path,
            report_path=report,
            client=_Client(archive),
            executor=executor,
        )

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID
    assert executor.calls == []


def test_archive_links_are_rejected_before_execution(tmp_path: Path) -> None:
    link = tarfile.TarInfo("freellmapi-release/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    archive = _archive(extra_member=link)
    candidate_path, plan_path = _write_inputs(tmp_path, archive)

    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_cli.run_live_build(
            candidate_path=candidate_path,
            plan_path=plan_path,
            report_path=tmp_path / "gludd-freellmapi-build-link/evidence.json",
            client=_Client(archive),
            executor=_Executor(),
        )

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID


def test_missing_upstream_script_is_rejected_without_running_npm(tmp_path: Path) -> None:
    files = _files()
    package = json.loads(files["package.json"])
    del package["scripts"]["lint"]
    files["package.json"] = json.dumps(package).encode()
    monkey_archive = io.BytesIO()
    with tarfile.open(fileobj=monkey_archive, mode="w:gz") as archive_file:
        for relative, body in files.items():
            member = tarfile.TarInfo(f"freellmapi-release/{relative}")
            member.size = len(body)
            archive_file.addfile(member, io.BytesIO(body))
    archive = monkey_archive.getvalue()
    candidate_path, plan_path = _write_inputs(tmp_path, archive)
    executor = _Executor()

    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_cli.run_live_build(
            candidate_path=candidate_path,
            plan_path=plan_path,
            report_path=tmp_path / "gludd-freellmapi-build-script/evidence.json",
            client=_Client(archive),
            executor=executor,
        )

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.SCRIPTS_INVALID
    assert executor.calls == []


def test_report_path_must_be_namespaced_and_written_atomically(tmp_path: Path) -> None:
    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_cli.safe_report_path(Path("/tmp/report.json"), _ROOT)
    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.INPUT_INVALID

    target = tmp_path / "gludd-freellmapi-report" / "evidence.json"
    build_cli.write_report(target, {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert list(target.parent.glob(".evidence.json.*")) == []


def test_make_and_gha_contracts_pin_node_matrix_and_release_dependency() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nfreellmapi-upstream-build:", 1)[1].split("\n\n", 1)[0]
    assert "scripts.freellmapi_upstream_build" in target
    assert "FREELLMAPI_BUILD_LIVE" in target

    workflow = (_ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    job = workflow.split("\n  freellmapi-upstream-build:", 1)[1].split("\n  test-shard:", 1)[0]
    assert 'node-version: ["20.20.2", "22.23.2"]' in job
    assert "make freellmapi-upstream-build" in job
    assert "FREELLMAPI_BUILD_LIVE=1" in job
    release = workflow.split("\n  release:", 1)[1]
    assert "freellmapi-upstream-build" in release.split("\n", 4)[1]


def test_public_helpers_are_content_free_hashes_only() -> None:
    assert build_cli.sha256_hex(b"private input") == hashlib.sha256(
        b"private input"
    ).hexdigest()
    assert build_cli.canonical_sha256({"b": 2, "a": 1}) == hashlib.sha256(
        b'{"a":1,"b":2}'
    ).hexdigest()
