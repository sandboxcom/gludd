"""Deep Dockerfile validation — multi-stage, layers, health, security, base images."""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = sorted(REPO_ROOT.glob("**/Dockerfile"))


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _collect_stage_names(lines: list[str]) -> list[str]:
    """Return stage names (AS <name>) in order."""
    names = []
    for line in lines:
        m = re.match(r"^FROM\s+\S+\s+AS\s+(\S+)", line, re.IGNORECASE)
        if m:
            names.append(m.group(1))
    return names


def _stage_count(lines: list[str]) -> int:
    return sum(1 for line in lines if re.match(r"^FROM\s", line, re.IGNORECASE))


def _has_user(lines: list[str]) -> bool:
    return any(re.match(r"^USER\s+\S", line) for line in lines)


# ---------------------------------------------------------------------------
# existence
# ---------------------------------------------------------------------------


def test_all_dockerfiles_exist():
    assert len(DOCKERFILES) >= 1, "No Dockerfiles found"
    for df in DOCKERFILES:
        assert df.exists(), f"Missing: {df}"
        assert df.stat().st_size > 0, f"Empty: {df}"


def test_expected_dockerfiles_present():
    names = {"root" if df.parent == REPO_ROOT else df.parent.name for df in DOCKERFILES}
    expected = {"root", "ollama", "vllm", "llamacpp"}
    missing = expected - names
    assert not missing, f"Expected Dockerfiles: {sorted(missing)}"


# ---------------------------------------------------------------------------
# multi-stage builds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("df", [p for p in DOCKERFILES])
def test_has_from_directive(df):
    content = df.read_text()
    assert re.search(r"^FROM\s", content, re.MULTILINE), f"{df.name}: no FROM"


@pytest.mark.parametrize("df", [p for p in DOCKERFILES])
def test_multi_stage_where_expected(df):
    lines = _lines(df)
    count = _stage_count(lines)
    name = df.parent.name if df.parent.name != "gludd" else "root"

    if name in ("root", "llamacpp"):
        assert count >= 2, f"{df.name}: expected multi-stage, got {count}"
    else:
        assert count >= 1, f"{df.name}: no stages"


def test_root_dockerfile_stage_names():
    lines = _lines(REPO_ROOT / "Dockerfile")
    names = _collect_stage_names(lines)
    assert "builder" in names, f"Expected 'builder' stage, got: {names}"
    assert "runtime" in names, f"Expected 'runtime' stage, got: {names}"


def test_llamacpp_dockerfile_stage_names():
    df = REPO_ROOT / "infra" / "local-models" / "llamacpp" / "Dockerfile"
    names = _collect_stage_names(_lines(df))
    assert "builder" in names, f"Expected 'builder' stage, got: {names}"


def test_multi_stage_has_copy_from():
    lines = _lines(REPO_ROOT / "Dockerfile")
    copies = [line for line in lines if re.match(r"COPY\s+--from=", line)]
    assert len(copies) >= 2, f"Expected >=2 COPY --from, got {len(copies)}"


# ---------------------------------------------------------------------------
# base image version check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "df_path,expected_patterns",
    [
        ("Dockerfile", [r"python:\$\{PYTHON_VERSION\}-slim-bookworm", r"uv:python\$\{PYTHON_VERSION\}-bookworm-slim"]),
        ("infra/local-models/ollama/Dockerfile", [r"ollama/ollama:latest"]),
        ("infra/local-models/vllm/Dockerfile", [r"vllm/vllm-openai:\$\{VLLM_VERSION\}"]),
        (
            "infra/local-models/llamacpp/Dockerfile",
            [
                r"nvidia/cuda:\$\{CUDA_VERSION\}-devel-ubuntu22\.04",
                r"nvidia/cuda:\$\{CUDA_VERSION\}-runtime-ubuntu22\.04",
            ],
        ),
    ],
)
def test_base_image_known(df_path, expected_patterns):
    content = (REPO_ROOT / df_path).read_text()
    for pat in expected_patterns:
        assert re.search(pat, content), f"Pattern not found: {pat}"


def test_root_dockerfile_no_latest_tag():
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert "python:latest" not in content, "Must not use python:latest"


def test_ollama_dockerfile_uses_latest():
    content = (REPO_ROOT / "infra/local-models/ollama/Dockerfile").read_text()
    assert "ollama/ollama:latest" in content


# ---------------------------------------------------------------------------
# EXPOSE ports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "df_path,expected_port",
    [
        ("Dockerfile", "8000"),
        ("infra/local-models/ollama/Dockerfile", "11434"),
        ("infra/local-models/vllm/Dockerfile", "8000"),
        ("infra/local-models/llamacpp/Dockerfile", "8080"),
    ],
)
def test_expose_port(df_path, expected_port):
    content = (REPO_ROOT / df_path).read_text()
    assert re.search(rf"EXPOSE\s+{expected_port}", content), f"Expected EXPOSE {expected_port}"


# ---------------------------------------------------------------------------
# HEALTHCHECK presence
# ---------------------------------------------------------------------------


def test_root_dockerfile_has_healthcheck():
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert "HEALTHCHECK" in content
    assert "--interval=30s" in content
    assert "--timeout=5s" in content
    assert "--retries=3" in content
    assert "/healthz" in content


@pytest.mark.parametrize(
    "df_path",
    [
        "infra/local-models/ollama/Dockerfile",
        "infra/local-models/vllm/Dockerfile",
        "infra/local-models/llamacpp/Dockerfile",
    ],
)
def test_infra_dockerfiles_no_healthcheck_expected(df_path):
    content = (REPO_ROOT / df_path).read_text()
    assert "HEALTHCHECK" not in content, f"{df_path} is a dev/infra image, HEALTHCHECK not expected"


# ---------------------------------------------------------------------------
# non-root user
# ---------------------------------------------------------------------------


def test_root_dockerfile_has_nonroot_user():
    lines = _lines(REPO_ROOT / "Dockerfile")
    assert _has_user(lines), "Missing USER directive"
    user_lines = [line for line in lines if line.startswith("USER ")]
    assert any("gludd" in line for line in user_lines), f"Expected USER gludd, got: {user_lines}"


def test_root_dockerfile_user_after_volume():
    lines = _lines(REPO_ROOT / "Dockerfile")
    user_idx = next(i for i, line in enumerate(lines) if line.startswith("USER "))
    volume_idx = max(i for i, line in enumerate(lines) if line.strip().startswith("VOLUME "))
    assert user_idx > volume_idx, "USER must come after VOLUME (permissions set before drop)"


def test_ollama_dockerfile_root_is_acceptable():
    lines = _lines(REPO_ROOT / "infra/local-models/ollama/Dockerfile")
    assert not _has_user(lines), "ollama dev image runs as root (acceptable)"


def test_llamacpp_dockerfile_no_user():
    lines = _lines(REPO_ROOT / "infra/local-models/llamacpp/Dockerfile")
    assert not _has_user(lines), "llamacpp runs as root (no USER directive)"


# ---------------------------------------------------------------------------
# OCI labels
# ---------------------------------------------------------------------------


def test_root_dockerfile_has_oci_labels():
    content = (REPO_ROOT / "Dockerfile").read_text()
    required = [
        "org.opencontainers.image.title",
        "org.opencontainers.image.version",
        "org.opencontainers.image.description",
        "org.opencontainers.image.licenses",
        "org.opencontainers.image.source",
    ]
    for label in required:
        assert label in content, f"Missing OCI label: {label}"


# ---------------------------------------------------------------------------
# ENTRYPOINT / CMD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "df_path,expected_entrypoint",
    [
        ("Dockerfile", "tini"),
        ("infra/local-models/ollama/Dockerfile", "ollama serve"),
        ("infra/local-models/vllm/Dockerfile", "vllm serve"),
        ("infra/local-models/llamacpp/Dockerfile", "llama-server"),
    ],
)
def test_entrypoint(df_path, expected_entrypoint):
    content = (REPO_ROOT / df_path).read_text()
    assert "ENTRYPOINT" in content, f"{df_path}: missing ENTRYPOINT"
    assert expected_entrypoint in content, f"{df_path}: ENTRYPOINT missing '{expected_entrypoint}'"


# ---------------------------------------------------------------------------
# VOLUME
# ---------------------------------------------------------------------------


def test_root_dockerfile_has_volume():
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert "VOLUME" in content
    assert "/var/lib/general-ludd" in content


# ---------------------------------------------------------------------------
# layer hygiene — apt cleanup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("df", DOCKERFILES)
def test_apt_layers_have_cleanup(df):
    lines = _lines(df)
    apt_installs = [i for i, line in enumerate(lines) if "apt-get install" in line]
    for idx in apt_installs:
        window = "\n".join(lines[idx : idx + 5])
        has_cleanup = bool(
            re.search(r"rm\s+-rf\s+/var/lib/apt/lists", window) or re.search(r"rm\s+-rf\s+/var/lib/apt/lists", window)
        )
        assert has_cleanup, f"{df.name}:{idx + 1} apt-get install without cleanup in next 5 lines"


# ---------------------------------------------------------------------------
# layering — COPY pyproject before src (cache efficiency)
# ---------------------------------------------------------------------------


def test_root_dockerfile_pyproject_copied_before_src():
    lines = _lines(REPO_ROOT / "Dockerfile")
    pyproject_line = next(i for i, line in enumerate(lines) if "COPY pyproject.toml" in line)
    src_line = next(i for i, line in enumerate(lines) if "COPY src" in line)
    assert pyproject_line < src_line, "pyproject.toml must be copied before src/ for layer caching"


# ---------------------------------------------------------------------------
# syntax directive
# ---------------------------------------------------------------------------


def test_root_dockerfile_has_syntax_directive():
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert content.startswith("# syntax=docker/dockerfile"), "Missing syntax directive"


# ---------------------------------------------------------------------------
# WORKDIR present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "df_path",
    ["Dockerfile", "infra/local-models/llamacpp/Dockerfile"],
)
def test_has_workdir(df_path: str):
    content = (REPO_ROOT / df_path).read_text()
    assert re.search(r"^WORKDIR\s+", content, re.MULTILINE), f"{df_path}: missing WORKDIR"


# ---------------------------------------------------------------------------
# no ADD when COPY would suffice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("df", DOCKERFILES)
def test_no_add_directive(df: Path):
    content = df.read_text()
    add_lines = [line for line in content.splitlines() if re.match(r"^ADD\s", line)]
    assert len(add_lines) == 0, f"{df.name}: uses ADD directive — prefer COPY ({add_lines[0]})"


# ---------------------------------------------------------------------------
# line endings check (LF)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("df", DOCKERFILES)
def test_unix_line_endings(df: Path):
    content = df.read_bytes()
    assert b"\r\n" not in content, f"{df.name}: has CRLF line endings"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
