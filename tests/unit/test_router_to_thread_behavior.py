"""Behavioral tests: async router handlers offload sync-blocking work.

The ansible / maintenance / skills routers each have `async def` FastAPI
handlers that call sync-blocking work (ansible-galaxy subprocess, git
subprocess, sync httpx fetch). Those calls are wrapped in
``await asyncio.to_thread(...)`` so they run on a worker thread instead of
freezing the event loop.

Each test monkeypatches the underlying blocking callable with a recorder that
detects, at call time, whether it is executing on the event loop thread. A
coroutine body runs on the loop thread, where ``asyncio.get_running_loop()``
succeeds; a ``to_thread`` worker runs on a plain thread, where it raises
``RuntimeError``. So ``on_loop is False`` proves the call was genuinely
offloaded. If a handler regressed to calling the sync work inline, the recorder
would run on the loop thread and the assertion would fail.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _on_event_loop_thread() -> bool:
    """True if running on a thread that owns a live asyncio event loop."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_ansible_search_offloaded(monkeypatch) -> None:
    import general_ludd.routers.ansible as ansible

    seen: dict[str, bool] = {}

    def fake_search(query: str, galaxy_type: str = "role") -> list[dict[str, str]]:
        seen["on_loop"] = _on_event_loop_thread()
        return [{"name": "demo", "description": "d"}]

    monkeypatch.setattr(ansible, "search_galaxy", fake_search)
    app = FastAPI()
    ansible.register(app, {})
    client = TestClient(app)

    resp = client.get("/admin/ansible/search", params={"query": "nginx"})
    assert resp.status_code == 200
    assert resp.json()["results"] == [{"name": "demo", "description": "d"}]
    # Offloaded: the blocking subprocess call ran off the event loop thread.
    assert seen.get("on_loop") is False


def test_ansible_install_offloaded(monkeypatch) -> None:
    import general_ludd.routers.ansible as ansible

    seen: dict[str, bool] = {}

    def fake_install(name: str, galaxy_type: str = "role") -> dict[str, object]:
        seen["on_loop"] = _on_event_loop_thread()
        return {"name": name, "type": galaxy_type, "success": True, "output": ""}

    monkeypatch.setattr(ansible, "install_galaxy", fake_install)
    app = FastAPI()
    ansible.register(app, {})
    client = TestClient(app)

    resp = client.post("/admin/ansible/install", json={"name": "ns.role"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert seen.get("on_loop") is False


def test_maintenance_hot_files_offloaded(monkeypatch) -> None:
    import general_ludd.routers.maintenance as maintenance
    from general_ludd.code_intelligence import git_intel

    seen: dict[str, bool] = {}

    def fake_hot(self, limit: int = 10) -> list[dict[str, object]]:
        seen["hot_on_loop"] = _on_event_loop_thread()
        return [{"path": "a.py", "changes": 3}]

    def fake_recent(self, limit: int = 20) -> list[dict[str, object]]:
        seen["recent_on_loop"] = _on_event_loop_thread()
        return [{"hash": "abc1234", "message": "m", "author": "x", "date": "d"}]

    monkeypatch.setattr(git_intel.GitIntelligence, "hot_files", fake_hot)
    monkeypatch.setattr(git_intel.GitIntelligence, "recent_commits", fake_recent)
    app = FastAPI()
    maintenance.register(app, {})
    client = TestClient(app)

    resp = client.get("/admin/code-intel/hot-files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["hot_files"] == [{"path": "a.py", "changes": 3}]
    assert body["recent_commits"][0]["hash"] == "abc1234"
    # Both git subprocess calls ran off the event loop thread.
    assert seen.get("hot_on_loop") is False
    assert seen.get("recent_on_loop") is False


def test_skills_fetch_offloaded(monkeypatch, tmp_path) -> None:
    import general_ludd.routers.skills as skills
    from general_ludd.security import sanitize
    from general_ludd.skills.fetcher import RemoteSkillFetcher

    seen: dict[str, bool] = {}

    # Force the SSRF guard to accept so the handler reaches the offloaded fetch.
    monkeypatch.setattr(sanitize, "is_safe_fetch_url", lambda url: True)

    def fake_install(self, url: str, target_dir: str) -> Path:
        seen["on_loop"] = _on_event_loop_thread()
        return Path(target_dir) / "demo.md"

    monkeypatch.setattr(RemoteSkillFetcher, "install", fake_install)
    app = FastAPI()
    app.state._config_dir = str(tmp_path)
    skills.register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/admin/skills/fetch", json={"url": "https://example.com/skill.md"}
    )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.com/skill.md"
    # The blocking sync-httpx fetch ran off the event loop thread.
    assert seen.get("on_loop") is False


def test_skills_fetch_github_offloaded(monkeypatch, tmp_path) -> None:
    import general_ludd.routers.skills as skills
    from general_ludd.skills.fetcher import GitHubSkillSource
    from general_ludd.skills.skill import Skill

    seen: dict[str, bool] = {}

    def fake_download(self, skill_path: str) -> Skill:
        seen["on_loop"] = _on_event_loop_thread()
        return Skill(name="demo", description="d", body="hello")

    monkeypatch.setattr(GitHubSkillSource, "download_skill", fake_download)
    app = FastAPI()
    app.state._config_dir = str(tmp_path)
    skills.register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/admin/skills/fetch-github",
        json={"repo": "owner/repo", "path": "skills/demo"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "demo"
    # The blocking sync-httpx download ran off the event loop thread.
    assert seen.get("on_loop") is False
