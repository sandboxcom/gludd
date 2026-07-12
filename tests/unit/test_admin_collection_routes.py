from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.routers.ansible import register


def _make_versioned_collection(base: Path, ns: str, coll: str, version: str) -> Path:
    coll_root = (
        base
        / "ansible_collections"
        / f"{ns}@{version}"
        / coll
    )
    coll_root.mkdir(parents=True)
    (coll_root / "plugins" / "modules").mkdir(parents=True)
    tasks_dir = coll_root / "roles" / "test_role" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "main.yml").write_text(
        f"- name: version {version}\n"
    )
    return coll_root


@pytest.fixture
def collections_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "collections"
    root.mkdir()
    _make_versioned_collection(root, "general_ludd", "agent", "0.1.0")
    _make_versioned_collection(root, "general_ludd", "agent", "0.2.0")
    _make_versioned_collection(root, "general_ludd", "agent", "latest")
    _make_versioned_collection(root, "general_ludd", "agent", "beta.1")
    _make_versioned_collection(root, "general_ludd", "worker", "0.3.0")
    monkeypatch.setattr(
        "general_ludd.ansible.paths._bundled_collections_root",
        lambda: root,
    )
    return root


# ---------------------------------------------------------------------------
# List versions
# ---------------------------------------------------------------------------

def test_list_versions_returns_semver_sorted(collections_root: Path) -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)
    resp = client.get(
        "/admin/ansible/collections/versions",
        params={"namespace": "general_ludd", "collection": "agent"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["namespace"] == "general_ludd"
    assert "0.2.0" in data["versions"]
    assert "0.1.0" in data["versions"]
    assert "beta.1" in data["versions"]
    assert "latest" in data["versions"]


def test_list_versions_filter_by_collection(collections_root: Path) -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)
    resp = client.get(
        "/admin/ansible/collections/versions",
        params={"namespace": "general_ludd", "collection": "worker"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["versions"] == ["0.3.0"]


def test_list_versions_unknown_namespace_returns_empty(collections_root: Path) -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)
    resp = client.get(
        "/admin/ansible/collections/versions",
        params={"namespace": "nope", "collection": "absent"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["versions"] == []


def test_list_versions_no_collections_dir_returns_404() -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)
    resp = client.get(
        "/admin/ansible/collections/versions",
        params={"namespace": "general_ludd"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------

def test_activate_returns_activation_root(
    collections_root: Path, tmp_path: Path,
) -> None:
    app = FastAPI()
    register(app, {})
    proj = tmp_path / "proj"
    proj.mkdir()
    app.state._project_root = str(proj)
    app.state._runner = AnsibleRunnerAdapter(project_root=proj)
    client = TestClient(app)

    resp = client.post(
        "/admin/ansible/collections/activate",
        json={
            "namespace": "general_ludd",
            "collection": "agent",
            "version": "0.1.0",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["namespace"] == "general_ludd"
    assert data["collection"] == "agent"
    assert "gludd-collections-" in data["activation_root"]
    app.state._runner.clear_collection_versions()


def test_activate_without_version_uses_precedence(
    collections_root: Path, tmp_path: Path,
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    coll_root = proj / ".gludd" / "collections"
    coll_root.mkdir(parents=True)
    _make_versioned_collection(coll_root, "general_ludd", "agent", "latest")
    _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")

    app = FastAPI()
    register(app, {})
    app.state._project_root = str(proj)
    app.state._runner = AnsibleRunnerAdapter(project_root=proj)
    client = TestClient(app)

    resp = client.post(
        "/admin/ansible/collections/activate",
        json={"namespace": "general_ludd", "collection": "agent"},
    )
    assert resp.status_code == 200
    app.state._runner.clear_collection_versions()


def test_activate_missing_namespace_returns_400() -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/admin/ansible/collections/activate",
        json={"collection": "agent"},
    )
    assert resp.status_code == 400


def test_activate_missing_collection_returns_400() -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/admin/ansible/collections/activate",
        json={"namespace": "general_ludd"},
    )
    assert resp.status_code == 400


def test_activate_no_runner_returns_503() -> None:
    app = FastAPI()
    register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/admin/ansible/collections/activate",
        json={
            "namespace": "general_ludd",
            "collection": "agent",
            "version": "0.1.0",
        },
    )
    assert resp.status_code == 503


def test_activate_missing_collection_raises_500(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    app = FastAPI()
    register(app, {})
    app.state._project_root = str(proj)
    app.state._runner = AnsibleRunnerAdapter(project_root=proj)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/admin/ansible/collections/activate",
        json={
            "namespace": "nope",
            "collection": "absent",
            "version": "0.1.0",
        },
    )
    assert resp.status_code == 500
    app.state._runner.clear_collection_versions()
