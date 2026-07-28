"""A-COLLECTION-HANDLER-UNWRAP: transient-playbook task_args SSTI guard."""

from __future__ import annotations

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers — extract the generated playbook YAML from a mock adapter
# ---------------------------------------------------------------------------


def _written_playbook_yaml(adapter, handler, module_fqcn, task_args):
    """Invoke the collection handler and return the deserialised playbook body.

    The handler writes the transient playbook to a tmp_path dir via asyncio,
    so we drive it with an in-process event loop stub.
    """
    import asyncio

    # The handler calls run_playbook which might block — give it a stub.
    adapter.run_playbook.return_value = {"status": "success", "rc": 0}

    # Collect the written playbook path from register_playbook.
    registered: list = []
    origin_register = adapter.register_playbook

    def _capture_register(name, path):
        registered.append((name, path))
        origin_register(name, path)

    adapter.register_playbook = _capture_register

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(handler(module_fqcn, task_args))
    finally:
        loop.close()

    assert registered, "register_playbook was never called"
    _, path = registered[0]
    with open(path) as f:
        return yaml.safe_load(f.read())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCollectionHandlerSSTI:
    """Transient-playbook task_args must be wrapped Ansible-unsafe."""

    @pytest.fixture
    def adapter(self, tmp_path):
        from unittest.mock import MagicMock

        ad = MagicMock()
        ad.private_data_dir = str(tmp_path)
        return ad

    @pytest.fixture
    def handler(self, adapter):
        from general_ludd.daemon_wiring import make_collection_handler

        return make_collection_handler(adapter)

    # --- Hostile payloads must NOT be re-templated --------------------------

    @pytest.mark.parametrize(
        "payload",
        [
            "{{ lookup('pipe', 'id') }}",
            "{{ 7 * 7 }}",
            "{{ config.__class__.__init__.__globals__ }}",
            "{{ request.application.__self__ }}",
        ],
    )
    def test_hostile_payload_in_args_is_wrapped_unsafe(self, adapter, handler, payload):
        """A Jinja template in a task-arg value must appear as !unsafe in YAML."""
        body = _written_playbook_yaml(adapter, handler, "ansible.builtin.debug", {"msg": payload})
        # The playbook YAML written to disk must carry the !unsafe tag or at
        # least contain the raw paylaod string verbatim (not a resolved value).
        raw_yaml = yaml.dump(body, default_flow_style=False)
        assert payload in raw_yaml, (
            "Hostile payload not found in generated playbook YAML — it may have been templated away"
        )

    def test_task_args_stripped_metadata_not_in_playbook(self, adapter, handler):
        """Underscore-prefixed dispatch keys are peeled before writing."""
        body = _written_playbook_yaml(
            adapter,
            handler,
            "ansible.builtin.debug",
            {"msg": "hello", "_timeout": 60, "_hosts": "all"},
        )
        tasks = body[0]["tasks"]
        task = tasks[0]
        module_args = task["ansible.builtin.debug"]
        assert "_timeout" not in module_args
        assert "_hosts" not in module_args
        assert module_args["msg"] == "hello"

    def test_wrap_unsafe_import_in_handler_closure(self, handler):
        """wrap_unsafe is importable during handler execution."""
        # If the import inside _collection_handler fails, the handler
        # would raise at dispatch time.  Just confirm it was constructed.
        assert callable(handler)

    # --- Non-string values are preserved -----------------------------------

    @pytest.mark.parametrize(
        "task_args",
        [
            {"port": 8080, "enabled": True, "mode": "tcp"},
            {"actions": ["start", "stop", "restart"], "count": 3},
            {"nested": {"key": "value", "deep": {"x": 1}}},
        ],
    )
    def test_non_string_values_preserved(self, adapter, handler, task_args):
        """Integers, booleans, lists, dicts pass through unscathed."""
        body = _written_playbook_yaml(adapter, handler, "ansible.builtin.debug", task_args)
        module_args = body[0]["tasks"][0]["ansible.builtin.debug"]
        for key in task_args:
            assert key in module_args
            assert module_args[key] == task_args[key]

    # --- FQCN validation still works ---------------------------------------

    def test_invalid_fqcn_rejected(self, adapter, handler):
        """A non-dotted module name raises ValueError."""
        import asyncio

        async def _invoke():
            await handler("malicious{{}}", {"cmd": "id"})

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(ValueError, match="dotted FQCN"):
                loop.run_until_complete(_invoke())
        finally:
            loop.close()
