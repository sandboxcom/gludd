"""Parent-owned exchange boundary for one local proposal worker."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Final, NoReturn, Protocol

from general_ludd.self_improve._callback_compat import validated_timeout_seconds
from general_ludd.self_improve.codex_comparison import (
    LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
    ProposalContract,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalEnvelope,
)
from general_ludd.self_improve.managed_mutation import _write_atomic_temp
from general_ludd.self_improve.managed_runner import _validation_retry_feedback
from general_ludd.self_improve.model_candidates import (
    LOCAL_PROPOSAL_INFRASTRUCTURE_ERROR_MARKER,
    BackendFailure,
    BackendInfrastructureError,
)

_MAX_TASK_BYTES: Final = 262_144
_MAX_PROPOSAL_BYTES: Final = 1_310_720


class _ObservableResult(Protocol):
    """Result fields consumed from the observable Make boundary."""

    @property
    def returncode(self) -> int:
        """Return the worker exit status."""

    @property
    def stdout(self) -> str:
        """Return bounded standard output."""

    @property
    def stderr(self) -> str:
        """Return bounded standard error."""


class _ObservableRunner(Protocol):
    """Minimal parent-owned process boundary used by local inference."""

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: float,
    ) -> _ObservableResult:
        """Run one observable Make target and return bounded evidence."""


def _publish_exchange_text(path: Path, text: str, suffix: str) -> None:
    temporary = _write_atomic_temp(path, text, 0o600, suffix)
    os.replace(temporary, path)


def _request_protocol(
    contract: ProposalContract | None,
    envelope: ManagedCandidateProposalEnvelope | None,
) -> str | None:
    if contract is not None:
        return contract.proposal_protocol
    if envelope is not None:
        return ProposalContract.from_json(
            envelope.request_contract_json
        ).proposal_protocol
    return None


def _raise_worker_failure(
    result: _ObservableResult,
    contract: ProposalContract | None,
    envelope: ManagedCandidateProposalEnvelope | None,
) -> NoReturn:
    if result.returncode == 124:
        raise TimeoutError("local proposal worker timed out")
    if result.returncode == 3:
        marker = LOCAL_PROPOSAL_INFRASTRUCTURE_ERROR_MARKER + " "
        output = result.stdout.splitlines() + result.stderr.splitlines()
        if any(line.startswith(marker) for line in output):
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        raise BackendInfrastructureError(BackendFailure.INTERNAL)
    marker = LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.error_marker + " "
    if any(line.startswith(marker) for line in result.stdout.splitlines()):
        feedback = _validation_retry_feedback(
            result.stdout,
            proposal_protocol=_request_protocol(contract, envelope),
        )
        raise ValueError(feedback)
    diagnostic = result.stderr.strip()[:300]
    suffix = f" {diagnostic}" if diagnostic else ""
    raise RuntimeError(f"local proposal worker failed rc={result.returncode}{suffix}")


def _read_bounded_proposal(proposal_path: Path) -> str:
    if (
        proposal_path.is_symlink()
        or not proposal_path.is_file()
        or proposal_path.stat().st_size > _MAX_PROPOSAL_BYTES
    ):
        raise RuntimeError("local proposal worker did not publish one bounded regular file")
    try:
        return proposal_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"local proposal output is not readable UTF-8: {exc}") from exc


def run_local_proposal_request(
    runner: _ObservableRunner,
    model_path: Path,
    request: str,
    *,
    contract: ProposalContract | None = None,
    envelope: ManagedCandidateProposalEnvelope | None = None,
    timeout_seconds: float = 300.0,
) -> str:
    """Run one bounded request through one isolated parent-owned Make worker."""
    if not model_path.is_file():
        raise FileNotFoundError(f"local GGUF is not readable: {model_path}")
    if not request.strip() or len(request.encode("utf-8")) > _MAX_TASK_BYTES:
        raise ValueError(f"proposal prompt must contain 1..{_MAX_TASK_BYTES} bytes")
    if contract is not None and envelope is not None:
        raise ValueError("proposal contract and envelope are mutually exclusive")
    if envelope is not None and envelope.request_text != request:
        raise ValueError("proposal envelope request does not match prompt bytes")
    timeout = validated_timeout_seconds(timeout_seconds)

    with tempfile.TemporaryDirectory(prefix="gludd-self-improve-proposal-") as raw:
        exchange = Path(raw)
        prompt_path = exchange / "prompt.txt"
        proposal_path = exchange / "proposal.json"
        contract_path = exchange / "contract.json"
        envelope_path = exchange / "envelope.json"
        _publish_exchange_text(prompt_path, request, ".prompt-tmp")
        variables = {
            "SELF_IMPROVE_MODEL_PATH": str(model_path),
            "SELF_IMPROVE_PROMPT_FILE": str(prompt_path),
            "SELF_IMPROVE_PROPOSAL_FILE": str(proposal_path),
        }
        if contract is not None:
            _publish_exchange_text(contract_path, contract.to_json(), ".contract-tmp")
            variables["SELF_IMPROVE_CONTRACT_FILE"] = str(contract_path)
        if envelope is not None:
            _publish_exchange_text(envelope_path, envelope.to_json(), ".envelope-tmp")
            variables["SELF_IMPROVE_ENVELOPE_FILE"] = str(envelope_path)
        result = runner.run_observable(
            "self-improve-local-proposal", variables, timeout=timeout
        )
        if result.returncode != 0:
            _raise_worker_failure(result, contract, envelope)
        return _read_bounded_proposal(proposal_path)
