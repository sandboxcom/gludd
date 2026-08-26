"""Video download and frame comparison for game E2E fidelity testing.

Downloads reference gameplay videos from YouTube, extracts frames,
and compares against AI-generated game frames using SSIM and motion analysis.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)
Frame = NDArray[np.uint8]
_MAX_REFERENCE_CLIP_OVERRUN_SECONDS = 2.0

class _StructuralSimilarity(Protocol):
    def __call__(
        self,
        frame_a: object,
        frame_b: object,
        *,
        data_range: float,
        channel_axis: int,
        win_size: int,
    ) -> float: ...


structural_similarity: _StructuralSimilarity | None
try:
    from skimage.metrics import structural_similarity as _skimage_structural_similarity
except ImportError:  # pragma: no cover
    structural_similarity = None
else:
    structural_similarity = cast(_StructuralSimilarity, _skimage_structural_similarity)
_HAS_SKIMAGE = structural_similarity is not None

cv2: ModuleType | None
try:
    import cv2 as _cv2
except ImportError:  # pragma: no cover
    cv2 = None
else:
    cv2 = _cv2
_HAS_CV2 = cv2 is not None

yt_dlp: ModuleType | None
try:
    import yt_dlp as _yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None
else:
    yt_dlp = _yt_dlp
_HAS_YTDLP = yt_dlp is not None

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
    approval_version: int = 1

    @property
    def cache_filename(self) -> str:
        """Stable user-cache filename; clips are never checked into the repository."""
        return f"{self.game_name}-{self.video_id}.mp4"

    @property
    def provenance_filename(self) -> str:
        """Sidecar committed last after a clip and decoded sample validate."""
        return f"{self.cache_filename}.provenance.json"


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


@dataclass(frozen=True)
class ReferenceCacheValidation:
    """Verified cache artifact that is safe to consume before Azure starts."""

    game_name: str
    cache_path: Path
    provenance_path: Path
    cache_status: str
    object_sha256: str
    decoded_frames_sha256: str
    reference_frame_count: int


def _reference_spec(game_name: str, video_id: str) -> ReferenceVideoSpec:
    return ReferenceVideoSpec(
        game_name=game_name,
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
    )


REFERENCE_VIDEO_SPECS: dict[str, ReferenceVideoSpec] = {
    "doom_e1m1_hallway": _reference_spec("doom_e1m1_hallway", "K0nlO87evhY"),
    "quake_dm6_arena": _reference_spec("quake_dm6_arena", "dPQO03UmicE"),
    "wipeout_racing": _reference_spec("wipeout_racing", "XASEBvDri4U"),
    "descent_tunnel": _reference_spec("descent_tunnel", "ofUvw5dXGO4"),
    "rogue_dungeon": _reference_spec("rogue_dungeon", "cZ7zWh_Ljr0"),
}

REFERENCE_VIDEOS: dict[str, str] = {
    game_name: spec.source_url for game_name, spec in REFERENCE_VIDEO_SPECS.items()
}


def _ensure_yt_dlp() -> ModuleType:
    if yt_dlp is None:
        raise ImportError("yt-dlp is required for video download — install with: pip install yt-dlp")
    return yt_dlp


def _ensure_cv2() -> ModuleType:
    if cv2 is None:
        raise ImportError(
            "opencv-python (cv2) is required for video processing — install with: pip install opencv-python"
        )
    return cv2


def download_youtube_video(
    url: str,
    output_path: str | Path,
    *,
    clip_start_seconds: float = 0.0,
    clip_duration_seconds: float | None = None,
    metadata_sink: Callable[[dict[str, object]], None] | None = None,
    progress_sink: Callable[[Mapping[str, object]], object] | None = None,
) -> str:
    """Download a bounded YouTube clip and surface selected-stream metadata.

    Args:
        url: YouTube video URL.
        output_path: Directory or file path for the downloaded video.
        clip_start_seconds: First source second to retain.
        clip_duration_seconds: Optional bounded duration; omitted only for legacy callers.
        metadata_sink: Optional receiver for sanitized selected-stream metadata.
        progress_sink: Optional receiver for sanitized yt-dlp progress fields.

    Returns:
        Absolute path to the downloaded video file.

    Raises:
        ImportError: If yt-dlp is not installed.
        RuntimeError: If download fails.
    """
    if clip_start_seconds < 0:
        raise ValueError("clip_start_seconds must be non-negative")
    if clip_duration_seconds is not None and clip_duration_seconds <= 0:
        raise ValueError("clip_duration_seconds must be positive")

    ytdlp_module = _ensure_yt_dlp()
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
    if progress_sink is not None:
        progress_fields = (
            "status",
            "downloaded_bytes",
            "total_bytes",
            "total_bytes_estimate",
            "speed",
            "eta",
            "fragment_index",
            "fragment_count",
        )

        def report_progress(raw: Mapping[str, object]) -> None:
            progress_sink(
                {
                    key: raw[key]
                    for key in progress_fields
                    if key in raw and raw[key] is not None
                }
            )

        ydl_opts["progress_hooks"] = [report_progress]
    if clip_duration_seconds is not None:
        clip_end_seconds = clip_start_seconds + clip_duration_seconds
        utils_module = getattr(ytdlp_module, "utils", None)
        download_range_func = getattr(utils_module, "download_range_func", None)
        if not callable(download_range_func):
            raise RuntimeError("yt-dlp download_range_func is unavailable")
        ydl_opts["download_ranges"] = download_range_func(
            [],
            [(clip_start_seconds, clip_end_seconds)],
        )
        ydl_opts["force_keyframes_at_cuts"] = True
        # The native HTTP downloader fetches the entire source before cutting.
        # ffmpeg seeks the remote stream and transfers only the requested range.
        ydl_opts["external_downloader"] = "ffmpeg"

    try:
        with ytdlp_module.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise RuntimeError("yt-dlp returned no info for url")
            requested_downloads = info.get("requested_downloads")
            selected = (
                requested_downloads[-1]
                if isinstance(requested_downloads, list) and requested_downloads
                else info
            )
            if not isinstance(selected, Mapping):
                selected = info
            if metadata_sink is not None:
                metadata_sink(
                    {
                        "format_id": selected.get("format_id", info.get("format_id", "unknown")),
                        "container": selected.get("ext", info.get("ext", "unknown")),
                        "video_codec": selected.get("vcodec", info.get("vcodec", "unknown")),
                        "pixel_format": selected.get("pix_fmt", "unknown"),
                        "width": selected.get("width", info.get("width")),
                        "height": selected.get("height", info.get("height")),
                        "fps": selected.get("fps", info.get("fps")),
                        "duration": info.get("duration"),
                        "uploader": info.get("uploader", "unknown"),
                        "channel": info.get("channel", "unknown"),
                        "channel_id": info.get("channel_id", "unknown"),
                        "license": info.get("license", "unknown"),
                        "yt_dlp_version": _yt_dlp_version(),
                    }
                )
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_frames(frames: Iterable[Frame]) -> str:
    digest = hashlib.sha256()
    for frame in frames:
        contiguous = np.ascontiguousarray(frame)
        digest.update(str(contiguous.dtype).encode("ascii"))
        digest.update(json.dumps(contiguous.shape).encode("ascii"))
        digest.update(contiguous.tobytes())
    return digest.hexdigest()


def _ffmpeg_version() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("ffmpeg is required before reference acquisition")
    completed = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
    if completed.returncode != 0 or not first_line:
        raise RuntimeError("ffmpeg version probe failed before reference acquisition")
    return first_line


def _video_duration_seconds(path: Path) -> float:
    """Return container duration using ffprobe, failing closed on invalid media."""
    executable = shutil.which("ffprobe")
    if executable is None:
        raise RuntimeError("ffprobe is required before reference acquisition")
    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    raw_duration = completed.stdout.strip()
    try:
        duration = float(raw_duration)
    except ValueError as error:
        raise RuntimeError("ffprobe returned an invalid reference duration") from error
    if completed.returncode != 0 or not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("ffprobe could not verify reference duration")
    return duration


def _yt_dlp_version() -> str:
    module = _ensure_yt_dlp()
    version_module = getattr(module, "version", None)
    return str(getattr(version_module, "__version__", "unknown"))


def _read_provenance(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid provenance sidecar: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("invalid provenance sidecar: expected a JSON object")
    return payload


def _decode_reference_frames(spec: ReferenceVideoSpec, path: Path) -> list[Frame]:
    frames = extract_frames(
        path,
        num_frames=spec.sample_frame_count,
        interval=spec.sample_interval_seconds,
    )
    if not frames:
        raise RuntimeError("reference clip decoded zero frames")
    shapes = {tuple(frame.shape) for frame in frames}
    if len(shapes) != 1:
        raise RuntimeError(f"reference clip frame dimensions changed: {sorted(shapes)}")
    return frames


def _validate_cached_reference(
    spec: ReferenceVideoSpec,
    cache_path: Path,
    provenance_path: Path,
) -> ReferenceCacheValidation:
    if not cache_path.is_file():
        raise RuntimeError("reference clip is missing")
    if not provenance_path.is_file():
        raise RuntimeError("reference provenance sidecar is missing")
    payload = _read_provenance(provenance_path)
    expected = {
        "schema_version": 1,
        "game_name": spec.game_name,
        "source_url": spec.source_url,
        "source_video_id": spec.video_id,
        "clip_start_seconds": spec.clip_start_seconds,
        "clip_duration_seconds": spec.clip_duration_seconds,
        "approval_version": spec.approval_version,
        "redistribution_allowed": spec.redistribution_allowed,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(f"provenance {key} mismatch")

    actual_duration = payload.get("clip_actual_duration_seconds")
    if (
        isinstance(actual_duration, bool)
        or not isinstance(actual_duration, (int, float))
        or not math.isfinite(float(actual_duration))
        or float(actual_duration) <= 0
        or float(actual_duration)
        > spec.clip_duration_seconds + _MAX_REFERENCE_CLIP_OVERRUN_SECONDS
    ):
        raise RuntimeError("provenance bounded clip duration is invalid")
    if payload.get("object_size_bytes") != cache_path.stat().st_size:
        raise RuntimeError("provenance object size mismatch")

    object_sha256 = _sha256_file(cache_path)
    if payload.get("object_sha256") != object_sha256:
        raise RuntimeError("object digest mismatch")
    frames = _decode_reference_frames(spec, cache_path)
    decoded_frames_sha256 = _sha256_frames(frames)
    if payload.get("decoded_frames_sha256") != decoded_frames_sha256:
        raise RuntimeError("decoded frame digest mismatch")
    if payload.get("decoded_frame_count") != len(frames):
        raise RuntimeError("decoded frame count mismatch")
    return ReferenceCacheValidation(
        game_name=spec.game_name,
        cache_path=cache_path,
        provenance_path=provenance_path,
        cache_status="verified",
        object_sha256=object_sha256,
        decoded_frames_sha256=decoded_frames_sha256,
        reference_frame_count=len(frames),
    )


def _acquire_reference(
    spec: ReferenceVideoSpec,
    cache_path: Path,
    provenance_path: Path,
    event_reporter: Callable[[str, Mapping[str, object]], object] | None = None,
) -> ReferenceCacheValidation:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temporary_video = cache_path.parent / f".{cache_path.stem}.{token}.tmp.mp4"
    temporary_sidecar = provenance_path.parent / f".{provenance_path.name}.{token}.tmp"
    metadata: dict[str, object] = {}
    downloaded_path: Path | None = None

    def report_progress(payload: Mapping[str, object]) -> None:
        if event_reporter is not None:
            event_reporter(
                "reference_acquisition_progress",
                {"game_name": spec.game_name, **payload},
            )

    try:
        ffmpeg_version = _ffmpeg_version()
        downloaded_path = Path(
            download_youtube_video(
                spec.source_url,
                temporary_video,
                clip_start_seconds=spec.clip_start_seconds,
                clip_duration_seconds=spec.clip_duration_seconds,
                metadata_sink=metadata.update,
                progress_sink=report_progress if event_reporter is not None else None,
            )
        )
        if not downloaded_path.is_file():
            raise RuntimeError("reference downloader did not produce a file")
        actual_duration = _video_duration_seconds(downloaded_path)
        maximum_duration = (
            spec.clip_duration_seconds + _MAX_REFERENCE_CLIP_OVERRUN_SECONDS
        )
        if actual_duration > maximum_duration:
            raise RuntimeError(
                "reference clip duration "
                f"{actual_duration:.3f}s exceeds bounded request maximum "
                f"{maximum_duration:.3f}s"
            )
        frames = _decode_reference_frames(spec, downloaded_path)
        object_sha256 = _sha256_file(downloaded_path)
        decoded_frames_sha256 = _sha256_frames(frames)
        first_shape = tuple(frames[0].shape)
        yt_dlp_version = str(metadata.pop("yt_dlp_version", "unknown"))
        payload: dict[str, object] = {
            "schema_version": 1,
            "approval_version": spec.approval_version,
            "game_name": spec.game_name,
            "source_url": spec.source_url,
            "source_video_id": spec.video_id,
            "clip_start_seconds": spec.clip_start_seconds,
            "clip_duration_seconds": spec.clip_duration_seconds,
            "clip_actual_duration_seconds": actual_duration,
            "object_size_bytes": downloaded_path.stat().st_size,
            "sample_frame_count": spec.sample_frame_count,
            "sample_interval_seconds": spec.sample_interval_seconds,
            "redistribution_allowed": spec.redistribution_allowed,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "object_sha256": object_sha256,
            "decoded_frames_sha256": decoded_frames_sha256,
            "decoded_frame_count": len(frames),
            "decoded_height": first_shape[0],
            "decoded_width": first_shape[1],
            "decoded_channels": first_shape[2] if len(first_shape) > 2 else 1,
            "yt_dlp_version": yt_dlp_version,
            "ffmpeg_version": ffmpeg_version,
            "opencv_version": str(getattr(cv2, "__version__", "unknown")),
            **metadata,
        }
        with temporary_sidecar.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(downloaded_path, cache_path)
        os.replace(temporary_sidecar, provenance_path)
        return ReferenceCacheValidation(
            game_name=spec.game_name,
            cache_path=cache_path,
            provenance_path=provenance_path,
            cache_status="downloaded",
            object_sha256=object_sha256,
            decoded_frames_sha256=decoded_frames_sha256,
            reference_frame_count=len(frames),
        )
    finally:
        partials = [temporary_video, temporary_sidecar]
        if downloaded_path is not None and downloaded_path not in {
            cache_path,
            provenance_path,
        }:
            partials.append(downloaded_path)
        for partial in partials:
            with suppress(FileNotFoundError):
                partial.unlink()


def preflight_reference_videos(
    game_names: Iterable[str],
    cache_dir: str | Path,
    *,
    allow_network: bool = False,
    event_reporter: Callable[[str, Mapping[str, object]], object] | None = None,
) -> dict[str, ReferenceCacheValidation]:
    """Verify all approved clips before any paid Azure resource is started."""
    root = Path(cache_dir)
    validations: dict[str, ReferenceCacheValidation] = {}
    failures: list[str] = []
    for game_name in game_names:
        try:
            spec = REFERENCE_VIDEO_SPECS[game_name]
        except KeyError:
            error = "no approved reference manifest"
            failures.append(f"{game_name}: {error}")
            if event_reporter is not None:
                event_reporter("reference_failed", {"game_name": game_name, "error": error})
            continue
        cache_path = root / spec.cache_filename
        provenance_path = root / spec.provenance_filename
        if event_reporter is not None:
            event_reporter(
                "reference_check_started",
                {
                    "game_name": game_name,
                    "cache_path": str(cache_path),
                    "allow_network": allow_network,
                },
            )
        try:
            validation = _validate_cached_reference(
                spec,
                cache_path,
                provenance_path,
            )
            validations[game_name] = validation
            if event_reporter is not None:
                event_reporter(
                    "reference_ready",
                    {
                        "game_name": game_name,
                        "cache_status": validation.cache_status,
                        "reference_frame_count": validation.reference_frame_count,
                        "object_sha256": validation.object_sha256,
                    },
                )
        except RuntimeError as error:
            if not allow_network:
                failures.append(f"{game_name}: {error}")
                if event_reporter is not None:
                    event_reporter(
                        "reference_failed",
                        {"game_name": game_name, "error": str(error)},
                    )
                continue
            if event_reporter is not None:
                event_reporter(
                    "reference_acquisition_started",
                    {"game_name": game_name, "reason": str(error)},
                )
            try:
                validation = _acquire_reference(
                    spec,
                    cache_path,
                    provenance_path,
                    event_reporter,
                )
                validations[game_name] = validation
                if event_reporter is not None:
                    event_reporter(
                        "reference_ready",
                        {
                            "game_name": game_name,
                            "cache_status": validation.cache_status,
                            "reference_frame_count": validation.reference_frame_count,
                            "object_sha256": validation.object_sha256,
                        },
                    )
            except Exception as acquisition_error:
                failures.append(f"{game_name}: acquisition failed: {acquisition_error}")
                if event_reporter is not None:
                    event_reporter(
                        "reference_failed",
                        {"game_name": game_name, "error": str(acquisition_error)},
                    )
    if failures:
        raise RuntimeError("reference cache preflight failed: " + "; ".join(failures))
    return validations


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
    cv2_module = _ensure_cv2()
    cap = cv2_module.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2_module.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0

    frame_interval = max(1, int(fps * interval))
    total_frames = int(cap.get(cv2_module.CAP_PROP_FRAME_COUNT))

    frames: list[Frame] = []
    frame_idx = 0
    extracted = 0

    while extracted < num_frames and frame_idx < total_frames:
        cap.set(cv2_module.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2RGB))
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
    cv2_module = _ensure_cv2()
    resized: list[Frame] = []
    for frame in frames:
        resized.append(cv2_module.resize(frame, target_size))
    return resized


def _average_pool_for_ssim(frame: NDArray[np.float64], factor: int) -> NDArray[np.float64]:
    """Average-pool spatial axes by an integer factor without mixing channels."""
    if factor <= 1:
        return frame
    height = (frame.shape[0] // factor) * factor
    width = (frame.shape[1] // factor) * factor
    cropped = frame[:height, :width]
    return cast(
        NDArray[np.float64],
        cropped.reshape(
            height // factor,
            factor,
            width // factor,
            factor,
            frame.shape[2],
        ).mean(axis=(1, 3)),
    )


def _global_ssim(frame_a: NDArray[np.float64], frame_b: NDArray[np.float64]) -> float:
    """Numerically stable global SSIM used when a local backend violates invariants."""
    mean_a = float(np.mean(frame_a))
    mean_b = float(np.mean(frame_b))
    centered_a = frame_a - mean_a
    centered_b = frame_b - mean_b
    variance_a = float(np.mean(centered_a * centered_a))
    variance_b = float(np.mean(centered_b * centered_b))
    covariance = float(np.mean(centered_a * centered_b))
    luminance_stabilizer = (0.01 * 255.0) ** 2
    contrast_stabilizer = (0.03 * 255.0) ** 2
    numerator = (2.0 * mean_a * mean_b + luminance_stabilizer) * (
        2.0 * covariance + contrast_stabilizer
    )
    denominator = (mean_a**2 + mean_b**2 + luminance_stabilizer) * (
        variance_a + variance_b + contrast_stabilizer
    )
    return float(np.clip(numerator / denominator, 0.0, 1.0))


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

    if np.array_equal(frame_a, frame_b):
        return 1.0

    if structural_similarity is not None and frame_a.ndim == 3 and frame_a.shape[2] == 3:
        float_a = frame_a.astype(np.float64)
        float_b = frame_b.astype(np.float64)
        pooling_factor = max(1, round(min(frame_a.shape[:2]) / 256))
        pooled_a = _average_pool_for_ssim(float_a, pooling_factor)
        pooled_b = _average_pool_for_ssim(float_b, pooling_factor)
        spatial_extent = min(pooled_a.shape[:2])
        if spatial_extent < 3:
            return _global_ssim(pooled_a, pooled_b)
        win_size = min(7, spatial_extent)
        if win_size % 2 == 0:
            win_size -= 1
        try:
            val: float = structural_similarity(
                pooled_a,
                pooled_b,
                data_range=255.0,
                channel_axis=2,
                win_size=win_size,
            )
            result = float(val)
            if not np.isfinite(result):
                return _global_ssim(pooled_a, pooled_b)
            if result > 0.75:
                normalized_mse = float(np.mean((pooled_a - pooled_b) ** 2)) / (255.0**2)
                if normalized_mse >= 0.25:
                    logger.warning(
                        "SSIM backend violated high-error invariant; using stable global SSIM"
                    )
                    return _global_ssim(pooled_a, pooled_b)
            return float(np.clip(result, 0.0, 1.0))
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
        Correlation coefficient in [-1, 1], or ``0.0`` when either signature
        is too short, constant, or contains a non-finite value.
    """
    if len(sig_a) < 2 or len(sig_b) < 2:
        return 0.0
    min_len = min(len(sig_a), len(sig_b))
    values_a = np.asarray(sig_a[:min_len], dtype=np.float64)
    values_b = np.asarray(sig_b[:min_len], dtype=np.float64)
    if not np.all(np.isfinite(values_a)) or not np.all(np.isfinite(values_b)):
        return 0.0
    if np.ptp(values_a) == 0 or np.ptp(values_b) == 0:
        return 0.0
    correlation = float(np.corrcoef(values_a, values_b)[0, 1])
    return correlation if np.isfinite(correlation) else 0.0


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
    "ReferenceCacheValidation",
    "ReferenceComparisonResult",
    "ReferenceVideoSpec",
    "compare_gameplay",
    "compare_gameplay_to_reference",
    "compute_motion_signature",
    "compute_ssim",
    "download_youtube_video",
    "extract_frames",
    "motion_correlation",
    "preflight_reference_videos",
    "resize_frames",
]
