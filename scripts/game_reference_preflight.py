#!/usr/bin/env python3
"""Acquire or verify every approved FPS reference before Azure can start."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from general_ludd.cloud.video_compare import (
    REFERENCE_VIDEO_SPECS,
    preflight_reference_videos,
)


GAME_NAMES = tuple(REFERENCE_VIDEO_SPECS)
_DUPLICATE_NATIVE_MARKERS = (
    "implemented in both",
    "duplicates must be removed",
    "spurious casting failures",
)


def _check_runtime_imports() -> None:
    """Fail when the real game runtime loads conflicting native libraries."""
    environment = dict(os.environ)
    environment.update(
        {
            "PYGAME_HIDE_SUPPORT_PROMPT": "1",
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", "import general_ludd.cloud.game_e2e"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown import failure"
        raise RuntimeError(f"game runtime import failed: {detail}")
    diagnostic = completed.stderr.lower()
    if any(marker in diagnostic for marker in _DUPLICATE_NATIVE_MARKERS):
        raise RuntimeError(
            "game runtime import loaded a duplicate native runtime; "
            "Pygame and the video backend must not load competing SDL libraries"
        )


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
    _check_runtime_imports()
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
