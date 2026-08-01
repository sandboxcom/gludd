"""Video download and frame comparison for game E2E fidelity testing.

Downloads reference gameplay videos from YouTube, extracts frames,
and compares against AI-generated game frames using SSIM and motion analysis.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_HAS_SKIMAGE: bool
try:
    from skimage.metrics import structural_similarity  # type: ignore[import-untyped]

    _HAS_SKIMAGE = True
except ImportError:  # pragma: no cover
    structural_similarity = None  # type: ignore[assignment]
    _HAS_SKIMAGE = False

_HAS_CV2: bool
try:
    import cv2  # type: ignore[import-untyped]

    _HAS_CV2 = True
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]
    _HAS_CV2 = False

_HAS_YTDLP: bool
try:
    import yt_dlp  # type: ignore[import-untyped]

    _HAS_YTDLP = True
except ImportError:  # pragma: no cover
    yt_dlp = None  # type: ignore[assignment]
    _HAS_YTDLP = False

REFERENCE_VIDEOS: dict[str, str] = {
    "doom_e1m1_hallway": "https://www.youtube.com/watch?v=YUU7d93IUBE",
    "quake_dm6_arena": "https://www.youtube.com/watch?v=h_RmqpBk7bU",
    "wipeout_racing": "https://www.youtube.com/watch?v=ZnTwZr6r0Hk",
    "descent_tunnel": "https://www.youtube.com/watch?v=GbWtHn3w8vA",
    "rogue_dungeon": "https://www.youtube.com/watch?v=wFikvjM5H1s",
}


def _ensure_yt_dlp() -> None:
    if not _HAS_YTDLP:
        raise ImportError("yt-dlp is required for video download — install with: pip install yt-dlp")


def _ensure_cv2() -> None:
    if not _HAS_CV2:
        raise ImportError(
            "opencv-python (cv2) is required for video processing — install with: pip install opencv-python"
        )


def download_youtube_video(url: str, output_path: str | Path) -> str:
    """Download a YouTube video using yt-dlp Python API.

    Args:
        url: YouTube video URL.
        output_path: Directory or file path for the downloaded video.

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
    }

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
) -> list[np.ndarray]:
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

    frames: list[np.ndarray] = []
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
    frames: list[np.ndarray],
    target_size: tuple[int, int] = (800, 600),
) -> list[np.ndarray]:
    """Resize frames to a consistent target size for comparison.

    Args:
        frames: List of frames as numpy arrays.
        target_size: (width, height) tuple.

    Returns:
        List of resized frames.
    """
    _ensure_cv2()
    resized: list[np.ndarray] = []
    for frame in frames:
        resized.append(cv2.resize(frame, target_size))
    return resized


def compute_ssim(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
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
    frames: list[np.ndarray],
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
    gen_frames: list[np.ndarray],
    ref_frames: list[np.ndarray],
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


__all__ = [
    "REFERENCE_VIDEOS",
    "compare_gameplay",
    "compute_motion_signature",
    "compute_ssim",
    "download_youtube_video",
    "extract_frames",
    "motion_correlation",
    "resize_frames",
]
