"""Operator-script branch coverage for exact three-arm replay."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripts.replay_freellmapi_three_arm as replay


class _Client:
    def __init__(self, archive: bytes) -> None:
        self.archive = archive

    def get_bytes(self, _endpoint: str) -> bytes:
        return self.archive


class _Executor:
    def __init__(self, *, version: str = "0.28.1", returncode: int = 0) -> None:
        self.version = version
        self.returncode = returncode

    def output(
        self,
        _argv: tuple[str, ...],
        *,
        cwd: Path,
        env: object,
    ) -> str:
        assert cwd.is_dir()
        assert env
        return self.version

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: object,
    ) -> int:
        assert env
        if self.returncode == 0:
            output = next(item.removeprefix("--outfile=") for item in argv if item.startswith("--outfile="))
            (cwd / output).write_bytes(b"var FreeLLMAPICandidateV0111 = (() => {})();\n")
        return self.returncode


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _candidate(archive: bytes, source: bytes) -> dict[str, object]:
    return {
        "upstream": {
            "repository": "tashfeenahmed/freellmapi",
            "commit": "4191d8e7abef39fcd93fab009123467036f39750",
            "tag": "v0.11.1",
        },
        "archive": {"sha256": _sha(archive), "size_bytes": len(archive)},
        "sources": {
            "scoring": {
                "path": "server/src/services/scoring.ts",
                "sha256": _sha(source),
            }
        },
    }


def _prepare_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, bytes, bytes]:
    archive = b"verified-archive"
    source = b"export function expectedReliability() { return 1; }\n"
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(_candidate(archive, source)), encoding="utf-8")
    adapter = tmp_path / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1_entry.ts"
    adapter.parent.mkdir(parents=True)
    adapter.write_text("export {};\n", encoding="utf-8")
    monkeypatch.setattr(replay, "_SOURCE_SHA256", _sha(source))
    monkeypatch.setattr(
        replay,
        "validate_candidate",
        lambda _value: (replay._CANDIDATE_ID, frozenset({"expectedReliability"})),
    )
    monkeypatch.setattr(
        replay,
        "inspect_upstream_archive",
        lambda _archive, max_archive_bytes: SimpleNamespace(scoring_bytes=source),
    )
    return candidate_path, archive, source


def test_compile_once_pins_esbuild_and_adds_strict_prologue(tmp_path: Path) -> None:
    esbuild = tmp_path / ".opencode/node_modules/.bin/esbuild"
    esbuild.parent.mkdir(parents=True)
    esbuild.write_text("locked", encoding="utf-8")

    output = replay._compile_once(
        root=tmp_path,
        source=b"export const value = 1;",
        adapter=b"export {};",
        executor=_Executor(),
    )

    assert output.startswith(b'"use strict";\nvar FreeLLMAPICandidateV0111')

    with pytest.raises(replay.ThreeArmReplayScriptError, match="bundler_invalid"):
        replay._compile_once(
            root=tmp_path,
            source=b"source",
            adapter=b"adapter",
            executor=_Executor(version="0.0.0"),
        )
    with pytest.raises(replay.ThreeArmReplayScriptError, match="bundle_build_failed"):
        replay._compile_once(
            root=tmp_path,
            source=b"source",
            adapter=b"adapter",
            executor=_Executor(returncode=1),
        )


def test_refresh_candidate_bundle_writes_reproducible_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, archive, _ = _prepare_refresh(tmp_path, monkeypatch)
    built = b'"use strict";\nvar FreeLLMAPICandidateV0111 = (() => {})();\n'
    monkeypatch.setattr(replay, "_compile_once", lambda **_kwargs: built)

    result = replay.refresh_candidate_bundle(
        repository_root=tmp_path,
        candidate_path=candidate_path,
        client=_Client(archive),
    )

    bundle = tmp_path / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1.iife.js"
    manifest = tmp_path / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1.json"
    assert bundle.read_bytes() == built
    assert json.loads(manifest.read_text(encoding="utf-8"))["bundle_sha256"] == _sha(built)
    assert result["runtime_admitted"] is False


def test_refresh_candidate_bundle_fails_on_nondeterminism_and_bad_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, archive, _ = _prepare_refresh(tmp_path, monkeypatch)
    builds = iter((b"first", b"second"))
    monkeypatch.setattr(replay, "_compile_once", lambda **_kwargs: next(builds))

    with pytest.raises(replay.ThreeArmReplayScriptError, match="bundle_nondeterministic"):
        replay.refresh_candidate_bundle(
            repository_root=tmp_path,
            candidate_path=candidate_path,
            client=_Client(archive),
        )

    with pytest.raises(replay.ThreeArmReplayScriptError, match="archive_invalid"):
        replay.refresh_candidate_bundle(
            repository_root=tmp_path,
            candidate_path=candidate_path,
            client=_Client(b"drift"),
        )


def test_refresh_config_mode_and_generic_failure_are_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.json"
    corpus = tmp_path / "corpus.json"
    report = tmp_path / "report.json"
    base = [
        "--candidate",
        str(tmp_path / "candidate.json"),
        "--plan",
        str(plan),
        "--corpus",
        str(corpus),
        "--report",
        str(report),
        "--repository-root",
        str(tmp_path),
    ]

    assert replay.main(["--mode", "refresh-config", *base]) == 0
    assert json.loads(plan.read_text(encoding="utf-8"))["decision"] == "hold_only"
    assert len(json.loads(corpus.read_text(encoding="utf-8"))["groups"]) == 32
    assert json.loads(capsys.readouterr().out)["runtime_admitted"] is False

    monkeypatch.setattr(
        replay,
        "_validate_replay_inputs",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private")),
    )
    assert replay.main(["--mode", "replay", *base]) == 2
    failure = json.loads(report.read_text(encoding="utf-8"))
    assert failure["fault"] == "replay_failure"
    assert failure["serving_bundle_sha256"] == replay._ADMITTED_BUNDLE
    assert "private" not in capsys.readouterr().err


def test_node_crosscheck_unavailable_is_a_content_free_typed_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(replay.shutil, "which", lambda *_args, **_kwargs: None)

    with pytest.raises(replay.ThreeArmReplayScriptError, match="node_unavailable"):
        replay._node_crosscheck(repository_root=tmp_path, inputs=())


def test_invalid_json_input_is_content_free(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")

    with pytest.raises(replay.ThreeArmReplayScriptError, match="input_invalid"):
        replay._load_object(invalid)


def test_subprocess_executor_returns_output_status_and_typed_failure(
    tmp_path: Path,
) -> None:
    executor = replay._SubprocessExecutor()
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    assert executor.output(
        (sys.executable, "-c", "print('ok')"),
        cwd=tmp_path,
        env=environment,
    ) == "ok"
    assert executor.run(
        (sys.executable, "-c", "raise SystemExit(3)"),
        cwd=tmp_path,
        env=environment,
    ) == 3
    with pytest.raises(replay.ThreeArmReplayScriptError, match="bundle_build_failed"):
        executor.output(
            (sys.executable, "-c", "raise SystemExit(3)"),
            cwd=tmp_path,
            env=environment,
        )


def test_refresh_candidate_rejects_identity_source_and_adapter_faults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, archive, source = _prepare_refresh(tmp_path, monkeypatch)
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["upstream"]["repository"] = "wrong/repository"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(replay.ThreeArmReplayScriptError, match="candidate_invalid"):
        replay.refresh_candidate_bundle(
            repository_root=tmp_path,
            candidate_path=candidate_path,
            client=_Client(archive),
        )

    candidate_path.write_text(json.dumps(_candidate(archive, source)), encoding="utf-8")
    monkeypatch.setattr(
        replay,
        "inspect_upstream_archive",
        lambda _archive, max_archive_bytes: SimpleNamespace(scoring_bytes=b"drift"),
    )
    with pytest.raises(replay.ThreeArmReplayScriptError, match="source_invalid"):
        replay.refresh_candidate_bundle(
            repository_root=tmp_path,
            candidate_path=candidate_path,
            client=_Client(archive),
        )

    monkeypatch.setattr(
        replay,
        "inspect_upstream_archive",
        lambda _archive, max_archive_bytes: SimpleNamespace(scoring_bytes=source),
    )
    adapter = tmp_path / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1_entry.ts"
    adapter.unlink()
    with pytest.raises(replay.ThreeArmReplayScriptError, match="adapter_invalid"):
        replay.refresh_candidate_bundle(
            repository_root=tmp_path,
            candidate_path=candidate_path,
            client=_Client(archive),
        )


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        ("[]", 1),
        ("not-json", 0),
        ("[]", 0),
        ("[true]", 0),
        ("[2.0]", 0),
    ],
)
def test_node_crosscheck_rejects_process_and_result_faults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int,
) -> None:
    scoring_input = replay.FreeLLMScoringInput(
        candidate_identity_digest="a" * 64,
        successes=1.0,
        failures=1.0,
        community_successes=1.0,
        community_failures=1.0,
        tokens_per_second=1.0,
        ttfb_ms=1.0,
        used_tokens=1.0,
        budget_tokens=1.0,
        rate_window_used_fraction=0.1,
        rate_limit_penalty=0.0,
    )
    monkeypatch.setattr(replay.shutil, "which", lambda *_args, **_kwargs: "/node")
    monkeypatch.setattr(
        replay.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
        ),
    )

    with pytest.raises(replay.ThreeArmReplayScriptError, match="node_failure"):
        replay._node_crosscheck(
            repository_root=tmp_path,
            inputs=(scoring_input,),
        )
