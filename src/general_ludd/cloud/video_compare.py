"""Video download and frame comparison for game E2E fidelity testing.

Downloads reference gameplay videos from YouTube, extracts frames,
and compares against AI-generated game frames using SSIM and motion analysis.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)
Frame = NDArray[np.uint8]

_HAS_SKIMAGE: bool
try:
    from skimage.metrics import structural_similarity  # type: ignore[import-not-found]

    _HAS_SKIMAGE = True
except ImportError:  # pragma: no cover
    structural_similarity = None
    _HAS_SKIMAGE = False

_HAS_CV2: bool
try:
    import cv2  # type: ignore[import-not-found]

    _HAS_CV2 = True
except ImportError:  # pragma: no cover
    cv2 = None
    _HAS_CV2 = False

_HAS_YTDLP: bool
try:
    import yt_dlp  # type: ignore[import-untyped]

    _HAS_YTDLP = True
except ImportError:  # pragma: no cover
    yt_dlp = None
    _HAS_YTDLP = False

@dataclass(frozen=True)
class ReferenceVideoSpec:
    """Auditable, bounded use of an online gameplay reference."""

    game_name: str
    source_url: str
    video_id: str
    clip_start_seconds: float = 0.0
    clip_duration_seconds: float = 12.0
    sample_frame_count: int = 30
    sample_interval_seconds: float = 0.4
    comparison_threshold: float = 0.35
    redistribution_allowed: bool = False

    @property
    def cache_filename(self) -> str:
        """Stable user-cache filename; clips are never checked into the repository."""
        return f"{self.game_name}-{self.video_id}.mp4"


@dataclass(frozen=True)
class ReferenceComparisonResult:
    """Comparison metrics carrying their source and sampling provenance."""

    game_name: str
    source_url: str
    source_video_id: str
    cache_path: str
    cache_status: str
    network_used: bool
    generated_frame_count: int
    reference_frame_count: int
    compared_frame_count: int
    threshold: float
    mean_ssim: float
    motion_correlation: float
    per_frame_ssim: tuple[float, ...]
    passed: bool


def _reference_spec(game_name: str, video_id: str) -> ReferenceVideoSpec:
    return ReferenceVideoSpec(
        game_name=game_name,
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
    )


REFERENCE_VIDEO_SPECS: dict[str, ReferenceVideoSpec] = {
    "doom_e1m1_hallway": _reference_spec("doom_e1m1_hallway", "YUU7d93IUBE"),
    "quake_dm6_arena": _reference_spec("quake_dm6_arena", "h_RmqpBk7bU"),
    "wipeout_racing": _reference_spec("wipeout_racing", "ZnTwZr6r0Hk"),
    "descent_tunnel": _reference_spec("descent_tunnel", "GbWtHn3w8vA"),
    "rogue_dungeon": _reference_spec("rogue_dungeon", "wFikvjM5H1s"),
}

REFERENCE_VIDEOS: dict[str, str] = {
    game_name: spec.source_url for game_name, spec in REFERENCE_VIDEO_SPECS.items()
}


def _ensure_yt_dlp() -> None:
    if not _HAS_YTDLP:
        raise ImportError("yt-dlp is required for video download — install with: pip install yt-dlp")


def _ensure_cv2() -> None:
    if not _HAS_CV2:
        raise ImportError(
            "opencv-python (cv2) is required for video processing — install with: pip install opencv-python"
        )


def download_youtube_video(
    url: str,
    output_path: str | Path,
    *,
    clip_start_seconds: float = 0.0,
    clip_duration_seconds: float | None = None,
) -> str:
    """Download a YouTube video using yt-dlp Python API.

    Args:
        url: YouTube video URL.
        output_path: Directory or file path for the downloaded video.
        clip_start_seconds: First source second to retain.
        clip_duration_seconds: Optional bounded duration; omitted only for legacy callers.

    Returns:
        Absolute path to the downloaded video file.

    Raises:
        ImportError: If yt-dlp is not installed.
        RuntimeError: If download fails.
    """
    _ensure_yt_dlp()
    out = Path(output_path)
    if out.suffix:
        out_dir = str(out.parent)
        out_template = out.stem
    else:
        out_dir = str(out)
        out_template = "%(title)s.%(ext)s"

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict[str, Any] = {
        "outtmpl": str(out_dir_path / f"{out_template}.%(ext)s"),
        "format": "best[height<=720]",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "noplaylist": True,
    }
    if clip_start_seconds < 0:
        raise ValueError("clip_start_seconds must be non-negative")
    if clip_duration_seconds is not None:
        if clip_duration_seconds <= 0:
            raise ValueError("clip_duration_seconds must be positive")
        clip_end_seconds = clip_start_seconds + clip_duration_seconds
        ydl_opts["download_sections"] = f"*{clip_start_seconds:.3f}-{clip_end_seconds:.3f}"
        ydl_opts["force_keyframes_at_cuts"] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise RuntimeError("yt-dlp returned no info for url")
            video_path = str(out_dir_path / f"{out_template}.mp4")
            if not os.path.exists(video_path):
                candidates = sorted(
                    out_dir_path.glob("*"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    video_path = str(candidates[0])
            return video_path
    except Exception as exc:
        raise RuntimeError(f"Failed to download video from {url}: {exc}") from exc


def extract_frames(
    video_path: str | Path,
    num_frames: int = 30,
    interval: float = 1.0,
) -> list[Frame]:
    """Extract frames from a video file at regular intervals.

    Args:
        video_path: Path to the video file.
        num_frames: Maximum number of frames to extract.
        interval: Time interval in seconds between extracted frames.

    Returns:
        List of frames as numpy arrays (H, W, 3) in RGB.
    """
    _ensure_cv2()
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0

    frame_interval = max(1, int(fps * interval))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    frames: list[Frame] = []
    frame_idx = 0
    extracted = 0

    while extracted < num_frames and frame_idx < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        extracted += 1
        frame_idx += frame_interval

    cap.release()
    return frames


def resize_frames(
    frames: list[Frame],
    target_size: tuple[int, int] = (800, 600),
) -> list[Frame]:
    """Resize frames to a consistent target size for comparison.

    Args:
        frames: List of frames as numpy arrays.
        target_size: (width, height) tuple.

    Returns:
        List of resized frames.
    """
    _ensure_cv2()
    resized: list[Frame] = []
    for frame in frames:
        resized.append(cv2.resize(frame, target_size))
    return resized


def compute_ssim(frame_a: Frame, frame_b: Frame) -> float:
    """Compute Structural Similarity Index between two frames.

    Uses skimage if available, falls back to pixel-difference approximation.

    Args:
        frame_a: First frame as numpy array.
        frame_b: Second frame as numpy array.

    Returns:
        SSIM value in [0, 1] where 1.0 is identical.
    """
    if frame_a.shape != frame_b.shape:
        return 0.0

    if _HAS_SKIMAGE and frame_a.ndim == 3 and frame_a.shape[2] == 3:
        try:
            val: float = structural_similarity(
                frame_a.astype(np.float64),
                frame_b.astype(np.float64),
                data_range=255.0,
                channel_axis=2,
                win_size=min(7, min(frame_a.shape[0], frame_a.shape[1]) or 7),
            )
            return val
        except (ValueError, RuntimeError):
            pass

    diff = frame_a.astype(np.float64) - frame_b.astype(np.float64)
    mse = np.mean(diff**2)
    if mse == 0:
        return 1.0
    max_val = max(frame_a.max(), frame_b.max(), 1.0)
    return float(1.0 / (1.0 + mse / max_val))


def compute_motion_signature(
    frames: list[Frame],
    window: int = 5,
) -> list[float]:
    """Capture motion patterns across a sequence of frames.

    Computes frame-to-frame differences over a sliding window to produce
    a motion intensity signature. High values indicate fast motion.

    Args:
        frames: List of frames as numpy arrays.
        window: Sliding window size for motion averaging.

    Returns:
        List of motion intensity values, one per frame (fewer than input
        due to windowing).
    """
    if len(frames) < window:
        return []

    signatures: list[float] = []
    for i in range(len(frames) - window):
        diffs: list[float] = []
        for j in range(window - 1):
            idx_a = i + j
            idx_b = i + j + 1
            diff_val = np.mean(np.abs(frames[idx_a].astype(np.float64) - frames[idx_b].astype(np.float64)))
            diffs.append(float(diff_val))
        signatures.append(float(np.mean(diffs)))
    return signatures


def motion_correlation(sig_a: list[float], sig_b: list[float]) -> float:
    """Compute correlation between two motion signatures.

    Args:
        sig_a: First motion signature.
        sig_b: Second motion signature.

    Returns:
        Correlation coefficient in [-1, 1].
    """
    if len(sig_a) < 2 or len(sig_b) < 2:
        return 0.0
    min_len = min(len(sig_a), len(sig_b))
    return float(np.corrcoef(sig_a[:min_len], sig_b[:min_len])[0, 1])


def compare_gameplay(
    gen_frames: list[Frame],
    ref_frames: list[Frame],
    threshold: float = 0.35,
) -> dict[str, Any]:
    """Compare AI-generated game frames against reference gameplay frames.

    Computes per-frame SSIM, mean SSIM, motion correlation, and pass/fail.

    Args:
        gen_frames: Frames captured from the generated game.
        ref_frames: Frames extracted from reference gameplay video.
        threshold: Minimum mean SSIM to pass.

    Returns:
        Dictionary with mean_ssim, per_frame_ssim, motion_correlation,
        pass_threshold, and frame_count.
    """
    if not gen_frames or not ref_frames:
        return {
            "mean_ssim": 0.0,
            "per_frame_ssim": [],
            "motion_correlation": 0.0,
            "pass_threshold": False,
            "frame_count": 0,
            "pass": False,
        }

    min_len = min(len(gen_frames), len(ref_frames))
    gen_resized = resize_frames(gen_frames[:min_len])
    ref_resized = resize_frames(ref_frames[:min_len])

    per_frame_ssim: list[float] = []
    for i in range(min_len):
        per_frame_ssim.append(compute_ssim(gen_resized[i], ref_resized[i]))

    mean_ssim = float(np.mean(per_frame_ssim)) if per_frame_ssim else 0.0

    gen_motion = compute_motion_signature(gen_resized, window=5)
    ref_motion = compute_motion_signature(ref_resized, window=5)
    mc = motion_correlation(gen_motion, ref_motion) if gen_motion and ref_motion else 0.0

    return {
        "mean_ssim": round(mean_ssim, 4),
        "per_frame_ssim": [round(v, 4) for v in per_frame_ssim],
        "motion_correlation": round(mc, 4),
        "pass_threshold": mean_ssim >= threshold,
        "frame_count": min_len,
        "pass": mean_ssim >= threshold,
    }


def compare_gameplay_to_reference(
    game_name: str,
    generated_frames: list[Frame],
    cache_dir: str | Path,
    *,
    allow_network: bool = False,
) -> ReferenceComparisonResult:
    """Compare generated frames to an approved, bounded reference clip.

    The cache is read-only by default. Network access must be explicitly enabled,
    and even then only the manifest's short clip window is requested. Cached media
    remains outside the repository and is marked non-redistributable by the spec.
    """
    try:
        source = REFERENCE_VIDEO_SPECS[game_name]
    except KeyError as exc:
        raise ValueError(f"No approved reference video for game: {game_name}") from exc

    cache_path = Path(cache_dir) / source.cache_filename
    network_used = False
    cache_status = "cached" if cache_path.is_file() else "missing"
    resolved_path: Path | None = cache_path if cache_status == "cached" else None

    if resolved_path is None and allow_network:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        downloaded_path = download_youtube_video(
            source.source_url,
            cache_path,
            clip_start_seconds=source.clip_start_seconds,
            clip_duration_seconds=source.clip_duration_seconds,
        )
        resolved_path = Path(downloaded_path)
        network_used = True
        cache_status = "downloaded"

    if resolved_path is None:
        return ReferenceComparisonResult(
            game_name=game_name,
            source_url=source.source_url,
            source_video_id=source.video_id,
            cache_path=str(cache_path),
            cache_status=cache_status,
            network_used=network_used,
            generated_frame_count=len(generated_frames),
            reference_frame_count=0,
            compared_frame_count=0,
            threshold=source.comparison_threshold,
            mean_ssim=0.0,
            motion_correlation=0.0,
            per_frame_ssim=(),
            passed=False,
        )

    reference_frames = extract_frames(
        resolved_path,
        num_frames=source.sample_frame_count,
        interval=source.sample_interval_seconds,
    )
    comparison = compare_gameplay(
        generated_frames,
        reference_frames,
        threshold=source.comparison_threshold,
    )
    per_frame_ssim = tuple(float(value) for value in comparison["per_frame_ssim"])
    return ReferenceComparisonResult(
        game_name=game_name,
        source_url=source.source_url,
        source_video_id=source.video_id,
        cache_path=str(resolved_path),
        cache_status=cache_status,
        network_used=network_used,
        generated_frame_count=len(generated_frames),
        reference_frame_count=len(reference_frames),
        compared_frame_count=int(comparison["frame_count"]),
        threshold=source.comparison_threshold,
        mean_ssim=float(comparison["mean_ssim"]),
        motion_correlation=float(comparison["motion_correlation"]),
        per_frame_ssim=per_frame_ssim,
        passed=bool(comparison["pass"]),
    )


__all__ = [
    "REFERENCE_VIDEOS",
    "REFERENCE_VIDEO_SPECS",
    "ReferenceComparisonResult",
    "ReferenceVideoSpec",
    "compare_gameplay",
    "compare_gameplay_to_reference",
    "compute_motion_signature",
    "compute_ssim",
    "download_youtube_video",
    "extract_frames",
    "motion_correlation",
    "resize_frames",
]
