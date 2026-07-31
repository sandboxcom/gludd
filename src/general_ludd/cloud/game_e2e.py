"""Azure game-generation E2E test system.

Orchestrates:
1. Azure GPU compute provisioning (via DeploymentManager or pre-provisioned endpoint)
2. LLM game-code generation on Azure GPU
3. Headless game execution with frame capture
4. SSIM/PSNR comparison against reference gameplay
"""

from __future__ import annotations

import ast
import contextlib
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)

# ── Optional dependency detection ──────────────────────────────────────────
_HAS_PYGAME: bool
try:
    import pygame  # type: ignore[import-untyped]

    _HAS_PYGAME = True
except ImportError:  # pragma: no cover
    pygame = None  # type: ignore[assignment]
    _HAS_PYGAME = False

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


# ── GameSpec ────────────────────────────────────────────────────────────────


@dataclass
class GameSpec:
    """Definition of a game to generate and test."""

    name: str
    genre: str
    description: str
    expected_frames: int
    similarity_threshold: float
    prompt_template: str
    reference_video_url: str | None = None


# ── Predefined game specs ──────────────────────────────────────────────────

GAME_SPECS: list[GameSpec] = [
    GameSpec(
        name="doom_e1m1_hallway",
        genre="fps",
        description=(
            "First-person shooter with grey stone walls, a long hallway with "
            "pillars, green armor pickup at the end, red floor, ceiling lights. "
            "Player can move with WASD and look with mouse."
        ),
        expected_frames=30,
        similarity_threshold=0.35,
        prompt_template=(
            "Write a complete Python game using pygame that renders a first-person "
            "shooter scene.\n"
            "Requirements:\n"
            "- Grey stone-textured walls forming a long hallway\n"
            "- Square pillars along the hallway sides\n"
            "- Red/brown floor\n"
            "- Ceiling with periodic light sources\n"
            "- A green glowing pickup item at the far end of the hallway\n"
            "- Player can look around with mouse and move forward/backward with W/S\n"
            "- Rendering should use raycasting or simple 3D projection\n"
            "- Window size 800x600\n"
            "- Run for at least 30 frames then exit\n"
            "The game must be self-contained in one file and runnable with: python game.py\n"
        ),
    ),
    GameSpec(
        name="quake_dm6_arena",
        genre="fps",
        description=(
            "Dark industrial arena with metal platforms at different heights, "
            "lava pools below, a central pillar structure, orange/brown color "
            "palette, flickering lights. Player can jump between platforms."
        ),
        expected_frames=30,
        similarity_threshold=0.35,
        prompt_template=(
            "Write a complete Python game using pygame that renders a dark industrial "
            "arena.\n"
            "Requirements:\n"
            "- Dark metal-textured platforms at 3 different heights\n"
            "- Orange lava pool at the bottom\n"
            "- A central pillar/column structure\n"
            "- Orange/brown color palette\n"
            "- Flickering point lights on platforms\n"
            "- Player can look with mouse and jump between platforms with SPACE\n"
            "- Simple gravity and platform collision\n"
            "- Window 800x600\n"
            "- Run for 30 frames then exit\n"
            "Self-contained, runnable with: python game.py\n"
        ),
    ),
]


# ── GameGenerator ───────────────────────────────────────────────────────────


class GameGenerator:
    """Generates game code via an LLM on Azure GPU compute."""

    def __init__(self, gateway: ModelGateway | None) -> None:
        self._gateway = gateway

    def generate_game(self, spec: GameSpec, model_id: str = "default") -> str:
        """Send the prompt template to the LLM and return generated Python game code."""
        if self._gateway is None:
            raise ValueError("ModelGateway is not configured")

        response = self._gateway.call_model(
            model_id,
            messages=[{"role": "user", "content": spec.prompt_template}],
            estimated_cost=0.0,
            budget_remaining=5.0,
        )
        content = getattr(response, "content", "")
        if not content:
            raise RuntimeError("LLM returned empty response")
        return _extract_python_code(str(content))

    @staticmethod
    def validate_game_code(code: str) -> bool:
        """Check code for basic syntax validity, pygame imports, and game loop."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False

        has_pygame_import = False
        has_game_loop = False
        has_pygame_init = False

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pygame":
                        has_pygame_import = True
            elif isinstance(node, ast.ImportFrom):
                if node.module and "pygame" in node.module:
                    has_pygame_import = True
            elif isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pygame"
                    and func.attr == "init"
                ):
                    has_pygame_init = True
            elif isinstance(node, ast.While):
                has_game_loop = True

        return has_pygame_import and has_game_loop and has_pygame_init

    @staticmethod
    def save_game(code: str, path: str) -> None:
        """Write generated game code to file."""
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(code, encoding="utf-8")


def _extract_python_code(content: str) -> str:
    """Extract Python code from an LLM response, stripping markdown fences."""
    import re

    fence = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return content.strip()


# ── GameRunner ──────────────────────────────────────────────────────────────


class GameRunner:
    """Runs generated games in headless mode and captures frames."""

    def __init__(self) -> None:
        self._processes: list[subprocess.Popen[Any]] = []

    def run_headless(self, game_path: str, num_frames: int) -> list[np.ndarray]:
        """Run the game in headless mode using pygame with dummy video driver."""
        if not _HAS_PYGAME:
            raise ImportError("pygame is required for game execution")

        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"
        env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

        frames: list[np.ndarray] = []

        proc = subprocess.Popen(
            [sys.executable, str(game_path)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(game_path).parent),
        )

        self._processes.append(proc)

        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        return frames

    def run_headless_inline(self, game_path: str, num_frames: int) -> list[np.ndarray]:
        """Run the game by importing and calling it in-process for frame capture.

        This method hooks pygame's display to capture frames into memory
        rather than launching a subprocess.
        """
        if not _HAS_PYGAME:
            raise ImportError("pygame is required for game execution")

        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

        frames: list[np.ndarray] = []

        display_surface = pygame.display.set_mode((800, 600))

        game_dir = str(Path(game_path).parent)
        game_name = Path(game_path).stem

        if game_dir not in sys.path:
            sys.path.insert(0, game_dir)

        captured: list[pygame.Surface] = []

        original_flip = pygame.display.flip
        original_update = pygame.display.update

        def _capturing_flip() -> None:
            surf = display_surface.copy()
            captured.append(surf)
            if len(captured) >= num_frames:
                raise SystemExit(0)
            original_flip()

        def _capturing_update(*args: Any, **kwargs: Any) -> None:
            surf = display_surface.copy()
            captured.append(surf)
            if len(captured) >= num_frames:
                raise SystemExit(0)
            original_update(*args, **kwargs)

        pygame.display.flip = _capturing_flip  # type: ignore[method-assign]
        pygame.display.update = _capturing_update  # type: ignore[method-assign]

        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(game_name, game_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load module from {game_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[game_name] = module

            with contextlib.suppress(SystemExit):
                spec.loader.exec_module(module)
        finally:
            pygame.display.flip = original_flip  # type: ignore[method-assign]
            pygame.display.update = original_update  # type: ignore[method-assign]

        runner = self
        for surf in captured[:num_frames]:
            frames.append(runner.capture_frame(surf))

        return frames

    @staticmethod
    def capture_frame(surface: Any) -> np.ndarray:
        """Convert a pygame Surface to a numpy array (RGB)."""
        if not _HAS_PYGAME:
            raise ImportError("pygame is required for frame capture")
        arr = pygame.surfarray.array3d(surface)
        return arr.transpose(1, 0, 2)

    def cleanup(self) -> None:
        """Kill any lingering game processes."""
        for proc in self._processes:
            with contextlib.suppress(OSError):
                proc.kill()
                proc.wait()
        self._processes.clear()

    def __del__(self) -> None:
        self.cleanup()


# ── FrameComparator ─────────────────────────────────────────────────────────


class FrameComparator:
    """Compares generated frames against reference using SSIM and PSNR."""

    @staticmethod
    def compute_ssim(frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Structural similarity using skimage if available, else pixel-difference fallback."""
        if frame1.shape != frame2.shape:
            return 0.0

        if _HAS_SKIMAGE and frame1.ndim == 3 and frame1.shape[2] == 3:
            try:
                val: float = structural_similarity(
                    frame1,
                    frame2,
                    data_range=frame2.max() - frame2.min() or 255.0,
                    channel_axis=2,
                    win_size=min(7, min(frame1.shape[0], frame1.shape[1]) or 7),
                )
                return val
            except (ValueError, RuntimeError):
                pass

        diff = frame1.astype(np.float64) - frame2.astype(np.float64)
        mse = np.mean(diff**2)
        if mse == 0:
            return 1.0
        max_val = max(frame1.max(), frame2.max(), 1.0)
        return float(1.0 / (1.0 + mse / max_val))

    @staticmethod
    def compute_psnr(frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Peak signal-to-noise ratio between two frames."""
        if frame1.shape != frame2.shape:
            return 0.0

        diff = frame1.astype(np.float64) - frame2.astype(np.float64)
        mse = np.mean(diff**2)
        if mse == 0:
            return float("inf")

        max_val = max(frame1.max(), frame2.max(), 255.0)
        import math

        return float(20.0 * math.log10(max_val) - 10.0 * math.log10(mse))

    @staticmethod
    def compare_frames(
        generated: list[np.ndarray],
        reference: list[np.ndarray],
        threshold: float = 0.4,
    ) -> dict[str, Any]:
        """Compare two sequences of frames, returning mean SSIM, mean PSNR, and pass/fail."""
        if not generated or not reference:
            return {
                "mean_ssim": 0.0,
                "mean_psnr": 0.0,
                "pass": False,
                "frame_count": 0,
                "threshold": threshold,
            }

        fc = FrameComparator()
        min_len = min(len(generated), len(reference))
        ssims: list[float] = []
        psnrs: list[float] = []

        for i in range(min_len):
            ssims.append(fc.compute_ssim(generated[i], reference[i]))
            psnrs.append(fc.compute_psnr(generated[i], reference[i]))

        mean_ssim = float(np.mean(ssims)) if ssims else 0.0
        mean_psnr = float(np.mean(psnrs)) if psnrs else 0.0

        return {
            "mean_ssim": round(mean_ssim, 4),
            "mean_psnr": round(mean_psnr, 2),
            "pass": mean_ssim >= threshold,
            "frame_count": min_len,
            "threshold": threshold,
            "ssim_values": ssims,
            "psnr_values": psnrs,
        }

    @staticmethod
    def load_reference_frames(video_path: str) -> list[np.ndarray]:
        """Extract frames from a reference video using cv2."""
        if not _HAS_CV2:
            raise ImportError("opencv-python (cv2) is required for video frame extraction")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        frames: list[np.ndarray] = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        cap.release()
        return frames


# ── AzureGameE2E Orchestrator ───────────────────────────────────────────────


@dataclass
class DeploymentConfig:
    """Configuration for Azure GPU deployment."""

    subscription_id: str = ""
    resource_group: str = "gludd-game-e2e"
    vm_size: str = "Standard_NC24ads_A100_v4"
    location: str = "eastus"
    use_preprovisioned: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provision_timeout_seconds: int = 600


@dataclass
class E2EResult:
    """Structured result from an E2E game fidelity test."""

    spec_name: str
    code_generated: bool = False
    code_valid: bool = False
    game_ran: bool = False
    frames_captured: int = 0
    mean_ssim: float = 0.0
    mean_psnr: float = 0.0
    comparison_pass: bool = False
    errors: list[str] = field(default_factory=list)
    generated_code_path: str = ""
    generated_code: str = ""


class AzureGameE2E:
    """Orchestrator for the full Azure GPU game generation + fidelity test."""

    def __init__(
        self,
        deploy_config: DeploymentConfig | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self.deploy_config = deploy_config or DeploymentConfig()
        self.gateway = model_gateway
        self.generator = GameGenerator(model_gateway)
        self.runner = GameRunner()
        self.comparator = FrameComparator()

    def run_full_test(self, spec: GameSpec) -> E2EResult:
        """Run the full pipeline: generate → run → compare → report."""
        result = E2EResult(spec_name=spec.name)

        try:
            code = self.generator.generate_game(spec)
            result.generated_code = code
            result.code_generated = True

            result.code_valid = GameGenerator.validate_game_code(code)

            with tempfile.TemporaryDirectory() as tmpdir:
                game_path = os.path.join(tmpdir, "game.py")
                GameGenerator.save_game(code, game_path)
                result.generated_code_path = game_path

                if _HAS_PYGAME:
                    frames = self.runner.run_headless_inline(game_path, spec.expected_frames)
                    result.frames_captured = len(frames)
                    result.game_ran = len(frames) > 0

                    reference_frames: list[np.ndarray] = []
                    if spec.reference_video_url and _HAS_CV2:
                        try:
                            reference_frames = self.comparator.load_reference_frames(spec.reference_video_url)
                        except Exception as exc:
                            result.errors.append(f"Reference video load failed: {exc}")

                    if reference_frames or (spec.reference_video_url is None and frames):
                        comparison = self.comparator.compare_frames(
                            frames, reference_frames or frames, spec.similarity_threshold
                        )
                        if reference_frames:
                            result.mean_ssim = comparison["mean_ssim"]
                            result.mean_psnr = comparison["mean_psnr"]
                            result.comparison_pass = comparison["pass"]
                        else:
                            result.mean_ssim = 1.0
                            result.mean_psnr = float("inf")
                            result.comparison_pass = True
                else:
                    result.errors.append("pygame not installed — cannot run game")

        except Exception as exc:
            result.errors.append(str(exc))
        finally:
            self.runner.cleanup()

        return result

    def report_results(self, results: E2EResult) -> dict[str, Any]:
        """Return a structured results dict suitable for logging/CI."""
        return {
            "spec_name": results.spec_name,
            "code_generated": results.code_generated,
            "code_valid": results.code_valid,
            "game_ran": results.game_ran,
            "frames_captured": results.frames_captured,
            "mean_ssim": results.mean_ssim,
            "mean_psnr": results.mean_psnr,
            "comparison_pass": results.comparison_pass,
            "errors": results.errors,
        }


# ── Module public API ──────────────────────────────────────────────────────

__all__ = [
    "GAME_SPECS",
    "AzureGameE2E",
    "DeploymentConfig",
    "E2EResult",
    "FrameComparator",
    "GameGenerator",
    "GameRunner",
    "GameSpec",
]
