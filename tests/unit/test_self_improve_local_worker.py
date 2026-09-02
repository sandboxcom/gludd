"""Lifecycle contracts for the isolated local self-improvement proposal worker."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import scripts.self_improve_local_proposal as worker_module
from scripts.run_self_improve_e2e import MakeResult, generate_local_proposal
from scripts.self_improve_local_proposal import run_worker

from general_ludd.self_improve.codex_comparison import ProposalContract, ProposalManifest


def _proposal_json() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "baseline_sha": "a" * 40,
            "task_id": "S83.133",
            "edits": [
                {
                    "operation": "replace",
                    "path": "docs/example.md",
                    "old_text": "before  ",
                    "new_text": "before",
                }
            ],
            "tests": ["tests/unit/test_markdown_docs_deep.py"],
            "make_commands": [
                "make test-files TESTFILES=tests/unit/test_markdown_docs_deep.py"
            ],
            "commit_message": "fix: normalize documentation",
        }
    )


class _FakeGateway:
    def __init__(self, _model_path: Path) -> None:
        self.calls: list[str] = []

    def propose(
        self,
        prompt: str,
        *,
        contract: ProposalContract | None = None,
    ) -> ProposalManifest:
        del contract
        self.calls.append(prompt)
        return ProposalManifest.from_json(_proposal_json())


def test_worker_writes_one_atomic_confined_proposal(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_text("repair the exact file", encoding="utf-8")

    output = run_worker(exchange, model, gateway_factory=_FakeGateway)

    assert output == exchange / "proposal.json"
    assert ProposalManifest.from_json(output.read_text(encoding="utf-8")).task_id == "S83.133"
    assert not list(exchange.glob("*.tmp"))


def test_worker_rejects_symlinked_exchange_input(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("escape", encoding="utf-8")
    (exchange / "prompt.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="prompt"):
        run_worker(exchange, model, gateway_factory=_FakeGateway)

    assert not (exchange / "proposal.json").exists()


class _OwnedRunner:
    def __init__(self, *, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[str, dict[str, str], int]] = []

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        self.calls.append((target, variables, timeout))
        if self.returncode == 0:
            Path(variables["SELF_IMPROVE_PROPOSAL_FILE"]).write_text(
                _proposal_json(), encoding="utf-8"
            )
        return MakeResult(
            argv=("make", target),
            returncode=self.returncode,
            stdout="worker complete" if self.returncode == 0 else "",
            stderr="" if self.returncode == 0 else "native signal 11",
            elapsed_seconds=1.0,
        )


def test_parent_delegates_inference_to_owned_make_worker(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    runner = _OwnedRunner()

    proposal = generate_local_proposal(runner, model, "repair exactly")

    assert proposal.task_id == "S83.133"
    assert runner.calls[0][0] == "self-improve-local-proposal"
    assert runner.calls[0][2] == 300
    variables = runner.calls[0][1]
    assert variables["SELF_IMPROVE_MODEL_PATH"] == str(model)
    assert not Path(variables["SELF_IMPROVE_PROMPT_FILE"]).exists()
    assert not Path(variables["SELF_IMPROVE_PROPOSAL_FILE"]).exists()


def test_parent_surfaces_native_worker_failure_without_parsing_output(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    runner = _OwnedRunner(returncode=139)

    with pytest.raises(RuntimeError, match=r"rc=139.*native signal 11"):
        generate_local_proposal(runner, model, "repair exactly")


@pytest.mark.parametrize(
    ("prompt_bytes", "precreate_output", "match"),
    [
        (b"", False, "empty"),
        (bytes([255]), False, "UTF-8"),
        (b"x" * 262_145, False, "exceeds"),
        (b"valid", True, "must not already exist"),
    ],
    ids=("empty", "non-utf8", "oversized", "stale-output"),
)
def test_worker_rejects_invalid_exchange_state(
    tmp_path: Path,
    prompt_bytes: bytes,
    precreate_output: bool,
    match: str,
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_bytes(prompt_bytes)
    if precreate_output:
        (exchange / "proposal.json").write_text("stale", encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        run_worker(exchange, model, gateway_factory=_FakeGateway)


@pytest.mark.parametrize(
    ("contract_bytes", "symlink", "match"),
    [
        (b"{", False, "valid JSON"),
        (bytes([255]), False, "UTF-8"),
        (b"x" * 196_609, False, "exceeds"),
        (
            json.dumps(
                {
                    "baseline_sha": "a" * 40,
                    "task_id": "S83.133",
                    "tests": ["tests/unit/test_example.py"],
                    "make_commands": [
                        "make test-files TESTFILES=tests/unit/test_example.py"
                    ],
                }
            ).encode(),
            True,
            "regular confined",
        ),
    ],
    ids=("malformed", "non-utf8", "oversized", "symlink"),
)
def test_worker_rejects_invalid_compact_contract_before_model_construction(
    tmp_path: Path,
    contract_bytes: bytes,
    symlink: bool,
    match: str,
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_text("repair", encoding="utf-8")
    contract = exchange / "contract.json"
    if symlink:
        outside = tmp_path / "outside-contract.json"
        outside.write_bytes(contract_bytes)
        contract.symlink_to(outside)
    else:
        contract.write_bytes(contract_bytes)

    with pytest.raises(ValueError, match=match):
        run_worker(exchange, model, gateway_factory=_FakeGateway)

    assert not (exchange / "proposal.json").exists()


def test_worker_main_validates_paths_and_surfaces_owned_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    prompt = exchange / "prompt.txt"
    proposal = exchange / "proposal.json"
    model = tmp_path / "model.gguf"
    prompt.write_text("repair", encoding="utf-8")
    model.write_bytes(b"gguf")
    calls: list[tuple[Path, Path]] = []

    def fake_worker(exchange_dir: Path, model_path: Path) -> Path:
        calls.append((exchange_dir, model_path))
        return proposal

    monkeypatch.setattr(worker_module, "run_worker", fake_worker)
    assert worker_module.main(
        [
            "--prompt-file",
            str(prompt),
            "--proposal-file",
            str(proposal),
            "--model-path",
            str(model),
        ]
    ) == 0
    assert calls == [(exchange, model)]

    assert worker_module.main(
        [
            "--prompt-file",
            str(exchange / "wrong.txt"),
            "--proposal-file",
            str(proposal),
            "--model-path",
            str(model),
        ]
    ) == 2
    assert "not canonical" in capsys.readouterr().err

    def failing_worker(_exchange_dir: Path, _model_path: Path) -> Path:
        raise ValueError("bounded failure")

    monkeypatch.setattr(worker_module, "run_worker", failing_worker)
    assert worker_module.main(
        [
            "--prompt-file",
            str(prompt),
            "--proposal-file",
            str(proposal),
            "--model-path",
            str(model),
        ]
    ) == 2
    assert "bounded failure" in capsys.readouterr().err


def test_worker_rejects_exchange_that_is_not_a_directory(tmp_path: Path) -> None:
    exchange = tmp_path / "exchange"
    exchange.write_text("not a directory", encoding="utf-8")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    with pytest.raises(ValueError, match="must be a directory"):
        run_worker(exchange, model, gateway_factory=_FakeGateway)


def test_worker_removes_temporary_output_when_atomic_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_text("repair", encoding="utf-8")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("publish failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="publish failed"):
        run_worker(exchange, model, gateway_factory=_FakeGateway)

    assert not list(exchange.glob(".proposal-*.tmp"))
    assert not (exchange / "proposal.json").exists()
