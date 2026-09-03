#!/usr/bin/env python3
"""Run ordered local proposal decodes inside one bounded, parent-owned process."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from general_ludd.self_improve.codex_comparison import (
    CompactSpanProposal,
    LocalProposalGateway,
    ProposalContract,
    ProposalManifest,
    _decode_compact_span_proposal,
    decode_compact_span_batch,
    decode_prompt_batch,
    decode_proposal_batch,
    encode_compact_span_batch,
    encode_prompt_batch,
    encode_proposal_batch,
)

_MAX_PROMPT_BYTES = 262_144
_MAX_CONTRACT_BYTES = 196_608
_MAX_PROPOSAL_BYTES = 1_310_720

__all__ = (
    "_decode_compact_span_proposal",
    "decode_compact_span_batch",
    "decode_proposal_batch",
    "encode_compact_span_batch",
    "encode_prompt_batch",
    "main",
    "run_worker",
)


class _ProposalGateway(Protocol):
    """Minimal proposal inference interface used by the owned worker."""

    def propose(
        self,
        prompt: str,
        *,
        contract: ProposalContract | None = None,
    ) -> ProposalManifest | CompactSpanProposal:
        """Generate one validated proposal."""


_GatewayFactory = Callable[[Path], _ProposalGateway]


def run_worker(
    exchange_dir: Path,
    model_path: Path,
    *,
    gateway_factory: _GatewayFactory = LocalProposalGateway,
) -> Path:
    """Decode one request or an ordered batch and atomically publish its result."""
    if exchange_dir.is_symlink():
        raise ValueError("exchange directory must not be a symlink")
    exchange = exchange_dir.resolve(strict=True)
    if not exchange.is_dir():
        raise ValueError("exchange path must be a directory")
    prompt_path = exchange / "prompt.txt"
    proposal_path = exchange / "proposal.json"
    if prompt_path.is_symlink() or not prompt_path.is_file():
        raise ValueError("prompt must be one regular confined file")
    if proposal_path.exists() or proposal_path.is_symlink():
        raise ValueError("proposal output must not already exist")
    if prompt_path.stat().st_size > _MAX_PROMPT_BYTES:
        raise ValueError(f"prompt exceeds {_MAX_PROMPT_BYTES} bytes")
    try:
        request = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"prompt is not readable UTF-8: {exc}") from exc
    if not request.strip():
        raise ValueError("prompt must not be empty")

    contract_path = exchange / "contract.json"
    contract: ProposalContract | None = None
    if contract_path.is_symlink() or contract_path.exists():
        if contract_path.is_symlink() or not contract_path.is_file():
            raise ValueError("proposal contract must be one regular confined file")
        if contract_path.stat().st_size > _MAX_CONTRACT_BYTES:
            raise ValueError(f"proposal contract exceeds {_MAX_CONTRACT_BYTES} bytes")
        try:
            contract_raw = contract_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"proposal contract is not readable UTF-8: {exc}") from exc
        contract = ProposalContract.from_json(contract_raw)

    prompts, protocol_digest = decode_prompt_batch(request)
    gateway = gateway_factory(model_path)
    proposals: list[ProposalManifest | CompactSpanProposal] = []
    total = len(prompts)
    print(
        f"SELF_IMPROVE_LOCAL_PROPOSAL_START model={model_path.name} "
        f"shards={total} mode={'compact' if contract is not None else 'legacy'} "
        f"prompt_bytes={len(request.encode('utf-8'))}",
        flush=True,
    )
    for index, prompt in enumerate(prompts, start=1):
        print(
            "SELF_IMPROVE_PROMPT_SHARD_START "
            f"shard={index}/{total} "
            f"protocol_digest={protocol_digest or 'legacy'} "
            f"prompt_bytes={len(prompt.encode('utf-8'))}",
            flush=True,
        )
        try:
            proposal = (
                gateway.propose(prompt, contract=contract)
                if contract is not None
                else gateway.propose(prompt)
            )
        except BaseException:
            print(
                f"SELF_IMPROVE_PROMPT_SHARD_END shard={index}/{total} succeeded=false",
                flush=True,
            )
            raise
        proposals.append(proposal)
        print(
            f"SELF_IMPROVE_PROMPT_SHARD_END shard={index}/{total} succeeded=true",
            flush=True,
        )

    if contract is not None and contract.proposal_protocol.endswith("-v4"):
        if protocol_digest is None or not all(
            isinstance(proposal, CompactSpanProposal) for proposal in proposals
        ):
            raise ValueError("compact-v4 worker result does not match its batch contract")
        serialized = encode_compact_span_batch(
            [
                proposal
                for proposal in proposals
                if isinstance(proposal, CompactSpanProposal)
            ],
            protocol_digest=protocol_digest,
        )
    elif protocol_digest is None:
        if len(proposals) != 1 or not isinstance(proposals[0], ProposalManifest):
            raise ValueError("legacy worker result does not match its request contract")
        serialized = proposals[0].to_json()
    else:
        if not all(isinstance(proposal, ProposalManifest) for proposal in proposals):
            raise ValueError("compact-v3 worker result does not match its batch contract")
        serialized = encode_proposal_batch(
            [proposal for proposal in proposals if isinstance(proposal, ProposalManifest)],
            protocol_digest=protocol_digest,
        )
    if len(serialized.encode("utf-8")) > _MAX_PROPOSAL_BYTES:
        raise ValueError(f"proposal output exceeds {_MAX_PROPOSAL_BYTES} bytes")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=exchange,
            prefix=".proposal-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, proposal_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(
        "SELF_IMPROVE_LOCAL_PROPOSAL_END "
        f"shards={total} output_bytes={proposal_path.stat().st_size}",
        flush=True,
    )
    return proposal_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded local self-improvement proposal worker"
    )
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--proposal-file", required=True, type=Path)
    parser.add_argument("--model-path", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the worker CLI with bounded, observable failure diagnostics."""
    args = _parser().parse_args(argv)
    prompt_file = args.prompt_file
    proposal_file = args.proposal_file
    if (
        prompt_file.name != "prompt.txt"
        or proposal_file.name != "proposal.json"
        or prompt_file.parent.resolve() != proposal_file.parent.resolve()
    ):
        print(
            "SELF_IMPROVE_LOCAL_PROPOSAL_ERROR exchange paths are not canonical",
            file=sys.stderr,
            flush=True,
        )
        return 2
    try:
        run_worker(prompt_file.parent, args.model_path)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            f"SELF_IMPROVE_LOCAL_PROPOSAL_ERROR {str(exc)[:2000]}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
