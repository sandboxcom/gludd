"""Tests for cyberchef.py local transform engine (NF.3 Binary RE)."""

from __future__ import annotations

import base64
import importlib
import json
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = Path(__file__).resolve().parents[2] / "collections/ansible_collections/general_ludd/binary_re"
_CYBERCHEF_FILE = _COLLECTION_ROOT / "roles" / "cyberchef_transform" / "files"

if str(_CYBERCHEF_FILE) not in sys.path:
    sys.path.insert(0, str(_CYBERCHEF_FILE))

try:
    cyberchef = importlib.import_module("cyberchef")
    RECIPES = cyberchef.RECIPES
    _transform_local = cyberchef._transform_local
    _xor_transform = cyberchef._xor_transform
    _xor_bruteforce = cyberchef._xor_bruteforce
    _strip_html_tags = cyberchef._strip_html_tags
except ModuleNotFoundError:
    pytest.skip("cyberchef module not available", allow_module_level=True)


class TestRecipesRegistry:
    def test_recipes_dict_populated(self):
        assert isinstance(RECIPES, dict)
        assert len(RECIPES) >= 15

    def test_each_recipe_has_required_keys(self):
        required = {"name", "func", "input_type", "module"}
        for recipe_name, cfg in RECIPES.items():
            missing = required - set(cfg.keys())
            assert not missing, f"recipe {recipe_name} missing keys: {missing}"

    def test_expected_recipes_present(self):
        expected = {
            "base64_decode", "base64_encode",
            "hex_decode", "hex_encode",
            "rot13", "rot47",
            "from_charcode", "to_charcode",
            "xor", "xor_bruteforce",
            "url_decode", "url_encode",
            "binary_decode", "binary_encode",
            "base32_decode", "base32_encode",
            "reverse", "strip_html",
        }
        assert expected.issubset(set(RECIPES.keys()))

    def test_recipe_modules_are_valid(self):
        valid = {"encoding", "encryption", "utility"}
        for cfg in RECIPES.values():
            assert cfg["module"] in valid

    def test_recipe_funcs_are_callable(self):
        for recipe_name, cfg in RECIPES.items():
            assert callable(cfg["func"]), f"{recipe_name} func not callable"


class TestBase64Recipes:
    def test_base64_decode_roundtrip(self):
        original = "Hello World"
        encoded = base64.b64encode(original.encode()).decode()
        result = _transform_local("base64_decode", encoded)
        assert result["output"] == original
        assert result["backend"] == "local"

    def test_base64_encode_roundtrip(self):
        result = _transform_local("base64_encode", "Hello")
        assert result["output"] == base64.b64encode(b"Hello").decode()

    def test_base64_decode_invalid_input_returns_error(self):
        result = _transform_local("base64_decode", "!!!notbase64!!!")
        assert "error" in result


class TestHexRecipes:
    def test_hex_decode(self):
        result = _transform_local("hex_decode", "48656c6c6f")
        assert result["output"] == "Hello"

    def test_hex_decode_with_spaces_and_0x_prefix(self):
        result = _transform_local("hex_decode", "0x48 0x65 0x6c 0x6c 0x6f")
        assert result["output"] == "Hello"

    def test_hex_encode(self):
        result = _transform_local("hex_encode", "Hi")
        assert result["output"] == "4869"


class TestRotRecipes:
    def test_rot13_roundtrip(self):
        original = "Hello"
        encoded = _transform_local("rot13", original)["output"]
        decoded = _transform_local("rot13", encoded)["output"]
        assert decoded == original

    def test_rot47_roundtrip(self):
        original = "Hello"
        encoded = _transform_local("rot47", original)["output"]
        decoded = _transform_local("rot47", encoded)["output"]
        assert decoded == original


class TestCharcodeRecipes:
    def test_to_charcode_then_from_charcode(self):
        encoded = _transform_local("to_charcode", "AB")["output"]
        decoded = _transform_local("from_charcode", encoded)["output"]
        assert decoded == "AB"


class TestXorTransform:
    def test_xor_with_key_roundtrip(self):
        original = "Secret"
        key = "k"
        xored = _xor_transform(original, key)
        recovered = _xor_transform(xored, key)
        assert recovered == original

    def test_xor_without_key_returns_error_message(self):
        result = _xor_transform("data")
        assert "ERROR" in result


class TestXorBruteforce:
    def test_bruteforce_finds_plaintext_key(self):
        plaintext = "Hello World this is a test"
        key_byte = 0x42
        encrypted = bytes(b ^ key_byte for b in plaintext.encode())
        result_json = _xor_bruteforce(encrypted.decode("latin-1"))
        results = json.loads(result_json)
        assert isinstance(results, list)
        assert any(r["key"] == key_byte for r in results)

    def test_bruteforce_returns_json_string(self):
        result = _xor_bruteforce("ABCDEFGH")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert isinstance(parsed, list)


class TestBinaryRecipes:
    def test_binary_encode_decode_roundtrip(self):
        encoded = _transform_local("binary_encode", "AB")["output"]
        decoded = _transform_local("binary_decode", encoded)["output"]
        assert decoded == "AB"


class TestUrlRecipes:
    def test_url_encode_decode_roundtrip(self):
        original = "hello world&foo=bar"
        encoded = _transform_local("url_encode", original)["output"]
        decoded = _transform_local("url_decode", encoded)["output"]
        assert decoded == original


class TestBase32Recipes:
    def test_base32_roundtrip(self):
        original = "Hello"
        encoded = _transform_local("base32_encode", original)["output"]
        decoded = _transform_local("base32_decode", encoded)["output"]
        assert decoded == original


class TestReverseAndStripHtml:
    def test_reverse(self):
        result = _transform_local("reverse", "abc")
        assert result["output"] == "cba"

    def test_strip_html_tags_direct(self):
        result = _strip_html_tags("<p>Hello <b>World</b></p>")
        assert result == "Hello World"

    def test_strip_html_via_recipe(self):
        result = _transform_local("strip_html", "<div>Text</div>")
        assert result["output"] == "Text"


class TestTransformLocalErrors:
    def test_unknown_recipe_returns_error_with_available_list(self):
        result = _transform_local("nonexistent_recipe", "input")
        assert "error" in result
        assert "available" in result
        assert "base64_decode" in result["available"]

    def test_xor_recipe_without_key(self):
        result = _transform_local("xor", "somedata")
        assert "ERROR" in result["output"]

    def test_output_length_field_present(self):
        result = _transform_local("reverse", "abc")
        assert result["output_length"] == 3

    def test_key_used_is_none_when_not_provided(self):
        result = _transform_local("rot13", "abc")
        assert result["key_used"] is None

    def test_key_used_recorded_when_provided(self):
        result = _transform_local("xor", "data", key="k")
        assert result["key_used"] == "k"


class TestModuleImportability:
    def test_transform_local_callable(self):
        assert callable(_transform_local)

    def test_main_callable(self):
        assert callable(cyberchef.main)
