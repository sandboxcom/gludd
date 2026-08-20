"""Tests for warning-free inference against the pinned local GGUF artifact."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest
from scripts import local_model_inference_smoke as smoke


class _FakeModel:
    def __call__(
        self,
        prompt: str,
        *,
        max_tokens: int,
        echo: bool,
    ) -> dict[str, object]:
        assert prompt == "def hello(): return"
        assert max_tokens == 32
        assert echo is True
        return {"choices": [{"text": "def hello(): return 'ready'"}]}


class _FakeFactory:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def __call__(self, **options: object) -> _FakeModel:
        self.options = options
        return _FakeModel()


def test_inference_uses_native_context_without_capacity_warning(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    factory = _FakeFactory()

    text = smoke.run_inference(model, factory)

    assert text.endswith("'ready'")
    assert factory.options == {
        "model_path": str(model),
        "n_ctx": 0,
        "verbose": False,
    }


def test_inference_fails_closed_when_artifact_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="GGUF artifact is not readable"):
        smoke.run_inference(tmp_path / "missing.gguf", _FakeFactory())


class _ResponseModel:
    def __init__(self, response: object) -> None:
        self.response = response

    def __call__(
        self,
        prompt: str,
        *,
        max_tokens: int,
        echo: bool,
    ) -> object:
        return self.response


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (None, "non-object response"),
        ({}, "no choices"),
        ({"choices": []}, "no choices"),
        ({"choices": [None]}, "no choices"),
        ({"choices": [{}]}, "no generated text"),
        ({"choices": [{"text": ""}]}, "no generated text"),
    ],
)
def test_inference_rejects_invalid_runtime_contracts(
    tmp_path: Path,
    response: object,
    message: str,
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")

    with pytest.raises(RuntimeError, match=message):
        smoke.run_inference(model, lambda **_options: _ResponseModel(response))


def test_main_loads_optional_runtime_and_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    factory = _FakeFactory()
    runtime = ModuleType("llama_cpp")
    runtime.__dict__["__version__"] = "test"
    runtime.__dict__["Llama"] = factory
    monkeypatch.setitem(sys.modules, "llama_cpp", runtime)

    assert smoke.main(["--model-path", str(model)]) == 0

    captured = capsys.readouterr()
    assert "SUCCESS: Local model inference works." in captured.out
    assert captured.err == ""
