"""Hosted-CI branch contracts for the game fidelity pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from types import ModuleType

import numpy as np
import pytest

import general_ludd.cloud.game_e2e as game_e2e
from general_ludd.cloud.game_e2e import AzureGameE2E, Frame, FrameComparator, GameSpec
from general_ludd.cloud.video_compare import ReferenceComparisonResult


def _game_spec(*, reference_url: str | None = None) -> GameSpec:
    """Build the smallest valid fidelity-test contract."""
    return GameSpec(
        name="hosted-coverage",
        genre="platformer",
        description="A deterministic hosted-CI game.",
        expected_frames=1,
        similarity_threshold=0.4,
        prompt_template="Generate {description}",
        reference_video_url=reference_url,
    )


def _frame(value: int = 0) -> Frame:
    """Return one small RGB frame with a stable dtype."""
    return np.full((8, 8, 3), value, dtype=np.uint8)


@pytest.mark.parametrize(
    ("attribute", "helper", "message"),
    [
        ("pygame", game_e2e._require_pygame, "pygame is required"),
        ("cv2", game_e2e._require_cv2, "opencv-python"),
    ],
)
def test_optional_game_runtimes_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    helper: object,
    message: str,
) -> None:
    """Missing optional runtimes must raise an actionable import error."""
    monkeypatch.setattr(game_e2e, attribute, None)
    with pytest.raises(ImportError, match=message):
        assert callable(helper)
        helper()


def test_ssim_falls_back_after_skimage_rejects_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """A skimage edge failure must retain deterministic pixel comparison."""

    def reject_frames(
        frame_a: object,
        frame_b: object,
        *,
        data_range: float,
        channel_axis: int,
        win_size: int,
    ) -> float:
        del frame_a, frame_b, data_range, channel_axis, win_size
        raise ValueError("unsupported frame window")

    monkeypatch.setattr(game_e2e, "structural_similarity", reject_frames)

    assert FrameComparator.compute_ssim(_frame(), _frame()) == 1.0
    score = FrameComparator.compute_ssim(_frame(), _frame(255))
    assert 0.0 < score < 1.0


class _Capture:
    """Minimal cv2 capture double with explicit release ownership."""

    def __init__(self, *, opened: bool, frames: list[Frame]) -> None:
        self._opened = opened
        self._frames: Iterator[Frame] = iter(frames)
        self.released = False

    def isOpened(self) -> bool:
        """Return the configured open state."""
        return self._opened

    def read(self) -> tuple[bool, Frame | None]:
        """Return one frame, then the cv2 end-of-stream marker."""
        try:
            return True, next(self._frames)
        except StopIteration:
            return False, None

    def release(self) -> None:
        """Record that the capture owner released the video handle."""
        self.released = True


class _Cv2Module(ModuleType):
    """Typed module-shaped cv2 double for the optional-runtime seam."""

    COLOR_BGR2RGB = 1

    def __init__(self, capture: _Capture) -> None:
        super().__init__("cv2")
        self._capture = capture

    def VideoCapture(self, _path: str) -> _Capture:
        """Return the single owned capture double."""
        return self._capture

    @staticmethod
    def cvtColor(frame: Frame, _mode: int) -> Frame:
        """Return the already-RGB fixture frame."""
        return frame


def _cv2_module(capture: _Capture) -> ModuleType:
    """Build a module-shaped cv2 double for the optional-runtime seam."""
    return _Cv2Module(capture)


def test_reference_video_open_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable reference clip must fail before frame processing."""
    capture = _Capture(opened=False, frames=[])
    monkeypatch.setattr(game_e2e, "cv2", _cv2_module(capture))

    with pytest.raises(FileNotFoundError, match="Cannot open video"):
        FrameComparator.load_reference_frames("missing.avi")


def test_reference_video_frames_are_converted_and_released(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful extraction must close the cv2 capture it acquired."""
    expected = _frame(12)
    capture = _Capture(opened=True, frames=[expected])
    monkeypatch.setattr(game_e2e, "cv2", _cv2_module(capture))

    frames = FrameComparator.load_reference_frames("reference.avi")

    assert frames == [expected]
    assert capture.released is True


def _configure_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    valid: bool = True,
    frames: list[Frame] | None = None,
) -> AzureGameE2E:
    """Configure a pipeline without model, filesystem, or pygame side effects."""
    pipeline = AzureGameE2E()

    def generate_game(_spec: GameSpec) -> str:
        return "import pygame\n"

    def validate_game_code(_code: str, _spec: GameSpec | None = None) -> bool:
        return valid

    def save_game(_code: str, _path: str) -> None:
        return None

    def run_inline(
        _game_path: str,
        _num_frames: int,
        *,
        input_script: tuple[game_e2e.GameInputEvent, ...] = (),
    ) -> list[Frame]:
        del input_script
        return list(frames or [_frame()])

    monkeypatch.setattr(pipeline.generator, "generate_game", generate_game)
    monkeypatch.setattr(game_e2e.GameGenerator, "validate_game_code", staticmethod(validate_game_code))
    monkeypatch.setattr(game_e2e.GameGenerator, "save_game", staticmethod(save_game))
    monkeypatch.setattr(pipeline.runner, "run_headless_inline", run_inline)
    return pipeline


def test_invalid_generated_game_returns_contract_error_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid generated code must stop before execution and still clean up."""
    pipeline = _configure_pipeline(monkeypatch, valid=False)
    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        cleaned = True

    monkeypatch.setattr(pipeline.runner, "cleanup", cleanup)

    result = pipeline.run_full_test(_game_spec())

    assert result.code_generated is True
    assert result.code_valid is False
    assert result.errors == ["Generated game failed its required controls/menu contract"]
    assert cleaned is True


def test_missing_pygame_is_reported_without_running_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator must report a missing runtime and preserve cleanup."""
    pipeline = _configure_pipeline(monkeypatch)
    monkeypatch.setattr(game_e2e, "_HAS_PYGAME", False)

    result = pipeline.run_full_test(_game_spec())

    assert result.game_ran is False
    assert result.errors == ["pygame not installed — cannot run game"]


def test_generated_frames_self_compare_without_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hermetic run without a reference clip must compare its own frames."""
    pipeline = _configure_pipeline(monkeypatch, frames=[_frame(33)])
    monkeypatch.setattr(game_e2e, "_HAS_PYGAME", True)

    result = pipeline.run_full_test(_game_spec())

    assert result.game_ran is True
    assert result.frames_captured == 1
    assert result.mean_ssim == 1.0
    assert result.comparison_pass is True


def test_missing_reference_cache_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A disabled-network cache miss must be visible in the E2E result."""
    pipeline = _configure_pipeline(monkeypatch)
    monkeypatch.setattr(game_e2e, "_HAS_PYGAME", True)

    def missing_reference(
        game_name: str,
        generated_frames: list[Frame],
        cache_dir: str,
        *,
        allow_network: bool,
    ) -> ReferenceComparisonResult:
        del generated_frames, cache_dir, allow_network
        return ReferenceComparisonResult(
            game_name=game_name,
            source_url="https://example.invalid/reference",
            source_video_id="reference",
            cache_path="",
            cache_status="missing",
            network_used=False,
            generated_frame_count=1,
            reference_frame_count=0,
            compared_frame_count=0,
            threshold=0.4,
            mean_ssim=0.0,
            motion_correlation=0.0,
            per_frame_ssim=(),
            passed=False,
        )

    monkeypatch.setattr(game_e2e, "compare_gameplay_to_reference", missing_reference)

    result = pipeline.run_full_test(_game_spec(reference_url="https://example.invalid/reference"))

    assert result.reference_cache_status == "missing"
    assert result.errors == ["Reference clip is not cached; network retrieval is disabled"]


def test_reference_comparison_exception_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reference comparison faults must not escape or skip owner cleanup."""
    pipeline = _configure_pipeline(monkeypatch)
    monkeypatch.setattr(game_e2e, "_HAS_PYGAME", True)

    def fail_comparison(
        game_name: str,
        generated_frames: list[Frame],
        cache_dir: str,
        *,
        allow_network: bool,
    ) -> ReferenceComparisonResult:
        del game_name, generated_frames, cache_dir, allow_network
        raise RuntimeError("decode failed")

    monkeypatch.setattr(game_e2e, "compare_gameplay_to_reference", fail_comparison)

    result = pipeline.run_full_test(_game_spec(reference_url="https://example.invalid/reference"))

    assert result.errors == ["Reference comparison failed: decode failed"]
