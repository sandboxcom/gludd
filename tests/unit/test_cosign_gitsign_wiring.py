from fastapi.testclient import TestClient

_ADMIN_TOKEN = "test-admin-token"
_ADMIN_HEADERS = {"X-Admin-Token": _ADMIN_TOKEN}


def _create_app_with_secrets(monkeypatch):
    from general_ludd.daemon import create_daemon_app

    monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
    app = create_daemon_app(tick_interval=0.01)

    class _FakeResolver:
        def __init__(self):
            self._store: dict[str, dict] = {}

        def write_secret(self, path: str, data: dict) -> None:
            self._store[path] = data

        def read_secret(self, path: str) -> dict | None:
            return self._store.get(path)

        def delete_secret(self, path: str) -> None:
            self._store.pop(path, None)

        def list_secrets(self, prefix: str) -> list[str]:
            return [k for k in self._store if k.startswith(prefix)]

    resolver = _FakeResolver()
    app.state._secrets_resolver = resolver
    return app, resolver


class TestCosignEndpoints:
    def test_cosign_generate_and_store(self, monkeypatch):
        app, _resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resp = client.post(
            "/admin/signing/cosign/generate",
            json={"project_id": "proj-1", "key_name": "cosign-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_name"] == "cosign-key"
        assert "public_key" in data
        assert "private_key" not in data

    def test_cosign_read_key(self, monkeypatch):
        app, resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resolver.write_secret(
            "projects/proj-1/cosign/mykey",
            {"key_name": "mykey", "private_key": "priv", "public_key": "pub", "created_at": "t"},
        )
        resp = client.get("/admin/signing/cosign/proj-1/mykey")
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_name"] == "mykey"
        assert data["public_key"] == "pub"
        assert "private_key" not in data

    def test_cosign_read_key_not_found(self, monkeypatch):
        app, _resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resp = client.get("/admin/signing/cosign/proj-1/nonexistent")
        assert resp.status_code == 404

    def test_cosign_delete_key(self, monkeypatch):
        app, resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resolver.write_secret(
            "projects/proj-1/cosign/mykey",
            {"key_name": "mykey", "private_key": "priv", "public_key": "pub"},
        )
        resp = client.delete("/admin/signing/cosign/proj-1/mykey")
        assert resp.status_code == 200
        assert resolver.read_secret("projects/proj-1/cosign/mykey") is None

    def test_cosign_list_keys(self, monkeypatch):
        app, resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resolver.write_secret(
            "projects/proj-1/cosign/key-a",
            {"key_name": "key-a", "private_key": "priv", "public_key": "pub"},
        )
        resolver.write_secret(
            "projects/proj-1/cosign/key-b",
            {"key_name": "key-b", "private_key": "priv", "public_key": "pub"},
        )
        resp = client.get("/admin/signing/cosign/list/proj-1")
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 2
        names = {k["key_name"] for k in keys}
        assert names == {"key-a", "key-b"}


class TestGitsignEndpoints:
    def test_gitsign_write_config(self, monkeypatch):
        app, _resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resp = client.post(
            "/admin/signing/gitsign/config",
            json={"project_id": "proj-1", "enabled": True},
        )
        assert resp.status_code == 200

    def test_gitsign_read_config(self, monkeypatch):
        app, resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resolver.write_secret(
            "projects/proj-1/gitsign/config",
            {"fulcio_url": "https://fulcio.sigstore.dev", "enabled": True},
        )
        resp = client.get("/admin/signing/gitsign/proj-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True

    def test_gitsign_read_config_not_found(self, monkeypatch):
        app, _resolver = _create_app_with_secrets(monkeypatch)
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resp = client.get("/admin/signing/gitsign/proj-1")
        assert resp.status_code == 404

    def test_cosign_no_secrets_resolver(self, monkeypatch):
        from general_ludd.daemon import create_daemon_app

        monkeypatch.setenv("GLUDD_ADMIN_TOKEN", _ADMIN_TOKEN)
        app = create_daemon_app(tick_interval=0.01)
        if hasattr(app.state, "_secrets_resolver"):
            delattr(app.state, "_secrets_resolver")
        client = TestClient(app, headers=_ADMIN_HEADERS)
        resp = client.get("/admin/signing/cosign/proj-1/key")
        assert resp.status_code == 503
