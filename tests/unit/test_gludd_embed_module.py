"""Unit test for the gludd_embed Ansible module.

Mirrors the gludd_environment module test: loads the real shipped module via
importlib, drives main() with a mocked AnsibleModule + GluddClient, and asserts
it POSTs ``/api/embeddings/similar`` (with the text/top_k/include_embedding
body) and injects the snapshot under ``ansible_facts.gludd_embed`` (read-only,
never changed=True). A 401 status maps to fail_json.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent.parent
MODULE_PATH = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
    / "plugins" / "modules" / "gludd_embed.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_embed", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAnsibleModule:
    """Stand-in for ansible.module_utils.basic.AnsibleModule."""

    def __init__(self, argument_spec: dict[str, Any], supports_check_mode: bool) -> None:
        self.argument_spec = argument_spec
        self.supports_check_mode = supports_check_mode
        self.params = {
            "op": "similar",
            "text": "diagnose a flaky 500 in the request handler",
            "corpus": "skills",
            "text_a": None,
            "text_b": None,
            "texts": None,
            "top_k": 5,
            "work_type": None,
            "include_embedding": False,
            "include_embeddings": False,
            "daemon_url": "http://localhost:8000",
            "psk": "test-psk",
            "timeout": 30,
        }
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeClient:
    """Records the path/body POSTed and returns a canned similarity snapshot."""

    last_path: str | None = None
    last_body: dict[str, Any] | None = None

    def __init__(self, base_url: str, psk: str, timeout: int) -> None:
        self.base_url = base_url
        self.psk = psk
        self.timeout = timeout

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        type(self).last_path = path
        type(self).last_body = body
        return {
            "query_embedding": None,
            "query_embedding_dim": 256,
            "results": [
                {
                    "task_type": "bug_fix",
                    "similarity_score": 0.82,
                    "canonical_text": "Fix a defect ...",
                    "embedding_dim": 256,
                },
                {
                    "task_type": "debugging",
                    "similarity_score": 0.71,
                    "canonical_text": "Debug a problem ...",
                    "embedding_dim": 256,
                },
            ],
            "embedding_method": "hash",
            "_status": 200,
        }


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def test_module_importable_and_has_main(module: ModuleType) -> None:
    assert hasattr(module, "main")


def test_main_posts_similar_and_injects_facts(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    _FakeClient.last_path = None
    _FakeClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _FakeClient)

    module.main()

    # It POSTed the embeddings endpoint with the request body.
    assert _FakeClient.last_path == "/api/embeddings/similar"
    assert _FakeClient.last_body is not None
    assert _FakeClient.last_body["text"] == fake_mod.params["text"]
    assert _FakeClient.last_body["top_k"] == 5
    assert _FakeClient.last_body["include_embedding"] is False
    # work_type was None -> omitted from the body.
    assert "work_type" not in _FakeClient.last_body

    # It injected the snapshot under ansible_facts.gludd_embed, read-only.
    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_mod.exited["changed"] is False
    facts = fake_mod.exited["ansible_facts"]
    assert "gludd_embed" in facts
    snap = facts["gludd_embed"]
    # Transport keys (leading underscore) are stripped.
    assert "_status" not in snap
    assert snap["embedding_method"] == "hash"
    assert snap["results"][0]["task_type"] == "bug_fix"


def test_work_type_included_when_set(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["work_type"] = "feature"
    _FakeClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _FakeClient)

    module.main()

    assert _FakeClient.last_body is not None
    assert _FakeClient.last_body["work_type"] == "feature"


def test_unauthorized_status_fails(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)

    class _UnauthClient(_FakeClient):
        def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            type(self).last_path = path
            return {"_status": 401}

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _UnauthClient)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert fake_mod.failed["status"] == 401


class _CompareClient(_FakeClient):
    """Records the compare POST and returns a canned compare snapshot."""

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        type(self).last_path = path
        type(self).last_body = body
        return {
            "similarity": 0.93,
            "matrix": None,
            "embedding_method": "hash",
            "dim": 256,
            "embeddings": None,
            "_status": 200,
        }


def test_compare_op_posts_compare_and_injects_facts(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "compare"
    fake_mod.params["text_a"] = "agent one says merge the duplicate records"
    fake_mod.params["text_b"] = "agent two says merge the duplicate rows"
    _CompareClient.last_path = None
    _CompareClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _CompareClient)

    module.main()

    assert _CompareClient.last_path == "/api/embeddings/compare"
    assert _CompareClient.last_body is not None
    assert _CompareClient.last_body["text_a"] == fake_mod.params["text_a"]
    assert _CompareClient.last_body["text_b"] == fake_mod.params["text_b"]
    assert _CompareClient.last_body["include_embeddings"] is False
    # Pairwise form must not carry the batch key.
    assert "texts" not in _CompareClient.last_body

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_mod.exited["changed"] is False
    snap = fake_mod.exited["ansible_facts"]["gludd_embed"]
    assert "_status" not in snap
    assert snap["similarity"] == 0.93
    assert snap["embedding_method"] == "hash"


def test_compare_op_batch_posts_texts(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "compare"
    fake_mod.params["texts"] = ["one", "two", "three"]
    _CompareClient.last_path = None
    _CompareClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _CompareClient)

    module.main()

    assert _CompareClient.last_path == "/api/embeddings/compare"
    assert _CompareClient.last_body is not None
    assert _CompareClient.last_body["texts"] == ["one", "two", "three"]
    assert "text_a" not in _CompareClient.last_body
    assert fake_mod.failed is None


def test_compare_op_missing_both_forms_fails(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "compare"
    # No text_a/text_b, no texts.

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _CompareClient)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert fake_mod.failed["failed"] is True


def test_similar_is_default_op(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no op set, the module still hits /similar (back-compat)."""
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    # op defaults to "similar" in the fake's params, mirroring the spec default.
    _FakeClient.last_path = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _FakeClient)

    module.main()

    assert _FakeClient.last_path == "/api/embeddings/similar"
    assert fake_mod.failed is None


class _SearchClient(_FakeClient):
    """Records the search POST and returns a canned search snapshot."""

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        type(self).last_path = path
        type(self).last_body = body
        return {
            "corpus": "skills",
            "query_embedding_dim": 256,
            "embedding_method": "hash",
            "query_embedding": None,
            "results": [
                {
                    "rank": 1,
                    "name": "deep-research",
                    "source_text": "Fan out web searches ...",
                    "similarity_score": 0.71,
                    "metadata": {"category": "research", "tags": ["web"]},
                },
            ],
            "_status": 200,
        }


def test_search_op_posts_search_and_injects_facts(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "search"
    fake_mod.params["text"] = "search the web and write a researched report"
    fake_mod.params["corpus"] = "skills"
    fake_mod.params["top_k"] = 3
    _SearchClient.last_path = None
    _SearchClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _SearchClient)

    module.main()

    assert _SearchClient.last_path == "/api/embeddings/search"
    assert _SearchClient.last_body is not None
    assert _SearchClient.last_body["text"] == fake_mod.params["text"]
    assert _SearchClient.last_body["corpus"] == "skills"
    assert _SearchClient.last_body["top_k"] == 3
    assert _SearchClient.last_body["include_embeddings"] is False
    # Search must not carry the compare/similar-only keys.
    assert "text_a" not in _SearchClient.last_body
    assert "work_type" not in _SearchClient.last_body

    assert fake_mod.failed is None
    assert fake_mod.exited is not None
    assert fake_mod.exited["changed"] is False
    snap = fake_mod.exited["ansible_facts"]["gludd_embed"]
    assert "_status" not in snap
    assert snap["corpus"] == "skills"
    assert snap["results"][0]["name"] == "deep-research"
    assert snap["results"][0]["rank"] == 1


def test_search_op_task_types_corpus(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "search"
    fake_mod.params["corpus"] = "task_types"
    _SearchClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _SearchClient)

    module.main()

    assert _SearchClient.last_path == "/api/embeddings/search"
    assert _SearchClient.last_body is not None
    assert _SearchClient.last_body["corpus"] == "task_types"
    assert fake_mod.failed is None


def test_search_op_traces_corpus(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "search"
    fake_mod.params["corpus"] = "traces"
    _SearchClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _SearchClient)

    module.main()

    assert _SearchClient.last_path == "/api/embeddings/search"
    assert _SearchClient.last_body is not None
    assert _SearchClient.last_body["corpus"] == "traces"
    assert fake_mod.failed is None


def test_search_op_events_corpus(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "search"
    fake_mod.params["corpus"] = "events"
    _SearchClient.last_body = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _SearchClient)

    module.main()

    assert _SearchClient.last_path == "/api/embeddings/search"
    assert _SearchClient.last_body is not None
    assert _SearchClient.last_body["corpus"] == "events"
    assert fake_mod.failed is None


def test_search_op_missing_text_fails(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)
    fake_mod.params["op"] = "search"
    fake_mod.params["text"] = None

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _SearchClient)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert fake_mod.failed["failed"] is True


def test_daemon_error_fails(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mod = _FakeAnsibleModule(argument_spec={}, supports_check_mode=True)

    class _ErrClient(_FakeClient):
        def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            return {"_error": "connection refused", "_status": 0}

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_mod)
    monkeypatch.setattr(module, "GluddClient", _ErrClient)

    module.main()

    assert fake_mod.exited is None
    assert fake_mod.failed is not None
    assert fake_mod.failed["failed"] is True
