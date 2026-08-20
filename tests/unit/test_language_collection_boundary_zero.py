"""TDD contract for the language collection's Python runtime boundary."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from scripts.check_collection_python_boundary import scan_collections

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = ROOT / "collections" / "ansible_collections" / "general_ludd" / "language"
MIGRATED_ROLES = {
    "bom_detect": "bom_detection.json",
    "encoding_detect": "encoding_detection.json",
    "homoglyph_scan": "homoglyph_scan.json",
    "language_detect": "language_detection.json",
    "locale_format": "locale_format.json",
    "phonetic_transcribe": "phonetic_transcription.json",
    "translate": "translation.json",
    "transliterate": "transliteration.json",
    "unicode_analyze": "unicode_analysis.json",
}


def test_language_collection_release_paths_are_strict_zero() -> None:
    """No shipped language plugin or managed-host role may import core Python."""
    assert scan_collections(COLLECTION) == []


@pytest.mark.parametrize(("role", "artifact"), MIGRATED_ROLES.items())
def test_roles_call_controller_action_and_write_managed_host_artifact(
    role: str,
    artifact: str,
) -> None:
    """Text analysis runs on the controller; only artifact writes run remotely."""
    tasks_path = COLLECTION / "roles" / role / "tasks" / "main.yml"
    tasks = yaml.safe_load(tasks_path.read_text(encoding="utf-8"))
    action_tasks = [task for task in tasks if "general_ludd.language.language_operation" in task]
    assert len(action_tasks) == 1
    action = action_tasks[0]["general_ludd.language.language_operation"]
    assert action["operation"] == role
    assert isinstance(action["payload"], dict)
    assert action["daemon_url"] == "{{ daemon_url }}"
    assert action["psk"] == "{{ psk }}"
    assert not any("ansible.builtin.script" in task for task in tasks)
    assert "executable" not in tasks_path.read_text(encoding="utf-8")

    copy_tasks = [task for task in tasks if "ansible.builtin.copy" in task]
    assert len(copy_tasks) == 1
    copy_args = copy_tasks[0]["ansible.builtin.copy"]
    assert copy_args["dest"] == f"{{{{ artifact_dir }}}}/{artifact}"
    assert ".result" in copy_args["content"]


@pytest.mark.parametrize("role", ["bom_detect", "encoding_detect", "unicode_analyze"])
def test_remote_file_inputs_are_slurped_before_controller_analysis(role: str) -> None:
    """Controller code must never assume a managed-host path is locally readable."""
    tasks = yaml.safe_load(
        (COLLECTION / "roles" / role / "tasks" / "main.yml").read_text(encoding="utf-8")
    )
    assert any("ansible.builtin.slurp" in task for task in tasks)


def test_language_client_reuses_one_authenticated_transport() -> None:
    """All operations reuse the agent collection's canonical HTTP transport."""
    from ansible_collections.general_ludd.language.plugins.module_utils.core import (
        LanguageClient,
    )

    transport = MagicMock()
    transport.post.return_value = {
        "_status": 200,
        "result": {"language": "English", "confidence": 0.99},
    }
    client = LanguageClient(psk="secret", transport=transport)
    result = client.execute("language_detect", {"input_text": "hello"})

    assert result["language"] == "English"
    transport.post.assert_called_once_with(
        "/api/language/execute",
        {"operation": "language_detect", "payload": {"input_text": "hello"}},
    )


def test_language_client_fails_closed_without_authentication() -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils.core import (
        LanguageClient,
    )

    with pytest.raises(ValueError, match="psk"):
        LanguageClient(psk="")


@pytest.mark.parametrize("timeout", [0, -1, True, 1.5])
def test_language_client_rejects_invalid_timeouts(timeout: object) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils.core import (
        LanguageClient,
    )

    with pytest.raises(ValueError, match="timeout"):
        LanguageClient(psk="secret", timeout=timeout)  # type: ignore[arg-type]


def test_language_client_rejects_invalid_requests_and_responses() -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils.core import (
        LanguageClient,
        LanguageServiceError,
    )

    transport = MagicMock()
    client = LanguageClient(psk="secret", transport=transport)
    with pytest.raises(ValueError, match="operation"):
        client.execute("", {})
    with pytest.raises(TypeError, match="payload"):
        client.execute("translate", [])  # type: ignore[arg-type]

    transport.post.return_value = {"_status": 401, "detail": "unauthorized"}
    with pytest.raises(LanguageServiceError, match="unauthorized"):
        client.execute("translate", {})

    transport.post.return_value = {"_status": 200, "result": []}
    with pytest.raises(LanguageServiceError, match="invalid result"):
        client.execute("translate", {})


def test_language_compatibility_wrappers_use_authenticated_service() -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import core

    client = MagicMock()
    client.execute.side_effect = [{"language": "English"}, {"translated_text": "hola"}, {"text": "x"}]
    with patch.object(core, "LanguageClient", return_value=client) as factory:
        assert core.detect_language("hello", psk="secret") == {"language": "English"}
        assert core.translate("hello", "en", "es", psk="secret") == {"translated_text": "hola"}
        assert core.transliterate("Москва", "ISO-9", psk="secret") == {"text": "x"}

    assert factory.call_count == 3
    client.execute.assert_any_call("language_detect", {"input_text": "hello"})
    client.execute.assert_any_call(
        "translate",
        {"input_text": "hello", "source_language": "en", "target_language": "es"},
    )
    client.execute.assert_any_call("transliterate", {"input_text": "Москва", "scheme": "ISO-9"})


def test_action_helper_constructs_one_client_and_preserves_result_schema() -> None:
    from ansible_collections.general_ludd.language.plugins.action.language_operation import (
        execute_action,
    )

    client = MagicMock()
    client.execute.return_value = {"translated_text": "hola"}
    factory = MagicMock(return_value=client)
    args: dict[str, Any] = {
        "operation": "translate",
        "payload": {"input_text": "hello", "target_language": "es"},
        "daemon_url": "http://127.0.0.1:8000",
        "psk": "secret",
        "timeout": 7,
    }

    assert execute_action(args, client_factory=factory) == {
        "changed": False,
        "failed": False,
        "result": {"translated_text": "hola"},
    }
    factory.assert_called_once_with(
        daemon_url="http://127.0.0.1:8000",
        psk="secret",
        timeout=7,
    )
    client.execute.assert_called_once_with(
        "translate",
        {"input_text": "hello", "target_language": "es"},
    )


@pytest.mark.parametrize(
    "args, error",
    [
        ({"payload": {}, "daemon_url": "http://daemon", "psk": "x"}, "operation"),
        ({"operation": "translate", "payload": []}, "payload"),
        ({"operation": "translate", "payload": {}, "daemon_url": "", "psk": "x"}, "daemon_url"),
        ({"operation": "translate", "payload": {}, "psk": ""}, "psk"),
        ({"operation": "translate", "payload": {}, "psk": "x", "timeout": 0}, "timeout"),
    ],
)
def test_action_helper_rejects_invalid_arguments(args: dict[str, Any], error: str) -> None:
    from ansible_collections.general_ludd.language.plugins.action.language_operation import (
        execute_action,
    )

    with pytest.raises((TypeError, ValueError), match=error):
        execute_action(args)


def test_action_module_maps_service_errors_without_leaking_secret() -> None:
    from ansible.plugins.action import ActionBase
    from ansible_collections.general_ludd.language.plugins.action import language_operation
    from ansible_collections.general_ludd.language.plugins.module_utils.core import (
        LanguageServiceError,
    )

    action = object.__new__(language_operation.ActionModule)
    action._task = SimpleNamespace(args={"operation": "translate", "psk": "secret"})
    with (
        patch.object(ActionBase, "run", return_value={"base": True}),
        patch.object(
            language_operation,
            "execute_action",
            side_effect=LanguageServiceError("service unavailable"),
        ),
    ):
        result = action.run()

    assert result == {
        "base": True,
        "changed": False,
        "failed": True,
        "msg": "service unavailable",
    }


def test_module_stub_fails_closed_when_action_plugin_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ansible.module_utils import basic
    from ansible_collections.general_ludd.language.plugins.modules import (
        language_operation,
    )

    class ModuleStopped(RuntimeError):
        pass

    fake_module = MagicMock()
    fake_module.fail_json.side_effect = ModuleStopped
    constructor = MagicMock(return_value=fake_module)
    monkeypatch.setattr(basic, "AnsibleModule", constructor)

    with pytest.raises(ModuleStopped):
        language_operation.main()
    with pytest.raises(ModuleStopped):
        runpy.run_path(
            str(COLLECTION / "plugins" / "modules" / "language_operation.py"),
            run_name="__main__",
        )

    argument_spec = constructor.call_args.kwargs["argument_spec"]
    assert argument_spec["psk"]["no_log"] is True
    assert fake_module.fail_json.call_count == 2


def test_detection_fallback_uses_authenticated_language_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client

    service = MagicMock()
    service.execute.return_value = {
        "iso_639_1": "fr",
        "confidence": 0.92,
        "script": "Latin",
    }
    monkeypatch.setattr(model_client, "_call_llm", lambda *_args, **_kwargs: None)

    result = model_client.detect_language_llm("bonjour", language_client=service)

    assert result == {
        "language_code": "fr",
        "confidence": 0.92,
        "script": "Latin",
        "method": "service",
    }
    service.execute.assert_called_once_with("language_detect", {"input_text": "bonjour"})


def test_translation_fallback_uses_authenticated_language_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client

    expected = {
        "source_language": "en",
        "source_text": "hello",
        "target_language": "es",
        "translated_text": "hola",
        "confidence": 1.0,
        "engine": "dictionary",
        "alternative": [],
        "error": "",
    }
    service = MagicMock()
    service.execute.return_value = expected
    monkeypatch.setattr(model_client, "_call_llm", lambda *_args, **_kwargs: None)

    result = model_client.translate_llm(
        "hello",
        "en",
        "es",
        language_client=service,
    )

    assert result == expected
    service.execute.assert_called_once_with(
        "translate",
        {
            "input_text": "hello",
            "source_language": "en",
            "target_language": "es",
        },
    )


@pytest.mark.parametrize(
    "response, expected",
    [
        ({"text": "from-text"}, "from-text"),
        ({"content": "from-content"}, "from-content"),
        ({"message": {"content": "from-message"}}, "from-message"),
        ({"message": "invalid"}, None),
    ],
)
def test_model_call_extracts_shared_service_response(
    response: dict[str, object],
    expected: str | None,
) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client

    client = MagicMock()
    client.chat.return_value = response

    assert model_client._call_llm("hello", "system", client=client) == expected
    messages = client.chat.call_args.args[0]
    assert [message["role"] for message in messages] == ["system", "user"]


def test_model_call_returns_none_on_transport_exception() -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client

    client = MagicMock()
    client.chat.side_effect = RuntimeError("offline")
    assert model_client._call_llm("hello", client=client) is None


def test_detection_model_success_and_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client

    assert model_client.detect_language_llm(" ") == {
        "language_code": "und",
        "confidence": 0.0,
        "script": "",
        "method": "none",
    }
    call = MagicMock(
        return_value='{"language_code":"de","confidence":0.8,"script":"Latin"}'
    )
    monkeypatch.setattr(model_client, "_call_llm", call)

    result = model_client.detect_language_llm("hallo", candidates=["de", "en"])

    assert result == {
        "language_code": "de",
        "confidence": 0.8,
        "script": "Latin",
        "method": "llm",
    }
    assert "de, en" in call.call_args.args[0]


def test_detection_invalid_model_and_failed_service_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client
    from ansible_collections.general_ludd.language.plugins.module_utils.core import (
        LanguageServiceError,
    )

    monkeypatch.setattr(model_client, "_call_llm", lambda *_args, **_kwargs: "not-json")
    service = MagicMock()
    service.execute.side_effect = LanguageServiceError("offline")

    assert model_client.detect_language_llm("bonjour", language_client=service) == {
        "language_code": "und",
        "confidence": 0.0,
        "script": "",
        "method": "unavailable",
    }


def test_translation_model_success_and_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client

    empty = model_client.translate_llm("", "en", "fr")
    assert empty["engine"] == "trivial"
    call = MagicMock(
        return_value='{"translated_text":"bonjour","confidence":0.9,"detected_source":"en"}'
    )
    monkeypatch.setattr(model_client, "_call_llm", call)

    result = model_client.translate_llm("hello", "auto", "fr", formality="formal")

    assert result["translated_text"] == "bonjour"
    assert result["source_language"] == "en"
    assert result["engine"] == "llm"
    assert "formal" in call.call_args.args[0]


def test_translation_invalid_model_and_failed_service_are_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ansible_collections.general_ludd.language.plugins.module_utils import model_client
    from ansible_collections.general_ludd.language.plugins.module_utils.core import (
        LanguageServiceError,
    )

    monkeypatch.setattr(
        model_client,
        "_call_llm",
        lambda *_args, **_kwargs: '{"confidence":"not-a-number"}',
    )
    service = MagicMock()
    service.execute.side_effect = LanguageServiceError("offline")

    result = model_client.translate_llm("hello", "en", "fr", language_client=service)

    assert result["translated_text"] == "hello"
    assert result["engine"] == "passthrough"
    assert result["error"] == "authenticated language services unavailable"
