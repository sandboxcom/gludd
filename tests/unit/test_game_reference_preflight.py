"""Standalone game-reference preflight CLI and Make contract tests."""

from __future__ import annotations

import subprocess
import tomllib
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
    monkeypatch.setattr(game_reference_preflight, "_check_runtime_imports", lambda: None)
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
    monkeypatch.setattr(game_reference_preflight, "_check_runtime_imports", lambda: None)
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


def test_runtime_import_smoke_rejects_duplicate_native_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["python", "-c", "import general_ludd.cloud.game_e2e"],
        returncode=0,
        stdout="",
        stderr=(
            "Class SDLApplication is implemented in both cv2/.dylibs/libSDL2 "
            "and /opt/homebrew/libSDL2. One of the duplicates must be removed."
        ),
    )
    monkeypatch.setattr(
        "scripts.game_reference_preflight.subprocess.run",
        lambda *args, **kwargs: completed,
    )

    with pytest.raises(RuntimeError, match="duplicate native runtime"):
        game_reference_preflight._check_runtime_imports()


def test_runtime_import_smoke_accepts_warning_free_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["python", "-c", "import general_ludd.cloud.game_e2e"],
        returncode=0,
        stdout="",
        stderr="",
    )
    monkeypatch.setattr(
        "scripts.game_reference_preflight.subprocess.run",
        lambda *args, **kwargs: completed,
    )

    game_reference_preflight._check_runtime_imports()


def test_game_extras_exclude_opencv_413_ffmpeg8_sdl_bundle() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional_dependencies = project["project"]["optional-dependencies"]

    for extra_name in ("game-e2e", "e2e-all"):
        assert "opencv-python-headless>=4.9.0,<4.13" in optional_dependencies[extra_name]


def test_game_extras_require_pillow_with_current_security_fixes() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional_dependencies = project["project"]["optional-dependencies"]

    for extra_name in ("game-e2e", "e2e-all"):
        assert "pillow>=12.3.0" in optional_dependencies[extra_name]


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
