#!/usr/bin/env python3
"""Acquire or verify every approved FPS reference before Azure can start."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path

from general_ludd.cloud.video_compare import (
    REFERENCE_VIDEO_SPECS,
    preflight_reference_videos,
)


GAME_NAMES = tuple(REFERENCE_VIDEO_SPECS)


def _stream_event(name: str, payload: Mapping[str, object]) -> None:
    game_name = payload.get("game_name", "unknown")
    details = " ".join(
        f"{key}={value}"
        for key, value in payload.items()
        if key != "game_name"
    )
    print(
        f"[game-reference-preflight] {name} game={game_name} {details}".rstrip(),
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--allow-network", required=True, choices=("0", "1"))
    parser.add_argument("--validate-only", required=True, choices=("0", "1"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration or stream provenance for every ready clip."""
    arguments = _parser().parse_args(argv)
    allow_network = arguments.allow_network == "1"
    if arguments.validate_only == "1":
        print(
            "GAME_REFERENCE_PREFLIGHT_CONFIG_OK "
            f"cache_dir={arguments.cache_dir} allow_network={int(allow_network)}",
            flush=True,
        )
        return 0

    validations = preflight_reference_videos(
        GAME_NAMES,
        arguments.cache_dir,
        allow_network=allow_network,
        event_reporter=_stream_event,
    )
    print(
        "GAME_REFERENCE_PREFLIGHT_OK "
        f"references={len(validations)} cache_dir={arguments.cache_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
