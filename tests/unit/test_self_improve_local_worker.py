"""Lifecycle contracts for the isolated local self-improvement proposal worker."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import scripts.self_improve_local_proposal as worker_module
from scripts.self_improve_local_proposal import run_worker

from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
    ProposalContract,
    ProposalManifest,
    encode_prompt_batch,
)
from general_ludd.self_improve.runtime import MakeResult, generate_local_proposal


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


def test_worker_writes_one_atomic_confined_proposal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_text("repair the exact file", encoding="utf-8")

    output = run_worker(exchange, model, gateway_factory=_FakeGateway)

    assert output == exchange / "proposal.json"
    assert ProposalManifest.from_json(output.read_text(encoding="utf-8")).task_id == "S83.133"
    assert not list(exchange.glob("*.tmp"))
    worker_output = capsys.readouterr().out
    assert (
        "SELF_IMPROVE_LOCAL_PROPOSAL_SAMPLING "
        f"shard=1/1 profile={DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID}"
    ) in worker_output
    assert "repair_state_sha256=none" in worker_output
    assert (
        "output_sha256=" + hashlib.sha256(output.read_bytes()).hexdigest()
    ) in worker_output


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
        run_worker(
            exchange,
            model,
            contract_path=contract,
            gateway_factory=_FakeGateway,
        )

    assert not (exchange / "proposal.json").exists()


def test_worker_rejects_implicit_or_noncanonical_contract_transport(
    tmp_path: Path,
) -> None:
    """Reject sibling discovery and every contract path outside the exchange."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_text("repair", encoding="utf-8")
    contract = exchange / "contract.json"
    contract.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="requires explicit canonical transport"):
        run_worker(exchange, model, gateway_factory=_FakeGateway)

    outside = tmp_path / "contract.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="path is not canonical"):
        run_worker(
            exchange,
            model,
            contract_path=outside,
            gateway_factory=_FakeGateway,
        )


@pytest.mark.parametrize(
    "field",
    ["sampling_seed", "sampling_context_sha256", "sampling_candidate_index"],
)
def test_worker_recomputes_repair_seed_context_before_model_construction(
    tmp_path: Path,
    field: str,
) -> None:
    """Reject a modified repair seed or commitment at the owned CLI boundary."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    request = encode_prompt_batch(("bounded repair prompt",), protocol_digest="f" * 64)
    (exchange / "prompt.txt").write_text(request, encoding="utf-8")
    contract = ProposalContract.for_request(
        request=request,
        baseline_sha="a" * 40,
        task_id="S83.133",
        tests=("tests/unit/test_example.py",),
        make_commands=("make test-files TESTFILES=tests/unit/test_example.py",),
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        sampling_profile=COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    )
    value = json.loads(contract.to_json())
    assert isinstance(contract.sampling_seed, int)
    if field == "sampling_seed":
        value[field] = contract.sampling_seed + 1
    elif field == "sampling_context_sha256":
        value[field] = "b" * 64
    else:
        value[field] = 1
    contract_path = exchange / "contract.json"
    contract_path.write_text(json.dumps(value), encoding="utf-8")
    constructions = 0

    def gateway_factory(_model_path: Path) -> _FakeGateway:
        nonlocal constructions
        constructions += 1
        return _FakeGateway(_model_path)

    with pytest.raises(ValueError, match="sampling context mismatch"):
        run_worker(
            exchange,
            model,
            contract_path=contract_path,
            gateway_factory=gateway_factory,
        )

    assert constructions == 0
    assert not (exchange / "proposal.json").exists()


def test_worker_output_digest_telemetry_never_echoes_proposal_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expose only a digest even when the proposal contains credential-like text."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "prompt.txt").write_text("repair", encoding="utf-8")
    secret = "TOKEN=never-publish-this"

    class SecretGateway(_FakeGateway):
        def propose(
            self,
            prompt: str,
            *,
            contract: ProposalContract | None = None,
        ) -> ProposalManifest:
            del prompt, contract
            return ProposalManifest.from_json(
                _proposal_json().replace("before  ", secret)
            )

    output = run_worker(exchange, model, gateway_factory=SecretGateway)
    worker_output = capsys.readouterr().out

    assert secret in output.read_text(encoding="utf-8")
    assert secret not in worker_output
    assert "output_sha256=" in worker_output


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


def test_worker_main_passes_one_explicit_canonical_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carry the immutable contract through the CLI instead of sibling discovery."""
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    prompt = exchange / "prompt.txt"
    proposal = exchange / "proposal.json"
    contract = exchange / "contract.json"
    model = tmp_path / "model.gguf"
    prompt.write_text("repair", encoding="utf-8")
    contract.write_text("{}", encoding="utf-8")
    model.write_bytes(b"gguf")
    calls: list[tuple[Path, Path, Path | None]] = []

    def fake_worker(
        exchange_dir: Path,
        model_path: Path,
        *,
        contract_path: Path | None = None,
        gateway_factory: object = None,
    ) -> Path:
        del gateway_factory
        calls.append((exchange_dir, model_path, contract_path))
        return proposal

    monkeypatch.setattr(worker_module, "run_worker", fake_worker)
    assert worker_module.main(
        [
            "--prompt-file",
            str(prompt),
            "--proposal-file",
            str(proposal),
            "--contract-file",
            str(contract),
            "--model-path",
            str(model),
        ]
    ) == 0
    assert calls == [(exchange, model, contract)]


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
