"""Health check verifying local model deployment readiness.

Checks:
- At least one local-model-capable profile is discoverable and enabled
- llama-cpp-python is importable (or a clear gap is reported)
- The model download directory is present and writable
- Hardware meets the minimum bar for local inference
"""

from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from general_ludd.hardware.probe import _MIN_MEMORY_GB_FOR_LOCAL_MODEL, HardwareProfile, probe_hardware
from general_ludd.small_models.download import DEFAULT_CACHE_DIR

# ── helpers ────────────────────────────────────────────────────────────────


def _collect_gaps(gaps: list[dict[str, Any]]) -> None:
    """Fail with a structured gap report if any gaps were recorded."""
    if not gaps:
        return
    lines = ["\nLocal-model deploy readiness gaps:"]
    for g in gaps:
        lines.append(f"  [{g['severity']}] {g['component']}: {g['detail']}")
    pytest.fail("\n".join(lines))


# ── 1. profiles enabled ────────────────────────────────────────────────────


def test_profiles_enabled_for_local_deploy() -> None:
    """At least one profile supporting local (non-API-metered) inference exists."""
    gaps: list[dict[str, Any]] = []

    try:
        from general_ludd.models import gateway as gw
    except Exception as exc:
        gaps.append({"severity": "ERROR", "component": "profiles", "detail": f"gateway import failed: {exc}"})
        _collect_gaps(gaps)
        return

    profiles_dict: dict[str, Any] | None = getattr(gw, "_profiles", None)
    if profiles_dict is None:
        singleton = getattr(gw, "_gateway_instance", None)
        profiles_dict = getattr(singleton, "_profiles", None) if singleton is not None else None
    if profiles_dict is None:
        from general_ludd.models.gateway import ModelGateway

        profiles_dict = getattr(ModelGateway, "_profiles", {})

    if not profiles_dict:
        gaps.append(
            {
                "severity": "WARN",
                "component": "profiles",
                "detail": "No profile registry discoverable — profiles may be loaded at daemon boot only",
            }
        )
    else:
        local_capable: list[str] = []
        api_only: list[str] = []
        for pid, prof in profiles_dict.items():
            if getattr(prof, "enabled", False):
                if getattr(prof, "api_metered", True):
                    api_only.append(pid)
                else:
                    local_capable.append(pid)

        if len(local_capable) == 0 and len(api_only) == 0:
            gaps.append(
                {
                    "severity": "WARN",
                    "component": "profiles",
                    "detail": "No enabled profiles found (local or remote). Deploy blocked until a profile is enabled.",
                }
            )
        elif len(local_capable) == 0:
            gaps.append(
                {
                    "severity": "WARN",
                    "component": "profiles",
                    "detail": (
                        f"{len(api_only)} enabled profile(s) are all api_metered. "
                        "No local (non-metered) profile enabled. "
                        "Local deploy needs at least one api_metered=False profile."
                    ),
                }
            )

    assert len(gaps) <= 1, f"profile check produced unexpected gaps: {gaps}"
    _collect_gaps(gaps)


# ── 2. llama-cpp-python importable ─────────────────────────────────────────


def _llama_cpp_gap() -> dict[str, Any]:
    return {
        "severity": "WARN",
        "component": "llama-cpp-python",
        "detail": (
            "llama_cpp is not installed. "
            "Install with: pip install llama-cpp-python[server] --extra-index-url "
            "https://abetlen.github.io/llama-cpp-python/whl/cpu"
        ),
    }


@pytest.mark.skipif(
    importlib.util.find_spec("llama_cpp") is None,
    reason="llama-cpp-python (optional local-inference extra) not installed",
)
def test_llama_cpp_python_importable() -> None:
    """llama_cpp is importable when the optional local-inference extra is installed."""
    importlib.import_module("llama_cpp")


def test_llama_cpp_missing_reports_structured_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap report for a missing llama_cpp is well-formed and actionable.

    Runs unconditionally (llama_cpp is an optional extra, so the gate must
    pass without it) by forcing the ImportError path.
    """
    gaps: list[dict[str, Any]] = []
    real_import = importlib.import_module

    def fake_import(name: str, *args: object, **kwargs: object) -> Any:
        if name == "llama_cpp":
            raise ImportError("No module named 'llama_cpp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    try:
        importlib.import_module("llama_cpp")
    except ImportError:
        gaps.append(_llama_cpp_gap())
    assert len(gaps) == 1
    assert gaps[0]["severity"] == "WARN"
    assert gaps[0]["component"] == "llama-cpp-python"
    assert "llama-cpp-python" in gaps[0]["detail"]


# ── 3. model download directory writable ───────────────────────────────────


def test_model_download_dir_writable() -> None:
    """The default cache directory for models exists and is writable."""
    gaps: list[dict[str, Any]] = []
    model_dir = Path(os.path.expanduser(DEFAULT_CACHE_DIR))

    if not model_dir.exists():
        try:
            model_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            gaps.append(
                {
                    "severity": "ERROR",
                    "component": "model-download-dir",
                    "detail": f"Cannot create {model_dir}: {exc}",
                }
            )
            _collect_gaps(gaps)
            return
        else:
            # Clean up the test-created directory if it was empty before
            if not any(model_dir.iterdir()):
                try:
                    model_dir.rmdir()
                    # Also remove empty parent chain (only if empty)
                    for parent in model_dir.parents:
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                except OSError:
                    pass

    if model_dir.exists():
        try:
            with tempfile.NamedTemporaryFile(dir=model_dir, delete=True) as tf:
                tf.write(b"deploy-readiness-check\n")
                tf.flush()
        except Exception as exc:
            gaps.append(
                {
                    "severity": "ERROR",
                    "component": "model-download-dir",
                    "detail": f"{model_dir} exists but is not writable: {exc}",
                }
            )
    else:
        gaps.append(
            {
                "severity": "WARN",
                "component": "model-download-dir",
                "detail": (
                    f"{model_dir} does not exist and could not be autocreated. Set GLUDD_MODEL_DIR to an existing path."
                ),
            }
        )

    _collect_gaps(gaps)


# ── 4. hardware supports local inference ──────────────────────────────────


def test_hardware_supports_local_inference() -> None:
    """The host meets the minimum memory bar for local model inference."""
    gaps: list[dict[str, Any]] = []
    profile: HardwareProfile = probe_hardware()

    assert isinstance(profile, HardwareProfile)

    if not profile.local_model_allowed:
        gaps.append(
            {
                "severity": "ERROR",
                "component": "hardware",
                "detail": (
                    f"Hardware profile reports local_model_allowed=False "
                    f"(RAM: {profile.total_memory_gb:.1f} GB < "
                    f"minimum {_MIN_MEMORY_GB_FOR_LOCAL_MODEL} GB). "
                    "Local model deployment is not permitted on this host."
                ),
            }
        )

    _collect_gaps(gaps)


def test_hardware_profile_fields_are_sane() -> None:
    """Structural sanity: every field in the hardware profile is set."""
    profile = probe_hardware()
    assert profile.cpu_count >= 1
    assert profile.total_memory_gb >= 0.0
    assert profile.recommended_workers >= 1
    assert profile.gunicorn_workers >= 1
    assert profile.thread_pool_size >= 1
    assert profile.network_concurrency >= 1
    assert isinstance(profile.local_model_allowed, bool)
