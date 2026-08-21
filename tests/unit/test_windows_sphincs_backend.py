"""Windows-safe SPHINCS+ backend and frozen-artifact contracts."""

from __future__ import annotations

import ast
import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from general_ludd.algorithms import sphincs_plus

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
SPEC = ROOT / "gludd.spec"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_sphincs_backend.py"


def _load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _locked_package(name: str) -> dict[str, object]:
    packages = _load_toml(UV_LOCK)["package"]
    assert isinstance(packages, list)
    matches = [package for package in packages if package.get("name") == name]
    assert len(matches) == 1, f"expected one locked {name!r} package, got {len(matches)}"
    package = matches[0]
    assert isinstance(package, dict)
    return package


def _hidden_imports() -> set[str]:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "Analysis":
            continue
        for keyword in node.keywords:
            if keyword.arg == "hiddenimports":
                return {
                    child.value
                    for child in ast.walk(keyword.value)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                }
    pytest.fail("gludd.spec does not define Analysis(hiddenimports=...)")


def _windows_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"^  windows:\s*\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\s*\n|\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "build workflow does not define the Windows job"
    return match.group("body")


def _load_smoke_script() -> ModuleType:
    assert SMOKE_SCRIPT.is_file(), "the reusable SPHINCS+ smoke script is missing"
    spec = importlib.util.spec_from_file_location("smoke_sphincs_backend", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_uses_the_pqclean_backend_without_pyspx() -> None:
    source = (ROOT / "src/general_ludd/algorithms/sphincs_plus.py").read_text(
        encoding="utf-8"
    )
    assert "from pqcrypto.sign import sphincs_shake_256s_simple as _spx" in source
    assert "pyspx" not in source.casefold()


def test_project_has_one_cross_platform_sphincs_dependency() -> None:
    project = _load_toml(PYPROJECT)["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)
    normalized = [str(dependency).casefold() for dependency in dependencies]
    assert [item for item in normalized if item.startswith("pqcrypto")] == [
        "pqcrypto>=0.4.0,<1"
    ]
    assert not any(item.startswith("pyspx") for item in normalized)


def test_lock_uses_pqcrypto_windows_macos_and_linux_wheels() -> None:
    packages = _load_toml(UV_LOCK)["package"]
    assert isinstance(packages, list)
    assert not any(package.get("name") == "pyspx" for package in packages)

    pqcrypto = _locked_package("pqcrypto")
    assert pqcrypto["version"] == "0.4.0"
    wheels = pqcrypto["wheels"]
    assert isinstance(wheels, list)
    urls = {str(wheel["url"]) for wheel in wheels}
    required_markers = {
        "Windows CPython 3.12 x86-64": "cp312-cp312-win_amd64.whl",
        "macOS CPython 3.12 arm64": "cp312-cp312-macosx_11_0_arm64.whl",
        "Linux CPython 3.12 x86-64": "cp312-cp312-manylinux_2_26_x86_64",
        "Linux CPython 3.12 arm64": "cp312-cp312-manylinux_2_26_aarch64",
    }
    for label, marker in required_markers.items():
        assert any(marker in url for url in urls), f"pqcrypto lock is missing {label} wheel"


def test_backend_preserves_shake_256s_category_five_sizes() -> None:
    params = sphincs_plus._PARAMS_SLH_DSA_SHAKE_256s
    assert params.n == 32
    assert params.pk_bytes == 64
    assert params.sk_bytes == 128
    assert params.sig_bytes == 29_792


def test_public_backend_roundtrip_and_tamper_rejection() -> None:
    message = b"gludd-windows-sphincs-smoke"
    public_key, secret_key = sphincs_plus.slh_keygen()
    signature = sphincs_plus.slh_sign(message, secret_key)
    assert sphincs_plus.slh_verify(message, signature, public_key)
    assert not sphincs_plus.slh_verify(message + b"-tampered", signature, public_key)


def test_frozen_spec_explicitly_includes_python_and_native_backend() -> None:
    assert {
        "general_ludd.algorithms.sphincs_plus",
        "pqcrypto.sign.sphincs_shake_256s_simple",
        "pqcrypto._sign.sphincs_shake_256s_simple",
    } <= _hidden_imports()


def test_windows_job_uses_frozen_install_and_warning_error_smoke() -> None:
    job = _windows_job()
    sync = "uv sync --frozen --python 3.12"
    smoke = (
        "uv run --frozen --python 3.12 python -W error "
        "scripts/smoke_sphincs_backend.py"
    )
    build = (
        "uv run --frozen --python 3.12 pyinstaller gludd.spec --clean --noconfirm"
    )
    assert sync in job
    assert smoke in job
    assert job.index(sync) < job.index(smoke) < job.index(build)
    assert "SPHINCS backend" in job
    assert "pyspx" not in job.casefold()


def test_reusable_smoke_script_exercises_public_adapter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke = _load_smoke_script()
    assert smoke.main() == 0
    output = capsys.readouterr().out
    assert "SPHINCS_BACKEND_SMOKE_PASS" in output
    assert "sphincs_shake_256s_simple" in output


def test_reusable_smoke_script_fails_closed_on_backend_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_script()
    monkeypatch.setattr(smoke, "slh_keygen", lambda: (b"public", b"secret"))
    monkeypatch.setattr(smoke, "slh_sign", lambda message, key: b"signature")
    monkeypatch.setattr(smoke, "slh_verify", lambda message, signature, key: False)

    with pytest.raises(RuntimeError, match=r"SPHINCS\+ backend round trip failed"):
        smoke.main()
