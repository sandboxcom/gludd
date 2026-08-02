"""Unit tests for the language HTTP router (routers/language.py)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.language import register


class TestRouterRegistration:
    def test_register_adds_three_routes(self) -> None:
        app = FastAPI()
        register(app, {})
        routes = {getattr(r, "path", "") for r in app.routes}
        assert "/api/language/detect" in routes
        assert "/api/language/translate" in routes
        assert "/api/language/transliterate" in routes


class TestDetectEndpoint:
    @staticmethod
    def _make_app() -> tuple[FastAPI, TestClient]:
        app = FastAPI()
        return app, TestClient(app)

    def test_returns_detection_result(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.detection.detect_language",
            return_value={
                "language": "en",
                "language_name": "English",
                "confidence": 0.95,
                "script": "Latin",
                "iso_639_1": "en",
                "alternative": [],
                "method": "stopword+freq",
            },
        ) as mock_detect:
            register(app, {})
            response = client.post("/api/language/detect", json={"text": "Hello world"})
        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "en"
        assert data["language_name"] == "English"
        assert data["confidence"] == 0.95
        assert data["script"] == "Latin"
        mock_detect.assert_called_once_with("Hello world")

    def test_empty_text_rejected_by_validation(self) -> None:
        app, client = self._make_app()
        register(app, {})
        response = client.post("/api/language/detect", json={"text": ""})
        assert response.status_code == 422

    def test_min_length_validation_rejects_empty_body(self) -> None:
        app, client = self._make_app()
        register(app, {})
        response = client.post("/api/language/detect", json={})
        assert response.status_code == 422


class TestTranslateEndpoint:
    @staticmethod
    def _make_app() -> tuple[FastAPI, TestClient]:
        app = FastAPI()
        return app, TestClient(app)

    def test_translates_with_source_and_target(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.translation.translate",
            return_value={
                "source_language": "en",
                "source_text": "hello",
                "target_language": "de",
                "translated_text": "hallo",
                "confidence": 1.0,
                "engine": "dictionary",
                "alternative": [],
                "error": "",
            },
        ) as mock_translate:
            register(app, {})
            response = client.post(
                "/api/language/translate",
                json={"text": "hello", "source": "en", "target": "de"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["translated_text"] == "hallo"
        assert data["source_language"] == "en"
        assert data["target_language"] == "de"
        assert data["engine"] == "dictionary"
        mock_translate.assert_called_once_with("hello", "en", "de")

    def test_default_source_and_target(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.translation.translate",
            return_value={
                "source_language": "auto",
                "source_text": "hello world",
                "target_language": "en",
                "translated_text": "hello world",
                "confidence": 1.0,
                "engine": "identity",
                "alternative": [],
                "error": "",
            },
        ) as mock_translate:
            register(app, {})
            response = client.post(
                "/api/language/translate",
                json={"text": "hello world"},
            )
        assert response.status_code == 200
        mock_translate.assert_called_once_with("hello world", "auto", "en")

    def test_passthrough_when_engine_unavailable(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.translation.translate",
            return_value={
                "source_language": "xx",
                "source_text": "ni hao",
                "target_language": "yy",
                "translated_text": "ni hao",
                "confidence": 0.0,
                "engine": "passthrough",
                "alternative": [],
                "error": "No translation engine available for xx -> yy",
            },
        ):
            register(app, {})
            response = client.post(
                "/api/language/translate",
                json={"text": "ni hao", "source": "xx", "target": "yy"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["engine"] == "passthrough"
        assert data["confidence"] == 0.0

    def test_empty_text_rejected_by_validation(self) -> None:
        app, client = self._make_app()
        register(app, {})
        response = client.post("/api/language/translate", json={"text": ""})
        assert response.status_code == 422


class TestTransliterateEndpoint:
    @staticmethod
    def _make_app() -> tuple[FastAPI, TestClient]:
        app = FastAPI()
        return app, TestClient(app)

    def test_transliterates_cyrillic_to_latin(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.transliteration.transliterate",
            return_value={
                "source_text": "Привет",
                "source_script": "Cyrillic",
                "target_script": "Latin",
                "transliterated_text": "Privet",
                "scheme": "cyrillic-to-latin",
                "reversible": True,
            },
        ) as mock_translit:
            register(app, {})
            response = client.post(
                "/api/language/transliterate",
                json={"text": "Привет", "target_script": "Latin"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["transliterated_text"] == "Privet"
        assert data["source_script"] == "Cyrillic"
        assert data["target_script"] == "Latin"
        assert data["scheme"] == "cyrillic-to-latin"
        assert data["reversible"] is True
        mock_translit.assert_called_once_with("Привет", "Latin", None)

    def test_transliterates_with_explicit_scheme(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.transliteration.transliterate",
            return_value={
                "source_text": "Москва",
                "source_script": "Cyrillic",
                "target_script": "Latin",
                "transliterated_text": "Moskva",
                "scheme": "cyrillic-to-latin",
                "reversible": True,
            },
        ) as mock_translit:
            register(app, {})
            response = client.post(
                "/api/language/transliterate",
                json={
                    "text": "Москва",
                    "target_script": "Latin",
                    "scheme": "cyrillic-to-latin",
                },
            )
        assert response.status_code == 200
        mock_translit.assert_called_once_with("Москва", "Latin", "cyrillic-to-latin")

    def test_default_target_script_latin(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.transliteration.transliterate",
            return_value={
                "source_text": "Καλημέρα",
                "source_script": "Greek",
                "target_script": "Latin",
                "transliterated_text": "Kalīméra",
                "scheme": "greek-to-latin",
                "reversible": True,
            },
        ) as mock_translit:
            register(app, {})
            response = client.post(
                "/api/language/transliterate",
                json={"text": "Καλημέρα"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["target_script"] == "Latin"
        mock_translit.assert_called_once_with("Καλημέρα", "Latin", None)

    def test_empty_text_rejected_by_validation(self) -> None:
        app, client = self._make_app()
        register(app, {})
        response = client.post("/api/language/transliterate", json={"text": ""})
        assert response.status_code == 422

    def test_same_script_returns_identity(self) -> None:
        app, client = self._make_app()
        with patch(
            "general_ludd.language.transliteration.transliterate",
            return_value={
                "source_text": "Hello",
                "source_script": "Latin",
                "target_script": "Latin",
                "transliterated_text": "Hello",
                "scheme": "identity",
                "reversible": True,
            },
        ):
            register(app, {})
            response = client.post(
                "/api/language/transliterate",
                json={"text": "Hello", "target_script": "Latin"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["transliterated_text"] == "Hello"
        assert data["scheme"] == "identity"
