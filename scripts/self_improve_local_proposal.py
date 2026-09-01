#!/usr/bin/env python3
"""Run one local proposal decode inside a bounded, parent-owned process."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from general_ludd.self_improve.codex_comparison import (
    LocalProposalGateway,
    ProposalManifest,
)

_MAX_PROMPT_BYTES = 262_144


class _ProposalGateway(Protocol):
    """Minimal proposal inference interface used by the owned worker."""

    def propose(self, prompt: str) -> ProposalManifest:
        """Generate one validated proposal."""


_GatewayFactory = Callable[[Path], _ProposalGateway]


def run_worker(
    exchange_dir: Path,
    model_path: Path,
    *,
    gateway_factory: _GatewayFactory = LocalProposalGateway,
) -> Path:
    """Decode one proposal and publish it atomically inside the exchange directory."""
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
        prompt = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"prompt is not readable UTF-8: {exc}") from exc
    if not prompt.strip():
        raise ValueError("prompt must not be empty")

    print(
        f"SELF_IMPROVE_LOCAL_PROPOSAL_START model={model_path.name} "
        f"prompt_bytes={len(prompt.encode('utf-8'))}",
        flush=True,
    )
    proposal = gateway_factory(model_path).propose(prompt)
    serialized = proposal.to_json()
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
            handle.write(serialized)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, proposal_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(
        f"SELF_IMPROVE_LOCAL_PROPOSAL_END output_bytes={proposal_path.stat().st_size}",
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
