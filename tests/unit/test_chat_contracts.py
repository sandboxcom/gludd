from __future__ import annotations

import argparse
from dataclasses import FrozenInstanceError

import pytest

from general_ludd.chat.contracts import ChatConfig, ChatMessage


class TestChatMessage:
    def test_construct_system_message(self) -> None:
        msg = ChatMessage(role="system", content="You are helpful.")
        assert msg.role == "system"
        assert msg.content == "You are helpful."
        assert msg.timestamp is None
        assert msg.model is None

    def test_construct_user_message(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_construct_assistant_message(self) -> None:
        msg = ChatMessage(role="assistant", content="Hi there!")
        assert msg.role == "assistant"
        assert msg.content == "Hi there!"

    def test_construct_with_timestamp(self) -> None:
        msg = ChatMessage(role="user", content="Hello", timestamp="2026-01-01T00:00:00Z")
        assert msg.timestamp == "2026-01-01T00:00:00Z"

    def test_construct_with_model(self) -> None:
        msg = ChatMessage(role="assistant", content="Hi", model="openai/gpt-4o")
        assert msg.model == "openai/gpt-4o"

    def test_as_api_message_system(self) -> None:
        msg = ChatMessage(role="system", content="You are helpful.")
        assert msg.as_api_message() == {"role": "system", "content": "You are helpful."}

    def test_as_api_message_user(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        assert msg.as_api_message() == {"role": "user", "content": "Hello"}

    def test_as_api_message_excludes_extra_fields(self) -> None:
        msg = ChatMessage(
            role="assistant",
            content="Hi",
            timestamp="2026-01-01T00:00:00Z",
            model="openai/gpt-4o",
        )
        api = msg.as_api_message()
        assert api == {"role": "assistant", "content": "Hi"}
        assert "timestamp" not in api
        assert "model" not in api

    def test_as_persistent_record_basic(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        record = msg.as_persistent_record()
        assert record == {"role": "user", "content": "Hello"}

    def test_as_persistent_record_with_timestamp(self) -> None:
        msg = ChatMessage(role="assistant", content="Hi", timestamp="2026-01-01T00:00:00Z")
        record = msg.as_persistent_record()
        assert record["timestamp"] == "2026-01-01T00:00:00Z"

    def test_as_persistent_record_with_model(self) -> None:
        msg = ChatMessage(role="assistant", content="Hi", model="openai/gpt-4o")
        record = msg.as_persistent_record()
        assert record["model"] == "openai/gpt-4o"

    def test_as_persistent_record_omits_none_fields(self) -> None:
        msg = ChatMessage(role="system", content="sys")
        record = msg.as_persistent_record()
        assert "timestamp" not in record
        assert "model" not in record

    def test_from_dict_basic(self) -> None:
        data: dict[str, str] = {"role": "user", "content": "Hello"}
        msg = ChatMessage.from_dict(data)
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.timestamp is None
        assert msg.model is None

    def test_from_dict_with_extra_fields(self) -> None:
        data = {
            "role": "assistant",
            "content": "Hi",
            "timestamp": "2026-01-01T00:00:00Z",
            "model": "openai/gpt-4o",
        }
        msg = ChatMessage.from_dict(data)
        assert msg.role == "assistant"
        assert msg.content == "Hi"
        assert msg.timestamp == "2026-01-01T00:00:00Z"
        assert msg.model == "openai/gpt-4o"

    def test_from_dict_roundtrip(self) -> None:
        original = ChatMessage(
            role="assistant",
            content="Hello world",
            timestamp="2026-01-01T00:00:00Z",
            model="openai/gpt-4o",
        )
        record = original.as_persistent_record()
        restored = ChatMessage.from_dict(record)
        assert restored == original

    def test_from_dict_rejects_unknown_role(self) -> None:
        with pytest.raises(ValueError, match="unsupported chat role"):
            ChatMessage.from_dict({"role": "intruder", "content": "Hello"})

    def test_frozen_prevents_mutation(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        with pytest.raises(FrozenInstanceError):
            msg.content = "changed"  # type: ignore[misc]


class TestChatConfig:
    def test_defaults(self) -> None:
        cfg = ChatConfig()
        assert cfg.model == "default"
        assert cfg.system_prompt is None
        assert cfg.eval_mode is False
        assert cfg.api_base_url is None
        assert cfg.api_key is None
        assert cfg.project_dir is None
        assert cfg.history_file is None
        assert cfg.save_interval == 5
        assert cfg.resume is False
        assert cfg.max_context is None
        assert cfg.stream is True
        assert cfg.export_format is None
        assert cfg.export_output is None

    def test_custom_values(self) -> None:
        cfg = ChatConfig(
            model="deepseek/deepseek-chat",
            system_prompt="Be brief.",
            eval_mode=True,
            api_base_url="https://api.example.com/v1",
            api_key="sk-test123",
            project_dir="/home/user/project",
            history_file="/tmp/history.jsonl",
            save_interval=10,
            resume=False,
            max_context=4096,
            stream=False,
            export_format="md",
            export_output="/tmp/export.md",
        )
        assert cfg.model == "deepseek/deepseek-chat"
        assert cfg.system_prompt == "Be brief."
        assert cfg.eval_mode is True
        assert cfg.api_base_url == "https://api.example.com/v1"
        assert cfg.api_key == "sk-test123"
        assert cfg.project_dir == "/home/user/project"
        assert cfg.history_file == "/tmp/history.jsonl"
        assert cfg.save_interval == 10
        assert cfg.resume is False
        assert cfg.max_context == 4096
        assert cfg.stream is False
        assert cfg.export_format == "md"
        assert cfg.export_output == "/tmp/export.md"

    def test_save_interval_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="save_interval must be >= 1"):
            ChatConfig(save_interval=0)

    def test_save_interval_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="save_interval must be >= 1"):
            ChatConfig(save_interval=-5)

    def test_to_session_kwargs_defaults(self) -> None:
        cfg = ChatConfig()
        kwargs = cfg.to_session_kwargs()
        assert kwargs["model"] == "default"
        assert kwargs["system_prompt"] is None
        assert kwargs["eval_mode"] is False
        assert kwargs["api_base_url"] is None
        assert kwargs["api_key"] is None
        assert kwargs["project_dir"] is None
        assert kwargs["history_file"] is None
        assert kwargs["save_interval"] == 5
        assert kwargs["resume"] is False
        assert kwargs["max_context"] is None

    def test_to_session_kwargs_custom(self) -> None:
        cfg = ChatConfig(
            model="deepseek/deepseek-chat",
            system_prompt="Be brief.",
            eval_mode=True,
            api_base_url="https://api.example.com/v1",
            api_key="sk-test123",
            project_dir="/home/user/project",
            history_file="/tmp/history.jsonl",
            save_interval=10,
            resume=True,
            max_context=4096,
        )
        kwargs = cfg.to_session_kwargs()
        assert kwargs["model"] == "deepseek/deepseek-chat"
        assert kwargs["system_prompt"] == "Be brief."
        assert kwargs["eval_mode"] is True
        assert kwargs["api_base_url"] == "https://api.example.com/v1"
        assert kwargs["api_key"] == "sk-test123"
        assert kwargs["project_dir"] == "/home/user/project"
        assert kwargs["history_file"] == "/tmp/history.jsonl"
        assert kwargs["save_interval"] == 10
        assert kwargs["resume"] is True
        assert kwargs["max_context"] == 4096

    def test_to_session_kwargs_excludes_export_fields(self) -> None:
        cfg = ChatConfig(export_format="md", export_output="/tmp/out.md")
        kwargs = cfg.to_session_kwargs()
        assert "export_format" not in kwargs
        assert "export_output" not in kwargs
        assert "stream" not in kwargs

    def test_frozen_prevents_mutation(self) -> None:
        cfg = ChatConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.model = "changed"  # type: ignore[misc]

    def test_stream_compare_false_allows_hash(self) -> None:
        cfg1 = ChatConfig(stream=True)
        cfg2 = ChatConfig(stream=False)
        assert cfg1 == cfg2
        assert hash(cfg1) == hash(cfg2)

    def test_from_cli_args_basic(self) -> None:
        ns = argparse.Namespace(
            model="openai/gpt-4o",
            system_prompt=None,
            eval="say hi",
            api_base=None,
            api_key=None,
            project_dir=None,
            history=None,
            save_interval=None,
            resume=False,
            max_context=None,
            stream=True,
            export=None,
            export_output=None,
        )
        cfg = ChatConfig.from_cli_args(ns)
        assert cfg.model == "openai/gpt-4o"
        assert cfg.eval_mode is True
        assert cfg.resume is False

    def test_from_cli_args_no_eval(self) -> None:
        ns = argparse.Namespace(
            model="default",
            system_prompt=None,
            eval=None,
            api_base=None,
            api_key=None,
            project_dir=None,
            history=None,
            save_interval=None,
            resume=False,
            max_context=None,
            stream=False,
            export=None,
            export_output=None,
        )
        cfg = ChatConfig.from_cli_args(ns)
        assert cfg.eval_mode is False

    def test_from_cli_args_resume(self) -> None:
        ns = argparse.Namespace(
            model="default",
            system_prompt=None,
            eval=None,
            api_base=None,
            api_key=None,
            project_dir=None,
            history=None,
            save_interval=None,
            resume=True,
            max_context=None,
            stream=True,
            export=None,
            export_output=None,
        )
        cfg = ChatConfig.from_cli_args(ns)
        assert cfg.resume is True

    def test_from_cli_args_export_fields(self) -> None:
        ns = argparse.Namespace(
            model="default",
            system_prompt=None,
            eval=None,
            api_base=None,
            api_key=None,
            project_dir=None,
            history="/tmp/session.jsonl",
            save_interval=None,
            resume=False,
            max_context=None,
            stream=True,
            export="md",
            export_output="/tmp/out.md",
        )
        cfg = ChatConfig.from_cli_args(ns)
        assert cfg.history_file == "/tmp/session.jsonl"
        assert cfg.export_format == "md"
        assert cfg.export_output == "/tmp/out.md"

    def test_from_cli_args_save_interval_default(self) -> None:
        ns = argparse.Namespace(
            model="default",
            system_prompt=None,
            eval=None,
            api_base=None,
            api_key=None,
            project_dir=None,
            history=None,
            save_interval=None,
            resume=False,
            max_context=None,
            stream=True,
            export=None,
            export_output=None,
        )
        cfg = ChatConfig.from_cli_args(ns)
        assert cfg.save_interval == 5

    def test_from_cli_args_max_context(self) -> None:
        ns = argparse.Namespace(
            model="default",
            system_prompt=None,
            eval=None,
            api_base=None,
            api_key=None,
            project_dir=None,
            history=None,
            save_interval=None,
            resume=False,
            max_context=8192,
            stream=True,
            export=None,
            export_output=None,
        )
        cfg = ChatConfig.from_cli_args(ns)
        assert cfg.max_context == 8192

    def test_equality(self) -> None:
        cfg1 = ChatConfig(model="openai/gpt-4o")
        cfg2 = ChatConfig(model="openai/gpt-4o")
        assert cfg1 == cfg2

    def test_inequality_different_model(self) -> None:
        cfg1 = ChatConfig(model="openai/gpt-4o")
        cfg2 = ChatConfig(model="deepseek/deepseek-chat")
        assert cfg1 != cfg2
