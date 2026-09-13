"""Focused contract for the parent-owned local proposal exchange."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from general_ludd.self_improve.local_worker_request import (
    run_local_proposal_request,
)


class _RecordingRunner:
    def __init__(self) -> None:
        self.call: tuple[str, dict[str, str], float] | None = None

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: float,
    ) -> object:
        self.call = (target, variables, timeout)
        Path(variables["SELF_IMPROVE_PROPOSAL_FILE"]).write_text(
            '{"proposal":true}', encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout="complete", stderr="")


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
