"""Focused contract for the parent-owned local proposal exchange."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from general_ludd.self_improve.codex_comparison import (
    LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
    ProposalContract,
)
from general_ludd.self_improve.local_worker_request import (
    _MAX_PROPOSAL_BYTES,
    _ObservableRunner,
    _raise_worker_failure,
    _read_bounded_proposal,
    _request_protocol,
    run_local_proposal_request,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalEnvelope,
)
from general_ludd.self_improve.model_candidates import (
    LOCAL_PROPOSAL_INFRASTRUCTURE_ERROR_MARKER,
    BackendFailure,
    BackendInfrastructureError,
)


class _RecordingRunner:
    def __init__(self, result: _WorkerResult | None = None) -> None:
        self.call: tuple[str, dict[str, str], float] | None = None
        self.result = result or _WorkerResult(0, "complete", "")

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: float,
    ) -> _WorkerResult:
        self.call = (target, variables, timeout)
        Path(variables["SELF_IMPROVE_PROPOSAL_FILE"]).write_text(
            '{"proposal":true}', encoding="utf-8"
        )
        return self.result


@dataclass(frozen=True)
class _WorkerResult:
    returncode: int
    stdout: str
    stderr: str


def _contract() -> ProposalContract:
    return ProposalContract(
        baseline_sha="a" * 40,
        task_id="S83.136",
        tests=("tests/unit/test_public.py",),
        make_commands=(
            "make test-specific TESTFILE=tests/unit/test_public.py",
        ),
    )


def _envelope(request: str) -> ManagedCandidateProposalEnvelope:
    return ManagedCandidateProposalEnvelope(
        request_text=request,
        request_contract_json=_contract().to_json(),
        response_instruction="Return one approved object.",
        response_schema_json="{}",
        protocol_digest="b" * 64,
        sampling_digest="c" * 64,
    )


def test_exchange_runs_one_bounded_owned_worker(tmp_path: Path) -> None:
    """The extracted boundary retains exact request, timeout, and cleanup semantics."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    runner = _RecordingRunner()

    result = run_local_proposal_request(
        runner,
        model,
        "repair exactly",
        timeout_seconds=37.0,
    )

    assert result == '{"proposal":true}'
    assert runner.call is not None
    target, variables, timeout = runner.call
    assert (target, timeout) == ("self-improve-local-proposal", 37.0)
    assert variables["SELF_IMPROVE_MODEL_PATH"] == str(model)
    assert not Path(variables["SELF_IMPROVE_PROMPT_FILE"]).exists()
    assert not Path(variables["SELF_IMPROVE_PROPOSAL_FILE"]).exists()


def test_exchange_rejects_a_missing_runner_method_before_creating_exchange(
    tmp_path: Path,
) -> None:
    """Runtime admission emits a stable boundary error before worker setup."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")

    with pytest.raises(
        TypeError,
        match=r"^local proposal runner does not satisfy its runtime protocol$",
    ):
        run_local_proposal_request(
            cast(_ObservableRunner, object()),
            model,
            "repair exactly",
        )


@pytest.mark.parametrize("optional_input", ("contract", "envelope"))
def test_exchange_publishes_each_optional_protocol_artifact(
    tmp_path: Path,
    optional_input: str,
) -> None:
    """Compatibility inputs are transported without changing their public API."""
    request = "repair exactly"
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    runner = _RecordingRunner()
    if optional_input == "contract":
        result = run_local_proposal_request(
            runner,
            model,
            request,
            contract=_contract(),
        )
    else:
        result = run_local_proposal_request(
            runner,
            model,
            request,
            envelope=_envelope(request),
        )

    assert result == '{"proposal":true}'

    assert runner.call is not None
    variable_name = f"SELF_IMPROVE_{optional_input.upper()}_FILE"
    assert variable_name in runner.call[1]
    assert not Path(runner.call[1][variable_name]).exists()


def test_request_protocol_reads_contract_and_envelope() -> None:
    """Both compatibility carriers resolve the same trusted protocol identity."""
    contract = _contract()

    assert _request_protocol(contract, None) == contract.proposal_protocol
    assert _request_protocol(None, _envelope("repair exactly")) == contract.proposal_protocol
    assert _request_protocol(None, None) is None


@pytest.mark.parametrize(
    ("result", "failure"),
    (
        (_WorkerResult(124, "", ""), TimeoutError),
        (
            _WorkerResult(
                3,
                f"{LOCAL_PROPOSAL_INFRASTRUCTURE_ERROR_MARKER} unavailable",
                "",
            ),
            BackendInfrastructureError,
        ),
        (_WorkerResult(3, "", "private detail"), BackendInfrastructureError),
        (
            _WorkerResult(
                2,
                f"{LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.error_marker} "
                "compact proposal must contain exactly e",
                "",
            ),
            ValueError,
        ),
        (_WorkerResult(9, "", "bounded diagnostic"), RuntimeError),
        (_WorkerResult(9, "", ""), RuntimeError),
    ),
)
def test_worker_failure_categories_are_normalized(
    result: _WorkerResult,
    failure: type[BaseException],
) -> None:
    """Every worker exit class becomes one bounded parent-owned exception."""
    with pytest.raises(failure) as captured:
        _raise_worker_failure(result, None, None)

    if isinstance(captured.value, BackendInfrastructureError):
        expected = (
            BackendFailure.UNAVAILABLE
            if LOCAL_PROPOSAL_INFRASTRUCTURE_ERROR_MARKER in result.stdout
            else BackendFailure.INTERNAL
        )
        assert captured.value.failure is expected


def test_nonzero_worker_exit_uses_parent_failure_mapping(tmp_path: Path) -> None:
    """The public exchange path never attempts to read output after failure."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    runner = _RecordingRunner(_WorkerResult(124, "", ""))

    with pytest.raises(TimeoutError, match="timed out"):
        run_local_proposal_request(runner, model, "repair exactly")


def test_bounded_proposal_reader_rejects_missing_oversized_and_non_utf8(
    tmp_path: Path,
) -> None:
    """Worker output must be one bounded, readable regular UTF-8 file."""
    with pytest.raises(RuntimeError, match="bounded regular file"):
        _read_bounded_proposal(tmp_path / "missing.json")

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (_MAX_PROPOSAL_BYTES + 1))
    with pytest.raises(RuntimeError, match="bounded regular file"):
        _read_bounded_proposal(oversized)

    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"\xff")
    with pytest.raises(RuntimeError, match="not readable UTF-8"):
        _read_bounded_proposal(invalid)


def test_exchange_rejects_invalid_inputs_before_worker_use(tmp_path: Path) -> None:
    """Legacy argument validation order remains stable after runtime admission."""
    runner = _RecordingRunner()
    missing = tmp_path / "missing.gguf"
    with pytest.raises(FileNotFoundError, match="local GGUF"):
        run_local_proposal_request(runner, missing, "repair")

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    for request in (" ", "x" * 262_145):
        with pytest.raises(ValueError, match="proposal prompt"):
            run_local_proposal_request(runner, model, request)

    envelope = _envelope("different request")
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_local_proposal_request(
            runner,
            model,
            "repair",
            contract=_contract(),
            envelope=envelope,
        )
    with pytest.raises(ValueError, match="does not match"):
        run_local_proposal_request(runner, model, "repair", envelope=envelope)
