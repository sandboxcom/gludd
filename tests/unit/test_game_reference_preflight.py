"""Standalone game-reference preflight CLI and Make contract tests."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import game_reference_preflight

ROOT = Path(__file__).resolve().parents[2]


def test_validate_only_checks_arguments_without_touching_media(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        game_reference_preflight,
        "preflight_reference_videos",
        lambda *args, **kwargs: pytest.fail("validation-only mode touched media"),
    )

    result = game_reference_preflight.main(
        [
            "--cache-dir",
            str(tmp_path),
            "--allow-network",
            "0",
            "--validate-only",
            "1",
        ]
    )

    assert result == 0
    assert "GAME_REFERENCE_PREFLIGHT_CONFIG_OK" in capsys.readouterr().out


def test_preflight_streams_each_verified_reference(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], Path, bool]] = []

    def fake_preflight(
        game_names: Iterable[str],
        cache_dir: Path,
        *,
        allow_network: bool,
        event_reporter: Callable[[str, Mapping[str, object]], object],
    ) -> dict[str, SimpleNamespace]:
        names = tuple(game_names)
        calls.append((names, cache_dir, allow_network))
        for name in names:
            event_reporter(
                "reference_ready",
                {
                    "game_name": name,
                    "cache_status": "verified",
                    "reference_frame_count": 8,
                    "object_sha256": f"sha-{name}",
                },
            )
        return {
            name: SimpleNamespace(
                game_name=name,
                cache_status="verified",
                reference_frame_count=8,
                object_sha256=f"sha-{name}",
                provenance_path=cache_dir / f"{name}.json",
            )
            for name in names
        }

    monkeypatch.setattr(
        game_reference_preflight,
        "preflight_reference_videos",
        fake_preflight,
    )

    result = game_reference_preflight.main(
        [
            "--cache-dir",
            str(tmp_path),
            "--allow-network",
            "1",
            "--validate-only",
            "0",
        ]
    )

    assert result == 0
    assert calls == [(game_reference_preflight.GAME_NAMES, tmp_path, True)]
    output = capsys.readouterr().out
    for name in game_reference_preflight.GAME_NAMES:
        assert f"reference_ready game={name}" in output
    assert "GAME_REFERENCE_PREFLIGHT_OK" in output


def test_make_target_has_a_network_free_behavioral_smoke(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            "make",
            "game-reference-preflight",
            "GAME_E2E_REFERENCE_NETWORK=0",
            f"GAME_E2E_REFERENCE_CACHE_DIR={tmp_path}",
            "GAME_E2E_REFERENCE_VALIDATE_ONLY=1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "GAME_REFERENCE_PREFLIGHT_CONFIG_OK" in completed.stdout
