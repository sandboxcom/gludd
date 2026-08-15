"""Integration tests for POST /admin/stream/dispatch (server-side stream dispatch).

Exercises the real daemon app via ASGITransport with PSK auth enabled, using a
synthetic collection root (containing a trivial ``foo`` role) wired onto
``app.state._role_cloner`` so the router can clone + enqueue without touching
the repo's bundled roles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.stream import RoleCloner

PSK = "test-stream-psk"
AUTH = {"Authorization": f"Bearer {PSK}"}


def _build_collection_root(tmp_path: Path) -> Path:
    root = tmp_path / "collection"
    (root / "roles" / "foo" / "tasks").mkdir(parents=True)
    (root / "roles" / "foo" / "defaults").mkdir(parents=True)
    (root / "roles" / "foo" / "tasks" / "main.yml").write_text(
        "- name: noop\n  ansible.builtin.debug:\n    msg: \"hello {{ stream_id }}\"\n"
    )
    (root / "roles" / "foo" / "defaults" / "main.yml").write_text("foo_default: bar\n")
    return root


def _build_collection_root_with_lint_role(tmp_path: Path) -> Path:
    """Collection root containing a role literally named ``lint_and_check`` —
    mirrors a real role name from the bundled ansible collection, used for the
    happy-path regression check on the role-name validator."""
    root = tmp_path / "collection-lint"
    tasks = root / "roles" / "lint_and_check" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text("- name: noop\n  ansible.builtin.debug:\n    msg: hi\n")
    return root


def _build_trivial_collection_root(tmp_path: Path) -> Path:
    """A collection root whose ``foo`` role writes a known file to artifact_dir."""
    root = tmp_path / "trivial-collection"
    tasks = root / "roles" / "trivial" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text(
        "- name: Write marker file\n"
        "  ansible.builtin.copy:\n"
        '    content: "{{ stream_id }}"\n'
        '    dest: "{{ artifact_dir }}/result.txt"\n'
        '    mode: "0644"\n'
    )
    return root


async def _make_app(monkeypatch, collection_root: Path, work_root: Path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory
    app.state._role_cloner = RoleCloner(
        collection_root=collection_root, work_root=work_root
    )
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestStreamDispatchApi:
    @pytest.mark.asyncio
    async def test_dispatch_unknown_role_returns_422(self, monkeypatch, tmp_path):
        col_root = _build_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "does_not_exist",
                    "source_role_invocation": {},
                    "extra_vars": {},
                },
                headers=AUTH,
            )
            assert resp.status_code == 422, resp.text
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_known_role_returns_task_id(self, monkeypatch, tmp_path):
        col_root = _build_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "foo",
                    "source_role_invocation": {"run_id": "r1"},
                    "extra_vars": {"stream_id": "s1"},
                    "wait_for_completion": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "queued"
            assert "task_id" in data
            assert "clone_path" in data
            assert Path(data["clone_path"]).is_dir()
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_extra_vars_injected_into_clone(self, monkeypatch, tmp_path):
        col_root = _build_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "foo",
                    "source_role_invocation": {},
                    "extra_vars": {"stream_id": "sid-XYZ", "chunk_index": 7},
                    "wait_for_completion": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            clone_path = Path(resp.json()["clone_path"])
            overrides = json.loads((clone_path / "clone-overrides.json").read_text())
            assert overrides["stream_id"] == "sid-XYZ"
            assert overrides["chunk_index"] == 7
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_with_processor_materializes_script(
        self, monkeypatch, tmp_path
    ):
        col_root = _build_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "foo",
                    "source_role_invocation": {},
                    "extra_vars": {"stream_id": "s1"},
                    "processor": {
                        "tool": "whisper.cpp",
                        "binary": "/usr/local/bin/whisper-cli",
                    },
                    "wait_for_completion": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            clone_path = Path(resp.json()["clone_path"])
            assert (clone_path / "process-chunk.sh").is_file()
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_with_agent_processor_creates_wait_playbook(
        self, monkeypatch, tmp_path
    ):
        col_root = _build_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "foo",
                    "source_role_invocation": {},
                    "extra_vars": {"stream_id": "s1"},
                    "processor": {"tool": "agent"},
                    "wait_for_completion": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            clone_path = Path(resp.json()["clone_path"])
            assert (clone_path / "wait-for-agent-result.yml").is_file()
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_wait_for_completion_returns_result(
        self, monkeypatch, tmp_path
    ):
        # Use a trivial collection whose role writes a known file when invoked
        # directly via ansible-playbook. We stub the subprocess invocation to
        # succeed without requiring ansible to be installed; the router just
        # needs to return status=completed.
        col_root = _build_trivial_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)

        # Stub subprocess for the synchronous wait_for_completion path so the
        # test does not require ansible-playbook on PATH.
        import general_ludd.routers.stream as stream_router

        class _FakeProc:
            returncode = 0

            def communicate(self, timeout=None):
                return (b"ok", b"")

        def _fake_popen(*args, **kwargs):
            return _FakeProc()

        original_popen = getattr(stream_router, "_run_subprocess", None)
        stream_router._run_subprocess = _fake_popen
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "trivial",
                    "source_role_invocation": {},
                    "extra_vars": {"stream_id": "sid-sync"},
                    "wait_for_completion": True,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "completed"
            assert "result" in data
            assert "clone_path" in data
        finally:
            if original_popen is not None:
                cast(Any, stream_router)._run_subprocess = original_popen
            else:
                del cast(Any, stream_router)._run_subprocess
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_requires_psk(self, monkeypatch, tmp_path):
        col_root = _build_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "foo",
                    "source_role_invocation": {},
                    "extra_vars": {},
                },
                # No Authorization header.
            )
            assert resp.status_code == 401
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_role_path_traversal_returns_422_and_never_clones(
        self, monkeypatch, tmp_path
    ):
        """role="../..", "/etc/passwd", "a/../../b" must be rejected by pydantic
        validation (422) before ever reaching RoleCloner.clone / shutil.copytree.

        CONFIRMED HIGH path-traversal regression test: prior to the fix, a
        role like "../../../etc" would be joined unconfined into the
        collection's roles/ path and copytree'd, letting a caller copy an
        arbitrary directory and then run ansible-playbook against it.
        """
        col_root = _build_collection_root(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)

        import shutil

        copytree_calls: list[tuple[object, ...]] = []
        original_copytree = shutil.copytree

        def _spy_copytree(*args: object, **kwargs: object) -> Path:
            copytree_calls.append(args)
            return original_copytree(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(shutil, "copytree", _spy_copytree)

        try:
            for bad_role in ("../..", "/etc/passwd", "a/../../b"):
                resp = await client.post(
                    "/admin/stream/dispatch",
                    json={
                        "role": bad_role,
                        "source_role_invocation": {},
                        "extra_vars": {},
                    },
                    headers=AUTH,
                )
                assert resp.status_code == 422, (bad_role, resp.text)
            assert copytree_calls == []
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_legit_underscored_role_name_still_works(
        self, monkeypatch, tmp_path
    ):
        """Happy-path regression: a real role name containing underscores
        (e.g. the bundled ``lint_and_check`` role) must still dispatch
        successfully after the role-name validator was added."""
        col_root = _build_collection_root_with_lint_role(tmp_path)
        work_root = tmp_path / "clones"
        engine, _factory, client, _app = await _make_app(monkeypatch, col_root, work_root)
        try:
            resp = await client.post(
                "/admin/stream/dispatch",
                json={
                    "role": "lint_and_check",
                    "source_role_invocation": {},
                    "extra_vars": {},
                    "wait_for_completion": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "queued"
            assert Path(data["clone_path"]).is_dir()
        finally:
            await client.aclose()
            await engine.dispose()


class TestStreamDispatchRequestRoleValidation:
    """Direct pydantic-model tests for the ``role`` field validator — no app
    or filesystem needed; these confirm the rejection happens at request
    parsing, before ``admin_stream_dispatch`` ever touches the RoleCloner."""

    @pytest.mark.parametrize("bad_role", ["../..", "/etc/passwd", "a/../../b"])
    def test_role_path_traversal_rejected(self, bad_role: str) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role=bad_role)

    def test_role_simple_identifier_accepted(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="lint_and_check")
        assert req.role == "lint_and_check"
