"""S.18 — Verify unused dependencies stay removed and runtime adapters stay declared."""
import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
SRC_DIR = PROJECT_ROOT / "src"


def _load_deps() -> dict[str, str]:
    data = tomllib.loads(PYPROJECT.read_text())
    deps_list: list[str] = data["project"]["dependencies"]
    result = {}
    for dep in deps_list:
        name, _, _ = dep.partition(">=")
        name = name.partition("[")[0]
        result[name] = dep
    return result


def _has_imports(import_name: str) -> bool:
    """Check if `import <name>` or `from <name>` (word-boundary) exists in src/."""
    pattern = re.compile(rf"^\s*(?:import {import_name}\b|from {import_name}\b)")
    for py_file in SRC_DIR.rglob("*.py"):
        text = py_file.read_text()
        for line in text.splitlines():
            if pattern.match(line):
                return True
    return False


def test_langchain_removed():
    deps = _load_deps()
    assert "langchain" not in deps, "langchain should be removed"
    assert not _has_imports("langchain"), "no langchain imports should exist in src/"


def test_langchain_openai_kept_for_openai_compatible_providers():
    deps = _load_deps()
    assert "langchain-openai" in deps, "OpenAI-compatible providers require langchain-openai"
    registry_source = (
        SRC_DIR / "general_ludd" / "models" / "provider_registry.py"
    ).read_text()
    assert '"langchain_openai"' in registry_source


def test_langgraph_kept():
    deps = _load_deps()
    assert "langgraph" in deps, "langgraph should be kept in deps"
    assert _has_imports("langgraph"), "langgraph imports should exist in src/"


def test_langsmith_kept():
    deps = _load_deps()
    assert "langsmith" in deps, "langsmith should be kept in deps"
    assert _has_imports("langsmith"), "langsmith imports should exist in src/"


def test_httpx2_kept_for_starlette_testclient():
    data = tomllib.loads(PYPROJECT.read_text())
    groups = (
        data["project"]["optional-dependencies"]["dev"],
        data["dependency-groups"]["dev"],
    )
    for requirements in groups:
        assert "httpx2>=2.7.0" in requirements
    assert "httpx2" not in _load_deps(), "TestClient backend is development-only"
    assert not _has_imports("httpx2"), "no httpx2 imports should exist in src/"
